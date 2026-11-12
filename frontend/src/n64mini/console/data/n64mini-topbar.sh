#!/bin/bash
# Starts the top-bar daemon (n64mini.console.topbar) in the background: the top bar's buttons,
# D-Pad Up/Down on the carousel and the drawer after a Reset. The daemon replaces any running one.
set -euo pipefail
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"

DISPLAY="${DISPLAY:-:0.0}" nohup python3 -m n64mini.console.topbar \
    </dev/null >/userdata/system/logs/n64mini-topbar.log 2>&1 &
