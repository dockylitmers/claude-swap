"""Tests for the SwiftBar plugin in contrib/swiftbar."""

from __future__ import annotations

import importlib.util
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "contrib" / "swiftbar" / "cswap.1m.py"
NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def plugin():
    spec = importlib.util.spec_from_file_location("cswap_swiftbar_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _window(pct: float, hours: float, countdown: str) -> dict:
    resets = (NOW + timedelta(hours=hours)).isoformat()
    return {"pct": pct, "resetsAt": resets, "countdown": countdown}


def _payload() -> dict:
    return {
        "schemaVersion": 1,
        "activeAccountNumber": 2,
        "accounts": [
            {
                "number": 3,
                "email": "john.doe@company.com",
                "alias": "Work",
                "active": False,
                "usageStatus": "ok",
                "usage": {
                    "fiveHour": _window(96, 2.6, "2h 37m"),
                    "sevenDay": _window(40, 117, "4d 21h"),
                    "scoped": [dict(_window(71, 117, "4d 21h"), name="Fable")],
                },
                "usageAgeSeconds": 10,
            },
            {
                "number": 2,
                "email": "john.doe@gmail.com",
                "alias": "Personal",
                "active": True,
                "usageStatus": "ok",
                "usage": {
                    "fiveHour": _window(76, 2.8, "2h 47m"),
                    "scoped": [dict(_window(59, 123, "5d 3h"), name="Fable")],
                },
                "usageAgeSeconds": 10,
            },
        ],
    }


def _decode_png(data: bytes) -> tuple[int, int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ppm = 8, b"", 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif tag == b"pHYs":
            ppm = struct.unpack(">I", body[:4])[0]
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    assert len(zlib.decompress(idat)) == height * (width * 4 + 1)
    return width, height, ppm


def test_severity_matches_tui_bands(plugin):
    assert plugin.severity(69.9) == 0
    assert plugin.severity(70) == 1
    assert plugin.severity(90) == 2


def test_text_bar_is_fixed_width(plugin):
    for pct in (0, 2, 50, 76, 100, 140):
        bar = plugin.text_bar(pct, track=239)
        glyphs = [c for c in bar if c in "━╸─"]
        assert len(glyphs) == plugin.BAR_CELLS


def test_title_image_is_a_retina_png(plugin):
    width, height, ppm = _decode_png(plugin.title_image([76, 40, 71]))
    assert (width, height) == (48, 36)
    assert ppm == round(144 / 0.0254)


def test_render_lists_accounts_in_number_order_with_switch_actions(plugin):
    lines = plugin.render(_payload(), "/bin/cswap", now=NOW.timestamp())
    title, headers = lines[0], [l for l in lines[1:] if l[:1].isdigit()]

    assert title.startswith("76% | image=")
    assert headers[0].startswith("2  john.doe@gmail.com")
    assert headers[1].startswith("3  john.doe@company.com")
    # The active account has no action; others switch on click.
    assert "bash=" not in headers[0] and "● active" in headers[0]
    assert "bash=/bin/cswap param1=switch param2=3" in headers[1]
    assert sum("resets 2h 37m" in l for l in lines) == 1


def test_bar_rows_keep_their_indentation(plugin):
    lines = plugin.render(_payload(), "/bin/cswap", now=NOW.timestamp())
    rows = [l for l in lines if l.startswith("   ")]
    assert len(rows) == 5
    assert all("trim=false" in row for row in rows)


def test_passed_reset_rolls_the_window_to_zero(plugin):
    payload = _payload()
    payload["accounts"][1]["usage"]["fiveHour"] = _window(76, -1, "")
    lines = plugin.render(payload, "/bin/cswap", now=NOW.timestamp())
    assert lines[0].startswith("59% | image=")


def test_unavailable_account_falls_back_to_last_known_reading(plugin):
    payload = _payload()
    acc = payload["accounts"][0]
    acc.update(
        usageStatus="unavailable",
        usageError="http-429",
        lastGoodUsage=acc.pop("usage"),
        lastGoodAgeSeconds=600,
    )
    card = plugin.account_card(acc, "/bin/cswap", 239, NOW.timestamp())
    assert "10m ago" in card[0]
    assert "last known reading; usage unavailable (http-429)" in card[-1]
    assert len(card) == 5


def test_no_accounts_points_at_cswap_add(plugin):
    lines = plugin.render({"accounts": []}, "/bin/cswap", now=NOW.timestamp())
    assert lines == ["cswap", "---", "No managed accounts. Run: cswap add"]
