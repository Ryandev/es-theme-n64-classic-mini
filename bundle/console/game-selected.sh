#!/bin/bash
# The engine's game-selected event (Scripting.cpp, run asynchronously): $1 the system, $2 the
# ROM's path, $3 the game's name. Remembers the highlighted N64 game for the flip hotkey (AC-T15).
[ "${1:-}" = n64 ] || exit 0
exec /userdata/system/n64mini/python/n64mini/console/data/n64mini.sh flip selected "$@"
