"""Render a Canvas clip into a Wallpaper Engine video-wallpaper project.

Canvases are short vertical (9:16) loops, usually 720x1280 and 3-8 s long.
A desktop is 16:9, so we compose the clip for the target resolution and
write a project folder Wallpaper Engine can open directly:

    <out>/<slug>/
        project.json     Wallpaper Engine metadata (type "video")
        wallpaper.mp4    the rendered loop
        preview.gif      animated thumbnail for the browser/Workshop (<1 MB)
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LAYOUTS = ("blur", "fill", "fit")
PREVIEW_MAX_BYTES = 1_000_000  # Steam Workshop preview limit


class RenderError(RuntimeError):
    pass


@dataclass
class RenderOptions:
    width: int = 1920
    height: int = 1080
    layout: str = "blur"
    fps: int | None = None          # None keeps the source frame rate
    crossfade: float = 0.0          # seconds blended at the loop seam
    still_seconds: float = 10.0     # duration used when the Canvas is an image
    crf: int = 18


@dataclass
class WallpaperMeta:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=lambda: ["Music"])
    content_rating: str = "Everyone"


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:80] or "canvas"


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RenderError(f"{cmd[0]} failed:\n{tail}")
    return proc.stdout


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RenderError(f"{tool} not found on PATH. Install FFmpeg: https://ffmpeg.org/download.html")


def probe(path: Path) -> dict:
    out = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name:format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    stream = data["streams"][0]
    num, _, den = stream.get("r_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(data.get("format", {}).get("duration") or 0)
    is_image = stream.get("codec_name") in ("mjpeg", "png", "webp") and duration <= 0.1
    return {"width": stream["width"], "height": stream["height"], "fps": fps,
            "duration": duration, "is_image": is_image}


def _layout_filter(opts: RenderOptions) -> str:
    """Filter graph from [src] to [laid] at the target size."""
    W, H = opts.width, opts.height
    if opts.layout == "fill":
        return (f"[src]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H}[laid]")
    if opts.layout == "fit":
        return (f"[src]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black[laid]")
    # blur: the clip centred as large as fits (full height on a desktop, full
    # width on a phone) over a blurred, darkened, zoomed copy of itself.
    # Blur at 1/4 scale, then upscale: same look, far cheaper.
    bw, bh = max(W // 4, 2) // 2 * 2, max(H // 4, 2) // 2 * 2
    return (
        "[src]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"gblur=sigma=12,eq=brightness=-0.12:saturation=1.15,scale={W}:{H}:flags=bicubic[bg];"
        f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[laid]"
    )


def _loop_filter(duration: float, crossfade: float) -> str:
    """Filter graph from [in] to [src] that makes the end blend into the start.

    Output = clip[c:D] whose last c seconds cross-fade into clip[0:c], so the
    final frame is ~clip[c], which is also the first frame. Length D - c.
    """
    c = crossfade
    if c <= 0 or duration <= 2 * c:
        return "[in]null[src]"
    return (
        "[in]split=2[a][b];"
        f"[a]trim=start={c},setpts=PTS-STARTPTS[body];"
        f"[b]trim=end={c},setpts=PTS-STARTPTS[head];"
        f"[body][head]xfade=transition=fade:duration={c}:offset={duration - 2 * c:.3f}[src]"
    )


def render_video(src: Path, dest: Path, opts: RenderOptions) -> dict:
    if opts.layout not in LAYOUTS:
        raise RenderError(f"layout must be one of {LAYOUTS}")
    info = probe(src)
    inputs: list[str] = []
    if info["is_image"]:
        inputs = ["-loop", "1", "-t", str(opts.still_seconds), "-i", str(src)]
        loop = "[in]null[src]"
    else:
        inputs = ["-i", str(src)]
        loop = _loop_filter(info["duration"], opts.crossfade)

    fps = opts.fps or (30 if info["is_image"] else round(info["fps"]) or 30)
    graph = f"[0:v]fps={fps},format=yuv420p[in];{loop};{_layout_filter(opts)};[laid]setsar=1,format=yuv420p[out]"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", graph, "-map", "[out]", "-an",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(opts.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(dest),
    ])
    return probe(dest)


def render_preview(video: Path, dest: Path) -> Path:
    """Animated GIF thumbnail, shrunk until it fits Steam's 1 MB preview limit."""
    for width, fps, secs in ((480, 12, 4), (400, 10, 4), (320, 10, 3), (256, 8, 3)):
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-t", str(secs), "-i", str(video),
            "-filter_complex",
            f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
            str(dest),
        ])
        if dest.stat().st_size <= PREVIEW_MAX_BYTES:
            return dest
    return dest


def average_color(video: Path) -> tuple[float, float, float]:
    """Average colour of the first second, for Wallpaper Engine's scheme colour."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "1", "-i", str(video), "-vf", "scale=1:1:flags=area",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    ).stdout
    if len(raw) < 3:
        return (0.0, 0.0, 0.0)
    return tuple(round(b / 255, 3) for b in raw[:3])  # type: ignore[return-value]


def hashes_close(a: int | None, b: int | None, max_bits: int = 6) -> bool:
    """Whether two frame hashes are of the same picture."""
    return a is not None and b is not None and bin(a ^ b).count("1") <= max_bits


def frame_hash(source: str, at: float) -> int | None:
    """64-bit difference hash of the frame at ``at`` seconds (9x8 greyscale)."""
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", *(["-ss", str(at)] if at else []), "-i", source, "-frames:v", "1",
             "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(raw) != 72 or max(raw) - min(raw) < 12:  # a flat frame (fade, black) matches anything
        return None
    return sum(1 << i for i in range(64) if raw[i // 8 * 9 + i % 8] > raw[i // 8 * 9 + i % 8 + 1])


@functools.lru_cache(maxsize=4096)
def fingerprint(source: str) -> tuple[int, ...]:
    """Frame hashes at 1 s and 3 s of a clip (a URL or a path), for spotting the same video in different files.

    Spotify often gives each track on an album, or each edition of it, its
    own copy of one Canvas, so the URLs differ but the video doesn't. FFmpeg
    only reads the start of the file, so a remote Canvas isn't downloaded
    whole. Images and very short clips fall back to their first frame.
    """
    hashes = tuple(h for h in (frame_hash(source, 1.0), frame_hash(source, 3.0)) if h is not None)
    return hashes or tuple(h for h in [frame_hash(source, 0)] if h is not None)


def same_clip(a: tuple[int, ...], b: tuple[int, ...], max_bits: int = 6) -> bool:
    """Whether two fingerprints show the same video (every compared frame within ``max_bits``)."""
    return bool(a and b and len(a) == len(b)) and all(hashes_close(x, y, max_bits) for x, y in zip(a, b))


def write_project(folder: Path, meta: WallpaperMeta, video_name: str, preview_name: str,
                  scheme: tuple[float, float, float]) -> Path:
    project = {
        "contentrating": meta.content_rating,
        "description": meta.description,
        "file": video_name,
        "general": {
            "properties": {
                "schemecolor": {
                    "order": 0,
                    "text": "ui_browse_properties_scheme_color",
                    "type": "color",
                    "value": " ".join(str(c) for c in scheme),
                }
            }
        },
        "preview": preview_name,
        "tags": meta.tags,
        "title": meta.title,
        "type": "video",
        "visibility": "private",
    }
    path = folder / "project.json"
    path.write_text(json.dumps(project, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def build_project(src: Path, out_dir: Path, meta: WallpaperMeta, opts: RenderOptions) -> Path:
    """Render ``src`` into a complete Wallpaper Engine project; returns its folder."""
    require_ffmpeg()
    folder = out_dir / slugify(meta.title)
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "wallpaper.mp4"
    render_video(src, video, opts)
    render_preview(video, folder / "preview.gif")
    write_project(folder, meta, video.name, "preview.gif", average_color(video))
    return folder


def default_wallpaper_engine_dir() -> Path | None:
    """Locate Wallpaper Engine's myprojects folder on a standard Steam install."""
    candidates = []
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Steam")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                candidates.insert(0, Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
    for steam in candidates:
        libs = [steam]
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [Path(p.replace("\\\\", "\\")) for p in
                     re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore"))]
        for lib in libs:
            we = lib / "steamapps" / "common" / "wallpaper_engine" / "projects" / "myprojects"
            if we.is_dir():
                return we
    return None


def install_project(folder: Path, we_dir: Path) -> Path:
    target = we_dir / folder.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(folder, target)
    return target
