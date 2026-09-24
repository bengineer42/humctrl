# humctrl

A humidity chamber held at a target relative humidity by blending a dry and a wet air line,
built on [flyball](https://github.com/bengineer42/flyball) (a control library and daemon for lab
rigs). This repo is the rig itself: the `dual_pump_blender` control device, the
`sim_humidity_chamber` plant for running it with no hardware attached, the rig file, tunings, a
CLI, and the [book](https://bengineer42.github.io/humctrl/) that documents all of it.

Split out of `flyball/examples/humidity` on 22 Sep 2026, keeping its history; it still runs
nested inside a flyball checkout the same way (`flyball-runner rig-multi-sensor.yaml` from this directory),
but is its own repo now, not a submodule.

## Running it

`./install.sh` sets everything up: `uv` if it's missing, this repo's Python deps, the
`flyball` CLI (built from the same flyball commit this repo is pinned to, straight to
`~/.local/bin`), and, on a detected Raspberry Pi, I2C/PWM hardware access
(`scripts/setup-pi-hardware.sh`, needs `sudo`).

With no hardware — a simulated chamber and pumps:

```sh
uv sync
uv run flyball-runner rig-multi-sensor.yaml sim.yaml
```

Against real hardware (a Raspberry Pi, a TB6612 dual motor driver, SHT4x sensors on I2C):

```sh
uv run flyball-runner rig-multi-sensor.yaml
```

Either way, that serves the API/WebSocket only -- no dashboard. `flyball run` (the Go CLI
`install.sh` builds) wraps the same thing and can serve the dashboard too, embedded in the CLI
binary itself, no separate UI build/install needed:

```sh
flyball run rig-multi-sensor.yaml
```

`blender.yaml` (shared by every rig file here) sets `runner.front`: the dashboard and the door on
`:8000`, a password to get in, and the runner started through this project's venv (`uv`). The
password itself -- a `$scrypt$` line from `flyball password` -- and anything else one machine
differs by go in a deployment overlay outside the checkout, laid over the rig file:

```sh
flyball run rig-multi-sensor.yaml ~/humidity-pi.yaml
```

Without a password the front serves this machine only. The book's
[Raspberry Pi setup](https://bengineer42.github.io/humctrl/4-hardware/pi/) has the overlay.

See the [book](https://bengineer42.github.io/humctrl/) for hardware setup, wiring, first run,
and the built-in humidity programs.

## What's here

| | |
| --- | --- |
| `blender.yaml` | the pump blender and its PWM chip, shared by every rig file below (`extends`) |
| `rig-multi-sensor.yaml` / `sim.yaml` | the real rig (three SHT4x), and the overlay that replaces hardware with a simulation |
| `rig-single-sensor.yaml` | the bring-up variant: one SHT4x on the chamber, nothing bound |
| `src/humidity/` | the blender device, its pump drivers, the sim plant, the CLI |
| `programs/` | example rig programs |
| `tunings/` | saved controller tunings |
| `book/` | the docs site, published to [bengineer42.github.io/humctrl](https://bengineer42.github.io/humctrl/) |

## Depends on

Pinned to a flyball commit in `pyproject.toml` (`[tool.uv.sources]`), not a local path — this
repo resolves standalone, it doesn't need to sit nested inside a flyball checkout to build.

## Licence

MIT — see [LICENSE](LICENSE). flyball itself is MIT too.

Contributions are certified by a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off, the same as flyball: add `Signed-off-by:` to each commit, which `git commit -s` does
for you. flyball's `CONTRIBUTING.md` explains what that line means.

Security: please report a vulnerability privately rather than in an issue —
<https://github.com/bengineer42/humctrl/security/advisories/new>. This rig drives pumps, so a
bug that moves an actuator is a safety problem as well as a security one. flyball's
`SECURITY.md` covers the engine, the daemon and the API underneath this repo.
