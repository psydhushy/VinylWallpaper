"""Small local web UI: search, preview canvases, build wallpapers."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import wallpaper as wp
from .cli import get_client, lookup, make_wallpaper
from .spotify import SpotifyError, Track


def serve(port: int, out_dir: Path, token: str | None = None, sp_dc: str | None = None) -> None:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks: dict[str, Track] = {}
    build_lock = threading.Lock()

    def track_json(t: Track) -> dict:
        return {
            "uri": t.uri, "label": t.label, "name": t.name, "artists": t.artists,
            "album": t.album, "album_art": t.album_art, "track_number": t.track_number,
            "canvas": {"url": t.canvas.url, "type": t.canvas.type} if t.canvas else None,
        }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                html = resources.files("canvas2we").joinpath("static/index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path.startswith("/out/"):
                f = (out_dir / unquote(path[5:])).resolve()
                if out_dir in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/api/lookup":
                    found = lookup(get_client(token, sp_dc), body["query"], body.get("kind", "track"))
                    tracks.update({t.uri: t for t in found})
                    return self._json(200, {"tracks": [track_json(t) for t in found]})
                if self.path == "/api/build":
                    t = tracks.get(body.get("uri", ""))
                    if not t:
                        return self._json(400, {"error": "Look the track up first."})
                    opts = wp.RenderOptions(
                        width=int(body.get("width", 1920)), height=int(body.get("height", 1080)),
                        layout=body.get("layout", "blur"), crossfade=float(body.get("crossfade", 0)),
                    )
                    with build_lock:
                        folder = make_wallpaper(get_client(token, sp_dc), t, out_dir, opts)
                        installed = None
                        if body.get("install"):
                            we_dir = wp.default_wallpaper_engine_dir()
                            if not we_dir:
                                raise wp.RenderError("Wallpaper Engine's myprojects folder wasn't found; "
                                                     "copy the folder over manually.")
                            installed = str(wp.install_project(folder, we_dir))
                    rel = folder.relative_to(out_dir).as_posix()
                    return self._json(200, {
                        "folder": str(folder), "installed": installed,
                        "video": f"/out/{rel}/wallpaper.mp4", "preview": f"/out/{rel}/preview.gif",
                    })
                self._json(404, {"error": "unknown endpoint"})
            except (SpotifyError, wp.RenderError, KeyError, ValueError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # surface anything else to the UI rather than hang
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"canvas2we UI at {url}  (wallpapers saved to {out_dir})  Ctrl+C to stop")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
