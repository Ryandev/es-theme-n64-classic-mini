#!/bin/bash
# n64mini.sh MODULE [ARGS]: run a console module (flip, showcase, setup, launch) from the package
# this script is installed in (<root>/n64mini/console/data/), for the hotkey and the event script.
set -euo pipefail
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m "n64mini.console.${1:?usage: n64mini.sh MODULE [ARGS]}" "${@:2}"
