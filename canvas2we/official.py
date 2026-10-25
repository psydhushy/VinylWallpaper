"""Official Spotify Web API, used with your own Spotify login.

Canvases still need the web-player token (they aren't in the public API),
but search and artist discographies are better through the official API:
proper search ranking, and every release via the paginated
/artists/{id}/albums endpoint rather than the first batch the artist page
pre-renders.

Login uses the Authorization Code flow with PKCE, so only a Client ID is
needed, no client secret. Create an app at
https://developer.spotify.com/dashboard, add the redirect URI the UI shows
(http://127.0.0.1:8501/ by default), and paste its Client ID into the UI.
Tokens are saved with the other settings (see config.py) and refreshed
automatically, so the CLI uses the same login.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import requests

from . import config
from .spotify import WEB_API, Candidate, SpotifyError

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
# Development-mode apps get small page sizes, so ask for few and follow `next`.
PAGE = 10
RELEASE_TYPES = {"album": "ALBUM", "single": "SINGLE", "compilation": "COMPILATION"}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def start_login(client_id: str, redirect_uri: str) -> str:
    """Return the Spotify authorize URL, remembering the PKCE verifier for the callback."""
    verifier = _b64url(secrets.token_bytes(64))
    state = secrets.token_urlsafe(16)
    # Keep a few pending logins so a link from another open tab still works.
    pending = dict(list((config.get("spotify_pending") or {}).items())[-4:])
    pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "client_id": client_id}
    config.update(spotify_client_id=client_id, spotify_pending=pending)
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "state": state,
    })


def finish_login(code: str, state: str) -> OfficialAPI:
    """Swap the callback's code for tokens and save the login."""
    pending = config.get("spotify_pending") or {}
    login = pending.pop(state, None)
    if not login:
        raise SpotifyError("That Spotify login link expired or didn't come from this app. Press Log in again.")
    config.update(spotify_pending=pending or None)  # each login link works once
    tokens = _token_request({
        "grant_type": "authorization_code", "code": code, "redirect_uri": login["redirect_uri"],
        "client_id": login["client_id"], "code_verifier": login["verifier"],
    })
    api = OfficialAPI(login["client_id"], tokens)
    me = api.get("/me")
    config.update(spotify_tokens=tokens, spotify_user=me.get("display_name") or me.get("id"))
    return api


def logout() -> None:
    config.update(spotify_tokens=None, spotify_user=None, spotify_pending=None)


def saved_login() -> OfficialAPI | None:
    tokens, client_id = config.get("spotify_tokens"), config.get("spotify_client_id")
    if tokens and client_id and tokens.get("refresh_token"):
        return OfficialAPI(client_id, tokens)
    return None


def _token_request(data: dict) -> dict:
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=20)
    except requests.RequestException as e:
        raise SpotifyError(f"Couldn't reach Spotify to log in: {e}") from e
    if r.status_code != 200:
        try:
            err = r.json()
            msg = err.get("error_description") or err.get("error")
        except ValueError:
            msg = r.text[:200]
        raise SpotifyError(f"Spotify login failed ({r.status_code}): {msg}")
    tokens = r.json()
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600) - 60
    return tokens


class OfficialAPI:
    def __init__(self, client_id: str, tokens: dict, session: requests.Session | None = None):
        self.client_id, self.tokens = client_id, tokens
        self.http = session or requests.Session()

    def access_token(self) -> str:
        if time.time() < self.tokens.get("expires_at", 0):
            return self.tokens["access_token"]
        try:
            new = _token_request({"grant_type": "refresh_token", "client_id": self.client_id,
                                  "refresh_token": self.tokens["refresh_token"]})
        except SpotifyError as e:
            logout()  # the refresh token was revoked or the app was changed
            raise SpotifyError(f"{e}\nYour Spotify login has expired; log in again.") from e
        new.setdefault("refresh_token", self.tokens["refresh_token"])  # Spotify may not rotate it
        self.tokens = new
        config.update(spotify_tokens=new)
        return new["access_token"]

    def get(self, path: str, max_wait: float = 60.0, **params) -> dict:
        url = path if path.startswith("http") else f"{WEB_API}{path}"
        waited, retried = 0.0, False
        while True:
            r = self.http.get(url, params=params, timeout=20,
                              headers={"Authorization": f"Bearer {self.access_token()}"})
            if r.status_code == 401 and not retried:
                self.tokens["expires_at"] = 0  # token revoked early; refresh once
                retried = True
                continue
            if r.status_code != 429:
                break
            delay = float(r.headers.get("Retry-After", 5) or 5) + 1
            if waited + delay > max_wait:
                raise SpotifyError(f"Rate limited by Spotify's API; retry after {delay:.0f}s.")
            time.sleep(delay)
            waited += delay
        if r.status_code == 403:
            raise SpotifyError(
                "Spotify's API refused the request (403). A development-mode app only works for the "
                "accounts added under User Management in the Spotify developer dashboard.")
        if r.status_code >= 400:
            raise SpotifyError(f"Spotify's API returned {r.status_code} for {path}.")
        return r.json()

    def _pages(self, path: str, key: str | None = None, **params):
        """Yield every item of a paginated endpoint, following `next`."""
        data = self.get(path, limit=PAGE, **params)
        while True:
            page = data[key] if key else data
            yield from page.get("items") or []
            if not page.get("next"):
                return
            data = self.get(page["next"])

    def search(self, query: str, kind: str, limit: int = 5) -> list[tuple[str, str]]:
        return [(c.id, c.name) for c in self.search_candidates(query, kind, limit)]

    def search_candidates(self, query: str, kind: str, limit: int = 8) -> list[Candidate]:
        data = self.get("/search", q=query, type=kind, limit=min(limit, PAGE))
        found = []
        for i in data[f"{kind}s"]["items"]:
            if not i:
                continue
            artists = ", ".join(a["name"] for a in i.get("artists", []))
            if kind == "artist":
                followers = (i.get("followers") or {}).get("total")
                bits = [f"{followers:,} followers" if followers else "", ", ".join((i.get("genres") or [])[:2])]
            elif kind == "album":
                bits = [artists, (i.get("release_date") or "")[:4], i.get("album_type", "").title()]
            else:
                bits = [artists, (i.get("album") or {}).get("name", "")]
            images = i.get("images") or (i.get("album") or {}).get("images") or []
            found.append(Candidate(i["id"], kind, i["name"], images[0]["url"] if images else "",
                                   " · ".join(b for b in bits if b)))
        return found

    def artist_name(self, artist_id: str) -> str:
        return self.get(f"/artists/{artist_id}").get("name", "")

    def artist_releases(self, artist_id: str) -> list[dict]:
        """Every album, single/EP and compilation by the artist, as {id, name, year, type}.

        "Appears on" releases (other people's albums) are left out, as on the
        artist page. Re-releases with the same name and type are kept once.
        """
        releases, seen = [], set()
        for a in self._pages(f"/artists/{artist_id}/albums", include_groups=",".join(RELEASE_TYPES),
                             market="from_token"):
            kind = RELEASE_TYPES.get(a.get("album_type", ""), "")
            dedupe = (a["name"].casefold(), kind)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            year = (a.get("release_date") or "")[:4]
            releases.append({"id": a["id"], "name": a["name"], "type": kind,
                             "year": int(year) if year.isdigit() else None})
        return releases
