#!/usr/bin/env bash
# Install cswap from this checkout together with SwiftBar and the cswap plugin.
#
#   contrib/swiftbar/install.sh                 # everything, SwiftBar opens at login
#   contrib/swiftbar/install.sh --no-login-item # leave login items alone
#   contrib/swiftbar/install.sh --skip-cswap    # cswap is already installed
#
# Safe to re-run: every step checks what is already there first.
set -euo pipefail

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

login_item=1
install_cswap=1
for arg in "$@"; do
  case "$arg" in
    --no-login-item) login_item=0 ;;
    --skip-cswap) install_cswap=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "SwiftBar is macOS only." >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
plugin_src="$script_dir/cswap.1m.py"
app_path="/Applications/SwiftBar.app"

step() { printf '\n==> %s\n' "$*"; }

# 1. cswap, editable so a `git pull` here updates the installed command too.
if [[ $install_cswap -eq 1 ]]; then
  step "Installing cswap from $repo_root"
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install it first: https://docs.astral.sh/uv/" >&2
    exit 1
  fi
  uv tool install --force --editable "$repo_root"
fi

# 2. SwiftBar.
step "Installing SwiftBar"
if [[ -d "$app_path" ]]; then
  echo "Already installed: $app_path"
else
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Install SwiftBar from https://swiftbar.app/ and re-run." >&2
    exit 1
  fi
  brew install --cask swiftbar
fi

# 3. Plugin folder. Keep one the user already chose so their other plugins stay.
step "Linking the cswap plugin"
plugin_dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [[ -z "$plugin_dir" ]]; then
  plugin_dir="$HOME/Library/Application Support/SwiftBar/Plugins"
  defaults write com.ameba.SwiftBar PluginDirectory -string "$plugin_dir"
fi
mkdir -p "$plugin_dir"
# The plugin renames its own link to change the refresh interval
# (cswap.1m.py -> cswap.30s.py), so reuse whichever name is there.
existing=""
for f in "$plugin_dir"/cswap.*.py; do
  [[ -e "$f" || -L "$f" ]] && existing="$f" && break
done
link="${existing:-$plugin_dir/cswap.1m.py}"
ln -sfn "$plugin_src" "$link"
chmod +x "$plugin_src"
echo "$link -> $plugin_src"

# 4. Two status items would show the same thing.
if [[ -f "$HOME/Library/LaunchAgents/com.cswap.menubar.plist" ]] && command -v cswap >/dev/null 2>&1; then
  step "Removing the cswap menubar service (the plugin replaces it)"
  cswap menubar --uninstall-service
fi

# 5. Open SwiftBar at login. SwiftBar's own "Launch at login" switch can only be
# flipped from inside the app, so register it as a login item instead; it
# shows under System Settings > General > Login Items.
if [[ $login_item -eq 1 ]]; then
  step "Opening SwiftBar at login"
  if osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null \
      | tr ',' '\n' | sed 's/^ *//' | grep -qx "SwiftBar"; then
    echo "Already a login item."
  elif osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$app_path\", hidden:false}" >/dev/null; then
    echo "Added SwiftBar to login items."
  else
    echo "Could not add the login item (Automation permission for System Events?)." >&2
    echo "Turn on SwiftBar > Preferences > Launch at login instead." >&2
  fi
fi

# 6. Start it, or reload plugins if it is already running.
step "Starting SwiftBar"
if pgrep -x SwiftBar >/dev/null; then
  open -g "swiftbar://refreshallplugins"
  echo "SwiftBar was running; plugins refreshed."
else
  open -a SwiftBar
  echo "SwiftBar started."
fi

echo
echo "Done. Look for the cswap usage bars in the menu bar."
