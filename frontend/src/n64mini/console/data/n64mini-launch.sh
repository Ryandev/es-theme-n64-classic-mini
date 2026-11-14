#!/bin/bash
# Batocera's N64 launch command (es_systems_n64.cfg): n64mini.console.launch decides between the
# port and emulatorlauncher, printing the command NUL-separated, and this runs it.
set -euo pipefail
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"  # installed: <root>/n64mini/console/data
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
mapfile -d '' -t command < <(python3 -m n64mini.console.launch "$@")
[ "${#command[@]}" -gt 0 ] || exit 1
if [ -f /userdata/themes/n64-classic-mini/art/launch_splash.png ] && [ "${command[0]}" != "setsid" ]; then
    # Its errors go to a log of their own: the launch must go on even if the animation fails.
    DISPLAY="${DISPLAY:-:0}" setsid -f python3 -m n64mini.console.launch_animation "$@" </dev/null \
        >>/userdata/system/logs/n64mini-launch-animation.log 2>&1
    sleep 0.90
fi
if [ "${command[0]}" = "setsid" ]; then
    exec "${command[@]}"  # detached: it returns at once, so the LED stays on the menu colour
fi
# Power LED: green while the game runs, red again after (n64mini.console.led; owner 2026-11-13).
# The game runs as a child this script waits on (no exec), so the LED can go back to red after
# it. A TERM/INT/HUP sent to this script is passed on to the game, and the LED still goes red
# (Gemini review #8): without the trap the game would be orphaned and the LED left green.
python3 -m n64mini.console.led game || true
"${command[@]}" <&0 &
game=$!
trap 'kill -TERM "$game" 2>/dev/null' TERM INT HUP
status=0
wait "$game" || status=$?
while kill -0 "$game" 2>/dev/null; do  # a trapped signal ends `wait` early: wait for the game
    wait "$game" || status=$?
done
python3 -m n64mini.console.led menu || true
exit "$status"
