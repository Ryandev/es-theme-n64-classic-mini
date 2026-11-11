#!/bin/bash
# One-time install (AC-S01), on a stock Batocera with this bundle on a USB stick, e.g. over SSH:
#   bash /media/<stick>/n64mini-bundle/install.sh
# Enables the boot service, then sets the console up from this bundle now. Every later update is
# the service's job: boot with a newer stick in.
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
install -D -m 755 "$here/console/n64mini-service.sh" /userdata/system/services/n64mini
batocera-services enable n64mini
mkdir -p /userdata/system/logs
bash "$here/update.sh"
