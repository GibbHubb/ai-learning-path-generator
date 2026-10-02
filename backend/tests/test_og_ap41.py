"""AP41 — og.render_profile_card_png's failure paths.

The card is bytes that only a decoder (or a human eye) can validate: an API
test that checks "some PNG bytes came back" passes for an image no browser can
open. So every test here DECODES the result with Pillow and asserts the real
pixel size, plus that something was actually drawn on it.
"""
import os
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageFont

import og

FULL_STATS = {"total_xp": 1234, "best_streak": 7, "earned_badges": ["a", "b"],
              "completed_paths": 3}


def _decode(png: bytes) -> Image.Image:
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG signature"
    img = Image.open(BytesIO(png))
    img.load()  # forces a full decode — a truncated stream raises here
    return img


def _text_pixels(img: Image.Image) -> int:
    """Pixels in the card's TEXT colour — 0 means no headline text was drawn."""
    return sum(1 for px in img.convert("RGB").getdata() if px == og.TEXT)


def test_happy_path_decodes_to_1200x630_with_text():
    img = _decode(og.render_profile_card_png("Learner #1", FULL_STATS))
    assert img.size == (1200, 630)
    assert img.format == "PNG"
    assert _text_pixels(img) > 500


def test_stats_missing_every_key_still_renders_a_valid_card():
    img = _decode(og.render_profile_card_png("Learner #2", {}))
    assert img.size == (1200, 630)
    assert _text_pixels(img) > 500


def test_earned_badges_none_is_treated_as_zero():
    img = _decode(og.render_profile_card_png("Learner #3", {"earned_badges": None}))
    assert img.size == (1200, 630)


def test_missing_bundled_font_falls_back_and_still_renders(monkeypatch, tmp_path):
    """The bundled DejaVu files are absent (e.g. a checkout without assets/):
    the system-font probe, and finally Pillow's default, must still yield a
    decodable 1200x630 card rather than an exception."""
    monkeypatch.setattr(og, "_ASSETS", tmp_path / "no-such-assets")
    tried = []
    real_truetype = ImageFont.truetype

    def recording_truetype(font, size, *a, **kw):
        tried.append(str(font))
        # AP48 — on Linux, Pillow retries a missing path by its BASENAME in the
        # system font dirs, so on a host with DejaVu installed (GitHub's Ubuntu
        # runner) the "missing" bundled file still loaded and no fallback ran.
        # Make the missing bundle actually missing.
        if "no-such-assets" in str(font):
            raise OSError("cannot open resource")
        return real_truetype(font, size, *a, **kw)

    monkeypatch.setattr(ImageFont, "truetype", recording_truetype)
    img = _decode(og.render_profile_card_png("Learner #4", FULL_STATS))
    assert img.size == (1200, 630)
    assert "no-such-assets" in tried[0], "the bundled path was not tried first"
    assert any("no-such-assets" not in t for t in tried), \
        "no fallback font was tried after the bundled one failed"


def test_no_truetype_font_anywhere_uses_the_default_font(monkeypatch):
    """Worst case: every truetype lookup fails. load_default() is the floor."""
    real_truetype = ImageFont.truetype

    def always_fails(font=None, *a, **kw):
        # Fail every lookup BY NAME/PATH. Pillow's own load_default() calls
        # truetype() on an in-memory font (a file object), which must still
        # work — that is the embedded floor this test is about.
        if isinstance(font, (str, os.PathLike)):
            raise OSError("no fonts on this host")
        return real_truetype(font, *a, **kw)

    used_default = []
    real_default = ImageFont.load_default

    def recording_default(*a, **kw):
        used_default.append(True)
        return real_default(*a, **kw)

    monkeypatch.setattr(ImageFont, "truetype", always_fails)
    monkeypatch.setattr(ImageFont, "load_default", recording_default)
    img = _decode(og.render_profile_card_png("Learner #5", {}))
    assert img.size == (1200, 630)
    assert used_default, "load_default() was never reached"


def test_the_bundled_fonts_exist_in_the_repo():
    """Control for the fallback tests: in a normal checkout the fallback is NOT
    what renders the card."""
    assets = Path(og.__file__).resolve().parent / "assets"
    assert (assets / "DejaVuSans.ttf").is_file()
    assert (assets / "DejaVuSans-Bold.ttf").is_file()


def test_a_truncated_png_is_rejected_by_the_decoder():
    """Control for _decode itself: it must be able to fail."""
    png = og.render_profile_card_png("Learner #6", FULL_STATS)
    with pytest.raises(Exception):
        _decode(png[: len(png) // 2])
