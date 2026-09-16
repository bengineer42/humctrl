# Configuration

*`rig.yaml` for this rig, field by field — and `sim.yaml`, the overlay that runs it with no hardware.*

## `rig.yaml`: the real rig

```yaml
name: humidity

links:
  i2c1: { tag: i2c, bus: 1 }
  pwm0: { tag: pwm, chip: 0 }

devices:
  hum_sensors:                             # hum_sensors.chamber/dry/wet .humidity/.temperature [RP]
    driver: sht4x_set
    label: Humidity sensors
    poll_s: 1
    config:
      link: i2c1
      sensors:
        chamber: { address: 0x44 }
        dry: { address: 0x45 }
        wet: { address: 0x46 }
    signals:
      chamber: { signals: { humidity: { warn: [20, 80] } } }
      dry: { poll_s: 5 }     # the supply lines drift slowly; no need to poll them as often
      wet: { poll_s: 5 }

  blender:                                 # two pumps blended into one settable humidity
    driver: dual_pump_blender
    label: Pump blender
    poll_s: 1
    config:
      link: pwm0
      dry: { channel: 0, deadband: 0.05, max_flow: 2.0 }   # L/min
      wet: { channel: 1, deadband: 0.05, max_flow: 2.0 }
      blend_flow: 1.0
    bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }

controllers:
  blender.humidity:
    signal: hum_sensors.chamber.humidity
    law: { tag: PI, kp: 0.8, ki: 0.02, tt: 60 }
    default: true
```

Two links, two devices, one controller. `links` are transports, not
devices, and neither is this package's own any more — both `i2c` and `pwm`
are `flyball-linux` links (`examples/humidity/pyproject.toml` depends on
`flyball-linux[i2c]`; this rig has no hardware driver of its own left,
only `dual_pump_blender`'s split-range arithmetic and
`sim_humidity_chamber`'s plant). `i2c1` is the Pi's I²C bus 1
(`flyball_linux.links.i2c.I2cConfig`, `/dev/i2c-1` through `smbus2`);
`pwm0` is PWM chip 0 (`flyball_linux.links.pwm.PwmConfig`, sysfs
`/sys/class/pwm/pwmchip0`, no extra library). Field by field:

- **`hum_sensors.config.sensors`** declares the device's tree — one
  namespace per key, each an SHT4x at that I²C address. This is
  `sht4x_set`'s own config (`flyball_linux.devices.chips.sht4x`), not an
  envelope key, so the tree can't be changed by `signals:` overrides, only
  by editing `sensors:` itself.
- **`hum_sensors.signals`** is envelope overrides only: `chamber`'s
  `humidity` gets a warn band, `dry` and `wet` are polled every 5 s instead
  of inheriting the device's `poll_s: 1`.
- **`blender.config`** is the two pump lines (PWM channel, deadband,
  max flow) and the starting `blend_flow`; `link: pwm0` is driven directly
  through `flyball_linux`'s `PwmLink` protocol (`configure`/`enable`) —
  the blender owns both channels itself rather than wrapping a
  `pwm_channel` device each, since the split-range arithmetic is its own.
  A `frequency_hz` field (default 20 000 Hz) is also available in
  `config`, shared by both lines, if `rig.yaml` needs to override it.
- **`blender.bound`** wires the blender to follow the two supply sensors'
  humidity directly — see [The blender device](dual-pumps.md#following-the-supply-lines).
- **`controllers.blender.humidity`** is named by its target (`blender`'s
  `humidity` signal), regulates the chamber's published humidity through a
  PI law, and is `default: true` — the controller a program step or a
  `flyball` command uses when it names none.

Run it: `flyball-daemon rig.yaml` — real hardware, loopback-only by
default. This package has no daemon of its own; the generic
`flyball-daemon` also picks up a `tunings/` (and a `programs/`) directory
beside the rig file automatically — see [Configuration: tunings](#tunings)
below.

## `sim.yaml`: the same addresses, no hardware

```yaml
name: humidity-sim
clock: { speed: 1 }

links:
  i2c1: null
  pwm0: null
  chamber:
    tag: sim_humidity_chamber
    dry: 10.0
    wet: 90.0
    tau_s: 45.0
    initial: 40.0
    temperature: 21.0
    noise: 0.3
    seed: 7

devices:
  hum_sensors:
    driver: sim_daq
    label: Humidity sensors (simulated)
    poll_s: 1
    config:
      link: chamber
      ports:
        chamber.humidity: { port: chamber_humidity, quantity: humidity, unit: "%RH" }
        chamber.temperature: { port: chamber_temperature, quantity: temperature, unit: "°C" }
        dry.humidity: { port: dry_humidity, quantity: humidity, unit: "%RH" }
        dry.temperature: { port: dry_temperature, quantity: temperature, unit: "°C" }
        wet.humidity: { port: wet_humidity, quantity: humidity, unit: "%RH" }
        wet.temperature: { port: wet_temperature, quantity: temperature, unit: "°C" }

  blender:
    driver: sim_drive
    label: Pump blender (simulated)
    bound: null                            # sim_drive follows nothing; the plant is driven directly
    config:
      link: chamber
      ports:
        humidity: { port: wet_fraction, quantity: humidity, unit: "%RH", limits: [0, 100] }

# blender.humidity -> hum_sensors.chamber.humidity: the same addresses as
# rig.yaml, so its controllers entry needs no override here.
```

An overlay is a second file passed alongside the first — `flyball-daemon
rig.yaml sim.yaml` — merged later-over-earlier: `null` deletes a key
(here, `i2c1` and `pwm0`, so no real link is built), a new link
(`chamber`, a `sim_humidity_chamber` plant — see [`HumidityChamber`
below](#the-plant-sim_humidity_chamber)) is added, and `hum_sensors` /
`blender` swap their real drivers for the generic `sim_daq` / `sim_drive`,
both pointed at the same plant so the chamber and the pumps interact.

Every address `rig.yaml` declares under `hum_sensors` is mirrored here,
same unit and access — `sim_daq`'s `ports:` keys may contain a dot
(`chamber.humidity: {...}`), which puts that port in a namespace exactly
as `sht4x_set` does. `blender` is reduced to its one `[W]` target,
`humidity`: `sim_drive` has no pump arithmetic to read back, so the manual
`dry_flow`/`wet_flow`/`dry_effort`/`wet_effort`/`blend_flow`/
`expected_humidity` signals have no analogue and are omitted. A program,
dashboard or session built against `rig.yaml` runs unchanged against the
overlay — it never demands the omitted signals.

## The plant: `sim_humidity_chamber`

`humidity.sim.HumidityChamber`, a `MultiPlant`: one input
(`wet_fraction`, 0 dry to 1 wet of the blend), six named outputs — one per
leaf `hum_sensors` declares (`chamber_humidity`, `chamber_temperature`,
`dry_humidity`, `dry_temperature`, `wet_humidity`, `wet_temperature`). The
chamber humidity settles towards the blend's expected value on a
first-order lag (time constant `tau_s`); the two supply lines and every
temperature are constants — there's no thermal model behind them. Gaussian
noise (`noise`, seeded by `seed` for repeatable tests) perturbs only the
chamber reading.

## Tunings

`examples/humidity/tunings/*.yaml` — each file a control law config, named
by its filename stem — are loaded onto `rig.tunings` when
`flyball-daemon` starts (`--tunings DIR` to point elsewhere; default
`tunings/` beside the first rig file). This rig ships two:

```yaml
# tunings/gentle.yaml -- the demo's starting law
# Proportional only, on top of the blender's feedforward: settles a little
# short of the setpoint and never oscillates.
tag: P
kp: 0.5
```

```yaml
# tunings/brisk.yaml
# PID for the simulated chamber: 1 s samples on a ~6 s lag at full flow, so
# the derivative gain is kept small -- at 8 it drove the pumps bang-bang on
# sensor noise. Integral time 10 s; tracking time 5 s for anti-windup.
tag: PID
kp: 0.8
ki: 0.08
kd: 1.0
tt: 5
```

A `regulate` program step's `tuning` names one by its stem —
`{setpoint: 45, tuning: brisk}` swaps `blender.humidity`'s law bumplessly
before aiming — see [Humidity programs](../operating/programs.md). Neither
file changes `rig.yaml`'s own `controllers.blender.humidity.law` (the PI
gains the controller starts with); they're alternatives a program or an
operator picks at runtime.

## `--set` and multiple overlays

`--set devices.blender.config.blend_flow=1.5` overrides one value from the
command line, applied after every file. A third file (say, a noisier
plant) would overlay both `rig.yaml` and `sim.yaml` the same way — later
file wins, in the order given.
