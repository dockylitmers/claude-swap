# SwiftBar plugin

Usage bars for every claude-swap account in the macOS menu bar, drawn with [SwiftBar](https://swiftbar.app/). It can stand in for `cswap menubar`.

- **Menu bar:** the active account's windows as stacked mini bars with their percentages. Bars turn amber at 70% and red at 90%, the same bands as `cswap watch`.
- **Dropdown:** every managed account with full-width bars and reset countdowns. The active account reads in the normal menu color and the others are muted. Click another account to switch to it (`cswap switch <num>`). *Open live dashboard* runs `cswap watch` in your terminal.
- **Settings:** the same choices as `cswap menubar`, plus a title style.

| Setting | What it does |
|---|---|
| Show account name in menu bar | Adds the active account's alias, or the start of its email |
| Title percentage | Which account-wide windows the title shows: none, 5h, 7d or both |
| Show model limits in title | Adds per-model weekly limits such as Fable |
| Title style | Bars and numbers, bars only, or numbers only |
| Refresh interval | 30 seconds, 60 seconds or 5 minutes |
| Auto-switch accounts | Runs `cswap auto` in the background under launchd |
| Auto-switch threshold | Sets `autoswitch.threshold` (80, 90, 95 or 98%) |

The plugin only drives the `cswap` command line. It needs no packages beyond the Python standard library and runs on the macOS system `python3` (3.9 or later).

## Install

```bash
brew install --cask swiftbar
mkdir -p ~/Library/Application\ Support/SwiftBar/Plugins
ln -s "$PWD/contrib/swiftbar/cswap.1m.py" ~/Library/Application\ Support/SwiftBar/Plugins/
defaults write com.ameba.SwiftBar PluginDirectory -string ~/Library/Application\ Support/SwiftBar/Plugins
open -a SwiftBar
```

Run the commands from the repository root. If you used `cswap menubar --install-service`, remove it with `cswap menubar --uninstall-service` so you don't get two status items.

The plugin finds `cswap` on `PATH`, then in `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. Set `CSWAP_BIN` to point it elsewhere.

## How the settings are stored

- Title settings live in `~/.claude-swap-backup/swiftbar_settings.json`. The first run starts from `cswap menubar`'s `menubar_settings.json`, if there is one.
- The refresh interval is the plugin file name, which SwiftBar reads: choosing one renames the link (`cswap.1m.py` → `cswap.30s.py`). cswap paces its own usage polling across every dashboard on the machine, so a shorter interval does not multiply API calls.
- *Auto-switch accounts* installs `~/Library/LaunchAgents/com.cswap.auto.plist`, which keeps `cswap auto` running and starts it at login. It logs to `~/Library/Logs/com.cswap.auto.{log,err}`. Turning the setting off unloads the agent and removes the plist.
- The threshold is cswap's own `autoswitch.threshold`, shared with `cswap auto` and the TUI. Changing it restarts the agent so the new value applies at once.
