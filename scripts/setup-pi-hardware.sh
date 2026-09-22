#!/usr/bin/env bash
# Enables I2C and the two PWM channels this rig needs, and grants a non-root
# user access to both -- idempotent, safe to re-run.
#
#   sudo ./scripts/setup-pi-hardware.sh           # apply, to whoever ran sudo ($SUDO_USER)
#   sudo ./scripts/setup-pi-hardware.sh USER      # apply, to a specific user instead
#   sudo ./scripts/setup-pi-hardware.sh --verify  # check only, change nothing
#
# I2C: dtparam=i2c_arm=on in /boot/firmware/config.txt, user added to the
# `i2c` group (covers /dev/i2c-1).
#
# PWM: dtoverlay=pwm-2chan in config.txt (PWM0 on GPIO18/pin 12, PWM1 on
# GPIO19/pin 35 -- see book/src/4-hardware/pi.md for moving them). Exported
# channels under /sys/class/pwm/pwmchipN/pwmX are created root-owned, fresh,
# every time they're exported -- not just at boot, so a one-off chown isn't
# enough. A udev rule (90-pwm.rules) re-chows /sys/class/pwm on any pwm*
# event, but it loses the race against this rig's own PWM link: it exports
# a channel and writes to it on the very next line, no delay, faster than
# udev can spawn a shell and chown recursively -- confirmed live (two real
# runs, both failed on a freshly-exported channel despite the rule being
# correctly installed). The udev rule stays as defence in depth for
# anything else that might touch a channel, but the actual fix is
# deterministic: pwm0/pwm1 (the two channels this rig ever uses) are
# pre-exported and chowned here, synchronously, before the rig ever runs --
# so by the time it asks for them, there's no export left to race. A
# systemd oneshot (flyball-pwm.service) repeats that at every boot, since
# exported channels don't survive a reboot. User added to `gpio` (usually
# already a member on Raspberry Pi OS, harmless if so).
#
# config.txt changes need a reboot; everything else (groups, the udev rule,
# the pre-export) takes effect immediately (group membership needs a fresh
# login/session though).
set -euo pipefail

CONFIG_TXT=/boot/firmware/config.txt
UDEV_RULE=/etc/udev/rules.d/90-pwm.rules
SYSTEMD_UNIT=/etc/systemd/system/flyball-pwm.service
PWM_OVERLAY="dtoverlay=pwm-2chan"
I2C_PARAM="dtparam=i2c_arm=on"
PWM_CHANNELS="0 1"  # dry, wet -- src/humidity/blender.py's PumpLineConfig.channel

verify=false
user=""
for arg in "$@"; do
  case "$arg" in
    --verify) verify=true ;;
    *) user="$arg" ;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "run as root: sudo $0 ${user:---verify}" >&2
  exit 2
fi

user="${user:-${SUDO_USER:-}}"
if ! $verify && [ -z "$user" ]; then
  echo "usage: sudo $0 [USER] | --verify -- no \$SUDO_USER to default to (running as root directly?), pass a username explicitly" >&2
  exit 2
fi

ok=true
check() {
  if [ "$2" = "yes" ]; then
    echo "  [ok]      $1"
  else
    echo "  [missing] $1"
    ok=false
  fi
}

has_line() { grep -qxF "$1" "$CONFIG_TXT" 2>/dev/null; }

if $verify; then
  echo "config.txt ($CONFIG_TXT):"
  has_line "$I2C_PARAM" && check "$I2C_PARAM" yes || check "$I2C_PARAM" no
  has_line "$PWM_OVERLAY" && check "$PWM_OVERLAY" yes || check "$PWM_OVERLAY" no
  echo "udev rule ($UDEV_RULE):"
  [ -f "$UDEV_RULE" ] && check "installed" yes || check "installed" no
  echo "pwm boot-time export (${SYSTEMD_UNIT}):"
  if [ -f "$SYSTEMD_UNIT" ] && systemctl is-enabled --quiet flyball-pwm.service 2>/dev/null; then
    check "installed and enabled" yes
  else
    check "installed and enabled" no
  fi
  echo "pwm0/pwm1 group-writable now:"
  # stat, not `[ -w ]` -- this script runs as root, which bypasses permission
  # bits entirely, so `-w` would report "yes" even when a real non-root
  # write would fail.
  for ch in $PWM_CHANNELS; do
    f="/sys/class/pwm/pwmchip0/pwm$ch/enable"
    if [ -e "$f" ] && [ "$(stat -c '%G' "$f" 2>/dev/null)" = "gpio" ] && [ "$(stat -c '%A' "$f" 2>/dev/null | cut -c6)" = "w" ]; then
      check "pwm$ch" yes
    else
      check "pwm$ch (not exported yet, or not group-writable)" no
    fi
  done
  echo "groups:"
  for g in i2c gpio; do
    if [ -n "$user" ] && id -nG "$user" 2>/dev/null | grep -qw "$g"; then
      check "$user in $g" yes
    else
      check "${user:-<user>} in $g" no
    fi
  done
  $ok && { echo "all set."; exit 0; } || { echo "run: sudo $0"; exit 1; }
fi

echo "==> config.txt"
if [ -f "$CONFIG_TXT" ]; then
  cp -n "$CONFIG_TXT" "$CONFIG_TXT.bak" 2>/dev/null || true
  has_line "$I2C_PARAM" || { echo "$I2C_PARAM" >> "$CONFIG_TXT"; echo "  added $I2C_PARAM"; }
  has_line "$PWM_OVERLAY" || { echo "$PWM_OVERLAY" >> "$CONFIG_TXT"; echo "  added $PWM_OVERLAY"; }
else
  echo "  $CONFIG_TXT not found -- not a Raspberry Pi OS boot partition layout?" >&2
  exit 1
fi

echo "==> groups"
for g in i2c gpio; do
  usermod -aG "$g" "$user"
  echo "  $user added to $g"
done

echo "==> udev rule"
cat > "$UDEV_RULE" <<'RULE'
# Exported PWM channels (/sys/class/pwm/pwmchipN/pwmX) are created root-owned
# fresh on every export, not just at boot -- this re-applies group access on
# any pwm* subsystem event so a freshly-exported channel is usable without
# root. Installed by scripts/setup-pi-hardware.sh.
SUBSYSTEM=="pwm*", PROGRAM="/bin/sh -c 'chown -R root:gpio /sys/class/pwm && chmod -R g+rw /sys/class/pwm'"
RULE
echo "  wrote $UDEV_RULE"
udevadm control --reload-rules
udevadm trigger --subsystem-match=pwm 2>/dev/null || true
echo "  reloaded udev rules"

export_and_fix_pwm() {
  # Idempotent: only exports a channel that isn't already exported, safe to
  # call every boot and every time this script runs.
  if [ -d /sys/class/pwm/pwmchip0 ]; then
    for ch in $PWM_CHANNELS; do
      [ -d "/sys/class/pwm/pwmchip0/pwm$ch" ] || echo "$ch" > /sys/class/pwm/pwmchip0/export
    done
    chown -R root:gpio /sys/class/pwm
    chmod -R g+rw /sys/class/pwm
  fi
}

echo "==> pre-exporting pwm0/pwm1 now"
if [ -d /sys/class/pwm/pwmchip0 ]; then
  export_and_fix_pwm
  echo "  done"
else
  echo "  /sys/class/pwm/pwmchip0 doesn't exist yet -- the PWM overlay just added to"
  echo "  config.txt needs a reboot first; the boot-time service below covers it from then on"
fi

echo "==> boot-time export service"
cat > "$SYSTEMD_UNIT" <<UNIT
[Unit]
Description=Export and fix permissions on this rig's PWM channels (flyball-pwm)
After=sysinit.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for ch in $PWM_CHANNELS; do [ -d /sys/class/pwm/pwmchip0/pwm\$ch ] || echo \$ch > /sys/class/pwm/pwmchip0/export; done; chown -R root:gpio /sys/class/pwm; chmod -R g+rw /sys/class/pwm'

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now flyball-pwm.service >/dev/null 2>&1 || systemctl enable --now flyball-pwm.service
echo "  wrote and enabled $SYSTEMD_UNIT"

echo
echo "Done. Reboot for config.txt to take effect (dtparam/dtoverlay are boot-time);"
echo "after that, flyball-pwm.service pre-exports both channels on every boot, no manual chown needed."
echo "Check now: ls /dev/i2c-1 /sys/class/pwm/pwmchip0 to confirm both are present."
