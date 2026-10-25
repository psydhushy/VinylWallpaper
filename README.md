# Wallpaper Engine Vinyl: Spotify Canvas to Wallpaper Engine

Takes a Spotify song (or a whole album), downloads its **Canvas** (the looping
vertical video Spotify plays behind a track) and turns it into a ready-to-use
**Wallpaper Engine** project: a 16:9 video, an animated preview, and a
`project.json`.

## Install

Needs Python 3.9+ and [FFmpeg](https://ffmpeg.org/download.html) on your PATH
(on Windows: `winget install ffmpeg`).

```bash
pip install -e .
playwright install chromium     # headless browser used to get a Spotify token
```

## Use it

### Browser app (Streamlit)

```bash
canvas2we ui
```

This opens http://localhost:8501. In the app:

1. Press **Connect to Spotify** at the top right. Spotify's login page opens in
   a normal Chrome window (or Edge, if Chrome isn't installed). Log in there
   (*Sign in with Google* works). The window closes by itself once you're
   in. You stay connected next time; **Disconnect** forgets the login.

   That window uses a Chrome profile kept just for this app (next to the
   settings file), not your everyday one, so it starts without your saved
   passwords. After you sign in once, it remembers you. Apps can't use your
   everyday Chrome profile: Chrome keeps it locked while it's open and blocks
   tools from reading its logins.
2. Type an artist such as **Taylor Swift**, choose *Artist*, *Album* or
   *Song*, and press **Search**, or click one of the popular artists under the
   search bar. Pick the right one from the results, which show each
   artist's picture and top tracks so similar names are easy to tell apart.
   Pasting a Spotify link skips this step.
   Once you're connected, the front page also shows **Most popular**: a couple
   of canvases from the top tracks of a dozen of Spotify's biggest artists
   (refreshed every 6 hours). Click one to make it a wallpaper.
3. Every Canvas plays in the grid. Narrow it to particular albums with the
   album filter above the grid.
   Each distinct Canvas shows once, grouped by album. Spotify often gives
   every track (and every edition of an album) its own copy of the same clip,
   so the app compares a couple of frames of each clip and merges the copies.
   A card says how many other tracks use it.
4. Click a card to open it in the panel on the right, choose the layout and
   resolution, and press **Make wallpaper**. The finished wallpaper plays in
   the panel, with its folder path and a **Download project (.zip)** button.

Under the panel, **Set it as your wallpaper** has steps for each device:

- **PC:** get [Wallpaper Engine on Steam](https://store.steampowered.com/app/431960/Wallpaper_Engine/),
  then use the installed project or the zip.
- **Android:** make it at *1080×2340 (phone)*, add it to Wallpaper Engine on
  your PC, and send it to the free Wallpaper Engine Android app (Mobile, at
  the top of Wallpaper Engine on PC).
- **iPhone:** no Wallpaper Engine needed. Save the Canvas video (there's a QR
  code to open it on your phone), turn it into a Live Photo with a free app
  such as intoLive, and set it with Photos → Share → Use as Wallpaper.

**Settings** (the gear, top right) sets the output folder and whether to copy
projects straight into Wallpaper Engine. It also has the raw `sp_dc` cookie,
if you'd rather paste it yourself, and the optional Web API login.

Set `CANVAS2WE_DONATE_URL` (for example `https://paypal.me/<you>`) before
launching to show a **Donate** button in the header and footer.

The Python package and command are still called `canvas2we`.

`canvas2we serve` still starts the older, minimal UI, which doesn't need
Streamlit.

### Share it with friends

Run it in **public mode**, which hides everything that acts on your computer
(Connect, Settings, copying into your Wallpaper Engine). Visitors search and
download zips, using your server's Spotify session if it has one (your saved
Connect, or `SPOTIFY_SP_DC`). Each visitor gets a private temporary folder,
and at most two wallpapers render at once (`CANVAS2WE_MAX_RENDERS`).

**Quick test from your PC** (free; works while your PC is on):

```bash
winget install --id Cloudflare.cloudflared
canvas2we ui --public
cloudflared tunnel --url http://localhost:8501
```

`cloudflared` prints a `https://<random-words>.trycloudflare.com` link. Send
that to your testers. It stops working when you press Ctrl+C or turn the PC
off, and every scan and render runs on your PC with your Spotify session.

**Always-on hosting on Streamlit Community Cloud** (free):

1. Put the project on GitHub: create a repository at github.com/new, then
   **Add file → Upload files** and drag in the project's contents (`app.py`,
   `requirements.txt`, `packages.txt`, the `.streamlit` and `canvas2we`
   folders, and the rest).
2. At share.streamlit.io, sign in with GitHub and choose **Create app → Deploy
   a public app from GitHub**. Pick the repository, branch `main`, and main
   file `app.py`. Under **Advanced settings**, choose Python 3.12 and paste
   into **Secrets**:

   ```toml
   CANVAS2WE_DONATE_URL = "https://paypal.me/<you>"
   # optional: a session cookie from a spare Spotify account
   # SPOTIFY_SP_DC = "..."
   ```

3. Press **Deploy**. The first start takes a few minutes while FFmpeg and
   Chromium install (`packages.txt`). Your link is
   `https://<name>.streamlit.app`.

`app.py` turns on public mode by itself, so the hosted copy never shows
Connect or Settings. The app sleeps after a while without visitors and wakes
on the next visit. To update it, upload the changed files to GitHub again;
the app redeploys by itself.

The `Dockerfile` runs the same public site on any host that runs containers
(Render, Fly.io, Railway, Google Cloud Run, a VPS); it listens on `$PORT`
(default 7860).

### Optional: Spotify Web API login (complete discographies)

**Connect to Spotify** is all most people need. For artists with very large
catalogues, you can also log in to Spotify's official Web API under
*Advanced*, which lists every release and gives better search ranking. That
needs a free app of your own on Spotify's developer site:

1. At [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   create an app with the **Web API** enabled.
2. In the app's settings, add the redirect URI the sidebar shows:
   `http://127.0.0.1:8501/` (or your `--port`). It must match exactly.
3. Paste the app's **Client ID** under *Settings* and press **Log in to the
   Web API**. No client secret is needed (the login uses PKCE).

While the app is in development mode, only accounts listed under **User
Management** in the dashboard can log in.

Once logged in, typed names are searched with the official API, and an artist
scan covers every album, single/EP and compilation, not just the ones on the
artist page. If an official call fails, the app falls back to the web player.
The command line uses the same saved login and remembered cookie.

Settings are saved in `%APPDATA%\canvas2we\config.json` (Windows) or
`~/.config/canvas2we/config.json`; set `CANVAS2WE_CONFIG_DIR` to move them.
That file holds your Spotify session cookie and login tokens, so keep it
private.

### Find out which canvases exist

```bash
canvas2we scan "Opeth"                         # every release by an artist
canvas2we scan "Ghost Reveries" --album        # one album
canvas2we scan https://open.spotify.com/artist/0ybFZ2Ab08V8hueghSXm6E
```

`scan` lists only the tracks that have a Canvas, grouped by album, with each
track's `spotify:track:` URI to pass to `make`. In the web UI, choose
**Artist**, **Album** or **Song** next to the search box.

Without the official login, an artist scan covers the releases Spotify shows
on the artist's page: albums, singles, compilations and popular releases. For
artists with very large catalogues, some older releases may be missing.

Without the official login, typed names are looked up through the web
player's own search page in the headless browser. If that fails, the app falls back to Spotify's Web API,
which often rate-limits these tokens. Pasting a link always works.

### Command line (test case: Opeth, *Ghost Reveries*)

```bash
# Which Ghost Reveries tracks have a Canvas?
canvas2we list "Opeth Ghost Reveries" --album

# Build the first one, at 1440p, and copy it straight into Wallpaper Engine
canvas2we make "Opeth Ghost Reveries" --album --size 2560x1440 --install

# A specific track (album track number), or every distinct Canvas on the album
canvas2we make "Opeth Ghost Reveries" --album --track 1
canvas2we make "Opeth Ghost Reveries" --album --all

# Any single song, by link or by search
canvas2we make "https://open.spotify.com/track/<id>"
canvas2we make "Opeth Ghost of Perdition"

# Already have the Canvas .mp4? Skip Spotify entirely
canvas2we local canvas.mp4 --title "Ghost of Perdition"
```

Rendering options (for `make`, `local`, and the UI):

| Option | Meaning |
|---|---|
| `--layout blur` (default) | Canvas centred at full height over a blurred, darkened copy of itself |
| `--layout fill` | Crop the Canvas to fill the screen (loses the top and bottom) |
| `--layout fit` | Canvas centred with black bars |
| `--size WxH` | Output resolution, for example `3840x2160` or `3440x1440` for ultrawide |
| `--crossfade 0.5` | Blend the loop seam, for canvases that jump when they restart |
| `--install` / `--we-dir` | Copy the project into `wallpaper_engine/projects/myprojects` |

Output goes to `./wallpapers/<title>/`:

```
project.json    type "video", title, description, Music tag, scheme colour
wallpaper.mp4   H.264 loop, no audio
preview.gif     animated thumbnail, kept under Steam's 1 MB preview limit
```

## Getting it into Wallpaper Engine and onto the Workshop

1. **Use it locally:** run with `--install` (or copy the folder into
   `...\steamapps\common\wallpaper_engine\projects\myprojects\`). It appears in
   Wallpaper Engine's *Installed* tab. You can also use **Open Wallpaper →
   Open from File** and pick `project.json`.
2. **Upload it:** open Wallpaper Engine's **Wallpaper Editor**, open the
   project, then use **Workshop → Share Wallpaper on Workshop**. Steam only
   allows Workshop uploads through the editor, so this step can't be
   automated.

> **Copyright:** a Canvas is the artist's or label's artwork. Steam's Workshop
> terms only allow uploads of content you have rights to, so a public Workshop
> upload of someone else's Canvas can be taken down. Keep it **Private** or
> **Friends only** unless you have permission. The generated project starts as
> private.

### Why a *video* wallpaper and not a *scene*?

A Canvas is just a video. Wallpaper Engine's video wallpaper type plays it
natively, loops it, and publishes to the Workshop like any other wallpaper.
Scene wallpapers (the docs you linked) are for layered, interactive
compositions built in the editor. If you want scene features such as audio
response, start a scene in the editor and add the rendered `wallpaper.mp4` to
it.

## How the Spotify part works, and when it breaks

- Canvas isn't in Spotify's public API. The app calls the same internal
  endpoint the Spotify apps use (`spclient.wg.spotify.com/canvaz-cache/v0/canvases`,
  protobuf, schema from librespot's `canvaz.proto`).
- That endpoint needs a **web-player token**. Spotify keeps changing how the
  web player mints its token, so instead of re-implementing that, the app
  opens open.spotify.com in headless Chromium and captures the token the page
  sends. Tokens are cached for about 45 minutes.
- If anonymous tokens get refused (HTTP 401/403), use a logged-in one: copy
  the `sp_dc` cookie from open.spotify.com in your browser (DevTools →
  Application → Cookies) and set `SPOTIFY_SP_DC=<value>`. Treat it like a
  password.
- You can also bypass the browser with `--token <bearer>`, taken from any
  request's `Authorization` header in DevTools on open.spotify.com.
- Not every track has a Canvas. `list` shows which ones do. Albums often reuse
  one Canvas across tracks; `--all` builds each distinct clip once.

Because this relies on undocumented Spotify internals, it may need updating
when Spotify changes them, and using it may be against Spotify's terms of use.

## Tests

```bash
pip install pytest && pytest -q
```

These cover the protobuf codec, link parsing, album track selection, and real
FFmpeg renders of all three layouts plus image canvases, using synthetic
9:16 clips.
