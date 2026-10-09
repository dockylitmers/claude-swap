"""Tests for the SwiftBar plugin in contrib/swiftbar."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "contrib" / "swiftbar" / "cswap.1m.py"
NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
LINK = "/Users/me/Library/Application Support/SwiftBar/Plugins/cswap.1m.py"
MUTED = "\x1b[38;5;245m"


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("cswap_swiftbar_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SETTINGS_PATH", tmp_path / "swiftbar_settings.json")
    monkeypatch.setattr(module, "LEGACY_SETTINGS_PATH", tmp_path / "menubar_settings.json")
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
                "active": True,
                "usageStatus": "ok",
                "usage": {
                    "fiveHour": _window(76, 2.8, "2h 47m"),
                    "sevenDay": _window(13, 120, "5d 0h"),
                    "scoped": [dict(_window(59, 123, "5d 3h"), name="Fable")],
                },
                "usageAgeSeconds": 10,
            },
        ],
    }


def _render(plugin, payload=None, **kwargs) -> list[str]:
    return plugin.render(payload or _payload(), "/bin/cswap", LINK, now=NOW.timestamp(), **kwargs)


def _cards(lines: list[str]) -> list[list[str]]:
    """Account cards: the blocks between separators that start with a number."""
    blocks, block = [], []
    for line in lines[1:]:
        if line == "---":
            blocks.append(block)
            block = []
        else:
            block.append(line)
    blocks.append(block)
    return [b for b in blocks if b and re.sub(r"\x1b\[[0-9;]*m", "", b[0])[:1].isdigit()]


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


def _title_bars(title: str) -> int:
    """How many bars the title image draws (its rows with any ink)."""
    if "image=" not in title:
        return 0
    data = base64.b64decode(title.split("image=")[1].split()[0])
    width, height, _ = _decode_png(data)
    pos, idat = 8, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        if data[pos + 4 : pos + 8] == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    raw, stride = zlib.decompress(idat), width * 4 + 1
    inked = [any(raw[y * stride + 1 + x * 4 + 3] for x in range(width)) for y in range(height)]
    return sum(1 for y in range(height) if inked[y] and (y == 0 or not inked[y - 1]))


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


def test_cards_are_in_number_order_and_others_switch_on_click(plugin):
    active, other = _cards(_render(plugin))
    assert active[0].startswith("2  john.doe@gmail.com")
    assert other[0].startswith(f"{MUTED}3  john.doe@company.com")
    assert all("refresh=true" in l and "bash=" not in l for l in active)
    assert all(f'bash="{LINK}" param1=switch param2=3' in l for l in other)


def test_active_account_reads_normal_and_others_muted(plugin):
    active, other = _cards(_render(plugin))
    text_of = lambda line: line.split(" | ")[0]
    # Active: email and row labels carry no color code; others are muted.
    assert "\x1b" not in text_of(active[0]).split("  ")[1]
    assert text_of(active[1]).startswith("   5h ")
    assert text_of(other[1]).startswith(f"   {MUTED}5h ")
    assert f"{MUTED}resets 2h 37m" in text_of(other[1])


def test_every_card_line_has_an_action_so_swiftbar_never_greys_it(plugin):
    for card in _cards(_render(plugin)):
        for line in card:
            assert "bash=" in line or "refresh=true" in line
            assert "trim=false" in line


def test_title_follows_settings(plugin):
    settings = dict(plugin.DEFAULT_SETTINGS, show_account_name=False)
    title = _render(plugin, settings=settings)[0]
    assert title.startswith("76% · 13% | image=") and _title_bars(title) == 2

    settings.update(title_limits=["5h", "7d", "Fable"], title_style="numbers")
    assert _render(plugin, settings=settings)[0].startswith("76% · 13% · Fable 59% | emojize")

    settings.update(title_limits=["5h"], title_style="bars", show_account_name=True)
    title = _render(plugin, settings=settings)[0]
    assert title.startswith("john.doe | image=") and _title_bars(title) == 1

    settings.update(title_limits=[])
    assert _render(plugin, settings=settings)[0].startswith("john.doe | emojize")


def test_title_can_show_reset_times(plugin):
    settings = dict(plugin.DEFAULT_SETTINGS, show_account_name=False, title_limits=["5h"], title_reset=True)
    title = _render(plugin, settings=settings)[0]
    assert title.startswith("76% 2h47m | image=") and _title_bars(title) == 1

    settings.update(title_limits=["5h", "Fable"], title_style="bars")
    assert _render(plugin, settings=settings)[0].startswith("2h47m · Fable 5d3h | image=")


def test_settings_menu_marks_current_choices(plugin):
    lines = _render(plugin, threshold=90.0, auto_on=True)
    checked = [l.split(" | ")[0].lstrip("-") for l in lines if "checked=true" in l]
    assert checked == ["Show account name in menu bar", "Session (5h)", "Weekly (7d)",
                       "Bars and numbers", "60 seconds", "Auto-switch accounts", "90%"]
    auto = next(l for l in lines if l.startswith("--Auto-switch accounts"))
    assert 'bash="' + LINK + '" param1=auto param2=off' in auto
    # Inside a level-2 submenu, a separator needs two "--" prefixes.
    assert "-------" in lines


def test_limit_items_toggle_one_limit_each(plugin):
    settings = dict(plugin.DEFAULT_SETTINGS, title_limits=["5h", "models"])
    lines = _render(plugin, settings=settings)
    items = {l.split(" | ")[0]: l for l in lines if "param2=title_limits" in l}
    assert list(items) == ["----Session (5h)", "----Weekly (7d)", "----Fable (weekly)"]
    assert "checked=true" in items["----Fable (weekly)"]
    assert "param3=Fable" in items["----Session (5h)"]
    assert "param3=5h,7d,Fable" in items["----Weekly (7d)"]
    assert "param3=5h " in items["----Fable (weekly)"]


def test_settings_start_from_cswap_menubar_and_round_trip(plugin):
    plugin.LEGACY_SETTINGS_PATH.write_text(json.dumps(
        {"show_account_name": False, "title_pct": "7d", "title_scoped": True, "refresh_interval": 300}
    ))
    assert plugin.load_settings() == {
        "show_account_name": False, "title_limits": ["7d", "models"],
        "title_style": "both", "title_reset": False,
    }
    assert plugin.run_action(["set", "title_limits", "5h"], LINK, None) == 0
    assert plugin.run_action(["set", "title_reset", "toggle"], LINK, None) == 0
    assert plugin.run_action(["set", "title_style", "bogus"], LINK, None) == 2
    settings = plugin.load_settings()
    assert settings["title_limits"] == ["5h"] and settings["title_reset"] is True
    assert plugin.run_action(["set", "title_limits", "none"], LINK, None) == 0
    assert plugin.load_settings()["title_limits"] == []


def test_interval_renames_the_plugin_link(plugin, tmp_path):
    link = tmp_path / "cswap.1m.py"
    link.write_text("")
    assert plugin.plugin_interval(str(link)) == "1m"
    assert plugin.run_action(["interval", "30s"], str(link), None) == 0
    assert not link.exists() and (tmp_path / "cswap.30s.py").exists()


def test_passed_reset_rolls_the_window_to_zero(plugin):
    payload = _payload()
    payload["accounts"][1]["usage"]["fiveHour"] = _window(76, -1, "")
    settings = dict(plugin.DEFAULT_SETTINGS, show_account_name=False, title_limits=["5h"])
    assert _render(plugin, payload, settings=settings)[0].startswith("0% | image=")


def test_unavailable_account_falls_back_to_last_known_reading(plugin):
    acc = _payload()["accounts"][0]
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
    lines = _render(plugin, {"accounts": []})
    assert lines[:3] == ["⇄ | emojize=false symbolize=false", "---", "No managed accounts. Run: cswap add"]


FAKE_CSWAP = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path(__file__).with_name("calls.jsonl")
with log.open("a") as fh:
    fh.write(json.dumps({"args": sys.argv[1:], "stdin": "" if sys.stdin.isatty() else sys.stdin.read()}) + "\\n")
if sys.argv[1:] == ["list", "--json"]:
    print(json.dumps({"accounts": [{"number": 1, "email": "a@example.com", "active": True}]}))
sys.exit(3 if "fail" in sys.argv else 0)
"""


@pytest.fixture()
def fake_cswap(plugin, tmp_path, monkeypatch):
    exe = tmp_path / "cswap"
    exe.write_text(FAKE_CSWAP)
    exe.chmod(0o755)
    ui = {"notify": [], "alert": [], "confirm": True, "ask": []}
    monkeypatch.setattr(plugin, "notify", lambda title, msg: ui["notify"].append(title))
    monkeypatch.setattr(plugin, "alert", lambda msg: ui["alert"].append(msg))
    monkeypatch.setattr(plugin, "confirm", lambda msg, button: ui["confirm"])
    monkeypatch.setattr(plugin, "ask", lambda prompt, hidden=False: ui["ask"].pop(0) if ui["ask"] else None)

    def calls():
        log = tmp_path / "calls.jsonl"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return str(exe), ui, calls


def test_accounts_menu_mirrors_cswap_menubar(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "LOG_PATH", tmp_path / "missing.log")
    payload = _payload()
    payload["accounts"][0]["disabled"] = True
    lines = plugin.accounts_menu(payload["accounts"], LINK)
    labels = [l.split(" | ")[0] for l in lines]
    assert labels[:3] == ["Rotate to next", "Switch to best", "Next available"]
    assert "--From current login" in labels and "--From setup-token…" in labels
    disable = next(l for l in lines if l.startswith("--3  Work  (john.doe@company.com)") and "enable" in l)
    assert "param1=enable param2=3" in disable and "checked=true" in disable
    remove = [l for l in lines if "param1=remove" in l]
    assert "param2=3 param3=john.doe@company.com" in remove[0]
    assert "--No switches logged yet" in labels


def test_switch_history_reads_newest_first(plugin, tmp_path):
    log = tmp_path / "claude-swap.log"
    log.write_text(
        "2026-09-28 09:46:15,862 - INFO - Switched from account 2 to 1\n"
        "2026-10-09 11:56:17,063 - INFO - Backed up account 2\n"
        "2026-10-09 11:56:17,157 - INFO - Switched from account 1 to 2\n"
    )
    assert plugin.switch_history(log) == ["1 → 2   2026-10-09 11:56", "2 → 1   2026-09-28 09:46"]


def test_switch_runs_cswap_and_notifies(plugin, fake_cswap):
    cswap, ui, calls = fake_cswap
    assert plugin.run_action(["switch", "2"], LINK, cswap) == 0
    assert plugin.run_action(["switch-strategy", "best"], LINK, cswap) == 0
    args = [c["args"] for c in calls()]
    assert ["switch", "2"] in args and ["switch", "--strategy", "best"] in args
    assert ui["notify"] == ["Now on account 1 (a@example.com)"] * 2


def test_failed_command_shows_an_alert_and_no_notification(plugin, fake_cswap):
    cswap, ui, _calls = fake_cswap
    assert plugin.run_action(["switch", "fail"], LINK, cswap) == 0
    assert ui["alert"] == ["cswap exited with 3"] and ui["notify"] == []


def test_add_token_passes_the_token_on_stdin(plugin, fake_cswap):
    cswap, ui, calls = fake_cswap
    ui["ask"] = ["me@example.com", "sk-ant-oat01-secret"]
    plugin.run_action(["add-token"], LINK, cswap)
    call = calls()[-1]
    assert call["args"] == ["add-token", "-", "--email", "me@example.com"]
    assert call["stdin"] == "sk-ant-oat01-secret\n"


def test_remove_needs_the_dialog_confirmation(plugin, fake_cswap):
    cswap, ui, calls = fake_cswap
    ui["confirm"] = False
    plugin.run_action(["remove", "2", "b@example.com"], LINK, cswap)
    assert calls() == []
    ui["confirm"] = True
    plugin.run_action(["remove", "2", "b@example.com"], LINK, cswap)
    assert calls()[-1] == {"args": ["remove", "2"], "stdin": "y\n"}
