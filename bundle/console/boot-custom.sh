#!/bin/bash
# Early-boot hook (/boot/boot-custom.sh, run by /etc/init.d/S00bootcustom before S03system-splash
# and S28splash): drops every Batocera logo, version subtitle, and bootloader text so only the
# N64 startup video (/userdata/splash/splash.mp4) plays on boot.
set -euo pipefail

strip_batocera_splash() {
    rm -f /usr/share/batocera/splash/boot-logo.png \
          /usr/share/batocera/splash/boot-logo-4x3.png \
          /usr/share/batocera/splash/splash.srt
}

install_boot() {
    [ -f /boot/batocera-boot.conf ] || return 0
    strip_batocera_splash
    local self
    self="$(readlink -f "$0")"
    if [ -f /boot/boot-custom.sh ] && cmp -s "$self" /boot/boot-custom.sh \
        && ! grep -q "Booting Batocera" /boot/EFI/BOOT/grub.cfg /boot/EFI/batocera/grub.cfg \
             /boot/boot/syslinux.cfg /boot/boot/syslinux/syslinux.cfg 2>/dev/null; then
        return 0
    fi
    mount -o remount,rw /boot
    cp "$self" /boot/boot-custom.sh
    local cfg
    for cfg in /boot/EFI/BOOT/grub.cfg /boot/EFI/batocera/grub.cfg; do
        [ -f "$cfg" ] || continue
        sed -i \
            -e 's/set timeout="1"/set timeout="0"/' \
            -e '/echo Booting Batocera/d' \
            -e 's/Batocera\.linux/Classic 64/g' \
            "$cfg"
    done
    for cfg in /boot/boot/syslinux.cfg /boot/boot/syslinux/syslinux.cfg; do
        [ -f "$cfg" ] || continue
        sed -i \
            -e '/SAY Booting Batocera/d' \
            -e 's/Batocera\.linux/Classic 64/g' \
            "$cfg"
    done
    sync
    mount -o remount,ro /boot
}

case "${1:-start}" in
    install) install_boot ;;
    *)       strip_batocera_splash ;;
esac
