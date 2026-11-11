#!/bin/bash
# Batocera's Reset-button hotkey command (common_context.conf, AC-T18): n64mini.console.reset
# checks whether an N64 game is running and in which mode. In Emulator mode it saves a suspend
# point, compacts the slot files from .st0, exits the emulator, and pins CurrentStateSlot; in Port
# mode it exits cleanly.
set -euo pipefail
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
