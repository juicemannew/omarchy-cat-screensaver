#!/bin/bash

# Fallback cat screensaver: a flat ASCII cat eating a cookie, pure bash.
# Used when the 3D cat can't start (no Python/numpy venv).
# While the charger is connected it also shows the charging progress.

# Lock timeout used on battery (cat-screensaver-ac-watch sets idle.lock to 0 on AC)
BATTERY_LOCK=300
[[ -r ~/.config/omarchy/cat-screensaver.conf ]] && source ~/.config/omarchy/cat-screensaver.conf
LOCK_AFTER=$(( BATTERY_LOCK - $(jq -r '.idle.screensaver // 150' ~/.config/omarchy/shell.json 2>/dev/null || echo 150) ))
FRAME_DELAY=0.35

screensaver_in_focus() {
  hyprctl activewindow -j | jq -e '.class == "org.omarchy.screensaver"' >/dev/null 2>&1
}

exit_screensaver() {
  printf '\033[?25h\033[0m'
  hyprctl eval 'hl.config({ cursor = { invisible = false } })' &>/dev/null || hyprctl keyword cursor:invisible false &>/dev/null || true
  pkill -f '[o]rg.omarchy.screensaver' 2>/dev/null
  exit 0
}

trap exit_screensaver SIGINT SIGTERM SIGHUP SIGQUIT EXIT

printf '\033]11;rgb:00/00/00\007' # black background
printf '\033[?25l\033[2J'          # hide cursor, clear
hyprctl eval 'hl.config({ cursor = { invisible = true } })' &>/dev/null || hyprctl keyword cursor:invisible true &>/dev/null

# Wait for the compositor to size the terminal (it starts at 80x24)
deadline=$((SECONDS + 2))
while ((SECONDS < deadline)) && [[ $(stty size 2>/dev/null) == "24 80" ]]; do sleep 0.02; done

rows=24 cols=80
resize() { read -r rows cols < <(stty size 2>/dev/null || echo "24 80"); printf '\033[2J'; }
trap resize SIGWINCH
resize

# --- power ----------------------------------------------------------------

ac_path=""
for s in /sys/class/power_supply/*; do
  [[ $(cat "$s/type" 2>/dev/null) == Mains ]] && ac_path=$s && break
done
bat_path=""
for s in /sys/class/power_supply/BAT*; do [[ -d $s ]] && bat_path=$s && break; done

on_ac() { [[ -n $ac_path && $(cat "$ac_path/online" 2>/dev/null) == 1 ]]; }
lid_open() { ! grep -qs closed /proc/acpi/button/lid/*/state; }

charge_line() {
  [[ -n $bat_path ]] || return
  local cap status now full rate left="" width=24 filled bar
  cap=$(cat "$bat_path/capacity" 2>/dev/null) || return
  status=$(cat "$bat_path/status" 2>/dev/null)
  now=$(cat "$bat_path/charge_now" 2>/dev/null || cat "$bat_path/energy_now" 2>/dev/null)
  full=$(cat "$bat_path/charge_full" 2>/dev/null || cat "$bat_path/energy_full" 2>/dev/null)
  rate=$(cat "$bat_path/current_now" 2>/dev/null || cat "$bat_path/power_now" 2>/dev/null)

  if [[ $status == Charging && $rate -gt 0 && $full -gt $now ]]; then
    local mins=$(( (full - now) * 60 / rate ))
    left=$(printf '  ~%d:%02d до полной' $((mins / 60)) $((mins % 60)))
  fi

  filled=$(( cap * width / 100 ))
  bar=$(printf '%*s' "$filled" '' | tr ' ' '#')$(printf '%*s' $((width - filled)) '' | tr ' ' '.')

  case $status in
  Charging) printf '⚡ заряжается %3d%%  [%s]%s' "$cap" "$bar" "$left" ;;
  Full) printf '✓ заряжено %3d%%  [%s]' "$cap" "$bar" ;;
  *) printf '🔌 подключено %3d%%  [%s]' "$cap" "$bar" ;;
  esac
}

# --- cat ------------------------------------------------------------------

C_CAT=$'\033[38;5;252m'
C_EYE=$'\033[38;5;196m'
C_COOKIE=$'\033[38;5;160m'
C_CHIP=$'\033[38;5;88m'
C_TXT=$'\033[38;5;250m'
C_BAR=$'\033[38;5;196m'
C_OFF=$'\033[0m'

# Cookie stages, 3 lines each, 8 chars wide
cookie_lines() {
  case $1 in
  0) COOKIE=("  .---.  " " ( o .o) " "  '---'  ") ;;
  1) COOKIE=("  .--.   " "  ;. o)  " "  '--'   ") ;;
  2) COOKIE=("   .-.   " "   ;o)   " "   '-'   ") ;;
  3) COOKIE=("         " "   ;)    " "  .  ,   ") ;;
  4) COOKIE=("         " "  .   '  " " ,  .  . ") ;;
  *) COOKIE=("         " "         " "         ") ;;
  esac
}

# draw <eyes> <mouth> <cookie stage> <paw: up|down> <bubble>
draw() {
  local e=$1 m=$2 stage=$3 paw=$4 bubble=$5
  cookie_lines "$stage"

  local paw_line="   (             )"
  [[ $paw == up ]] && paw_line="   (   ,     ,-~~"
  local cat=(
    "     /\\_____/\\"
    "    /  ${C_EYE}$e${C_CAT}   ${C_EYE}$e${C_CAT}  \\"
    "   ( ==   $m   == )"
    "    )           ("
    "$paw_line"
    "  ( (  )     (  ) )"
    " (__(__)_____(__)__)~~"
  )
  # visible width of each cat line (without colour codes) for padding
  local pad=18 i plain art=(
    "${C_TXT}$(printf '%-30s' "$bubble")${C_OFF}"
    ""
  )
  for i in "${!cat[@]}"; do
    plain=${cat[$i]//$C_EYE/}; plain=${plain//$C_CAT/}
    local line="${C_CAT}${cat[$i]}$(printf '%*s' $((pad - ${#plain})) '')"
    case $i in
    2) line+="${C_COOKIE}${COOKIE[0]}" ;;
    3) line+="${C_COOKIE}${COOKIE[1]}" ;;
    4) line+="${C_COOKIE}${COOKIE[2]}" ;;
    esac
    art+=("${line}${C_OFF}")
  done

  local h=${#art[@]} w=32
  local top=$(( (rows - h - 3) / 2 )) left=$(( (cols - w) / 2 ))
  ((top < 0)) && top=0
  ((left < 0)) && left=0

  local out="" i
  for i in "${!art[@]}"; do
    out+=$'\033['$((top + i + 1))';'$((left + 1))$'H\033[K'"${art[$i]}"
  done

  local line=""
  on_ac && line=$(charge_line)
  local plain=${#line}
  out+=$'\033['$((top + h + 3))$';1H\033[K'
  if [[ -n $line ]]; then
    local lpad=$(( (cols - plain) / 2 ))
    ((lpad < 0)) && lpad=0
    out+=$'\033['$((top + h + 3))';'$((lpad + 1))'H'"${C_BAR}${line}${C_OFF}"
  fi

  printf '%s' "$out"
}

# One loop of the animation: eyes mouth cookie paw bubble
FRAMES=(
  "o|w|0|down|"
  "o|w|0|down|"
  "o|w|0|up|  ?"
  "O|O|0|up|  !"
  "-|~|1|up|  nom"
  "-|w|1|up|  nom nom"
  "o|O|1|up|"
  "-|~|2|up|  nom"
  "-|w|2|up|  nom nom"
  "o|O|2|up|"
  "-|~|3|up|  *crunch*"
  "-|w|3|down|  *crunch*"
  "^|w|4|down|  mmm..."
  "^|~|4|down|  mmm..."
  "^|w|4|down|"
  "-|w|4|down|"
  "^|w|5|down|  ~ purr ~"
  "^|w|5|down|  ~ purr ~"
  "-|w|5|down|  ~ purr ~"
  "o|w|5|down|"
  "o|w|5|down|  ещё?"
  "o|w|5|down|  ещё?"
)

start=$SECONDS
ac_seen=0
frame=0
last_focus_check=0
while true; do
  if lid_open; then
    IFS='|' read -r e m c p b <<<"${FRAMES[frame]}"
    draw "$e" "$m" "$c" "$p" "$b"
    frame=$(( (frame + 1) % ${#FRAMES[@]} ))
  fi

  if read -rsn1 -t "$FRAME_DELAY"; then exit_screensaver; fi

  if ((SECONDS - last_focus_check >= 1)); then
    last_focus_check=$SECONDS
    screensaver_in_focus || exit_screensaver

    # On AC the idle lock is disabled (see cat-screensaver-ac-watch), so the idle
    # service won't lock this cycle. If the charger gets unplugged while we are
    # showing, lock ourselves once the usual lock delay has passed.
    if on_ac; then
      ac_seen=1
    elif ((ac_seen && LOCK_AFTER > 0 && SECONDS - start >= LOCK_AFTER + 5)); then
      omarchy-system-lock
      exit_screensaver
    fi
  fi
done
