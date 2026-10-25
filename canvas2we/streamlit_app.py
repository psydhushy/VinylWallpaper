"""Streamlit UI for Wallpaper Engine Vinyl: search Spotify, preview canvases,
build Wallpaper Engine projects.

Run with ``canvas2we ui`` (or ``streamlit run canvas2we/streamlit_app.py``).
Set CANVAS2WE_DONATE_URL (for example a paypal.me link) to show a Donate
button in the header and footer.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

import segno
import streamlit as st

from canvas2we import config, official, theme, wallpaper as wp
from canvas2we.cli import get_client, lookup, make_wallpaper
from canvas2we.spotify import (Candidate, SpotifyError, Track, browser_login, clean_sp_dc, installed_browser,
                               parse_spotify_ref)

APP_NAME = "Wallpaper Engine Vinyl"
# Public mode (canvas2we ui --public, or CANVAS2WE_PUBLIC=1) is for sharing the app with other
# people: it hides everything that acts on the computer running it.
PUBLIC = os.environ.get("CANVAS2WE_PUBLIC") == "1"
# Hosted copies (public mode) usually get about 1 GB of memory, so they do less at once.
MAX_RENDERS = int(os.environ.get("CANVAS2WE_MAX_RENDERS", "1" if PUBLIC else "2"))  # wallpapers rendered at once
FINGERPRINT_WORKERS = int(os.environ.get("CANVAS2WE_FINGERPRINT_WORKERS", "4" if PUBLIC else "24"))
DONATE_URL = os.environ.get("CANVAS2WE_DONATE_URL") or config.get("donate_url")
MAX_CARDS = 60
COLUMNS = 5
PILL_ALBUMS = 6  # more albums than this get a dropdown instead of buttons
KINDS = {"Artist": "artist", "Album": "album", "Song": "track"}
# Shown on the front page: the first few as example chips, all of them in "Most popular".
POPULAR_ARTISTS = {
    "Taylor Swift": "06HL4z0CvFAxyc27GXpf02", "The Weeknd": "1Xyo4u8uXC1ZmMpatF05PJ",
    "Sabrina Carpenter": "74KM79TiuVKeVCqs8QtB0B", "Billie Eilish": "6qqNVTkY8uBg9cP3Jd7DAH",
    "Bad Bunny": "4q3ewBCX7sLwd24euuV69X", "Drake": "3TVXtAsR1Inumwj472S9r4",
    "Dua Lipa": "6M2wZ9GZgrQXHCFfjv46we", "Ariana Grande": "66CXWjxzNUsdJxJ2JdwvnR",
    "Kendrick Lamar": "2YZyLoL8N0Wb9xBt1NhZWg", "SZA": "7tYKF4w9nC0nq9CsPZTHyP",
    "Bruno Mars": "0du5cEVh5yTK9QJze8zA0C", "Olivia Rodrigo": "1McMsnEElThX1knmY4oliG",
}
EXAMPLES = list(POPULAR_ARTISTS)[:6]
PER_ARTIST = 2  # canvases per artist in "Most popular"
FINGERPRINTS = config.config_dir() / "fingerprints.json"  # cache for spotting repeated clips
SIZES = {"1920×1080 (HD)": (1920, 1080), "2560×1440 (QHD)": (2560, 1440), "3840×2160 (4K)": (3840, 2160),
         "3440×1440 (ultrawide)": (3440, 1440), "1080×2340 (phone)": (1080, 2340)}
STEAM_URL = "https://store.steampowered.com/app/431960/Wallpaper_Engine/"
PLAY_URL = "https://play.google.com/store/search?q=Wallpaper%20Engine&c=apps"
INTOLIVE_URL = "https://apps.apple.com/search?term=intoLive"
LAYOUTS = {"Blurred backdrop": "blur", "Crop to fill": "fill", "Fit (black bars)": "fit"}

st.set_page_config(page_title=APP_NAME, page_icon="🍌", layout="wide", initial_sidebar_state="collapsed")
st.markdown(theme.css(), unsafe_allow_html=True)
state = st.session_state
state.setdefault("tracks", [])
state.setdefault("result", None)
state.setdefault("candidates", [])
state.setdefault("source", None)    # what the grid shows: {uri, kind, title, image}
state.setdefault("selected", None)  # uri of the card open in the detail panel
state.setdefault("pick", None)      # a search result or link waiting to be scanned
state.setdefault("popular", [])     # the Most popular canvases on the front page
state.setdefault("sp_dc", config.get("sp_dc") or os.environ.get("SPOTIFY_SP_DC", ""))
state.setdefault("remember_sp_dc", bool(config.get("sp_dc")))
state.setdefault("client_id", config.get("spotify_client_id") or os.environ.get("SPOTIFY_CLIENT_ID", ""))
if "new_login" in state:  # from Connect to Spotify; applied here, before the sp_dc box exists
    account = state.pop("new_login")
    state.sp_dc, state.remember_sp_dc = account["sp_dc"], True
    config.update(sp_dc=account["sp_dc"], spotify_name=account["name"])

# Spotify sends the browser back here after a Web API login, with ?code=...&state=...
REDIRECT_URI = f"http://127.0.0.1:{st.get_option('server.port')}/"
params = st.query_params
if not PUBLIC and ("code" in params or "error" in params):
    code, login_state, error = params.get("code"), params.get("state", ""), params.get("error")
    st.query_params.clear()
    if error:
        state.login_msg = ("error", f"Spotify login didn't complete: {error}")
    else:
        try:
            official.finish_login(code, login_state)
            state.login_msg = ("success", f"Logged in to Spotify as {config.get('spotify_user')}.")
        except SpotifyError as e:
            state.login_msg = ("error", str(e))


def zip_folder(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.iterdir()):
            z.write(f, f"{folder.name}/{f.name}")
    return buf.getvalue()


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def progress_html(fraction: float | None = None, placeholders: str = "cards", header: str = "") -> str:
    """Loading state: an optional header, a thin bar (sliding when ``fraction`` is None) and
    shimmering placeholders shaped like what's coming ("cards", "results" or "")."""
    fill = (f'<div class="fill" style="width:{max(fraction, .03) * 100:.0f}%"></div>' if fraction is not None
            else '<div class="fill sliding"></div>')
    shapes = {"cards": ("c2w-skeleton", COLUMNS * 2), "results": ("c2w-skeleton rows", 8)}
    cls, count = shapes.get(placeholders, ("", 0))
    cards = f'<div class="{cls}">' + "<div></div>" * count + "</div>" if count else ""
    return f'{header}<div class="c2w-progress"><div class="track">{fill}</div></div>{cards}'


@st.cache_data(ttl=3600, show_spinner=False, max_entries=500)
def search(query: str, kind: str, sp_dc: str | None) -> list[Candidate]:
    warm_up(sp_dc).join()
    return get_client(sp_dc=sp_dc, log=lambda *_: None).search_candidates(query, kind)


@st.cache_resource
def render_slots() -> threading.BoundedSemaphore:
    """Shared by every session: at most MAX_RENDERS wallpapers render at once."""
    return threading.BoundedSemaphore(MAX_RENDERS)


@st.cache_resource(ttl=40 * 60)
def warm_up(sp_dc: str | None) -> threading.Thread:
    """Fetch the Spotify token in the background when the page opens, so the first search needn't wait."""
    def fetch() -> None:
        try:
            get_client(sp_dc=sp_dc, log=lambda *_: None)
        except SpotifyError:
            pass  # the real request will report it

    thread = threading.Thread(target=fetch, daemon=True)
    thread.start()
    return thread


def save_cookie() -> None:
    """Save or forget the sp_dc cookie after the user edits it or the Remember box."""
    config.update(sp_dc=clean_sp_dc(state.sp_dc) if state.remember_sp_dc else None)


# --- header: logo, donate, Spotify connection, settings ------------------------

# Public mode has only the Donate button on the right.
nav = st.columns([7.55, 1.25] if PUBLIC else [5, 1.25, 2, 0.55], vertical_alignment="center")
nav[0].markdown(f'<div class="c2w-logo">{theme.banana()}{APP_NAME}</div>', unsafe_allow_html=True)
if DONATE_URL:
    with nav[1].container(key="donate"):
        st.link_button("♥ Donate", DONATE_URL, use_container_width=True)

def local_controls() -> tuple[Path, bool, str, str | None]:
    """Connect/Disconnect and Settings, for the app running on your own computer.

    Returns (output folder, copy into Wallpaper Engine?, its myprojects folder, sp_dc).
    """
    # Connecting and disconnecting run before the sp_dc box in Settings exists,
    # so they can still change what it shows.
    with nav[2]:
        if clean_sp_dc(state.sp_dc):
            with st.popover(f"● {config.get('spotify_name') or 'Spotify connected'}", use_container_width=True):
                st.caption("Connected to Spotify, so canvases come through.")
                if st.button("Disconnect", use_container_width=True):
                    state.sp_dc, state.remember_sp_dc = "", False
                    config.update(sp_dc=None, spotify_name=None)
                    st.rerun()
        else:
            st.button("Connect to Spotify", type="primary", use_container_width=True,
                      on_click=lambda: state.update(connecting=True))

    with nav[3].popover("", icon=":material/settings:", help="Settings", use_container_width=True):
        html('<div class="c2w-label">Wallpaper output</div>')
        out_dir = Path(st.text_input("Output folder", value=str(Path("wallpapers").resolve())))
        detected = wp.default_wallpaper_engine_dir()
        install = st.checkbox("Copy into Wallpaper Engine", value=bool(detected))
        we_dir = st.text_input("Wallpaper Engine myprojects folder", value=str(detected or ""),
                               disabled=not install,
                               placeholder=r"...\steamapps\common\wallpaper_engine\projects\myprojects")

        html('<div class="c2w-label">Spotify session</div>')
        # The saved copy changes only when these two are edited, never on a rerun:
        # other tabs (or sessions started before a Connect) would otherwise
        # overwrite or delete it with their own stale values.
        sp_dc = clean_sp_dc(st.text_input(
            "sp_dc cookie", key="sp_dc", type="password", on_change=save_cookie,
            help="Filled in by Connect to Spotify. You can also paste it from open.spotify.com: "
                 "DevTools → Application → Cookies → sp_dc.",
        ))
        st.checkbox("Remember on this computer", key="remember_sp_dc", on_change=save_cookie,
                    help=f"Saves the cookie in {config.config_path()} so you stay connected "
                         "(the command line uses it too). Untick to delete it.")

        html('<div class="c2w-label">Spotify Web API (optional)</div>')
        if official.saved_login():
            st.caption(f"Logged in as {config.get('spotify_user') or 'Spotify user'}: search and "
                       "complete artist discographies use the official API.")
            if st.button("Log out of the Web API"):
                official.logout()
                st.rerun()
        else:
            st.caption("For complete discographies of artists with huge catalogues. Needs your own "
                       "app from developer.spotify.com/dashboard with this redirect URI: "
                       f"`{REDIRECT_URI}`")
            client_id = st.text_input("Client ID", key="client_id")
            if client_id.strip():
                # One login link per session; target=_self keeps the login in this tab.
                if state.get("login_for") != client_id.strip():
                    state.login_url = official.start_login(client_id.strip(), REDIRECT_URI)
                    state.login_for = client_id.strip()
                html(f'<a href="{escape(state.login_url)}" target="_self">Log in to the Web API ↗</a>')
    return out_dir, install, we_dir, sp_dc


def visitor_folder() -> Path:
    """A private output folder per visitor in public mode; folders untouched for 2 hours are removed."""
    root = Path(tempfile.gettempdir()) / "canvas2we-visitors"
    root.mkdir(parents=True, exist_ok=True)
    for old in root.iterdir():
        try:
            if time.time() - old.stat().st_mtime > 2 * 3600:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    state.setdefault("visitor", uuid.uuid4().hex)
    return root / state.visitor


if PUBLIC:
    # Shared with other people: nothing here may touch this computer's Spotify
    # login or Wallpaper Engine. The server's own session (SPOTIFY_SP_DC, or a
    # saved Connect) is used for everyone, and each visitor downloads a zip.
    out_dir, install, we_dir = visitor_folder(), False, ""
    sp_dc = clean_sp_dc(os.environ.get("SPOTIFY_SP_DC")) or config.get("sp_dc")
else:
    out_dir, install, we_dir, sp_dc = local_controls()

if "login_msg" in state:
    kind_, msg = state.pop("login_msg")
    getattr(st, kind_)(msg)

warm_up(sp_dc)  # starts fetching the Spotify token in the background

if state.pop("connecting", False):
    browser = installed_browser()
    html('<div class="c2w-note">' + (
        f"Log in to Spotify in the <b>{browser[0]}</b> window that just opened (Sign in with Google works). "
        f"It closes by itself once you're in, and {browser[0]} remembers you for next time."
        if browser else "Log in to Spotify in the window that just opened.") + "</div>")
    with st.spinner("Waiting for you to log in…"):
        try:
            state.new_login = browser_login()
            state.login_msg = ("success", "Connected to Spotify.")
        except SpotifyError as e:
            state.login_msg = ("error", str(e))
    st.rerun()


# --- actions ----------------------------------------------------------------------

def choose(uri: str, kind: str, title: str, image: str = "") -> None:
    state.pick = {"uri": uri, "kind": kind, "title": title, "image": image}


def select(uri: str) -> None:
    """Open a card: the pop-up with its preview and Make wallpaper shows on this rerun."""
    state.selected = state.dialog = uri


def go_home() -> None:
    state.tracks, state.candidates, state.source, state.selected, state.result = [], [], None, None, None


def pick_example() -> None:
    name, state.example = state.example, None
    if name:
        choose(f"spotify:artist:{POPULAR_ARTISTS[name]}", "artist", name)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def popular_canvases(sp_dc: str) -> list[Track]:
    """A few distinct canvases from each popular artist's top tracks, alternating between artists."""
    client = get_client(sp_dc=sp_dc, log=lambda *_: None)

    def top(artist_id: str) -> list[Track]:
        try:
            return client.artist_top_tracks(artist_id)
        except (SpotifyError, OSError):
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        tops = list(pool.map(top, POPULAR_ARTISTS.values()))
    client.attach_canvases([t for ts in tops for t in ts])
    # Only each artist's first few canvases are compared, to keep the first load quick.
    tops = [[t for t in ts if t.canvas][:PER_ARTIST + 3] for ts in tops]
    clip_of = same_clips(list(dict.fromkeys(t.canvas.url for ts in tops for t in ts)))
    per_artist = []
    for ts in tops:
        seen, picked = set(), []
        for t in ts:
            if clip_of[t.canvas.url] not in seen and len(picked) < PER_ARTIST:
                seen.add(clip_of[t.canvas.url])
                picked.append(t)
        per_artist.append(picked)
    return [ts[i] for i in range(PER_ARTIST) for ts in per_artist if i < len(ts)]


def scan(pick: dict) -> None:
    """Load every track behind a chosen artist/album/song and look up their canvases."""
    box = st.empty()
    # The page shows what's being loaded (name and picture) straight away, with no step-by-step text.
    header = ""
    if pick["title"] != pick["uri"]:
        header = (f'<div class="c2w-source {pick["kind"]}">'
                  + (f'<img src="{escape(pick["image"])}" alt="">' if pick["image"] else "")
                  + f'<div><div class="name">{escape(pick["title"])}</div></div></div>')

    def show(fraction: float) -> None:
        box.markdown(progress_html(fraction, header=header), unsafe_allow_html=True)

    # Rough share of the bar per stage: token 0-10%, releases 10-60%, canvases 60-70%, repeats 70-100%.
    show(0.04)
    try:
        warm_up(sp_dc).join()
        client = get_client(sp_dc=sp_dc, log=lambda *_: None)
        show(0.1)
        tracks = lookup(client, pick["uri"], log=lambda *_: None,
                        progress=lambda done, total: show(0.1 + 0.5 * done / total))
    except SpotifyError as e:
        box.empty()
        st.error(str(e))
        return
    urls = list(dict.fromkeys(t.canvas.url for t in tracks if t.canvas))
    show(0.7)
    state.clip_of = same_clips(urls, progress=lambda done, total: show(0.7 + 0.3 * done / total))
    box.empty()
    if pick["title"] == pick["uri"] and tracks:  # a pasted link: name it after what it found
        first = tracks[0]
        pick["title"] = {"artist": Counter(a for t in tracks for a in t.artists[:1]).most_common(1)[0][0],
                         "album": first.album or first.name}.get(pick["kind"], first.name)
    pick["image"] = pick["image"] or (tracks[0].album_art if tracks else "")
    state.tracks, state.source, state.result = tracks, pick, None
    state.selected = next((t.uri for t in tracks if t.canvas), None)
    state.pop("album_filter", None)
    state.pop("album_filter_many", None)


def build(track: Track, opts: wp.RenderOptions) -> bool:
    box = st.empty()
    box.markdown(progress_html(placeholders=""), unsafe_allow_html=True)
    quiet = lambda *_: None  # noqa: E731
    try:
        client = get_client(sp_dc=sp_dc, log=quiet)
        with render_slots():  # renders are CPU-heavy; others wait their turn
            folder = make_wallpaper(client, track, out_dir, opts, log=quiet)
        installed = None
        if install:
            if not we_dir:
                raise wp.RenderError("Set the Wallpaper Engine myprojects folder in Settings (top right).")
            installed = wp.install_project(folder, Path(we_dir))
        state.result = {"track": track, "folder": folder, "installed": installed}
        return True
    except (SpotifyError, wp.RenderError) as e:
        st.error(str(e))
        return False
    finally:
        box.empty()


# --- pieces of markup ---------------------------------------------------------------

def media_html(t: Track) -> str:
    if t.canvas and t.canvas.is_video:
        return (f'<video src="{escape(t.canvas.url)}" poster="{escape(t.album_art)}" '
                'autoplay muted loop playsinline></video>')
    src = t.canvas.url if t.canvas else t.album_art
    return f'<img src="{escape(src)}" alt="">' if src else ""


def card_html(t: Track, selected: bool, also: int = 0, avatar: bool = True) -> str:
    """A card; ``also`` counts the other tracks that use the same Canvas."""
    cls = "c2w-card" + (" selected" if selected else "") + ("" if t.canvas else " none")
    badge = ('<span class="c2w-badge">No Canvas</span>' if not t.canvas
             else f'<span class="c2w-badge">+{also} track{"s" * (also != 1)}</span>' if also else "")
    pic = ("" if not avatar else f'<img src="{escape(t.album_art)}" alt="">' if t.album_art
           else '<div class="ph"></div>')
    sub = (f"Also on {also} other track{'s' * (also != 1)}" if also
           else ", ".join(t.artists) if not avatar else t.album or ", ".join(t.artists))
    return (f'<div class="{cls}"><div class="c2w-media">{media_html(t)}{badge}</div></div>'
            f'<div class="c2w-cap">{pic}<div><b>{escape(t.name)}</b><small>{escape(sub)}</small></div></div>')


@st.cache_resource
def fingerprint_store() -> dict[str, list]:
    """Frame hashes by Canvas URL, shared by every session and kept on disk between runs."""
    try:
        return json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_fingerprints(store: dict[str, list]) -> None:
    try:
        FINGERPRINTS.parent.mkdir(parents=True, exist_ok=True)
        tmp = FINGERPRINTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(list(store.items())[-20000:])), encoding="utf-8")
        tmp.replace(FINGERPRINTS)
    except OSError:
        pass  # only a cache


def same_clips(urls: list[str], progress=None) -> dict[str, str]:
    """Map each Canvas URL to the first URL in the list that shows the same video.

    Spotify often gives each track on an album, and each edition of it, its
    own copy of one Canvas, so matching URLs alone misses most repeats.
    Every clip gets one frame hash (at 1 s); only clips whose hash is close
    to another's get a second (at 3 s) to confirm. Hashes are cached by URL.
    """
    store = fingerprint_store()
    todo = [u for u in urls if u not in store]

    def first(url: str) -> list:
        h = wp.frame_hash(url, 1.0)
        return [h if h is not None else wp.frame_hash(url, 0)]  # stills and very short clips

    with ThreadPoolExecutor(max_workers=FINGERPRINT_WORKERS) as pool:
        for done, (url, hashes) in enumerate(zip(todo, pool.map(first, todo)), 1):
            store[url] = hashes
            if progress:
                progress(done, len(todo))
    h1 = {u: store[u][0] for u in urls}
    lookalikes = [u for u in urls if len(store[u]) == 1
                  and any(v != u and wp.hashes_close(h1[u], h1[v]) for v in urls)]
    with ThreadPoolExecutor(max_workers=FINGERPRINT_WORKERS) as pool:
        for url, h in zip(lookalikes, pool.map(lambda u: wp.frame_hash(u, 3.0), lookalikes)):
            store[url] = [h1[url], h]
    if todo or lookalikes:
        save_fingerprints(store)

    def same(u: str, v: str) -> bool:
        a, b = store[u], store[v]
        if not wp.hashes_close(a[0], b[0]):
            return False
        # Confirm on the second frame when both clips have one.
        return len(a) < 2 or len(b) < 2 or a[1] is None or b[1] is None or wp.hashes_close(a[1], b[1])

    firsts: list[str] = []
    clip_of = {}
    for url in urls:
        clip_of[url] = next((f for f in firsts if same(url, f)), url)
        if clip_of[url] == url:
            firsts.append(url)
    return clip_of


def unique_canvases(tracks: list[Track], only_canvas: bool = True,
                    clip_of: dict[str, str] | None = None) -> list[tuple[Track, list[Track]]]:
    """Each distinct Canvas once, as (first track using it, every track using it), in track order.

    ``clip_of`` (from same_clips) merges different files of the same video.
    Tracks without a Canvas each get their own entry when ``only_canvas`` is off.
    """
    clip_of = clip_of or {}
    by_clip: dict[str, list[Track]] = {}
    entries: list[tuple[Track, list[Track]]] = []
    for t in tracks:
        if t.canvas:
            clip = clip_of.get(t.canvas.url, t.canvas.url)
            if clip not in by_clip:
                by_clip[clip] = [t]
                entries.append((t, by_clip[clip]))
            else:
                by_clip[clip].append(t)
        elif not only_canvas:
            entries.append((t, [t]))
    return entries


def candidate_html(c: Candidate) -> str:
    img = f'<img src="{escape(c.image)}" alt="">' if c.image else '<div class="ph"></div>'
    return (f'<div class="c2w-cand {c.kind}">{img}<div><b>{escape(c.name)}</b>'
            f'<small>{escape(c.detail)}</small></div></div>')


# --- hero + search ------------------------------------------------------------------

idle = not state.tracks and not state.candidates and not state.pick
html(f'<div class="c2w-hero{"" if idle else " small"}">{theme.banana()}<h1>{APP_NAME}</h1>'
     + ("<p>Turn the looping video behind any Spotify song into a Wallpaper Engine wallpaper. "
        "Search an artist, pick a Canvas, done.</p>" if idle else "") + "</div>")

_, mid, _ = st.columns([1, 5, 1])
with mid, st.form("search", border=False):
    c1, c2 = st.columns([5, 1.1], vertical_alignment="center")
    query = c1.text_input("Search", key="query", label_visibility="collapsed",
                          placeholder="Search an artist, album or song, or paste a Spotify link")
    submitted = c2.form_submit_button("Search", type="primary", use_container_width=True)
    kind_label = st.segmented_control("Search for", list(KINDS), default="Artist", key="kind",
                                      label_visibility="collapsed")
if idle:
    with mid:
        st.pills("Try", EXAMPLES, key="example", on_change=pick_example)

if submitted and query.strip():
    q, kind = query.strip(), KINDS[kind_label or "Artist"]
    ref = parse_spotify_ref(q)
    if ref:
        state.candidates = []
        choose(q, ref[0], q)
    else:
        box = st.empty()
        box.markdown(progress_html(placeholders="results"), unsafe_allow_html=True)
        try:
            state.candidates = search(q, kind, sp_dc)
        except SpotifyError as e:
            state.candidates = []
            st.error(str(e))
        box.empty()
        exact = [c for c in state.candidates if c.name.casefold() == q.casefold()]
        if len(exact) == 1:  # exactly what was typed, and nothing else by that name: no need to ask
            state.candidates = []
            choose(exact[0].uri, exact[0].kind, exact[0].name, exact[0].image)
        elif not state.candidates:
            st.warning(f"No {(kind_label or 'Artist').lower()} found for “{q}”.")

if state.pick:
    pick, state.pick, state.candidates = state.pick, None, []
    scan(pick)

if state.candidates:
    st.button("← Back", on_click=lambda: state.update(candidates=[]))
    html('<div class="c2w-heading">Which one?</div>'
         '<div class="c2w-sub">Pick the right match to scan it for canvases.</div>')
    cols = st.columns(4)
    for i, c in enumerate(state.candidates):
        with cols[i % 4].container(key=f"tile-cand-{c.id}"):
            html(candidate_html(c))
            st.button("Choose", key=f"pick-cand-{c.id}", on_click=choose, args=(c.uri, c.kind, c.name, c.image))


# --- results: card grid + detail panel ------------------------------------------------

def results(tracks: list[Track]) -> None:
    src = state.source or {}
    hits = [t for t in tracks if t.canvas]
    st.button("← Home", on_click=go_home)
    clip_of = state.get("clip_of") or {}
    clips = len({clip_of.get(t.canvas.url, t.canvas.url) for t in hits})
    html(f'<div class="c2w-source {src.get("kind", "")}">'
         + (f'<img src="{escape(src["image"])}" alt="">' if src.get("image") else "")
         + f'<div><div class="name">{escape(src.get("title", ""))}</div><small>{len(hits)} of {len(tracks)} '
           f'tracks have a Canvas · {clips} distinct clip{"s" * (clips != 1)}</small></div></div>')
    if not hits and not sp_dc and not PUBLIC:
        html('<div class="c2w-note">No canvases came back. Spotify only sends them to a connected account: '
             'press <b>Connect to Spotify</b> at the top, then search again.</div>')

    f1, f2 = st.columns([5, 1.3], vertical_alignment="center")
    only = f2.toggle("Only with Canvas", value=True)
    entries = unique_canvases(tracks, only, state.get("clip_of"))
    state.shared = {t.uri: [x.name for x in sharing[1:]] for t, sharing in entries}
    per_album = Counter(t.album for t, _ in entries)
    picked = []
    album_label = lambda a: f"{a or 'Single'} · {per_album[a]}"  # noqa: E731
    if 1 < len(per_album) <= PILL_ALBUMS:
        picked = f1.pills("Albums", list(per_album), selection_mode="multi", key="album_filter",
                          format_func=album_label, label_visibility="collapsed")
    elif len(per_album) > PILL_ALBUMS:  # a long discography: a searchable dropdown fits better
        picked = f1.multiselect("Albums", list(per_album), key="album_filter_many", format_func=album_label,
                                placeholder=f"All {len(per_album)} albums (type to filter)",
                                label_visibility="collapsed")
    shown = [(t, sharing) for t, sharing in entries if not picked or t.album in picked]
    if not shown:
        html('<div class="c2w-empty">None of these tracks has a Canvas. Turn off <b>Only with Canvas</b> '
             'to see them all.</div>')
    groups: dict[str, list[tuple[Track, list[Track]]]] = {}
    for entry in shown[:MAX_CARDS]:
        groups.setdefault(entry[0].album, []).append(entry)
    for album, group in groups.items():
        plays = sum(len(sharing) for _, sharing in group)
        clips_here = sum(1 for t, _ in group if t.canvas)
        cover = group[0][0].album_art
        html(f'<div class="c2w-album">' + (f'<img src="{escape(cover)}" alt="">' if cover else "")
             + f'<div><b>{escape(album or "Singles")}</b><small>{clips_here} Canvas{"es" * (clips_here != 1)}'
               f' · used on {plays} track{"s" * (plays != 1)}</small></div></div>')
        cols = st.columns(COLUMNS)
        for i, (t, sharing) in enumerate(group):
            with cols[i % COLUMNS].container(key=f"tile-{t.id}"):
                html(card_html(t, t.uri == state.selected, also=len(sharing) - 1, avatar=False))
                st.button("Select", key=f"pick-{t.id}", on_click=select, args=(t.uri,))
    if len(shown) > MAX_CARDS:
        st.caption(f"Showing the first {MAX_CARDS} of {len(shown)}. Pick albums above to narrow it down.")


def open_dialog(t: Track) -> None:
    """The card's pop-up. It's a fragment, so changing options or building reruns only the pop-up
    and it stays open; closing it (✕, Esc or clicking outside) returns to the page as it was."""
    st.dialog(t.name, width="large")(detail)(t)


def detail(t: Track) -> None:
    r = state.result if state.result and state.result["track"].uri == t.uri else None
    left, right = st.columns([1, 1.1], gap="large")
    with left:
        if r:
            st.video(str(r["folder"] / "wallpaper.mp4"), autoplay=True, muted=True, loop=True)
        else:
            html(f'<div class="c2w-preview">{media_html(t)}</div>')
    with right:
        others = state.get("shared", {}).get(t.uri, [])
        html(f'<div class="c2w-meta">{escape(", ".join(t.artists))}'
             + (f" · {escape(t.album)}" if t.album and t.album not in t.artists else "") + "</div>"
             + (f'<div class="c2w-meta">Also plays on {escape(", ".join(others[:4]))}'
                + (f" and {len(others) - 4} more" if len(others) > 4 else "") + "</div>" if others else ""))
        layout = LAYOUTS[st.selectbox("Layout", list(LAYOUTS), key="layout")]
        size = st.selectbox("Resolution", list(SIZES), key="size",
                            help="Pick 1080×2340 (phone) for a wallpaper you'll send to an Android phone.")
        crossfade = st.slider("Loop crossfade (seconds)", 0.0, 2.0, 0.0, 0.1, key="crossfade",
                              help="Blend the end into the start, for canvases that jump when they loop.")
        if r:
            st.download_button("Download project (.zip)", zip_folder(r["folder"]), type="primary",
                               file_name=f"{r['folder'].name}.zip", mime="application/zip",
                               use_container_width=True, on_click="ignore")
            if not PUBLIC:  # visitors can't reach this computer's folders; they get the zip
                if r["installed"]:
                    st.caption(f"Also installed to Wallpaper Engine: `{r['installed']}`")
                else:
                    st.caption(f"Saved in `{r['folder']}`. In Wallpaper Engine: **Open Wallpaper → Open "
                               "from File** → `project.json`.")
        if st.button("Make again with these settings" if r else "Make wallpaper",
                     type="secondary" if r else "primary", disabled=not t.canvas, use_container_width=True):
            w, h = SIZES[size]
            if build(t, wp.RenderOptions(width=w, height=h, layout=layout, crossfade=crossfade)):
                st.rerun(scope="fragment")  # show the finished wallpaper; the pop-up stays open
        if not t.canvas:
            st.caption("This track has no Canvas on Spotify.")
    if t.canvas:
        setup_guide(t)


def qr_svg(url: str) -> str:
    return segno.make(url, error="m").svg_inline(scale=4, border=2, dark=theme.BG, light=theme.TEXT)


def setup_guide(t: Track) -> None:
    """How to use the wallpaper on a PC, an Android phone, or an iPhone."""
    html('<div class="c2w-label">Set it as your wallpaper</div>')
    pc, android, iphone = st.tabs(["PC", "Android", "iPhone"])
    with pc:
        st.markdown(
            "1. Get **Wallpaper Engine** on Steam (one-time purchase).\n"
            "2. Press **Make wallpaper** above, then **Download project (.zip)**.\n"
            "3. Unzip it, and in Wallpaper Engine choose **Open Wallpaper → Open from File** → "
            "`project.json`. It stays in your **Installed** tab."
            if PUBLIC else
            "1. Get **Wallpaper Engine** on Steam (one-time purchase).\n"
            "2. Press **Make wallpaper** above. With *Copy into Wallpaper Engine* on (Settings, top right), "
            "it appears in Wallpaper Engine's **Installed** tab.\n"
            "3. Otherwise, **Download project (.zip)**, unzip it, and in Wallpaper Engine choose "
            "**Open Wallpaper → Open from File** → `project.json`.")
        st.link_button("Get Wallpaper Engine on Steam ↗", STEAM_URL, use_container_width=True)
    with android:
        st.markdown(
            "Wallpaper Engine's free Android app plays wallpapers sent from the PC version.\n"
            "1. On your PC, choose **1080×2340 (phone)** above, press **Make wallpaper**, and add it to "
            "Wallpaper Engine (see the PC tab).\n"
            "2. Install **Wallpaper Engine** from Google Play on your phone.\n"
            "3. In Wallpaper Engine on your PC, click **Mobile** at the top and scan the QR code it shows "
            "with the app (PC and phone on the same Wi-Fi).\n"
            "4. Send this wallpaper to your phone, then apply it in the app.")
        c1, c2 = st.columns(2)
        c1.link_button("Google Play ↗", PLAY_URL, use_container_width=True)
        c2.link_button("Steam ↗", STEAM_URL, use_container_width=True)
        st.caption("No PC? Save the phone video from the iPhone tab and set it with any video wallpaper app.")
    with iphone:
        st.markdown(
            "iPhone can't run Wallpaper Engine, but this Canvas makes a great **Live Photo** wallpaper. "
            "It's already phone-shaped.\n"
            "1. On your iPhone, open the video (scan the code below if you're on a computer), tap "
            "**Share → Save Video**.\n"
            "2. Turn it into a Live Photo with a free app such as **intoLive**.\n"
            "3. In **Photos**, open the Live Photo, tap **Share → Use as Wallpaper**, and keep the Live "
            "Photo button on. It moves when you wake your phone (iOS 17 and later).\n\n"
            "Want a still instead? Pause the video, take a screenshot, and use that.")
        html(f'<div class="c2w-qr">{qr_svg(t.canvas.url)}<small>Scan with your iPhone camera to open '
             f'the video</small></div>')
        c1, c2 = st.columns(2)
        c1.link_button("Video ↗", t.canvas.url, use_container_width=True)
        c2.link_button("intoLive ↗", INTOLIVE_URL, use_container_width=True)


def most_popular() -> None:
    html('<div class="c2w-heading">Most popular</div>'
         '<div class="c2w-sub">Canvases from the biggest artists on Spotify. Click one to make it a wallpaper.</div>')
    box = st.empty()
    box.markdown(progress_html(), unsafe_allow_html=True)
    try:
        state.popular = popular_canvases(sp_dc)
    except SpotifyError as e:
        st.error(str(e))
        return
    finally:
        box.empty()
    if not state.popular:
        popular_canvases.clear()  # don't keep an empty answer for hours; ask Spotify again next visit
        html('<div class="c2w-note">' + (
            "Spotify isn't sending canvases right now. Try again in a little while." if PUBLIC else
            "Spotify didn't send any canvases. Press <b>Connect to Spotify</b> at the top (or disconnect "
            "and connect again) to see them here.") + "</div>")
        return
    cols = st.columns(COLUMNS)
    for i, t in enumerate(state.popular):
        with cols[i % COLUMNS].container(key=f"tile-pop-{t.id}"):
            html(card_html(t, False))
            st.button("Open", key=f"pick-pop-{t.id}", on_click=select, args=(t.uri,))


if state.candidates:
    pass  # picking a search result; the previous results come back if nothing is chosen
elif state.tracks:
    results(state.tracks)
else:
    most_popular()

if "dialog" in state:  # a card was just clicked
    uri = state.pop("dialog")
    card = next((t for t in [*state.tracks, *state.popular] if t.uri == uri), None)
    if card:
        open_dialog(card)

html(f'<div class="c2w-footer"><span>{APP_NAME} is free to use. Not affiliated with Spotify or Wallpaper '
     'Engine; every Canvas belongs to its artist.</span><span>'
     f'<a href="{STEAM_URL}" target="_blank" rel="noopener">Get Wallpaper Engine on Steam ↗</a>'
     + (f' <a href="{escape(DONATE_URL)}" target="_blank" rel="noopener">♥ Support it on PayPal</a>'
        if DONATE_URL else "") + "</span></div>")
