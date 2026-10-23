#!/bin/bash

# Cat screensaver installer. Run it from the plugin checkout, usually
# ~/.config/omarchy/plugins/<id>/install.sh after `omarchy plugin add`.
#
#   ./install.sh                         pick components with checkboxes
#   ./install.sh --all                   everything
#   ./install.sh --components core,ac    only these (core fallback ac noblank cursor)

set -e

ROOT=$(dirname "$(readlink -f "$0")")
ID=$(jq -r .id "$ROOT/manifest.json")
DATA=~/.local/share/cat-screensaver
LINKS=$DATA/bin
VENV=$DATA/venv
CONF=~/.config/omarchy/cat-screensaver.conf
HYPR=~/.config/hypr/hyprland.lua
SERVICE=cat-screensaver-ac-watch.service
MARK_BEGIN="-- >>> cat-screensaver >>>"
MARK_END="-- <<< cat-screensaver <<<"

ALL="ALL for catbookmeow PRO 2011 🐱💻"
declare -A LABELS=(
  [core]="3D cat screensaver (cookie / ramen / milk / sleep)"
  [fallback]="Flat bash cat when Python/numpy is missing"
  [ac]="On AC: no idle lock (charge bar under the cat)"
  [noblank]="On AC: never blank the display"
  [cursor]="Watchdog that restores a hidden mouse cursor"
)
ORDER=(core fallback ac noblank cursor)

step() { printf '\033[1;31m::\033[0m %s\n' "$*"; }

declare -A want
case $1 in
--all) for c in "${ORDER[@]}"; do want[$c]=1; done ;;
--components)
  IFS=, read -ra picked <<<"$2"
  for c in "${picked[@]}"; do
    [[ -n ${LABELS[$c]} ]] || { echo "Unknown component: $c" >&2; exit 1; }
    want[$c]=1
  done
  ;;
"")
  options=("$ALL")
  for c in "${ORDER[@]}"; do options+=("${LABELS[$c]}"); done
  chosen=$(gum choose --no-limit --header "Cat screensaver: what to install? (space toggles, enter confirms)" \
    --selected "${LABELS[core]}" --selected "${LABELS[fallback]}" "${options[@]}") || exit 1
  while IFS= read -r line; do
    if [[ $line == "$ALL" ]]; then
      for c in "${ORDER[@]}"; do want[$c]=1; done
    fi
    for c in "${ORDER[@]}"; do [[ $line == "${LABELS[$c]}" ]] && want[$c]=1; done
  done <<<"$chosen"
  ;;
*)
  sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
  ;;
esac

if [[ ! -f $HYPR ]]; then
  echo "$HYPR not found: this plugin needs Omarchy 4 (Lua Hyprland config)." >&2
  exit 1
fi

if [[ -z ${want[core]} ]]; then
  echo "The 3D cat (core) is required; nothing installed." >&2
  exit 1
fi

set_conf() {
  local key=$1 value=$2
  if grep -q "^$key=" "$CONF"; then
    sed -i "s|^$key=.*|$key=$value|" "$CONF"
  else
    echo "$key=$value" >>"$CONF"
  fi
}

# --- Python venv with numpy for the ray-marched cat
if "$VENV/bin/python" -c 'import numpy' 2>/dev/null; then
  step "Python venv with numpy already in $VENV"
else
  step "Creating Python venv with numpy in $VENV"
  mkdir -p "$DATA"
  if command -v uv >/dev/null; then
    uv venv -q "$VENV"
    uv pip install -q --python "$VENV/bin/python" numpy
  else
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q numpy
  fi
fi

# --- Settings
mkdir -p "$(dirname "$CONF")"
if [[ ! -f $CONF ]]; then
  step "Writing $CONF"
  cat >"$CONF" <<'EOF'
# Cat screensaver settings (sourced by bash; restart not needed).
# Scene: auto (by time of day), cookie, ramen, milk or sleep.
SCENE=auto
# Terminal font size for the screensaver window: smaller = more detailed cat.
FONT_SIZE=10
# Seconds of idle before locking while on battery (used by the AC watch).
BATTERY_LOCK=300
EOF
fi
grep -q "^SCENE=" "$CONF" || echo "SCENE=auto" >>"$CONF"
grep -q "^FONT_SIZE=" "$CONF" || echo "FONT_SIZE=10" >>"$CONF"
set_conf FALLBACK "$([[ -n ${want[fallback]} ]] && echo simple || echo omarchy)"
set_conf CURSOR_WATCHDOG "$([[ -n ${want[cursor]} ]] && echo 1 || echo 0)"

# --- Commands that shadow Omarchy's, in a directory put first in PATH
step "Linking commands into $LINKS"
mkdir -p "$LINKS" ~/.local/bin
rm -f "$LINKS"/*
ln -s "$ROOT/bin/omarchy-screensaver" "$ROOT/bin/omarchy-launch-screensaver" "$ROOT/bin/cat-screensaver" "$LINKS/"
[[ -n ${want[noblank]} ]] && ln -s "$ROOT/bin/omarchy-brightness-display" "$LINKS/"
ln -sf "$ROOT/bin/cat-screensaver" ~/.local/bin/cat-screensaver

if ! grep -qF -- "$MARK_BEGIN" "$HYPR"; then
  if [[ -z $1 ]] && ! gum confirm "Append a marked PATH block to $HYPR (a backup is kept)?"; then
    echo "Without it Omarchy keeps starting its own screensaver; nothing else to undo: run uninstall.sh." >&2
    exit 1
  fi
  step "Adding $LINKS to Hyprland's PATH in $HYPR"
  cp "$HYPR" "$HYPR.bak.$(date +%s)"
  cat >>"$HYPR" <<EOF

$MARK_BEGIN
-- Cat screensaver (~/.config/omarchy/plugins/$ID): its commands shadow
-- Omarchy's, so they must come before Omarchy's bin, which envs.lua puts first.
local cat_bin = os.getenv("HOME") .. "/.local/share/cat-screensaver/bin"
local cat_path = { cat_bin }
for entry in (os.getenv("PATH") or "/usr/local/bin:/usr/bin"):gmatch("[^:]+") do
  if entry ~= cat_bin then table.insert(cat_path, entry) end
end
hl.env("PATH", table.concat(cat_path, ":"))
$MARK_END
EOF
fi

# --- AC watch
mkdir -p ~/.config/systemd/user
if [[ -n ${want[ac]} ]]; then
  step "Enabling $SERVICE"
  sed "s|@ROOT@|$ROOT|" "$ROOT/systemd/$SERVICE" >~/.config/systemd/user/$SERVICE
  systemctl --user daemon-reload
  systemctl --user enable --now $SERVICE >/dev/null 2>&1
  systemctl --user restart $SERVICE
elif [[ -f ~/.config/systemd/user/$SERVICE ]]; then
  step "Disabling $SERVICE"
  systemctl --user disable --now $SERVICE >/dev/null 2>&1 || true
  rm -f ~/.config/systemd/user/$SERVICE
  systemctl --user daemon-reload
fi

# --- Apply: new PATH for Hyprland and the shell that starts the screensaver
step "Reloading Hyprland and the Omarchy shell"
hyprctl reload >/dev/null || true
omarchy-restart-shell >/dev/null 2>&1 || echo "Run 'omarchy restart shell' to finish." >&2
for _ in {1..20}; do omarchy-shell shell ping >/dev/null 2>&1 && break; sleep 0.5; done
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
omarchy plugin enable "$ID" >/dev/null 2>&1 || true

echo
step "Done. Try: cat-screensaver demo   (or cat-screensaver menu)"
