# SwiftBar plugin

Usage bars for every claude-swap account in the macOS menu bar, drawn with [SwiftBar](https://swiftbar.app/).

- **Menu bar:** the active account's windows (5h, 7d, and per-model weekly limits such as Fable) as stacked mini bars, next to its highest percentage. Bars turn amber at 70% and red at 90%, the same bands as `cswap watch`.
- **Dropdown:** every managed account with full-width bars and reset countdowns. Click another account to switch to it (`cswap switch <num>`). *Open live dashboard* runs `cswap watch` in your terminal.

The plugin only reads `cswap list --json`. It needs no packages beyond the Python standard library and runs on the macOS system `python3` (3.9 or later).

## Install

```bash
brew install --cask swiftbar
mkdir -p ~/Library/Application\ Support/SwiftBar/Plugins
ln -s "$PWD/contrib/swiftbar/cswap.1m.py" ~/Library/Application\ Support/SwiftBar/Plugins/
defaults write com.ameba.SwiftBar PluginDirectory -string ~/Library/Application\ Support/SwiftBar/Plugins
open -a SwiftBar
```

Run the commands from the repository root. The `1m` in the file name sets the refresh interval; rename the link (for example `cswap.30s.py`) to change it. cswap paces its own usage polling across every dashboard on the machine, so a shorter interval does not multiply API calls.

The plugin finds `cswap` on `PATH`, then in `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. Set `CSWAP_BIN` to point it elsewhere.

If you also run `cswap menubar`, you will see two status items. `cswap menubar --uninstall-service` stops the built-in one.
