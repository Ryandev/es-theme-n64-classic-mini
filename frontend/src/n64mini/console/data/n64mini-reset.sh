#!/bin/bash
# Batocera's Reset-button hotkey command (common_context.conf, AC-T18): n64mini.console.reset
# checks whether an N64 game is running and in which mode. In Emulator mode it saves a suspend
# point, compacts the slot files from .st0, exits the emulator, and pins CurrentStateSlot; in Port
# mode it exits cleanly.
set -euo pipefail
# hotkeygen runs this with os.system and reads no keys until it returns, including the save_state
# and exit keys sent below through `hotkeygen --send`. So hand the work to a detached copy and
# return at once; otherwise nothing is saved and `open` waits forever for the game to quit.
if [ "${N64MINI_RESET_DETACHED:-}" != 1 ]; then
    N64MINI_RESET_DETACHED=1 setsid "$(readlink -f "$0")" "$@" </dev/null \
        >>"${N64MINI_RESET_LOG:-/userdata/system/logs/n64mini-reset.log}" 2>&1 &
    exit 0
fi
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
mode="$(python3 -m n64mini.console.reset decide "$@")"
case "$mode" in
    emulator)
        # Only a state written after this moment is the new suspend point (B3).
        # (python3, not `date +%s%N`: BusyBox date may lack %N.)
        since="$(python3 -c 'import time; print(time.time_ns())')"
        rm -f /tmp/n64mini-reset-shot.png
        batocera-screenshot /tmp/n64mini-reset-shot.png >/dev/null 2>&1 || true
        hotkeygen --send save_state
        python3 -m n64mini.console.reset stage --since-ns "$since" "$@"
        hotkeygen --send exit
        python3 -m n64mini.console.reset open --since-ns "$since" "$@"
        ;;
    port)
        hotkeygen --send exit
        pkill -TERM -f /userdata/system/n64mini/ports/ || true
        ;;
esac
