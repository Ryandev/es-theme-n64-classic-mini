#!/bin/bash
# Brings the console to this bundle's versions (AC-S02); run by the boot service and install.sh.
#
# Most components are set up while the engine runs, with progress on screen. Those it reads only
# at start-up (its settings, the theme, the N64 launch override) are set up while it's suspended:
# the engine writes its whole in-memory settings back as it quits (ViewController::saveState), so
# a file changed under a running engine is lost. Batocera's own suspend quits the engine and holds
# its launch loop; resume lets the loop start it again (frontend/vendor/batocera-43.1).
set -uo pipefail
bundle="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
engine=/etc/init.d/S31emulationstation
setup() { PYTHONPATH="$bundle/python" python3 -m n64mini.console.setup "$bundle" "$@"; }
bash "$bundle/console/boot-custom.sh" install || true
rm -f "/userdata/roms/n64/Showcase or Full.n64mini"

if ! pidof emulationstation >/dev/null; then
    setup  # no engine to show progress or to stop: set everything up at once
    exit
fi
status=0
setup --phase live --engine || status=1
if ! pending="$(setup --phase stopped --pending)"; then
    exit 1
fi
[ -n "$pending" ] || exit "$status"
"$engine" suspend
for _ in $(seq 60); do
    pidof emulationstation >/dev/null || break
    sleep 1
done
if pidof emulationstation >/dev/null; then
    echo "update: the engine didn't stop; left for the next boot: $pending" >&2
    exit 1
fi
setup --phase stopped || status=1
timeout 10 "$engine" resume || { echo "update: the engine didn't resume" >&2; status=1; }
/userdata/system/n64mini/python/n64mini/console/data/n64mini-topbar.sh || true
exit "$status"
