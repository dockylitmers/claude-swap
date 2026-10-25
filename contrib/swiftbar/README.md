# SwiftBar plugin

Usage bars for every claude-swap account in the macOS menu bar, drawn with [SwiftBar](https://swiftbar.app/). It can stand in for `cswap menubar`.

- **Menu bar:** the active account's 5h and 7d windows as stacked mini bars with their percentages and reset times (`25% 59m · 58% 4d12h`). Bars turn amber at 70% and red at 90%, the same bands as `cswap watch`.
- **Dropdown:** every managed account with full-width bars and reset countdowns. The active account reads in the normal menu color and the others are muted. Click another account to switch to it (`cswap switch <num>`). *Open live dashboard* runs `cswap watch` in your terminal.
- **Auto-switch accounts:** a check mark under the accounts shows whether `cswap auto` runs in the background under launchd; click it to turn it on or off. It is on by default.
- **Add account…** saves the current login (`cswap add`), opens Claude's sign-in page in your browser (`claude auth login`), and adds the new account once you sign in; a dialog reports the result.
- **Accounts:** the rest of `cswap menubar`'s account menu, in one submenu. *Rotate to next*, *Switch to best* and *Next available* run `cswap switch` with each strategy. *Add account with setup-token…* takes a token from `claude setup-token` in a dialog (`cswap add-token`). There are also *Disable / enable account*, *Remove account* (asks first), *Refresh current credentials* (saves Claude Code's current login, adding it if it was made outside this menu) and *Switch history*. A switch posts a notification, and a failed command shows its error in a dialog.
- **Settings:** the same choices as `cswap menubar`, plus a title style and reset times.

| Setting | What it does | Default |
|---|---|---|
| Show account name in menu bar | Adds the active account's alias, or the start of its email | Off |
| Limits in menu bar | Which limits the title shows, each on or off: Session (5h), Weekly (7d), and every per-model weekly limit an account reports, such as Fable | 5h and 7d |
| Title style | Bars and numbers, bars only, or numbers only; *Show reset time* adds each limit's countdown (`45% 3h52m`) | Bars and numbers, with reset time |
| Refresh interval | 30 seconds, 60 seconds or 5 minutes | 60 seconds |
| Auto-switch threshold | Sets `autoswitch.threshold` (80, 90, 95 or 98%): the switch line for the 5h window, and for every window while no weekly line is set | cswap's own (90%) |
| Weekly switch line | Sets `autoswitch.weeklyThreshold` (95, 97, 99 or 99.9%): its own switch line for the 7d and per-model windows, so the weekly budget can run closer to its limit. *Same as threshold* unsets it. Shown with cswap 0.29 or later | Same as threshold |

The plugin only drives the `cswap` command line. It needs no packages beyond the Python standard library and runs on the macOS system `python3` (3.9 or later).

## Install

From the repository root:

```bash
contrib/swiftbar/install.sh
```

It installs `cswap` from this checkout (`uv tool install --editable`, so a `git pull` updates it), installs SwiftBar with Homebrew if it is missing, links the plugin into SwiftBar's plugin folder (keeping one you already chose), removes the `cswap menubar` launchd service if you had it, adds SwiftBar to your login items so it opens at login, and starts SwiftBar. It is safe to re-run.

| Option | What it does |
|---|---|
| `--no-login-item` | Leave login items alone |
| `--skip-cswap` | Don't (re)install `cswap` |

SwiftBar's own *Launch at login* switch can only be flipped from inside the app, so the script registers SwiftBar under System Settings → General → Login Items instead. The first run may ask to let your terminal control System Events.

<details>
<summary>Manual install</summary>

```bash
brew install --cask swiftbar
mkdir -p ~/Library/Application\ Support/SwiftBar/Plugins
ln -s "$PWD/contrib/swiftbar/cswap.1m.py" ~/Library/Application\ Support/SwiftBar/Plugins/
defaults write com.ameba.SwiftBar PluginDirectory -string ~/Library/Application\ Support/SwiftBar/Plugins
open -a SwiftBar
```

Run the commands from the repository root. If you used `cswap menubar --install-service`, remove it with `cswap menubar --uninstall-service` so you don't get two status items.

</details>

The plugin finds `cswap` on `PATH`, then in `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. Set `CSWAP_BIN` to point it elsewhere.

## How the settings are stored

- Title settings live in `~/.claude-swap-backup/swiftbar_settings.json`. The first run starts from `cswap menubar`'s `menubar_settings.json`, if there is one.
- The refresh interval is the plugin file name, which SwiftBar reads: choosing one renames the link (`cswap.1m.py` → `cswap.30s.py`). cswap paces its own usage polling across every dashboard on the machine, so a shorter interval does not multiply API calls.
- *Auto-switch accounts* installs `~/Library/LaunchAgents/com.cswap.auto.plist`, which keeps `cswap auto` running and starts it at login. It logs to `~/Library/Logs/com.cswap.auto.{log,err}`. The plugin installs it on its first run; turning it off unloads the agent, removes the plist and records `"auto_switch": false` in `swiftbar_settings.json`, so it stays off.
- The two lines are cswap's own `autoswitch.threshold` and `autoswitch.weeklyThreshold`, shared with `cswap auto` and the TUI. Changing one restarts the agent so the new value applies at once. A line is where auto-switch starts looking for a better account, not a cap: once every account is past its line, it stays on the active one until that hits its limit, then moves to one with quota left.
