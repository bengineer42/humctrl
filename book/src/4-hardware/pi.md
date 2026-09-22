# Raspberry Pi setup

A Raspberry Pi 4 running Raspberry Pi OS Lite (64-bit) is the rig's
computer: it runs the daemon, talks to the sensors over I²C and drives the
pumps from its two hardware PWM channels. Nothing else on it is special.

## The OS

Flash Raspberry Pi OS Lite (64-bit) with the Raspberry Pi Imager; in its
settings give the machine a hostname, a user, and enable SSH. Do not use a
default password on a Pi that will sit on a network -- the daemon's own
[door](https://bengineer42.github.io/flyball/latest/1-running/daemon/access/) is not a substitute for the
machine's.

## I²C and PWM

Two kernel features must be on: the I²C bus on the GPIO header (the
sensors) and the hardware PWM channels (the pumps). `scripts/setup-pi-hardware.sh`
in the repository does both, idempotently, with a backup of `config.txt`,
and grants the named user access without root:

```
sudo ./scripts/setup-pi-hardware.sh $USER
sudo ./scripts/setup-pi-hardware.sh --verify     # check only, change nothing
```

then reboot when it says so. By hand, it is `dtparam=i2c_arm=on` and
`dtoverlay=pwm-2chan` in `/boot/firmware/config.txt`, a udev rule so
`/sys/class/pwm` is writable by the user, and the user in the `i2c` group.
`pwm-2chan` puts PWM0 on GPIO18 (header pin 12) and PWM1 on GPIO19 (pin
35); `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` moves them to
GPIO12/13 if those pins suit the board better -- then change the board
profile to match.

After the reboot, `ls /dev/i2c-1 /sys/class/pwm/pwmchip0` shows both, and
`i2cdetect -y 1` lists the sensors at `44`, `45` and `46`.

## flyball and this package

```
sudo apt install git python3
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/bengineer42/humctrl.git && cd humctrl
uv sync                                  # flyball, flyball-linux[i2c] and this package, into .venv
uv run flyball-runner rig.yaml           # the real rig; `rig.yaml sim.yaml` for the simulation
```

`flyball-linux` needs no compiled extensions for I²C and PWM (it talks to
the kernel interfaces directly), so `uv sync` on the Pi is a few minutes,
mostly downloading. Serving it on the network, a token, a sub-path, running
under systemd: the flyball book's [Starting a rig](https://bengineer42.github.io/flyball/latest/1-running/daemon/).
