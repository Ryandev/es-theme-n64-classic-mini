#!/bin/bash
# Batocera's N64 launch command (es_systems_n64.cfg): n64mini.console.launch decides between the
# port and emulatorlauncher, printing the command NUL-separated, and this runs it.
set -euo pipefail
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"  # installed: <root>/n64mini/console/data
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
mapfile -d '' -t command < <(python3 -m n64mini.console.launch "$@")
[ "${#command[@]}" -gt 0 ] || exit 1
if [ -f /userdata/themes/n64-classic-mini/art/launch_splash.png ] && [ "${command[0]}" != "setsid" ]; then
    DISPLAY="${DISPLAY:-:0}" setsid -f python3 -m n64mini.console.launch --animate "$@" </dev/null >/dev/null 2>&1
    sleep 0.90
fi
exec "${command[@]}"
