#!/usr/bin/env bash
# Enables I2C and the two PWM channels this rig needs, and grants a non-root
# user access to both -- idempotent, safe to re-run.
#
#   sudo ./scripts/setup-pi-hardware.sh USER      # apply
#   sudo ./scripts/setup-pi-hardware.sh --verify  # check only, change nothing
#
# I2C: dtparam=i2c_arm=on in /boot/firmware/config.txt, user added to the
# `i2c` group (covers /dev/i2c-1).
#
# PWM: dtoverlay=pwm-2chan in config.txt (PWM0 on GPIO18/pin 12, PWM1 on
# GPIO19/pin 35 -- see book/src/4-hardware/pi.md for moving them). Exported
# channels under /sys/class/pwm/pwmchipN/pwmX are created root-owned, fresh,
# every time they're exported -- not just at boot, so a one-off chown isn't
# enough. Fixed with a udev rule (90-pwm.rules) that re-chowns the whole
# /sys/class/pwm tree to root:gpio, g+rw, on any pwm* subsystem event, so a
# freshly-exported channel picks up group-write automatically. User added to
# `gpio` (usually already a member on Raspberry Pi OS, harmless if so).
#
# config.txt changes need a reboot; the udev rule and group membership take
# effect immediately (group membership needs a fresh login/session though).
set -euo pipefail

CONFIG_TXT=/boot/firmware/config.txt
UDEV_RULE=/etc/udev/rules.d/90-pwm.rules
PWM_OVERLAY="dtoverlay=pwm-2chan"
I2C_PARAM="dtparam=i2c_arm=on"

verify=false
user=""
for arg in "$@"; do
  case "$arg" in
    --verify) verify=true ;;
    *) user="$arg" ;;
  esac
done

if ! $verify && [ -z "$user" ]; then
  echo "usage: $0 USER | --verify" >&2
  exit 2
fi

if [ "$EUID" -ne 0 ]; then
  echo "run as root: sudo $0 ${user:---verify}" >&2
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
  echo "groups:"
  for g in i2c gpio; do
    if [ -n "${SUDO_USER:-}" ] && id -nG "$SUDO_USER" 2>/dev/null | grep -qw "$g"; then
      check "$SUDO_USER in $g" yes
    else
      check "${SUDO_USER:-<user>} in $g" no
    fi
  done
  $ok && { echo "all set."; exit 0; } || { echo "run: sudo $0 \${SUDO_USER:-\$USER}"; exit 1; }
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

echo
echo "Done. Reboot for config.txt to take effect (dtparam/dtoverlay are boot-time)."
echo "After reboot: ls /dev/i2c-1 /sys/class/pwm/pwmchip0 to confirm both are present."
