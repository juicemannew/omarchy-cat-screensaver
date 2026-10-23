#!/bin/bash

# Undo install.sh: Omarchy's own screensaver comes back. Keeps
# ~/.config/omarchy/cat-screensaver.conf; pass --purge to also delete it,
# the Python venv and the frame cache.

ROOT=$(dirname "$(readlink -f "$0")")
DATA=~/.local/share/cat-screensaver
HYPR=~/.config/hypr/hyprland.lua
SERVICE=cat-screensaver-ac-watch.service

if [[ -f ~/.config/systemd/user/$SERVICE ]]; then
  systemctl --user disable --now $SERVICE >/dev/null 2>&1
  rm -f ~/.config/systemd/user/$SERVICE
  systemctl --user daemon-reload

  # The watch may have left idle.lock at 0 (charger connected): restore it.
  BATTERY_LOCK=300
  [[ -r ~/.config/omarchy/cat-screensaver.conf ]] && source ~/.config/omarchy/cat-screensaver.conf
  shell_json=~/.config/omarchy/shell.json
  if [[ $(jq -r '.idle.lock // empty' "$shell_json" 2>/dev/null) == 0 ]]; then
    tmp=$(mktemp) && jq --argjson v "$BATTERY_LOCK" '.idle.lock = $v' "$shell_json" >"$tmp" && cat "$tmp" >"$shell_json"
    rm -f "$tmp"
  fi
fi

if grep -qF -- "-- >>> cat-screensaver >>>" "$HYPR"; then
  cp "$HYPR" "$HYPR.bak.$(date +%s)"
  sed -i '/^-- >>> cat-screensaver >>>$/,/^-- <<< cat-screensaver <<<$/d' "$HYPR"
fi

rm -rf "$DATA/bin"
[[ $(readlink ~/.local/bin/cat-screensaver) == "$ROOT"/* ]] && rm -f ~/.local/bin/cat-screensaver

if [[ $1 == --purge ]]; then
  rm -rf "$DATA" ~/.cache/cat-screensaver ~/.config/omarchy/cat-screensaver.conf
fi

hyprctl reload >/dev/null
omarchy-restart-shell >/dev/null 2>&1 || echo "Run 'omarchy restart shell' to finish." >&2
echo "Cat screensaver removed. Remove the plugin itself with: omarchy plugin remove $(jq -r .id "$ROOT/manifest.json")"
