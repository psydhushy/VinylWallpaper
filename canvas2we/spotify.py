"""Spotify access: web-player token, track/album lookup, and Canvas fetch.

Canvas isn't part of Spotify's public Web API. The web player and the
desktop/mobile apps get it from spclient's ``canvaz-cache`` endpoint using
the same bearer token the web player uses. Spotify keeps changing how the
web player mints that token (TOTP-signed /api/token calls, rotating
secrets), so instead of re-implementing that, we load open.spotify.com in
a headless Chromium and capture the token the page itself sends.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import requests

from . import config
from .proto import Canvas, decode_canvas_response, encode_canvas_request

if TYPE_CHECKING:
    from .official import OfficialAPI

WEB_API = "https://api.spotify.com/v1"
EMBED_URL = "https://open.spotify.com/embed"
CANVAS_ENDPOINT = "https://spclient.wg.spotify.com/canvaz-cache/v0/canvases"
LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_URI_RE = re.compile(
    r"(?:spotify:(?P<kind1>track|album|artist):|open\.spotify\.com/(?:intl-[a-z-]+/)?(?P<kind2>track|album|artist)/)"
    r"(?P<id>[A-Za-z0-9]{22})"
)


class SpotifyError(RuntimeError):
    pass


@dataclass
class Auth:
    access_token: str
    client_token: str = ""
    logged_in: bool = False
    obtained_at: float = field(default_factory=time.time)

    def fresh(self, max_age: float = 45 * 60) -> bool:
        return time.time() - self.obtained_at < max_age


@dataclass
class Track:
    uri: str
    name: str
    artists: list[str]
    album: str = ""
    album_art: str = ""
    track_number: int = 0
    canvas: Canvas | None = None

    @property
    def id(self) -> str:
        return self.uri.rsplit(":", 1)[-1]

    @property
    def label(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"


@dataclass
class Candidate:
    """One search result, with enough detail to tell similar names apart."""
    id: str
    kind: str  # artist, album or track
    name: str
    image: str = ""
    detail: str = ""

    @property
    def uri(self) -> str:
        return f"spotify:{self.kind}:{self.id}"


def clean_sp_dc(value: str | None) -> str | None:
    """Tidy a pasted sp_dc cookie: drop whitespace, quotes and a leading 'sp_dc='."""
    if not value:
        return None
    value = value.strip().strip('"\'').strip()
    if value.lower().startswith("sp_dc="):
        value = value[6:].strip()
    return value or None


def parse_spotify_ref(text: str) -> tuple[str, str] | None:
    """Return (kind, id) for a Spotify track/album/artist URL or URI, else None."""
    m = _URI_RE.search(text.strip())
    if not m:
        return None
    return (m.group("kind1") or m.group("kind2"), m.group("id"))


def _require_playwright():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as e:
        raise SpotifyError(
            "Playwright is required to talk to Spotify's web player.\n"
            "  pip install playwright && playwright install chromium\n"
            "or pass a token yourself with --token / SPOTIFY_TOKEN."
        ) from e
    return sync_playwright, Error


def _new_page(p, PlaywrightError, sp_dc: str | None, headless: bool):
    """Launch Chromium with the optional sp_dc login cookie; returns (browser, page)."""
    # CANVAS2WE_CHROMIUM points at a Chrome/Chromium binary of your choice. Without
    # it, and without Playwright's own download, use a system Chromium (on hosts
    # that install it as a package, like Streamlit Community Cloud).
    exe = os.environ.get("CANVAS2WE_CHROMIUM")
    if not exe and not os.path.exists(p.chromium.executable_path):
        exe = shutil.which("chromium") or shutil.which("chromium-browser")
    try:
        browser = p.chromium.launch(headless=headless, executable_path=exe or None)
    except PlaywrightError as e:
        raise SpotifyError(
            "Couldn't start the headless browser. Run `playwright install chromium` "
            f"(or set CANVAS2WE_CHROMIUM to a Chrome binary).\n{str(e).splitlines()[0]}"
        ) from e
    ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US")
    if sp_dc:
        ctx.add_cookies([{
            "name": "sp_dc", "value": sp_dc, "domain": ".spotify.com",
            "path": "/", "secure": True, "httpOnly": True,
        }])
    return browser, ctx.new_page()


def _goto(page, PlaywrightError, url: str, timeout: float) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
    except PlaywrightError as e:
        raise SpotifyError(f"Couldn't load {url}: {str(e).splitlines()[0]}") from e


def _run_worker(task: str, worker_timeout: float = 180, **kwargs):
    """Run a browser task in a separate Python process and return its JSON result.

    Playwright needs asyncio's default event loop. Streamlit (via Tornado)
    switches Windows to the selector loop, under which Playwright can't start
    Chromium (NotImplementedError). A clean child process sidesteps that
    wherever the app is hosted.
    """
    # Import the worker from this exact copy of the package: put its parent
    # folder first on sys.path inside the child. Looking it up by name (-m)
    # can pick up a different "canvas2we" from the current folder or an older
    # pip install.
    pkg_root = str(Path(__file__).resolve().parent.parent)
    bootstrap = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
                 "from canvas2we._browser_worker import main; sys.exit(main())")
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap, pkg_root, task, json.dumps(kwargs)],
        capture_output=True, text=True, timeout=worker_timeout,
    )
    lines = proc.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise SpotifyError(f"The headless browser helper crashed:\n{tail}") from None
    if "error" in result:
        raise SpotifyError(result["error"])
    return result["value"]


def capture_web_player_auth(sp_dc: str | None = None, timeout: float = 45.0) -> Auth:
    """Open the web player in headless Chromium and grab its bearer token.

    ``sp_dc`` is the optional login cookie from a logged-in browser session.
    Without it you get an anonymous token, which Spotify typically answers
    with no canvases.
    """
    v = _run_worker("auth", sp_dc=sp_dc, timeout=timeout)
    return Auth(v["access_token"], v["client_token"], logged_in=v["logged_in"])


def browser_search(query: str, kind: str, sp_dc: str | None = None, limit: int = 5) -> list[tuple[str, str]]:
    """Search with the web player's own search page; returns [(id, name)] best first."""
    return [tuple(hit) for hit in _run_worker("search", query=query, kind=kind, sp_dc=sp_dc, limit=limit)]


def installed_browser() -> tuple[str, str] | None:
    """(name, path) of an installed Chrome, or else Edge, for the login window."""
    env = os.environ.get
    candidates = [
        ("Chrome", env("CANVAS2WE_LOGIN_BROWSER")),
        ("Chrome", os.path.join(env("ProgramFiles", ""), r"Google\Chrome\Application\chrome.exe")),
        ("Chrome", os.path.join(env("ProgramFiles(x86)", ""), r"Google\Chrome\Application\chrome.exe")),
        ("Chrome", os.path.join(env("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")),
        ("Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ("Chrome", shutil.which("google-chrome") or shutil.which("chrome")),
        ("Edge", os.path.join(env("ProgramFiles(x86)", ""), r"Microsoft\Edge\Application\msedge.exe")),
        ("Edge", os.path.join(env("ProgramFiles", ""), r"Microsoft\Edge\Application\msedge.exe")),
    ]
    return next(((name, path) for name, path in candidates if path and os.path.isfile(path)), None)


def browser_login(timeout: float = 600.0) -> dict:
    """Log in to Spotify in a browser window; returns {sp_dc, name}.

    With Chrome (or Edge) installed, this opens Spotify's login page in a
    normal window of that browser, on a profile kept just for this app, so
    "Sign in with Google" works and the browser remembers you next time.
    Nothing drives the pages: the app only lists the window's tabs through
    the browser's local DevTools port, and once a tab reaches the web player
    it reads the session cookie and closes the window. If you close it first,
    the session is read from the profile instead. Without either browser it
    falls back to a Playwright-controlled Chromium window.
    """
    browser = installed_browser()
    if not browser:
        return _run_worker("login", worker_timeout=timeout + 30, timeout=timeout)
    profile = config.config_dir() / "login-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return _run_worker("chrome_login", worker_timeout=timeout + 90, executable=browser[1],
                       profile=str(profile), timeout=timeout)


def _chrome_login_inprocess(executable: str, profile: str, timeout: float = 600.0) -> dict:
    profile_dir = Path(profile)
    deadline = time.time() + timeout
    for attempt in range(2):
        port_file = profile_dir / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        started = time.time()
        proc = subprocess.Popen([executable, f"--user-data-dir={profile}", "--remote-debugging-port=0",
                                 "--no-first-run", "--no-default-browser-check", "--new-window", LOGIN_URL])
        login = _watch_login(proc, profile_dir, port_file, deadline)
        if login:
            return login
        # On a brand-new profile Chrome sometimes quits at once without showing
        # a window; nobody logs in that fast, so open it once more.
        if time.time() - started > 8:
            break
    return _read_login_inprocess(executable, profile)  # the window was closed by hand


def _watch_login(proc: subprocess.Popen, profile: Path, port_file: Path, deadline: float) -> dict | None:
    """Wait for the login window to reach the web player logged in (returns the login) or close (None).

    While the DevTools port answers, the window is open. Once it stops, the
    profile's lock ('lockfile' on Windows, 'SingletonLock' elsewhere) says
    whether Chrome has really finished with it; the process we started isn't
    reliable on its own, as Chrome can hand over to a restarted copy.
    """
    port, closed_since, last_check = None, None, 0.0
    while time.time() < deadline:
        if port is None and port_file.exists():
            try:
                port = int(port_file.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                pass
        pages = None
        if port:
            try:
                pages = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
            except (requests.RequestException, ValueError):
                pass
        if pages is not None:
            closed_since = None
            on_player = any(p.get("type") == "page" and p.get("url", "").startswith("https://open.spotify.com")
                            for p in pages)
            if on_player and time.time() - last_check > 5:
                last_check = time.time()
                login = _take_login(port)
                if login:
                    return login
        elif proc.poll() is None or (profile / "lockfile").exists() or os.path.lexists(profile / "SingletonLock"):
            closed_since = None
        elif closed_since is None:
            closed_since = time.time()
        elif time.time() - closed_since > 2:
            return None
        time.sleep(1)
    proc.terminate()
    raise SpotifyError("Timed out waiting for you to log in to Spotify.")


def _take_login(port: int) -> dict | None:
    """Read the session from the open login window and close it; None if not logged in yet."""
    sync_playwright, PlaywrightError = _require_playwright()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        except PlaywrightError:
            return None
        ctx = browser.contexts[0]
        sp_dc = next((c["value"] for c in ctx.cookies("https://open.spotify.com") if c["name"] == "sp_dc"), None)
        if not sp_dc:
            browser.close()  # only disconnects; the window stays open
            return None
        name = _account_name(ctx.new_page(), PlaywrightError)
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except PlaywrightError:
            pass
    return {"sp_dc": sp_dc, "name": name}


def _read_login_inprocess(executable: str, profile: str) -> dict:
    """Read the Spotify session from the login profile after its window was closed."""
    sync_playwright, PlaywrightError = _require_playwright()
    with sync_playwright() as p:
        # Chrome can take a few seconds to finish shutting down; until then it
        # won't open the profile a second time.
        for attempt in range(8):
            try:
                ctx = p.chromium.launch_persistent_context(profile, executable_path=executable, headless=True,
                                                           user_agent=USER_AGENT)
                break
            except PlaywrightError as e:
                if attempt == 7:
                    raise SpotifyError("Couldn't read the Spotify login. Close every window the login opened, "
                                       f"then try again.\n{str(e).splitlines()[0]}") from e
                time.sleep(2)
        try:
            sp_dc = next((c["value"] for c in ctx.cookies("https://open.spotify.com") if c["name"] == "sp_dc"), None)
            if not sp_dc:
                raise SpotifyError("The window was closed before you finished logging in to Spotify. "
                                   "Press Connect to Spotify and try again.")
            name = _account_name(ctx.new_page(), PlaywrightError)
        finally:
            ctx.close()
    return {"sp_dc": sp_dc, "name": name}


def _account_name(page, PlaywrightError) -> str | None:
    """The logged-in account's display name, via the web player's own token (best effort)."""
    bearer: dict[str, str] = {}

    def on_request(req):
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            bearer.setdefault("token", auth.split(" ", 1)[1])

    page.on("request", on_request)
    try:
        page.goto("https://open.spotify.com/", wait_until="domcontentloaded", timeout=30_000)
        for _ in range(20):
            if "token" in bearer:
                break
            page.wait_for_timeout(500)
    except PlaywrightError:
        return None
    if "token" not in bearer:
        return None
    try:
        me = requests.get(f"{WEB_API}/me", headers={"Authorization": f"Bearer {bearer['token']}"}, timeout=10)
        return (me.json().get("display_name") or me.json().get("id")) if me.ok else None
    except (requests.RequestException, ValueError):
        return None


def _browser_login_inprocess(timeout: float = 300.0) -> dict:
    sync_playwright, PlaywrightError = _require_playwright()
    closed = SpotifyError("The Spotify login window was closed before you finished logging in.")

    with sync_playwright() as p:
        browser, page = _new_page(p, PlaywrightError, None, headless=False)
        try:
            _goto(page, PlaywrightError, LOGIN_URL, 60)
            deadline = time.time() + timeout
            sp_dc = None
            while not sp_dc:
                if time.time() > deadline:
                    raise SpotifyError("Timed out waiting for you to log in to Spotify.")
                if page.is_closed():
                    raise closed
                sp_dc = next((c["value"] for c in page.context.cookies("https://open.spotify.com")
                              if c["name"] == "sp_dc"), None)
                page.wait_for_timeout(500)
            name = _account_name(page, PlaywrightError)
        except PlaywrightError as e:
            raise closed from e
        finally:
            try:
                browser.close()
            except PlaywrightError:
                pass
    return {"sp_dc": sp_dc, "name": name}


def _capture_auth_inprocess(sp_dc: str | None = None, url: str = "https://open.spotify.com/",
                            timeout: float = 45.0, headless: bool = True) -> Auth:
    sync_playwright, PlaywrightError = _require_playwright()
    captured: dict[str, str] = {}

    def on_request(req):
        if "spotify.com" not in req.url:
            return
        headers = req.headers
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and "access_token" not in captured:
            captured["access_token"] = auth.split(" ", 1)[1]
        if headers.get("client-token") and "client_token" not in captured:
            captured["client_token"] = headers["client-token"]

    def on_response(resp):
        # The token endpoint's JSON is the most direct source when visible.
        if "access_token" in captured or not re.search(r"/(api/token|get_access_token)\b", resp.url):
            return
        try:
            token = resp.json().get("accessToken")
        except Exception:
            return
        if token:
            captured["access_token"] = token

    with sync_playwright() as p:
        browser, page = _new_page(p, PlaywrightError, sp_dc, headless)
        try:
            page.on("request", on_request)
            page.on("response", on_response)
            _goto(page, PlaywrightError, url, timeout)
            deadline = time.time() + timeout
            token_at = None
            # Once we have the bearer token, give the client-token header (which
            # spclient increasingly expects) a few more seconds to show up.
            while time.time() < deadline:
                if "access_token" in captured:
                    token_at = token_at or time.time()
                    if "client_token" in captured or time.time() - token_at > 6:
                        break
                page.wait_for_timeout(250)
        finally:
            browser.close()

    if "access_token" not in captured:
        raise SpotifyError(
            "Could not capture a Spotify web-player token (open.spotify.com didn't load or "
            "Spotify changed its web player). Pass --token manually; see README."
        )
    return Auth(captured["access_token"], captured.get("client_token", ""), logged_in=bool(sp_dc))


def _browser_search_inprocess(query: str, kind: str, sp_dc: str | None = None, limit: int = 5,
                              timeout: float = 30.0, headless: bool = True) -> list[tuple[str, str]]:
    """Reads result links off the rendered page rather than calling Spotify's
    internal search API, whose request format changes often.
    """
    sync_playwright, PlaywrightError = _require_playwright()
    with sync_playwright() as p:
        browser, page = _new_page(p, PlaywrightError, sp_dc, headless)
        try:
            # Only the result links matter: skip images, video and fonts so the page settles sooner.
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in
                       ("image", "media", "font") else route.continue_())
            _goto(page, PlaywrightError, f"https://open.spotify.com/search/{quote(query)}/{kind}s", timeout)
            selector = f'main a[href*="/{kind}/"]'
            try:
                page.wait_for_selector(selector, timeout=timeout * 1000)
            except PlaywrightError:
                return []
            page.wait_for_timeout(500)  # let the first batch of results finish rendering
            links = page.eval_on_selector_all(
                selector, "els => els.map(e => [e.getAttribute('href'), e.getAttribute('title') || e.textContent])")
        finally:
            browser.close()
    hits: dict[str, str] = {}
    for href, text in links:
        m = re.search(rf"/{kind}/([A-Za-z0-9]{{22}})", href or "")
        if m and m.group(1) not in hits:
            hits[m.group(1)] = (text or "").strip()
        if len(hits) >= limit:
            break
    return list(hits.items())


def _sort_releases(releases: list[dict]) -> list[dict]:
    """Albums, then EPs, singles and compilations; newest first within each."""
    order = {"ALBUM": 0, "EP": 1, "SINGLE": 2, "COMPILATION": 3}
    return sorted(releases, key=lambda x: (order.get(x["type"], 4), -(x["year"] or 0)))


class SpotifyClient:
    def __init__(self, auth: Auth, session: requests.Session | None = None, sp_dc: str | None = None,
                 official: OfficialAPI | None = None):
        self.auth = auth
        self.sp_dc = sp_dc  # lets searches run logged in, like the token
        self.official = official  # the user's Web API login, for search and discographies
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT

    def _headers(self, **extra: str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.auth.access_token}", "app-platform": "WebPlayer"}
        if self.auth.client_token:
            h["client-token"] = self.auth.client_token
        h.update(extra)
        return h

    def _get(self, path: str, max_wait: float = 120.0, **params) -> dict:
        url = path if path.startswith("http") else f"{WEB_API}{path}"
        waited = 0.0
        while True:
            r = self.http.get(url, params=params, headers=self._headers(), timeout=20)
            if r.status_code != 429:
                break
            # Anonymous tokens hit 429 quickly; honour Retry-After for short waits.
            delay = float(r.headers.get("Retry-After", 5) or 5) + 1
            if waited + delay > max_wait:
                raise SpotifyError(f"Rate limited by Spotify; retry after {delay:.0f}s.")
            print(f"Rate limited by Spotify, waiting {delay:.0f}s...")
            time.sleep(delay)
            waited += delay
        if r.status_code == 401:
            raise SpotifyError("Spotify rejected the token (401). It may have expired; fetch a new one.")
        r.raise_for_status()
        return r.json()

    # --- metadata -------------------------------------------------------

    def _embed(self, kind: str, sid: str) -> dict:
        """Entity data from the public embed player page (no token, no rate limit).

        The Web API (api.spotify.com) answers web-player tokens with 429s,
        so metadata comes from here instead.
        """
        r = self.http.get(f"{EMBED_URL}/{kind}/{sid}", timeout=20)
        if r.status_code == 404:
            raise SpotifyError(f"Spotify has no {kind} with id {sid}")
        r.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
        try:
            return json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise SpotifyError(f"Couldn't read Spotify's embed page for {kind} {sid}; its format may have changed.") from e

    @staticmethod
    def _cover(entity: dict) -> str:
        images = (entity.get("visualIdentity") or {}).get("image") or []
        return max(images, key=lambda i: i.get("maxWidth") or 0)["url"] if images else ""

    def track(self, track_id: str) -> Track:
        e = self._embed("track", track_id)
        return Track(uri=e["uri"], name=e["name"], artists=[a["name"] for a in e.get("artists", [])],
                     album_art=self._cover(e))

    def album_tracks(self, album_id: str) -> list[Track]:
        e = self._embed("album", album_id)
        cover = self._cover(e)
        return [
            Track(uri=t["uri"], name=t["title"], artists=[a.strip() for a in t.get("subtitle", "").split(",") if a.strip()],
                  album=e["name"], album_art=cover, track_number=n)
            for n, t in enumerate(e.get("trackList", []), 1)
        ]

    def artist_top_tracks(self, artist_id: str) -> list[Track]:
        """The artist's top tracks (usually 10), from the embed page; album is set to the artist's name."""
        e = self._embed("artist", artist_id)
        image = self._cover(e)
        return [Track(uri=t["uri"], name=t["title"], album=e["name"], album_art=image,
                      artists=[a.strip() for a in t.get("subtitle", "").split(",") if a.strip()])
                for t in e.get("trackList", [])]

    def artist_releases(self, artist_id: str, log=print) -> tuple[str, list[dict]]:
        """(artist name, releases), each release {id, name, year, type}.

        With an official login this is the full discography from the Web
        API; otherwise it falls back to the artist page.
        """
        if self.official:
            try:
                name = self.official.artist_name(artist_id)
                return name, _sort_releases(self.official.artist_releases(artist_id))
            except SpotifyError as e:
                log(f"Official API discography failed, using the artist page instead: {e}")
        return self._artist_page_releases(artist_id)

    def _artist_page_releases(self, artist_id: str) -> tuple[str, list[dict]]:
        """(artist name, releases) from the artist page's server-rendered state.

        Covers the page's albums, singles, compilations and popular releases
        (Spotify only pre-renders the first batch of each, so very large
        discographies may be partial).
        """
        # A plain user agent gets the server-rendered page; a full browser UA
        # gets an empty app shell.
        r = self.http.get(f"https://open.spotify.com/artist/{artist_id}",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code == 404:
            raise SpotifyError(f"Spotify has no artist with id {artist_id}")
        r.raise_for_status()
        m = re.search(r'<script[^>]*id="initialState"[^>]*>(.*?)</script>', r.text, re.S)
        try:
            raw = m.group(1).strip()
            state = json.loads(raw) if raw.startswith("{") else json.loads(base64.b64decode(raw))
            artist = state["entities"]["items"][f"spotify:artist:{artist_id}"]
            disco = artist["discography"]
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise SpotifyError(f"Couldn't read Spotify's artist page for {artist_id}; its format may have changed.") from e

        releases: dict[str, dict] = {}

        def walk(o):
            if isinstance(o, dict):
                uri = o.get("uri", "")
                if uri.startswith("spotify:album:") and o.get("name"):
                    releases.setdefault(uri, {
                        "id": uri.rsplit(":", 1)[-1], "name": o["name"],
                        "year": (o.get("date") or {}).get("year"), "type": o.get("type", ""),
                    })
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        # topTracks is skipped: it points at whatever album each hit is on,
        # including other people's compilations.
        for key in ("albums", "singles", "compilations", "popularReleasesAlbums", "latest"):
            walk(disco.get(key))
        name = (artist.get("profile") or {}).get("name", "")
        return name, _sort_releases(list(releases.values()))

    def artist_tracks(self, artist_id: str, log=print, progress=None) -> list[Track]:
        """Every track on the artist's releases; ``progress(done, total)`` is called as releases load."""
        name, releases = self.artist_releases(artist_id, log=log)
        log(f"Scanning {len(releases)} releases by {name or artist_id}...")
        results: list[list[Track] | None] = [None] * len(releases)
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(self._album_tracks_or_none, r["id"]): i for i, r in enumerate(releases)}
            for done, future in enumerate(as_completed(futures), 1):
                results[futures[future]] = future.result()
                if progress:
                    progress(done, len(releases))
        tracks, seen = [], set()
        for release, album in zip(releases, results):
            if album is None:
                log(f"  skipped {release['name']} (couldn't load it)")
                continue
            for t in album:
                if t.uri not in seen:
                    seen.add(t.uri)
                    tracks.append(t)
        return tracks

    def _album_tracks_or_none(self, album_id: str) -> list[Track] | None:
        try:
            return self.album_tracks(album_id)
        except (SpotifyError, requests.RequestException):
            return None

    def search(self, query: str, kind: str = "track", log=print) -> list[tuple[str, str]]:
        """[(id, name)] best match first. Uses the official API when logged in,
        else the web player's search page, then the Web API with the
        web-player token (which often rate-limits)."""
        if self.official:
            try:
                return self.official.search(query, kind)
            except SpotifyError as e:
                log(f"Official API search failed, trying the web player: {e}")
        return self._fallback_search(query, kind)

    def search_candidates(self, query: str, kind: str = "artist", limit: int = 8, log=print) -> list[Candidate]:
        """Search results with pictures and details, best match first, for picking from."""
        if self.official:
            try:
                return self.official.search_candidates(query, kind, limit)
            except SpotifyError as e:
                log(f"Official API search failed, trying the web player: {e}")
        hits = self._fallback_search(query, kind, limit)
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(lambda hit: self._embed_candidate(kind, *hit), hits))

    def _fallback_search(self, query: str, kind: str, limit: int = 5) -> list[tuple[str, str]]:
        try:
            hits = browser_search(query, kind, sp_dc=self.sp_dc, limit=limit)
        except SpotifyError:
            hits = []
        if hits:
            return hits
        try:
            data = self._get("/search", max_wait=30, q=query, type=kind, limit=limit)
        except SpotifyError as e:
            raise self._search_hint(e) from e
        return [(i["id"], i["name"]) for i in data[f"{kind}s"]["items"]]

    def _embed_candidate(self, kind: str, sid: str, name: str) -> Candidate:
        """Fill in a picture and details for a bare search hit from its embed page."""
        try:
            e = self._embed(kind, sid)
        except (SpotifyError, requests.RequestException):
            return Candidate(sid, kind, name)
        if kind == "artist":
            top = [t["title"] for t in e.get("trackList", [])[:3]]
            detail = "Top tracks: " + ", ".join(top) if top else ""
        elif kind == "album":
            released = e.get("releaseDate") or ""
            if isinstance(released, dict):
                released = released.get("isoString") or ""
            detail = " · ".join(x for x in (e.get("subtitle", ""), str(released)[:4]) if x)
        else:
            detail = ", ".join(a["name"] for a in e.get("artists", []))
        return Candidate(sid, kind, e.get("name") or name, self._cover(e), detail)

    def resolve(self, query: str, kind: str = "track", log=print, progress=None) -> list[Track]:
        """Turn a URL/URI or search text into a list of tracks.

        ``kind`` says what search text names: a track (one track), an album
        (its tracks) or an artist (every track on their releases). A link
        or URI decides the kind by itself.
        """
        ref = parse_spotify_ref(query)
        if ref:
            kind, sid = ref
        else:
            hits = self.search(query, kind, log=log)
            if not hits:
                raise SpotifyError(f"No {kind} found for {query!r}")
            sid = hits[0][0]
            log(f"Best {kind} match: {hits[0][1] or sid}")
        if kind == "artist":
            return self.artist_tracks(sid, log=log, progress=progress)
        if kind == "album":
            return self.album_tracks(sid)
        return [self.track(sid)]

    def _search_hint(self, e: SpotifyError) -> SpotifyError:
        return SpotifyError(f"{e}\nSearch uses Spotify's Web API, which often rate-limits web-player "
                            "tokens. Paste the track, album or artist link instead.")

    # --- canvas ---------------------------------------------------------

    def canvases(self, track_uris: list[str], batch: int = 50) -> dict[str, Canvas]:
        """Look up the Canvas for each track URI. Tracks without one are absent."""
        found: dict[str, Canvas] = {}
        for i in range(0, len(track_uris), batch):
            found.update(self._canvas_batch(track_uris[i:i + batch]))
        return found

    def _canvas_batch(self, track_uris: list[str]) -> dict[str, Canvas]:
        r = self.http.post(
            CANVAS_ENDPOINT,
            data=encode_canvas_request(track_uris),
            headers=self._headers(**{
                "Content-Type": "application/x-protobuf",
                "Accept": "application/protobuf",
            }),
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise SpotifyError(
                f"Canvas endpoint refused the token ({r.status_code}). Try again with a logged-in "
                "token: set SPOTIFY_SP_DC to your sp_dc cookie (see README)."
            )
        r.raise_for_status()
        return {c.entity_uri: c for c in decode_canvas_response(r.content) if c.url}

    @property
    def anonymous(self) -> bool:
        return not self.auth.logged_in

    def attach_canvases(self, tracks: list[Track]) -> list[Track]:
        found = self.canvases([t.uri for t in tracks])
        for t in tracks:
            t.canvas = found.get(t.uri)
        return tracks

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.http.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        return dest
