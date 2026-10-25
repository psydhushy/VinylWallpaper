"""Command-line interface.

    canvas2we scan "Opeth"
    canvas2we scan "Ghost Reveries" --album
    canvas2we list "Opeth Ghost Reveries" --album
    canvas2we make "https://open.spotify.com/track/..." --install
    canvas2we make "Opeth Ghost Reveries" --album --all
    canvas2we local canvas.mp4 --title "My wallpaper"
    canvas2we ui
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, official, theme, wallpaper as wp
from .spotify import Auth, SpotifyClient, SpotifyError, Track, capture_web_player_auth, clean_sp_dc

_auth_cache: dict[str | None, Auth] = {}  # keyed by sp_dc, so logging in gets a new token


def get_client(token: str | None = None, sp_dc: str | None = None, log=print) -> SpotifyClient:
    """A client using, in order: the given token/cookie, the environment, then saved settings."""
    token = token or os.environ.get("SPOTIFY_TOKEN")
    sp_dc = clean_sp_dc(sp_dc or os.environ.get("SPOTIFY_SP_DC") or config.get("sp_dc"))
    login = official.saved_login()  # the Spotify login made in `canvas2we ui`
    if token:
        return SpotifyClient(Auth(token, os.environ.get("SPOTIFY_CLIENT_TOKEN", ""), logged_in=True),
                             sp_dc=sp_dc, official=login)
    auth = _auth_cache.get(sp_dc)
    if auth is None or not auth.fresh():
        log("Getting a Spotify web-player token (headless browser)...")
        auth = _auth_cache[sp_dc] = capture_web_player_auth(sp_dc)
    return SpotifyClient(auth, sp_dc=sp_dc, official=login)


def lookup(client: SpotifyClient, query: str, kind: str = "track", log=print, progress=None) -> list[Track]:
    tracks = client.attach_canvases(client.resolve(query, kind, log=log, progress=progress))
    if client.anonymous and not any(t.canvas for t in tracks):
        log("Note: no Canvas came back. Spotify usually only returns canvases to a logged-in token;\n"
            "set SPOTIFY_SP_DC to your sp_dc cookie and try again (see README).")
    return tracks


def make_wallpaper(client: SpotifyClient, track: Track, out_dir: Path, opts: wp.RenderOptions,
                   log=print) -> Path:
    if not track.canvas:
        raise SpotifyError(f"{track.label} has no Canvas on Spotify.")
    ext = ".mp4" if track.canvas.is_video else ".jpg"
    raw = out_dir / "_downloads" / f"{track.id}{ext}"
    log(f"Downloading Canvas for {track.label}...")
    client.download(track.canvas.url, raw)
    meta = wp.WallpaperMeta(
        title=f"{track.label} (Spotify Canvas)",
        description=(f"Spotify Canvas for \"{track.name}\" by {', '.join(track.artists)}"
                     + (f", from the album {track.album}." if track.album else ".")
                     + f"\n{track.uri}"),
    )
    log(f"Rendering {opts.width}x{opts.height} ({opts.layout})...")
    return wp.build_project(raw, out_dir, meta, opts)


def pick_tracks(tracks: list[Track], number: int | None, all_: bool) -> list[Track]:
    with_canvas = [t for t in tracks if t.canvas]
    if number is not None:
        match = [t for t in tracks if t.track_number == number] or tracks[number - 1:number]
        return match
    if all_:
        # Albums often reuse one clip across tracks; build each clip once.
        seen, unique = set(), []
        for t in with_canvas:
            if t.canvas.url not in seen:
                seen.add(t.canvas.url)
                unique.append(t)
        return unique
    return with_canvas[:1]


def print_tracks(tracks: list[Track]) -> None:
    urls: dict[str, int] = {}
    for t in tracks:
        if t.canvas:
            urls.setdefault(t.canvas.url, len(urls) + 1)
    for t in tracks:
        tag = f"canvas #{urls[t.canvas.url]} ({t.canvas.type.lower()})" if t.canvas else "no canvas"
        print(f"{t.track_number:>3}. {t.label:<60} {tag}")
    for url, n in urls.items():
        print(f"  canvas #{n}: {url}")


def print_scan(tracks: list[Track]) -> None:
    """Only the tracks that have a Canvas, grouped by album."""
    hits = [t for t in tracks if t.canvas]
    clips = {t.canvas.url for t in hits}
    print(f"\n{len(hits)} of {len(tracks)} tracks have a Canvas ({len(clips)} distinct clip(s)).")
    album = None
    for t in hits:
        if t.album != album:
            album = t.album
            print(f"\n{album or '(single track)'}")
        print(f"  {t.track_number:>3}. {t.name:<45} {t.canvas.type.lower():<14} {t.uri}")
    if hits:
        print("\nBuild one with:  canvas2we make <spotify:track:...> --install")


def _size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1920x1080")
    if w % 2 or h % 2:
        raise argparse.ArgumentTypeError("width and height must be even")
    return w, h


def _render_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--size", type=_size, default=(1920, 1080), help="output resolution (default 1920x1080)")
    p.add_argument("--layout", choices=wp.LAYOUTS, default="blur",
                   help="blur: clip centred over a blurred copy (default); fill: crop to fill; fit: black bars")
    p.add_argument("--fps", type=int, help="output frame rate (default: same as the Canvas)")
    p.add_argument("--crossfade", type=float, default=0.0, metavar="SEC",
                   help="blend the loop seam over SEC seconds, for canvases that don't loop cleanly")
    p.add_argument("-o", "--out", type=Path, default=Path("wallpapers"), help="output folder (default ./wallpapers)")
    p.add_argument("--install", action="store_true",
                   help="also copy the project into Wallpaper Engine's myprojects folder")
    p.add_argument("--we-dir", type=Path, help="Wallpaper Engine myprojects folder, if not auto-detected")


def _query_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("query", help="Spotify track/album/artist link or URI, or search text")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--album", dest="kind", action="store_const", const="album", default="track",
                   help="search text is an album name")
    g.add_argument("--artist", dest="kind", action="store_const", const="artist",
                   help="search text is an artist name (covers all their releases)")


def _opts(a) -> wp.RenderOptions:
    return wp.RenderOptions(width=a.size[0], height=a.size[1], layout=a.layout, fps=a.fps, crossfade=a.crossfade)


def _finish(folder: Path, a) -> None:
    print(f"Wallpaper Engine project: {folder.resolve()}")
    if a.install:
        we_dir = a.we_dir or wp.default_wallpaper_engine_dir()
        if not we_dir:
            print("Couldn't find Wallpaper Engine; pass --we-dir <...>/wallpaper_engine/projects/myprojects")
            return
        print(f"Installed to: {wp.install_project(folder, we_dir)}")


def run_ui(port: int, host: str = "127.0.0.1", public: bool = False, open_browser: bool = True) -> int:
    """Start the Streamlit app and open it in the browser once it answers.

    ``public`` hides everything that acts on this computer (Connect, Settings,
    installing into Wallpaper Engine), for sharing the app with other people.
    """
    import socket
    import subprocess
    import time
    import urllib.request
    import webbrowser

    url = f"http://127.0.0.1:{port}/"
    # Otherwise the health check below would find whatever already holds the
    # port and report this (failed) launch as running.
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Port {port} is already in use, maybe by another `canvas2we ui`.\n"
                  f"Open {url} if it's that, or pick another port with --port.", file=sys.stderr)
            return 1

    app = Path(__file__).with_name("streamlit_app.py")
    # headless=true skips Streamlit's first-run email prompt, which otherwise
    # waits in the terminal and leaves the browser page blank.
    proc = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.port", str(port), "--server.address", host, "--server.headless", "true",
        "--client.toolbarMode", "minimal", "--browser.gatherUsageStats", "false",
        *theme.streamlit_flags(),
    ], env={**os.environ, "CANVAS2WE_PUBLIC": "1"} if public else None)
    try:
        for _ in range(120):
            if proc.poll() is not None:
                print("Streamlit exited before starting; see the messages above.", file=sys.stderr)
                return proc.returncode or 1
            try:
                urllib.request.urlopen(url + "_stcore/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        print(f"\ncanvas2we is running at {url}{'  (public mode)' if public else ''}  (Ctrl+C to stop)\n")
        if open_browser:
            webbrowser.open(url)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="canvas2we", description="Spotify Canvas -> Wallpaper Engine wallpaper")
    ap.add_argument("--token", help="Spotify web-player bearer token (skips the headless browser)")
    ap.add_argument("--sp-dc", help="sp_dc cookie for a logged-in token (or set SPOTIFY_SP_DC)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="find which tracks by an artist (or on an album) have a Canvas")
    p.add_argument("query", help="artist or album name, or a Spotify link")
    p.add_argument("--album", dest="kind", action="store_const", const="album", default="artist",
                   help="search text is an album name (default: an artist name)")

    p = sub.add_parser("list", help="show every track and whether it has a Canvas")
    _query_args(p)

    p = sub.add_parser("make", help="download a Canvas and build a wallpaper")
    _query_args(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--track", type=int, metavar="N", help="with an album: use track number N")
    g.add_argument("--all", action="store_true", help="with an album or artist: build every distinct Canvas")
    _render_args(p)

    p = sub.add_parser("local", help="build a wallpaper from a Canvas file you already have")
    p.add_argument("file", type=Path)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    _render_args(p)

    p = sub.add_parser("ui", help="open the Streamlit app in your browser")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8501)))
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 on a server)")
    p.add_argument("--public", action="store_true",
                   help="for sharing: hide Connect, Settings and anything that acts on this computer")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window")

    p = sub.add_parser("serve", help="run the minimal built-in web UI (no Streamlit needed)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("-o", "--out", type=Path, default=Path("wallpapers"))

    a = ap.parse_args(argv)
    try:
        if a.cmd == "local":
            meta = wp.WallpaperMeta(title=a.title, description=a.description)
            _finish(wp.build_project(a.file, a.out, meta, _opts(a)), a)
        elif a.cmd == "ui":
            return run_ui(a.port, host=a.host, public=a.public, open_browser=not a.no_browser)
        elif a.cmd == "serve":
            from .web import serve
            serve(a.port, a.out, token=a.token, sp_dc=a.sp_dc)
        else:
            client = get_client(a.token, a.sp_dc)
            tracks = lookup(client, a.query, a.kind)
            if a.cmd == "scan":
                print_scan(tracks)
                return 0
            if a.cmd == "list":
                print_tracks(tracks)
                return 0
            chosen = pick_tracks(tracks, a.track, a.all)
            if not chosen:
                print_tracks(tracks)
                print("None of these tracks has a Canvas.", file=sys.stderr)
                return 1
            for t in chosen:
                _finish(make_wallpaper(client, t, a.out, _opts(a)), a)
    except (SpotifyError, wp.RenderError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
