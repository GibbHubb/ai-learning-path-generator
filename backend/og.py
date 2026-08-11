"""AP30-fu1 — per-user Open Graph card for public profiles.

AP30 shipped the public profile page but left `frontend/index.html` carrying a
single static OG card, so every shared `/u/:id` link unfurled identically. That
kills the growth loop the feature exists for: a shared profile is meant to
advertise *that* learner's progress.

Two pieces live here, deliberately kept out of `main.py`/`routes.py`:

* :func:`build_profile_meta_html` — the crawler-facing HTML shell.
* :func:`render_profile_card_png` — the 1200x630 card image.

Both read the stats dict produced by ``routes._compute_user_stats``, so the
numbers can never drift from what the API serves.

**Privacy.** ``User`` has no name or handle column — only ``email``, which is
PII and must never appear in a public card (AP30's rule). The public identity
is therefore a synthetic, non-identifying handle: ``Learner #42``. Nothing here
touches ``user.email``; the only field read off the user is ``id``.
"""
from __future__ import annotations

import html
from io import BytesIO

# 1200x630 is the size every major unfurler (Slack, X, Facebook, LinkedIn)
# crops to; anything else gets letterboxed or centre-cropped unpredictably.
CARD_W, CARD_H = 1200, 630

# Brand-ish palette. Kept local rather than imported from the frontend so the
# backend has no build-time coupling to Vite assets.
BG = (14, 16, 26)
PANEL = (23, 26, 40)
ACCENT = (124, 92, 255)
ACCENT_2 = (56, 189, 248)
TEXT = (245, 246, 250)
MUTED = (150, 157, 178)


def public_display_name(user_id: int) -> str:
    """The public handle for a learner.

    A synthetic handle, not a name: there is no name column, and the only
    human-readable field on ``User`` is the email address, which is PII.
    """
    return f"Learner #{user_id}"


# ---------------------------------------------------------------------------
# PNG card
# ---------------------------------------------------------------------------

def _font(size: int, bold: bool = False):
    """Best-available font at ``size``.

    Pillow's bundled bitmap default does not scale, so a 1200x630 card drawn
    with it is illegibly small. Try a few fonts that exist on typical Linux and
    Windows hosts before falling back — the fallback still renders, just
    smaller, so a missing font degrades the card rather than 500-ing the route.
    """
    from PIL import ImageFont

    candidates = (
        ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "seguisb.ttf"]
        if bold else
        ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _stat_block(draw, x: int, y: int, value: str, label: str, colour) -> None:
    draw.text((x, y), value, font=_font(78, bold=True), fill=colour)
    draw.text((x, y + 92), label.upper(), font=_font(24), fill=MUTED)


def render_profile_card_png(display_name: str, stats: dict) -> bytes:
    """Render the 1200x630 OG card as PNG bytes.

    ``stats`` is a ``_compute_user_stats`` result. Only the PII-free numeric
    fields are drawn.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    d = ImageDraw.Draw(img)

    # Accent bar down the left edge — cheap way to make the card recognisable
    # at thumbnail size, where text is unreadable anyway.
    d.rectangle([0, 0, 18, CARD_H], fill=ACCENT)

    d.text((78, 74), "AI LEARNING PATH", font=_font(28, bold=True), fill=ACCENT_2)
    d.text((78, 128), display_name, font=_font(86, bold=True), fill=TEXT)

    # Stats panel
    d.rounded_rectangle([78, 268, CARD_W - 78, 496], radius=24, fill=PANEL)

    xp = f"{stats.get('total_xp', 0):,}"
    streak = str(stats.get("best_streak", 0))
    badges = str(len(stats.get("earned_badges") or []))
    completed = str(stats.get("completed_paths", 0))

    _stat_block(d, 126, 310, xp, "total xp", TEXT)
    _stat_block(d, 450, 310, streak, "best streak", ACCENT_2)
    _stat_block(d, 700, 310, badges, "badges", ACCENT)
    _stat_block(d, 930, 310, completed, "paths done", TEXT)

    d.text((78, 542), "Track your own learning path", font=_font(30), fill=MUTED)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Crawler HTML shell
# ---------------------------------------------------------------------------

_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="description" content="{desc}">

<meta property="og:type" content="profile">
<meta property="og:site_name" content="AI Learning Path">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{card}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{canonical}">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{card}">

<link rel="canonical" href="{spa}">
<meta http-equiv="refresh" content="0;url={spa}">
</head>
<body>
<p>Redirecting to <a href="{spa}">{spa}</a>&hellip;</p>
<script>location.replace({spa_js});</script>
</body>
</html>
"""


def build_profile_meta_html(display_name: str, stats: dict, card_url: str,
                            spa_url: str, canonical_url: str = "") -> str:
    """The HTML a crawler receives for ``GET /u/{id}``.

    Crawlers do not execute JS or follow ``http-equiv=refresh``, so they read
    the per-user meta and stop. Humans are bounced to the SPA immediately by
    both the refresh and ``location.replace``. That means no user-agent
    sniffing is needed — which matters, because UA lists rot and a missed
    crawler silently falls back to the generic card.
    """
    xp = stats.get("total_xp", 0)
    streak = stats.get("best_streak", 0)
    badges = len(stats.get("earned_badges") or [])
    completed = stats.get("completed_paths", 0)

    title = f"{display_name} — {xp:,} XP on AI Learning Path"
    desc = (f"{xp:,} XP · {streak}-day best streak · {badges} "
            f"badge{'s' if badges != 1 else ''} earned · "
            f"{completed} path{'s' if completed != 1 else ''} completed.")

    esc = html.escape
    return _SHELL.format(
        title=esc(title, quote=True),
        desc=esc(desc, quote=True),
        card=esc(card_url, quote=True),
        spa=esc(spa_url, quote=True),
        canonical=esc(canonical_url or spa_url, quote=True),
        # json-ish literal for location.replace(); escaped separately because
        # HTML-escaping inside a <script> is not the right encoding.
        spa_js="'" + spa_url.replace("\\", "\\\\").replace("'", "\\'") + "'",
    )
