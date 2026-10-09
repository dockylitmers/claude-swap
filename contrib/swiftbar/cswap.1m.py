#!/usr/bin/env python3
# <xbar.title>claude-swap usage</xbar.title>
# <xbar.version>v1.1</xbar.version>
# <xbar.author>dockylitmers</xbar.author>
# <xbar.desc>Usage bars for every claude-swap account. Click an account to switch to it.</xbar.desc>
# <xbar.dependencies>python3,claude-swap</xbar.dependencies>
# <xbar.abouturl>https://github.com/realiti4/claude-swap</xbar.abouturl>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
"""SwiftBar plugin: claude-swap usage bars in the macOS menu bar.

The title draws the active account's windows as stacked mini bars with
their percentages. The dropdown lists every managed account with full bars
and reset countdowns, like ``cswap watch``; clicking another account
switches to it. A Settings submenu mirrors ``cswap menubar``'s: what the
title shows, the refresh interval, and the auto-switcher (``cswap auto``,
kept running by a launchd agent).

It only drives the ``cswap`` command line, so it needs nothing beyond the
Python standard library and runs on the macOS system ``python3`` (3.9).
Menu actions call this script again with arguments (see ``run_action``).
"""

from __future__ import annotations

import base64
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

ICON = "⇄"

# Severity band edges and colors from claude_swap.tui.theme. The dropdown uses
# the nearest xterm-256 indexes because SwiftBar's ANSI parser has no 24-bit
# color.
WARN_PCT = 70.0
CRIT_PCT = 90.0
RGB_OK = (0x87, 0xAF, 0x87)
RGB_WARN = (0xD7, 0xAF, 0x5F)
RGB_CRIT = (0xD7, 0x5F, 0x5F)
RGBA_TITLE_TRACK = (128, 128, 128, 115)  # readable on light and dark menu bars
ANSI_SEVERITY = (108, 179, 167)
ANSI_ACCENT = 173
ANSI_MUTED = 245
ANSI_TRACK = {"Dark": 239, "Light": 250}

BAR_CELLS = 24
TITLE_MAX_BARS = 4
STALE_AFTER_S = 120
# SwiftBar trims leading whitespace and expands :emoji:/:sf-symbol: names by
# default; the bar rows need their indentation and literal text.
ROW = "font=Menlo size=12 ansi=true trim=false emojize=false symbolize=false"
TITLE = "emojize=false symbolize=false"

# Settings, with cswap menubar's labels and choices.
TITLE_PCT_CHOICES = (("off", "None"), ("5h", "Session (5h)"), ("7d", "Weekly (7d)"), ("both", "Both (5h · 7d)"))
TITLE_STYLE_CHOICES = (("both", "Bars and numbers"), ("bars", "Bars only"), ("numbers", "Numbers only"))
REFRESH_CHOICES = (("30s", "30 seconds"), ("1m", "60 seconds"), ("5m", "5 minutes"))
AUTO_THRESHOLD_CHOICES = (80, 90, 95, 98)
DEFAULT_SETTINGS = {
    "show_account_name": True,
    "title_pct": "both",
    "title_scoped": False,
    "title_style": "both",
}
_CHOICES = {"title_pct": TITLE_PCT_CHOICES, "title_style": TITLE_STYLE_CHOICES}

# cswap's backup root on macOS. menubar_settings.json is cswap menubar's own
# display settings, read as the starting values.
BACKUP_DIR = Path.home() / ".claude-swap-backup"
SETTINGS_PATH = BACKUP_DIR / "swiftbar_settings.json"
LEGACY_SETTINGS_PATH = BACKUP_DIR / "menubar_settings.json"

AUTO_LABEL = "com.cswap.auto"
AUTO_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AUTO_LABEL}.plist"
AUTO_LOG_DIR = Path.home() / "Library" / "Logs"
LAUNCHCTL = "/bin/launchctl"

STATUS_NOTES = {
    "token_expired": "token expired; Claude Code renews it on your next message",
    "relogin_required": "login expired; log in again and re-add the account",
    "api_key": "API key account; no subscription quota",
    "keychain_unavailable": "Keychain unavailable",
    "foreign_credential": "live login belongs to another account; a switch repairs it",
    "no_credentials": "no stored credentials",
    "unavailable": "usage unavailable",
}


class Window(NamedTuple):
    label: str
    pct: float
    countdown: str | None
    kind: str  # "5h", "7d", "model" or "spend"


def find_cswap() -> str | None:
    override = os.environ.get("CSWAP_BIN")
    if override:
        return override
    found = shutil.which("cswap")
    if found:
        return found
    # SwiftBar's PATH may not include the uv / pipx bin directory.
    for candidate in (
        Path.home() / ".local/bin/cswap",
        Path("/opt/homebrew/bin/cswap"),
        Path("/usr/local/bin/cswap"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def load_accounts(cswap: str) -> dict:
    proc = subprocess.run(
        [cswap, "list", "--json"], capture_output=True, text=True, timeout=30
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"cswap exited with {proc.returncode}")
    if isinstance(payload.get("error"), dict):
        raise RuntimeError(payload["error"].get("message") or "cswap reported an error")
    return payload


# -- settings ----------------------------------------------------------------


def load_settings(path: Path | None = None, legacy: Path | None = None) -> dict:
    """Defaults, overlaid by cswap menubar's settings, then by our own."""
    settings = dict(DEFAULT_SETTINGS)
    for source in (legacy or LEGACY_SETTINGS_PATH, path or SETTINGS_PATH):
        try:
            data = json.loads(source.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, default in DEFAULT_SETTINGS.items():
            value = data.get(key)
            if type(value) is not type(default):
                continue
            if key in _CHOICES and value not in dict(_CHOICES[key]):
                continue
            settings[key] = value
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    path = path or SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)


def plugin_interval(plugin_path: str) -> str | None:
    """SwiftBar reads the refresh interval from ``name.<interval>.ext``."""
    parts = Path(plugin_path).name.split(".")
    return parts[-2] if len(parts) >= 3 else None


def set_plugin_interval(plugin_path: str, interval: str) -> None:
    path = Path(plugin_path)
    parts = path.name.split(".")
    if len(parts) < 3:
        raise ValueError(f"{path.name} has no interval to change")
    parts[-2] = interval
    target = path.with_name(".".join(parts))
    if target != path:
        os.rename(path, target)  # SwiftBar reloads the renamed plugin


def read_threshold(cswap: str) -> float | None:
    try:
        proc = subprocess.run(
            [cswap, "config", "get", "autoswitch.threshold"],
            capture_output=True, text=True, timeout=15,
        )
        return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def auto_enabled() -> bool:
    return AUTO_PLIST.exists()


def enable_auto(cswap: str) -> None:
    """Keep ``cswap auto`` running under launchd, like ``cswap menubar --install-service``."""
    plist = {
        "Label": AUTO_LABEL,
        "ProgramArguments": [cswap, "auto"],
        "EnvironmentVariables": {
            "PATH": f"{Path(cswap).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(AUTO_LOG_DIR / f"{AUTO_LABEL}.log"),
        "StandardErrorPath": str(AUTO_LOG_DIR / f"{AUTO_LABEL}.err"),
    }
    AUTO_PLIST.parent.mkdir(parents=True, exist_ok=True)
    with AUTO_PLIST.open("wb") as fh:
        plistlib.dump(plist, fh)
    _bootout()
    subprocess.run(
        [LAUNCHCTL, "bootstrap", f"gui/{os.getuid()}", str(AUTO_PLIST)], capture_output=True, check=True
    )


def _bootout(wait_s: float = 10.0) -> None:
    """Unload the agent and wait for it: bootout returns before the job is gone,
    and bootstrapping over a job still tearing down fails."""
    service = f"gui/{os.getuid()}/{AUTO_LABEL}"
    subprocess.run([LAUNCHCTL, "bootout", service], capture_output=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if subprocess.run([LAUNCHCTL, "print", service], capture_output=True).returncode != 0:
            return
        time.sleep(0.2)


def disable_auto() -> None:
    _bootout()
    if AUTO_PLIST.exists():
        AUTO_PLIST.unlink()


def set_threshold(cswap: str, pct: int) -> None:
    subprocess.run(
        [cswap, "config", "set", "autoswitch.threshold", str(pct)],
        capture_output=True, check=True, timeout=15,
    )
    if auto_enabled():
        # cswap auto reads its settings once at start.
        subprocess.run(
            [LAUNCHCTL, "kickstart", "-k", f"gui/{os.getuid()}/{AUTO_LABEL}"], capture_output=True
        )


def run_action(args: list[str], plugin_path: str, cswap: str | None) -> int:
    """Handle a menu click: ``set KEY VALUE``, ``interval``, ``auto``, ``threshold``."""
    action, rest = args[0], args[1:]
    if action == "set" and len(rest) == 2:
        key, value = rest
        settings = load_settings()
        if key in ("show_account_name", "title_scoped") and value == "toggle":
            settings[key] = not settings[key]
        elif key in _CHOICES and value in dict(_CHOICES[key]):
            settings[key] = value
        else:
            return 2
        save_settings(settings)
    elif action == "interval" and len(rest) == 1 and rest[0] in dict(REFRESH_CHOICES):
        set_plugin_interval(plugin_path, rest[0])
    elif action == "auto" and rest in (["on"], ["off"]) and cswap:
        enable_auto(cswap) if rest[0] == "on" else disable_auto()
    elif action == "threshold" and len(rest) == 1 and rest[0].isdigit() and cswap:
        set_threshold(cswap, int(rest[0]))
    else:
        return 2
    return 0


# -- usage -------------------------------------------------------------------


def severity(pct: float) -> int:
    if pct >= CRIT_PCT:
        return 2
    if pct >= WARN_PCT:
        return 1
    return 0


def _reset_passed(resets_at: object, now: float) -> bool:
    if not isinstance(resets_at, str):
        return False
    try:
        when = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when.timestamp() <= now


def _window(raw: object, label: str, kind: str, now: float) -> Window | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("pct"), (int, float)):
        return None
    if _reset_passed(raw.get("resetsAt"), now):
        # The reading predates its window's reset, so the window restarted.
        return Window(label, 0.0, None, kind)
    countdown = raw.get("countdown")
    return Window(label, float(raw["pct"]), countdown if isinstance(countdown, str) else None, kind)


def usage_windows(usage: object, now: float) -> list[Window]:
    """Every window of an account's usage, in ``cswap watch`` order."""
    if not isinstance(usage, dict):
        return []
    found = [
        _window(usage.get("fiveHour"), "5h", "5h", now),
        _window(usage.get("sevenDay"), "7d", "7d", now),
    ]
    for raw in usage.get("scoped") or []:
        if isinstance(raw, dict) and raw.get("name"):
            found.append(_window(raw, str(raw["name"]), "model", now))
    found.append(_window(usage.get("spend"), "Spend", "spend", now))
    return [w for w in found if w is not None]


def _account_usage(acc: dict) -> tuple[object, object]:
    """The usage to draw and its age; falls back to the last-known reading."""
    if isinstance(acc.get("usage"), dict):
        return acc["usage"], acc.get("usageAgeSeconds")
    return acc.get("lastGoodUsage"), acc.get("lastGoodAgeSeconds")


def format_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m"
    if minutes < 48 * 60:
        return f"{minutes // 60}h"
    return f"{minutes // 1440}d"


# -- title -------------------------------------------------------------------


def _png(width: int, height: int, rows: list[bytes], dpi: int = 144) -> bytes:
    """Encode RGBA rows; the pHYs DPI makes NSImage draw it at retina size."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ppm = round(dpi / 0.0254)
    raw = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def title_image(pcts: list[float], width_pt: int = 24, height_pt: int = 18) -> bytes:
    """Stacked rounded bars, one per window, colored by severity."""
    scale, samples = 2, 4
    width, height = width_pt * scale, height_pt * scale
    count = len(pcts)
    bar_h = 8 if count <= 2 else 6 if count == 3 else 5
    gap = 4 if count <= 2 else 3
    top = (height - (count * bar_h + (count - 1) * gap)) // 2
    colors = (RGB_OK, RGB_WARN, RGB_CRIT)
    bars = []
    for index, pct in enumerate(pcts):
        y0 = top + index * (bar_h + gap)
        fill_x = width * min(max(pct, 0.0), 100.0) / 100.0
        bars.append((y0, fill_x, colors[severity(pct)] + (255,)))

    radius = bar_h / 2.0
    rows = []
    for py in range(height):
        row = bytearray()
        for px in range(width):
            acc_rgb = [0.0, 0.0, 0.0]
            acc_a = 0.0
            for y0, fill_x, fill in bars:
                if not y0 <= py < y0 + bar_h:
                    continue
                for sy in range(samples):
                    y = py + (sy + 0.5) / samples - y0
                    for sx in range(samples):
                        x = px + (sx + 0.5) / samples
                        cx = min(max(x, radius), width - radius)
                        if (x - cx) ** 2 + (y - radius) ** 2 > radius**2:
                            continue
                        r, g, b, a = fill if x < fill_x else RGBA_TITLE_TRACK
                        weight = a / 255.0
                        acc_rgb[0] += r * weight
                        acc_rgb[1] += g * weight
                        acc_rgb[2] += b * weight
                        acc_a += weight
            n = samples * samples
            if acc_a:
                row += bytes(round(c / acc_a) for c in acc_rgb) + bytes([round(255 * acc_a / n)])
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return _png(width, height, rows)


def _local_part(email: str, limit: int = 12) -> str:
    """Email text before '@', truncated with a trailing '*' like cswap menubar."""
    local = email.split("@", 1)[0]
    return local[: limit - 1] + "*" if len(local) > limit else local


def title_line(accounts: list[dict], settings: dict, now: float) -> str:
    active = next((a for a in accounts if a.get("active")), None)
    if active is None:
        return f"{ICON} | {TITLE}"
    texts = []
    if settings["show_account_name"]:
        texts.append(clean(active.get("alias") or _local_part(str(active.get("email", "")))))

    usage, _age = _account_usage(active)
    windows = usage_windows(usage, now)
    if not windows:
        return f"{' · '.join(texts + ['!']) if texts else ICON + ' !'} | {TITLE}"

    kinds = {"off": (), "5h": ("5h",), "7d": ("7d",), "both": ("5h", "7d")}[settings["title_pct"]]
    if settings["title_scoped"]:
        kinds += ("model",)
    shown = [w for w in windows if w.kind in kinds]
    style = settings["title_style"]
    if style != "bars":
        texts += [f"{w.label} {w.pct:.0f}%" if w.kind == "model" else f"{w.pct:.0f}%" for w in shown]
    text = " · ".join(texts)
    if style != "numbers" and shown:
        image = base64.b64encode(title_image([w.pct for w in shown[:TITLE_MAX_BARS]])).decode("ascii")
        return f"{text} | image={image} {TITLE}"
    return f"{text or ICON} | {TITLE}"


# -- dropdown ----------------------------------------------------------------


def fg(code: int, text: str) -> str:
    return f"\x1b[38;5;{code}m{text}\x1b[39m"


def clean(text: object) -> str:
    """Keep SwiftBar from reading account text as its ``|`` separator."""
    return str(text).replace("|", "¦")


def text_bar(pct: float, track: int, cells: int = BAR_CELLS) -> str:
    """``━━━━╸────`` with the same half-cell rounding as the TUI."""
    filled = cells * min(max(pct, 0.0), 100.0) / 100.0
    full = int(filled)
    half = (filled - full) >= 0.5 and full < cells
    fill = "━" * full + ("╸" if half else "")
    rest = "─" * (cells - len(fill))
    out = fg(ANSI_SEVERITY[severity(pct)], fill) if fill else ""
    return out + (fg(track, rest) if rest else "")


def _param(value: object) -> str:
    value = str(value)
    return f'"{value}"' if " " in value else value


def command(program: str, *args: object, refresh: bool = True) -> str:
    """SwiftBar params that run ``program args…`` in the background on click."""
    out = f"bash={_param(program)}"
    for index, arg in enumerate(args, 1):
        out += f" param{index}={_param(arg)}"
    out += " terminal=false"
    return out + (" refresh=true" if refresh else "")


def account_card(acc: dict, cswap: str, track: int, now: float) -> list[str]:
    """The active account reads in the normal menu color; the others are muted.

    Every line carries an action: SwiftBar draws an actionless line disabled
    (grey), whatever color its text asks for. A line of another account
    switches to it; a line of the active account refreshes the plugin.
    """
    active = bool(acc.get("active"))
    number = acc.get("number")

    def text(s: str) -> str:
        return s if active else fg(ANSI_MUTED, s)

    header = text(f"{number}  {clean(acc.get('email', '?'))}")
    if acc.get("alias"):
        header += "  " + fg(ANSI_MUTED, f"[{clean(acc['alias'])}]")
    if active:
        header += "  " + fg(ANSI_ACCENT, "● active")
    if acc.get("disabled"):
        header += "  " + fg(ANSI_MUTED, "(disabled)")
    usage, age = _account_usage(acc)
    if isinstance(age, (int, float)) and age >= STALE_AFTER_S:
        header += "  " + fg(ANSI_MUTED, f"· {format_age(age)} ago")

    if active or number is None:
        action = "refresh=true"
    else:
        header += "  " + fg(ANSI_MUTED, "› switch")
        action = command(cswap, "switch", number) + f' tooltip="Switch to account {number}"'
    lines = [f"{header} | {ROW} {action}"]

    windows = usage_windows(usage, now)
    label_w = max((len(w.label) for w in windows), default=0)
    for w in windows:
        pct = f"{w.pct:3.0f}%"
        lines.append(
            f"   {text(clean(w.label).ljust(label_w))}  {text_bar(w.pct, track)}"
            f"  {fg(ANSI_SEVERITY[severity(w.pct)], pct) if active else text(pct)}"
            f"  {text(f'resets {w.countdown}' if w.countdown else 'reset')} | {ROW} {action}"
        )

    status = acc.get("usageStatus", "unavailable")
    if status != "ok":
        note = STATUS_NOTES.get(status, status)
        if status == "unavailable" and acc.get("usageError"):
            note += f" ({acc['usageError']})"
        if windows:
            note = "last known reading; " + note
        lines.append(f"   {fg(ANSI_MUTED, clean(note))} | {ROW} {action}")
    return lines


def settings_menu(
    settings: dict, plugin_path: str, threshold: float | None, auto_on: bool
) -> list[str]:
    def item(label: str, checked: bool, *args: object, depth: int = 1, refresh: bool = True) -> str:
        mark = " checked=true" if checked else ""
        return f"{'--' * depth}{label} | {command(plugin_path, *args, refresh=refresh)}{mark}"

    interval = plugin_interval(plugin_path)
    lines = ["Settings"]
    lines.append(item("Show account name in menu bar", settings["show_account_name"],
                      "set", "show_account_name", "toggle"))
    lines.append("--Title percentage")
    for value, label in TITLE_PCT_CHOICES:
        lines.append(item(label, settings["title_pct"] == value, "set", "title_pct", value, depth=2))
    lines.append(item("Show model limits in title", settings["title_scoped"], "set", "title_scoped", "toggle"))
    lines.append("--Title style")
    for value, label in TITLE_STYLE_CHOICES:
        lines.append(item(label, settings["title_style"] == value, "set", "title_style", value, depth=2))
    lines.append("--Refresh interval")
    for value, label in REFRESH_CHOICES:
        # No refresh: the rename makes SwiftBar reload the plugin anyway.
        lines.append(item(label, interval == value, "interval", value, depth=2, refresh=False))
    lines.append(item("Auto-switch accounts", auto_on, "auto", "off" if auto_on else "on"))
    lines.append("--Auto-switch threshold")
    for pct in AUTO_THRESHOLD_CHOICES:
        lines.append(item(f"{pct}%", threshold == pct, "threshold", pct, depth=2))
    return lines


def render(
    payload: dict,
    cswap: str,
    plugin_path: str,
    settings: dict | None = None,
    threshold: float | None = None,
    auto_on: bool = False,
    appearance: str = "Dark",
    now: float | None = None,
) -> list[str]:
    if now is None:
        now = time.time()
    settings = settings or dict(DEFAULT_SETTINGS)
    accounts = [a for a in payload.get("accounts") or [] if isinstance(a, dict)]
    lines = [title_line(accounts, settings, now), "---"]
    if not accounts:
        lines.append("No managed accounts. Run: cswap add")
    else:
        track = ANSI_TRACK.get(appearance, ANSI_TRACK["Dark"])
        lines.append("Claude accounts · click one to switch | size=11 color=#8a8a8a")
        for acc in sorted(accounts, key=lambda a: (a.get("number") is None, a.get("number") or 0)):
            lines.append("---")
            lines.extend(account_card(acc, cswap, track, now))
    lines.append("---")
    lines.extend(settings_menu(settings, plugin_path, threshold, auto_on))
    lines.append(f"Open live dashboard (cswap watch) | bash={_param(cswap)} param1=watch terminal=true")
    return lines


def main(argv: list[str]) -> int:
    # Menu actions call the script by the path SwiftBar ran it from (the
    # plugin-folder link), so a rename changes the link, not its target.
    plugin_path = os.environ.get("SWIFTBAR_PLUGIN_PATH") or os.path.abspath(argv[0])
    cswap = find_cswap()
    if len(argv) > 1:
        return run_action(argv[1:], plugin_path, cswap)
    if cswap is None:
        print(f"{ICON} ?\n---\nclaude-swap not found. Install: uv tool install claude-swap")
        return 0
    try:
        payload = load_accounts(cswap)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"{ICON} !\n---\n{clean(exc)} | {ROW} refresh=true")
        return 0
    lines = render(
        payload,
        cswap,
        plugin_path,
        settings=load_settings(),
        threshold=read_threshold(cswap),
        auto_on=auto_enabled(),
        appearance=os.environ.get("OS_APPEARANCE", "Dark"),
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
