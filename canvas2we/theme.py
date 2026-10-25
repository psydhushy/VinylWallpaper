"""Look of the Streamlit UI: a quiet, editorial dark style (warm near-black,
a soft serif for headings, pill buttons, big rounded cards with captions
underneath), with a cartoon banana mascot and banana yellow as the only
accent colour.

The colours go to Streamlit twice: as theme flags when ``canvas2we ui``
starts it (so widgets match), and as CSS variables for the custom parts.
.streamlit/config.toml repeats the theme colours for hosts that run
Streamlit directly; keep it in step.
"""

from __future__ import annotations

BG = "#0c0a09"
SURFACE = "#171514"
SURFACE_HI = "#201e1d"
BORDER = "#2a2725"
TEXT = "#f3f1ee"
MUTED = "#a29d97"
ACCENT = "#ffd93b"

# The mascot: a cartoon banana with an outline, brown tips and a small face.
BANANA_SVG = (
    '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<path d="M14 12 C10 40 34 58 56 44 C38 40 24 30 20 13 Z" fill="#ffd93b" stroke="#3b2a10" '
    'stroke-width="2.5" stroke-linejoin="round"/>'
    '<path d="M20 20 C22 34 32 43 46 45" fill="none" stroke="#fff3a6" stroke-width="2.5" '
    'stroke-linecap="round" opacity=".85"/>'
    '<path d="M13 5 L17 12" stroke="#6b4a1e" stroke-width="4.5" stroke-linecap="round"/>'
    '<circle cx="56" cy="44" r="2.6" fill="#6b4a1e"/>'
    '<circle cx="24" cy="35" r="1.9" fill="#3b2a10"/><circle cx="30" cy="38.5" r="1.9" fill="#3b2a10"/>'
    '<path d="M25.5 40 Q28.5 43 32 41" fill="none" stroke="#3b2a10" stroke-width="1.6" stroke-linecap="round"/>'
    '</svg>'
)


def banana(cls: str = "") -> str:
    return f'<div class="c2w-banana {cls}">{BANANA_SVG}</div>'


def streamlit_flags() -> list[str]:
    return ["--theme.base", "dark", "--theme.primaryColor", ACCENT, "--theme.backgroundColor", BG,
            "--theme.secondaryBackgroundColor", SURFACE_HI, "--theme.textColor", TEXT]


_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Inter:wght@400;500;600&display=swap');

.stApp { background: var(--bg); font-family: "Inter", system-ui, sans-serif; }
[data-testid="stHeader"], [data-testid="stSidebarCollapsedControl"] { display: none; }
.block-container { max-width: 1240px; padding: 1.2rem 1.5rem 3rem; }
p, label, .stMarkdown { font-family: "Inter", system-ui, sans-serif; }

/* pills everywhere */
button, [data-testid^="stBaseLinkButton"] { border-radius: 999px !important; font-weight: 500 !important; }
button[kind="secondary"], [data-testid="stBaseLinkButton-secondary"] {
    background: var(--surface-hi) !important; border: 1px solid transparent !important; color: var(--text) !important; }
button[kind="secondary"]:hover, [data-testid="stBaseLinkButton-secondary"]:hover { border-color: var(--border) !important; }
button[kind="primary"], button[kind="primaryFormSubmit"] {
    background: var(--text) !important; color: var(--bg) !important; border: none !important; }
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover { background: #fff !important; }
.st-key-donate a { background: transparent !important; border: 1px solid var(--border) !important; }
.st-key-donate a:hover { border-color: var(--muted) !important; }
[data-testid="stTextInput"] input, [data-baseweb="select"] > div {
    border-radius: 999px !important; background: var(--surface-hi) !important; border-color: transparent !important; }
[data-testid="stTextInput"] > div > div { border-radius: 999px !important; border-color: transparent !important; }
.st-key-kind [data-testid="stButtonGroup"], .st-key-kind [data-testid="stButtonGroup"] > div {
    justify-content: center; width: 100%; }
.st-key-example { margin-top: -.4rem; }
.st-key-example [data-testid="stButtonGroup"] div { flex-wrap: wrap; }
.st-key-example label p { color: var(--muted); font-size: .85rem; }
.st-key-query input { height: 3rem; font-size: 1.02rem; padding-left: 1.3rem; }
[data-testid="stPopoverBody"] { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; }

/* nav + hero */
.c2w-logo { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 1.02rem; color: var(--text); }
.c2w-banana { width: 28px; height: 28px; flex: none; filter: drop-shadow(0 0 10px rgba(255, 217, 59, .25)); }
.c2w-banana svg { width: 100%; height: 100%; display: block; }
.c2w-hero { text-align: center; margin: 3.2rem 0 1.4rem; }
.c2w-hero .c2w-banana { width: 64px; height: 64px; margin: 0 auto 1rem; animation: c2w-wobble 3.2s ease-in-out infinite; }
.c2w-hero.small .c2w-banana { width: 44px; height: 44px; }
@keyframes c2w-wobble { 0%, 100% { transform: rotate(-6deg); } 50% { transform: rotate(6deg) translateY(-2px); } }
.c2w-hero h1 { font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 3.3rem;
    line-height: 1.05; letter-spacing: -.01em; color: var(--text); padding: 0; margin: 0; }
.c2w-hero p { color: var(--muted); font-size: 1.08rem; max-width: 560px; margin: .9rem auto 0; line-height: 1.5; }
.c2w-hero.small { margin: 1.6rem 0 .8rem; }
.c2w-hero.small h1 { font-size: 2.2rem; }
.c2w-status { display: inline-flex; align-items: center; gap: 8px; font-size: .9rem; color: var(--muted); }
.c2w-status .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }

.c2w-heading { font-family: "Instrument Serif", Georgia, serif; font-size: 1.9rem; color: var(--text);
    line-height: 1.1; margin: 1.6rem 0 .2rem; }
.c2w-sub { color: var(--muted); font-size: .95rem; }
.c2w-label { color: var(--muted); font-size: .8rem; margin: .9rem 0 .1rem; }

/* cards: media inset in a rounded card, caption underneath */
.c2w-card { background: var(--surface); border-radius: 16px; padding: 14px; border: 1px solid transparent;
    transition: background .15s, border-color .15s; }
.c2w-media { position: relative; aspect-ratio: 3 / 4; overflow: hidden; border-radius: 10px; background: #050404; }
.c2w-media video, .c2w-media img { width: 100%; height: 100%; object-fit: cover; display: block; }
.c2w-card.none .c2w-media { filter: grayscale(.8) brightness(.5); }
.c2w-card.selected { border-color: var(--accent); }
.c2w-badge { position: absolute; top: 8px; left: 8px; font-size: .7rem; padding: 2px 9px; border-radius: 999px;
    background: rgba(12, 10, 9, .75); color: var(--muted); }
.c2w-cap { display: flex; gap: 10px; align-items: center; padding: 10px 2px 18px; }
.c2w-cap img, .c2w-cap .ph { width: 30px; height: 30px; border-radius: 50%; object-fit: cover; flex: none;
    background: var(--surface-hi); }
.c2w-cap div { min-width: 0; }
.c2w-cap b, .c2w-cap small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.c2w-cap b { color: var(--text); font-weight: 500; font-size: .93rem; }
.c2w-cap small { color: var(--muted); font-size: .85rem; }
.c2w-cand { display: flex; gap: 12px; align-items: center; padding: 10px 14px 10px 10px; border-radius: 16px;
    min-height: 82px; background: var(--surface); border: 1px solid transparent; transition: background .15s, border-color .15s; }
.c2w-cand img, .c2w-cand .ph { width: 60px; height: 60px; object-fit: cover; border-radius: 10px; flex: none;
    background: var(--surface-hi); }
.c2w-cand.artist img, .c2w-cand.artist .ph { border-radius: 50%; }
.c2w-cand b { display: block; color: var(--text); font-weight: 500; }
.c2w-cand small { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    color: var(--muted); font-size: .8rem; line-height: 1.35; }

/* whole card clickable: a transparent Streamlit button laid over it */
div[class*="st-key-tile-"] { position: relative; }
div[class*="st-key-tile-"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
div[class*="st-key-tile-"]:hover .c2w-card:not(.selected), div[class*="st-key-tile-"]:hover .c2w-cand {
    background: var(--surface-hi); border-color: var(--border); }
div[class*="st-key-pick-"] { position: absolute; inset: 0; z-index: 2; width: 100% !important; }
div[class*="st-key-pick-"] div, div[class*="st-key-pick-"] button { width: 100% !important; height: 100%; }
div[class*="st-key-pick-"] button { opacity: 0; cursor: pointer; }
.st-key-album_filter [data-testid="stButtonGroup"] div { flex-wrap: wrap; }

/* source header + detail card */
.c2w-source { display: flex; gap: 16px; align-items: center; margin: 1.4rem 0 .6rem; }
.c2w-source img { width: 64px; height: 64px; object-fit: cover; border-radius: 12px; }
.c2w-source.artist img { border-radius: 50%; }
.c2w-source .name { font-family: "Instrument Serif", Georgia, serif; font-size: 2.2rem; line-height: 1; color: var(--text); }
.c2w-source small { color: var(--muted); font-size: .92rem; }
.st-key-detail { background: var(--surface); border-radius: 16px; padding: 16px; position: sticky; top: 1rem; }
.c2w-preview { display: flex; justify-content: center; background: #050404; border-radius: 10px; overflow: hidden; }
.c2w-preview video, .c2w-preview img { max-height: 420px; max-width: 100%; display: block; }
.c2w-title { font-family: "Instrument Serif", Georgia, serif; font-size: 1.6rem; line-height: 1.1; color: var(--text);
    margin-top: 10px; }
.c2w-meta { color: var(--muted); font-size: .88rem; }
.c2w-album { display: flex; gap: 14px; align-items: center; margin: 1.6rem 0 .7rem; }
.c2w-album img { width: 48px; height: 48px; border-radius: 8px; object-fit: cover; }
.c2w-album b { display: block; font-family: "Instrument Serif", Georgia, serif; font-weight: 400;
    font-size: 1.5rem; line-height: 1.1; color: var(--text); }
.c2w-album small { color: var(--muted); font-size: .85rem; }
.c2w-qr { display: flex; gap: 12px; align-items: center; margin: .4rem 0 .8rem; }
.c2w-qr svg { width: 96px; height: 96px; border-radius: 8px; flex: none; }
.c2w-qr small { color: var(--muted); font-size: .82rem; }
.st-key-detail [data-testid="stMarkdownContainer"] li,
.st-key-detail [data-testid="stMarkdownContainer"] p { font-size: .9rem; color: var(--muted); }
.st-key-detail [data-testid="stMarkdownContainer"] li strong { color: var(--text); font-weight: 500; }
.c2w-progress { margin: 1.2rem 0 1.2rem; }
.c2w-progress .track { height: 3px; border-radius: 999px; background: var(--surface-hi); overflow: hidden; }
.c2w-progress .fill { height: 100%; border-radius: 999px; background: var(--accent); }
.c2w-progress .fill.sliding { width: 30%; animation: c2w-slide 1.1s ease-in-out infinite; }
@keyframes c2w-slide { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
[data-testid="stDialog"] > div > div { background: var(--surface) !important; border-radius: 18px !important;
    border: 1px solid var(--border); }
[data-testid="stDialog"] h2, [data-testid="stDialog"] h2 * { font-family: "Instrument Serif", Georgia, serif !important; font-weight: 400 !important;
    font-size: 2.1rem !important; }
[data-testid="stDialog"] .c2w-preview video, [data-testid="stDialog"] .c2w-preview img { max-height: 560px; }
.c2w-skeleton { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-top: 1.2rem; }
.c2w-skeleton div { aspect-ratio: 3 / 4.6; border-radius: 16px; background-size: 300% 100%;
    background-image: linear-gradient(100deg, var(--surface) 35%, var(--surface-hi) 50%, var(--surface) 65%);
    animation: c2w-shimmer 1.4s linear infinite; }
.c2w-skeleton.rows div { aspect-ratio: auto; height: 82px; }
@keyframes c2w-shimmer { from { background-position: 100% 0; } to { background-position: 0 0; } }
.c2w-note { background: var(--surface); border-radius: 16px; padding: 14px 18px; color: var(--muted);
    margin-bottom: 1rem; }
.c2w-note b { color: var(--text); font-weight: 500; }
.c2w-empty { color: var(--muted); text-align: center; padding: 48px 12px; }

/* footer */
.c2w-footer { margin-top: 4rem; padding-top: 1.6rem; border-top: 1px solid var(--border); color: var(--muted);
    font-size: .85rem; display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.c2w-footer a { color: var(--text); text-decoration: none; border: 1px solid var(--border); border-radius: 999px;
    padding: 4px 12px; }
.c2w-footer a:hover { border-color: var(--muted); }
"""


def css() -> str:
    root = (f":root {{ --bg: {BG}; --surface: {SURFACE}; --surface-hi: {SURFACE_HI}; --border: {BORDER}; "
            f"--text: {TEXT}; --muted: {MUTED}; --accent: {ACCENT}; }}")
    return f"<style>{_CSS}{root}</style>"
