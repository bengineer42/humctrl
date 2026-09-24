# Raspberry Pi setup

A Raspberry Pi 4 running Raspberry Pi OS Lite (64-bit) is the rig's
computer: it runs the daemon, talks to the sensors over I²C and drives the
pumps from its two hardware PWM channels. Nothing else on it is special.

## The OS

Flash Raspberry Pi OS Lite (64-bit) with the Raspberry Pi Imager; in its
settings give the machine a hostname, a user, and enable SSH. Do not use a
default password on a Pi that will sit on a network -- the daemon's own
[door](https://bengineer42.github.io/flyball/latest/1-running/runner/access/) is not a substitute for the
machine's.

## I²C and PWM

Two kernel features must be on: the I²C bus on the GPIO header (the
sensors) and the hardware PWM channels (the pumps). `scripts/setup-pi-hardware.sh`
in the repository does both, idempotently, with a backup of `config.txt`,
and grants the named user access without root -- `install.sh` runs it for
you automatically when it detects a Pi, or run it by hand:

```
sudo ./scripts/setup-pi-hardware.sh              # to whoever ran sudo
sudo ./scripts/setup-pi-hardware.sh --verify     # check only, change nothing
```

then reboot when it says so. By hand, it is `dtparam=i2c_arm=on` and
`dtoverlay=pwm-2chan` in `/boot/firmware/config.txt`, the user in the `i2c`
and `gpio` groups, and permissions on the two PWM channels this rig uses
(`pwmchip0/pwm0`, `pwm1`). An exported channel is created root-owned fresh
*every time it's exported*, not just at boot, and this rig exports and
writes to a channel back to back with no delay -- too fast for a udev rule
alone to win the race reliably (confirmed live: a udev rule was tried
first and lost that race in practice). Fixed deterministically instead: the
script pre-exports and `chown`s both channels itself, and installs a
`flyball-pwm.service` systemd oneshot that repeats that on every boot,
before the rig ever runs -- by the time it asks for a channel, there's
nothing left to export. A udev rule (`/etc/udev/rules.d/90-pwm.rules`)
still gets installed too, as a defence-in-depth catch-all for anything
else that touches a channel. `pwm-2chan` puts PWM0 on GPIO18 (header pin
12) and PWM1 on GPIO19 (pin 35); `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`
moves them to GPIO12/13 if those pins suit the board better -- then change
the board profile to match.

After the reboot, `ls /dev/i2c-1 /sys/class/pwm/pwmchip0` shows both, and
`i2cdetect -y 1` lists the sensors at `44`, `45` and `46`.

## flyball and this package

```
sudo apt install git python3
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/bengineer42/humctrl.git && cd humctrl
./install.sh                             # uv, this package's deps, the flyball CLI, PWM/I2C (Pi detected)
flyball password                         # asks for a password, prints a $scrypt$ line
```

`blender.yaml`, which every rig file here extends, sets `runner.front`: the
dashboard and the door on `:8000` for the whole network, `auth: password`,
and the runner started through this project's venv (`uv: true`). What it
does not hold is the password, or anything else only this Pi differs by.

## This Pi's own settings

Keep them in a deployment overlay **outside the checkout**, so a `git pull`
never touches them and they never reach the repository. `flyball run` lays
each rig file over the one before, key by key, so the overlay carries only
what differs:

```yaml
# ~/humidity-pi.yaml -- this Pi only; not in git
runner:
  front:
    password: $scrypt$…                  # the line `flyball password` printed
    # trusted_proxies: [127.0.0.1]       # behind nginx on this Pi (below)
devices:
  blender:
    dry: { deadband: 0.128, max_flow: 1.9 }   # this rig's pumps, measured
    wet: { deadband: 0.081, max_flow: 2.0 }
```

```
chmod 600 ~/humidity-pi.yaml
flyball run rig-multi-sensor.yaml ~/humidity-pi.yaml
```

Put the rig file first: `flyball run` starts the runner from the first
file's directory (the `uv` project) and keys its state by it. Without the
overlay the rig still runs, but the front has no password and serves this
Pi only (`127.0.0.1`), saying why. The pump values are what
[Pumps](pumps.md) says to measure: the duty where each pump's flow line
crosses zero, and its flow at full duty.

`flyball-linux` needs no compiled extensions for I²C and PWM (it talks to
the kernel interfaces directly), so `uv sync` (part of `install.sh`) on the Pi is a few
minutes, mostly downloading. A token, a sub-path, running under systemd: the
flyball book's [Starting a rig](https://bengineer42.github.io/flyball/latest/1-running/runner/)
and [Access](https://bengineer42.github.io/flyball/latest/1-running/runner/access/).

## nginx

`scripts/setup-nginx.sh` reverse-proxies port 80 at the front (installing
nginx if needed), so the rig is reachable without naming its port:

```
sudo ./scripts/setup-nginx.sh                                # the front on :8000, the default
sudo ./scripts/setup-nginx.sh --port 8080 --server-name humidity.local
```

Point nginx at the port `runner.front.listen` gives the front -- the runner
behind it has no port of its own. The script passes the browser's `Host`
through, which the front checks. Uncomment `trusted_proxies: [127.0.0.1]`
in the overlay so the sign-in limit counts each client rather than nginx,
and, if nginx is to be the only way in, set `listen: 127.0.0.1:8000` there
too.
