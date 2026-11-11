#!/bin/bash
# Batocera user service (AC-S02): S99userservices runs it at every boot with "start", in the
# background, once `batocera-services enable n64mini` has enabled it (install.sh does). With an
# N64 Mini USB stick in (DEC-S1), it brings the console to the stick's versions, showing progress
# through the engine. Either way it then draws the strip of box art under the cards (AC-T20) for
# the list as it is now, so art scraped since the last boot shows in it.
[ "${1:-}" = start ] || exit 0
# Boot never deletes save files. A `.pending` is a whole Reset suspend point (placed by rename) that
# the drawer offers again, and a `.stN.tmp` can be a real slot moved aside mid-renumber.
rm -f /tmp/n64mini-resume.json
log=/userdata/system/logs/n64mini-setup.log
for bundle in /media/*/n64mini-bundle; do
    [ -f "$bundle/n64mini.json" ] || continue
    {
        date
        bash "$bundle/update.sh"
    } >>"$log" 2>&1
    break
done
/userdata/system/n64mini/python/n64mini/console/data/n64mini.sh strip \
    >>/userdata/system/logs/n64mini-strip.log 2>&1
/userdata/system/n64mini/python/n64mini/console/data/n64mini-topbar.sh \
    >>/userdata/system/logs/n64mini-topbar.log 2>&1 || true
