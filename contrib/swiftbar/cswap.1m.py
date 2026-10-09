#!/usr/bin/env python3
# <xbar.title>claude-swap usage</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>dockylitmers</xbar.author>
# <xbar.desc>Usage bars for every claude-swap account. Click an account to switch to it.</xbar.desc>
# <xbar.dependencies>python3,claude-swap</xbar.dependencies>
# <xbar.abouturl>https://github.com/realiti4/claude-swap</xbar.abouturl>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
"""SwiftBar plugin: claude-swap usage bars in the macOS menu bar.

The title draws the active account's windows (5h, 7d and per-model weekly
limits such as Fable) as stacked mini bars next to its highest percentage.
The dropdown lists every managed account with full bars and reset
countdowns, like ``cswap watch``; clicking another account switches to it.

It only reads ``cswap list --json``, so it needs nothing beyond the Python
standard library and runs on the macOS system ``python3`` (3.9).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# Severity band edges and colors from claude_swap.tui.theme. The dropdown uses
# the nearest xterm-256 indexes because SwiftBar's ANSI parser has no 24-bit
# color.
WARN_PCT = 70.0
CRIT_PCT = 90.0
RGB_OK = (0x87, 0xAF, 0x87)
RGB_WARN = (0xD7, 0xAF, 0x5F)
RGB_CRIT = (0xD7, 0x5F, 0x5F)
RGBA_TITLE_TRACK = (128, 128, 128, 115)  # readable on light and dark menu bars
ANSI_OK = 108
ANSI_WARN = 179
ANSI_CRIT = 167
ANSI_ACCENT = 173
ANSI_MUTED = 245
ANSI_TRACK = {"Dark": 239, "Light": 250}

BAR_CELLS = 24
TITLE_MAX_BARS = 4
STALE_AFTER_S = 120
# SwiftBar trims leading whitespace and expands :emoji:/:sf-symbol: names by
# default; the bar rows need their indentation and literal text.
ROW = "font=Menlo size=12 ansi=true trim=false emojize=false symbolize=false"

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


# -- usage -----------------------------------------------------------------


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


def _window(raw: object, label: str, now: float) -> Window | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("pct"), (int, float)):
        return None
    if _reset_passed(raw.get("resetsAt"), now):
        # The reading predates its window's reset, so the window restarted.
        return Window(label, 0.0, None)
    countdown = raw.get("countdown")
    return Window(label, float(raw["pct"]), countdown if isinstance(countdown, str) else None)


def usage_windows(usage: object, now: float) -> list[Window]:
    """Every window of an account's usage, in ``cswap watch`` order."""
    if not isinstance(usage, dict):
        return []
    found = [
        _window(usage.get("fiveHour"), "5h", now),
        _window(usage.get("sevenDay"), "7d", now),
    ]
    for raw in usage.get("scoped") or []:
        if isinstance(raw, dict) and raw.get("name"):
            found.append(_window(raw, str(raw["name"]), now))
    found.append(_window(usage.get("spend"), "Spend", now))
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


# -- title image -------------------------------------------------------------


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
    out = fg((ANSI_OK, ANSI_WARN, ANSI_CRIT)[severity(pct)], fill) if fill else ""
    return out + (fg(track, rest) if rest else "")


def _param(value: str) -> str:
    return f'"{value}"' if " " in value else value


def account_card(acc: dict, cswap: str, track: int, now: float) -> list[str]:
    number = acc.get("number")
    header = f"{number}  {clean(acc.get('email', '?'))}"
    if acc.get("alias"):
        header += "  " + fg(ANSI_MUTED, f"[{clean(acc['alias'])}]")
    if acc.get("active"):
        header += "  " + fg(ANSI_ACCENT, "● active")
    if acc.get("disabled"):
        header += "  " + fg(ANSI_MUTED, "(disabled)")

    usage, age = _account_usage(acc)
    if isinstance(age, (int, float)) and age >= STALE_AFTER_S:
        header += "  " + fg(ANSI_MUTED, f"· {format_age(age)} ago")

    params = ROW
    if not acc.get("active") and number is not None:
        header += "  " + fg(ANSI_MUTED, "› switch")
        params += (
            f" bash={_param(cswap)} param1=switch param2={number}"
            f" terminal=false refresh=true tooltip=\"Switch to account {number}\""
        )
    lines = [f"{header} | {params}"]

    windows = usage_windows(usage, now)
    label_w = max((len(w.label) for w in windows), default=0)
    for w in windows:
        resets = f"resets {w.countdown}" if w.countdown else "reset"
        lines.append(
            f"   {fg(ANSI_MUTED, clean(w.label).ljust(label_w))}  {text_bar(w.pct, track)}"
            f"  {fg((ANSI_OK, ANSI_WARN, ANSI_CRIT)[severity(w.pct)], f'{w.pct:3.0f}%')}"
            f"  {fg(ANSI_MUTED, resets)} | {ROW}"
        )

    status = acc.get("usageStatus", "unavailable")
    if status != "ok":
        note = STATUS_NOTES.get(status, status)
        if status == "unavailable" and acc.get("usageError"):
            note += f" ({acc['usageError']})"
        if windows:
            note = "last known reading; " + note
        lines.append(f"   {fg(ANSI_MUTED, clean(note))} | {ROW}")
    return lines


def title_line(accounts: list[dict], now: float) -> str:
    active = next((a for a in accounts if a.get("active")), None)
    if active is None:
        return "cswap"
    usage, _age = _account_usage(active)
    windows = usage_windows(usage, now)
    if not windows:
        return "cswap !"
    pcts = [w.pct for w in windows[:TITLE_MAX_BARS]]
    image = base64.b64encode(title_image(pcts)).decode("ascii")
    return f"{max(pcts):.0f}% | image={image} emojize=false symbolize=false"


def render(payload: dict, cswap: str, appearance: str = "Dark", now: float | None = None) -> list[str]:
    if now is None:
        now = time.time()
    accounts = [a for a in payload.get("accounts") or [] if isinstance(a, dict)]
    lines = [title_line(accounts, now), "---"]
    if not accounts:
        lines.append("No managed accounts. Run: cswap add")
        return lines
    track = ANSI_TRACK.get(appearance, ANSI_TRACK["Dark"])
    lines.append("Claude accounts · click one to switch | size=11 color=#8a8a8a")
    for acc in sorted(accounts, key=lambda a: (a.get("number") is None, a.get("number") or 0)):
        lines.append("---")
        lines.extend(account_card(acc, cswap, track, now))
    lines.append("---")
    lines.append(f"Open live dashboard (cswap watch) | bash={_param(cswap)} param1=watch terminal=true")
    return lines


def main() -> int:
    cswap = find_cswap()
    if cswap is None:
        print("cswap ?\n---\nclaude-swap not found. Install: uv tool install claude-swap")
        return 0
    try:
        payload = load_accounts(cswap)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"cswap !\n---\n{clean(exc)} | {ROW}")
        return 0
    print("\n".join(render(payload, cswap, os.environ.get("OS_APPEARANCE", "Dark"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
