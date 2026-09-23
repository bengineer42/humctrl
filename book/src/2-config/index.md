# Configuration

!!! abstract "Where you are: Configuration (the humidity rig)"
    For the person **setting the rig up**: the two files that describe it, the real and the simulated, and its tunings.

    | if instead you want to… | go to |
    | --- | --- |
    | operate it | [Running the rig](../1-running/index.md) |
    | understand or change its two devices | [The rig's devices](../3-devices/index.md) |
    | build or repair it | [Hardware](../4-hardware/index.md) |
    | look the package up | [Reference](../5-reference/humidity.md) |
    | anything about flyball itself -- the device model, the UI, the CLI, the API | [the flyball book](https://bengineer42.github.io/flyball/latest/) |

*`rig-multi-sensor.yaml` for this rig, field by field — and `sim.yaml`, the overlay that runs it with no hardware.*

## `rig-multi-sensor.yaml`: the real rig

The rig is two files: `blender.yaml` holds what every humidity rig shares — the
PWM chip and the `dual_pump_blender` device on it — and each rig file `extends`
it, adding its own sensors, the controller, and (here) `bound:` on the blender.
`rig-single-sensor.yaml` is the bring-up variant: one `sht4x` on the chamber,
nothing bound, the blender assuming dry 0 %RH / wet 100 %RH. Shown merged:

```yaml
name: humidity
extends: [blender.yaml]                    # pwm0 and the blender device come from here

links:
  i2c1: { type: i2c, bus: 1 }
  pwm0: { type: pwm, chip: 0 }              # (blender.yaml)

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
      chamber: { signals: { humidity: { warning: [20, 80] } } }
      dry: { poll_s: 5 }     # the supply lines drift slowly; no need to poll them as often
      wet: { poll_s: 5 }

  blender:                                 # (blender.yaml) two pumps blended into one settable humidity
    driver: dual_pump_blender
    label: Pump blender
    poll_s: 1
    config:
      link: pwm0
      dry: { channel: 0, deadband: 0.05, max_flow: 2.0 }   # L/min
      wet: { channel: 1, deadband: 0.05, max_flow: 2.0 }
      blend_flow: 1.0
    bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }   # this file's own addition

controllers:
  blender.humidity:
    measured: hum_sensors.chamber.humidity
    law: { type: PI, kp: 0.8, ki: 0.02, tt: 60 }
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
  `sht4x_set`'s own config (`flyball_chips.sht4x`), not an
  envelope key, so the tree can't be changed by `signals:` overrides, only
  by editing `sensors:` itself.
- **`hum_sensors.signals`** is envelope overrides only: `chamber`'s
  `humidity` gets a warning band, `dry` and `wet` are polled every 5 s instead
  of inheriting the device's `poll_s: 1`.
- **`blender.config`** is the two pump lines (PWM channel, deadband,
  max flow) and the starting `blend_flow`; `link: pwm0` is driven directly
  through `flyball_linux`'s `PwmLink` protocol (`configure`/`enable`) —
  the blender owns both channels itself rather than wrapping a
  `pwm_channel` device each, since the split-range arithmetic is its own.
  A `frequency_hz` field (default 20 000 Hz) is also available in
  `config`, shared by both lines, if `rig-multi-sensor.yaml` needs to override it.
- **`blender.bound`** wires the blender to follow the two supply sensors'
  humidity directly — see [The blender device](../3-devices/blender.md#following-the-supply-lines).
- **`controllers.blender.humidity`** is named by its output (`blender`'s
  `humidity` demand), regulates the chamber's published humidity (its
  `measured:` signal) through a
  PI law, and is `default: true` — the controller a program step or a
  `flyball` command uses when it names none.

Run it: `flyball-runner rig-multi-sensor.yaml` — real hardware, loopback-only by
default. This package has no runner of its own; the generic
`flyball-runner` also picks up a `tunings/` (and a `programs/`) directory
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
    type: sim_humidity_chamber
    volume_l: 3.0                  # litres
    dry_flow_l_per_min: 2.0        # match blender.dry.max_flow below
    wet_flow_l_per_min: 2.0        # match blender.wet.max_flow below
    flow_l_per_min: 1.0            # nominal total, feedforward only
    dry_rh: 10.0                   # %RH, before drift
    wet_rh: 90.0                   # %RH, before drift
    ambient_rh: 45.0               # %RH the chamber leaks towards
    exchange_per_min: 0.01         # fraction of volume/min exchanged with the room
    initial_rh: 40.0               # %RH
    temperature_c: 21.0            # °C, every temperature's steady baseline
    sensor_tau_s: 3.0              # s, the chamber sensor's own lag
    noise_rh: 0.3                  # %RH, Gaussian, chamber reading only
    supply_noise_rh: 0.15          # %RH, Gaussian, dry/wet readings each sample
    supply_drift_rh: 2.0           # ± %RH, slow sinusoidal supply wander (wet: 88-92)
    supply_drift_period_s: 600.0   # s, period of the supply and temperature drift
    temperature_noise_c: 0.05      # °C, Gaussian, every temperature reading
    temperature_drift_c: 0.3       # ± °C, slow sinusoidal drift around temperature_c
    flow_warming_c_per_lpm: 0.1    # °C per L/min: chamber warms a little under flow
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
    driver: dual_pump_blender               # the *real* driver: the chamber stands in for pwm0
    label: Pump blender (simulated)
    poll_s: 1
    config:
      link: chamber
      dry: { channel: 0, deadband: 0.05, max_flow: 2.0 }   # L/min
      wet: { channel: 1, deadband: 0.05, max_flow: 2.0 }   # L/min
      blend_flow: 1.0
    bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }

# blender.humidity -> hum_sensors.chamber.humidity: the same addresses as
# rig-multi-sensor.yaml, so its controllers entry needs no override here.
```

An overlay is a second file passed alongside the first — `flyball-runner
rig-multi-sensor.yaml sim.yaml` — merged later-over-earlier: `null` deletes a key
(here, `i2c1` and `pwm0`, so no real link is built), a new link
(`chamber`, a `sim_humidity_chamber` plant — see [`HumidityChamber`
below](#the-plant-sim_humidity_chamber)) is added, and `hum_sensors` swaps
its real driver for the generic `sim_daq`. `blender` keeps its *real*
driver, `dual_pump_blender`, unmodified: `HumidityChamber` doubles as a
`flyball_linux.links.pwm.PwmLink` (`configure`/`enable`), so the blender
drives it exactly as it would drive `pwm0` -- channel 0 the dry line,
channel 1 the wet line, matching `rig-multi-sensor.yaml`'s `dry.channel`/`wet.channel`.
`dry.max_flow`/`wet.max_flow` above are set equal to the chamber's own
`dry_flow_l_per_min`/`wet_flow_l_per_min`, so what the blender believes it
delivers is what the chamber actually receives.

Every `hum_sensors` address `rig-multi-sensor.yaml` declares is mirrored here, same
unit and access — `sim_daq`'s `ports:` keys may contain a dot
(`chamber.humidity: {...}`), which puts that port in a namespace exactly
as `sht4x_set` does. Because `blender` is the genuine `DualPumpBlender`
class in both files, *every* one of its signals, units, access, limits and
commands match exactly — `dry_flow`/`wet_flow`/`dry_effort`/`wet_effort`/
`blend_flow`/`expected_humidity` are the blender's own bookkeeping (from
its configured `max_flow`s and the supply humidity it observes through
`bound`), not read back from the chamber, precisely as on the real rig. A
program, dashboard or session built against `rig-multi-sensor.yaml` runs unchanged
against the overlay.

## The plant: `sim_humidity_chamber`

`humidity.sim.HumidityChamber` is two things at once:

- a `MultiPlant` — `sim_daq` (`hum_sensors`) reads its six named outputs,
  one per leaf `hum_sensors` declares (`chamber_humidity`,
  `chamber_temperature`, `dry_humidity`, `dry_temperature`,
  `wet_humidity`, `wet_temperature`);
- a `PwmLink` (`configure`/`enable`) — the real `dual_pump_blender` driver
  drives it exactly as it would a hardware PWM chip.

The chamber mixes the dry and wet lines' actual delivered flow (each
line's channel duty times its own `..._flow_l_per_min`) into a chamber of
`volume_l`, settling on the mixing arithmetic (`wet_fraction` 0 rests at
`dry_rh`, 1 at `wet_rh`, in between along that span), diluted further
towards `ambient_rh` at `exchange_per_min`; the chamber's own sensor lags
the true value by `sensor_tau_s`, plus Gaussian noise (`noise_rh`, seeded
by `seed`). The dry and wet supplies drift slowly — a sinusoid of
±`supply_drift_rh` over `supply_drift_period_s`, decorrelated by phase so
they don't move in lockstep — and carry their own reading noise
(`supply_noise_rh`); this drift feeds the *real* physics (the blender
observes it through `bound`, same as a real sensor's drift would), not
just the display. Every temperature drifts the same slow way around
`temperature_c` with its own noise (`temperature_noise_c`), the chamber's
also warming a little under total flow (`flow_warming_c_per_lpm`).

## Tunings

`examples/humidity/tunings/*.yaml` — each file a control law config, named
by its filename stem — are loaded onto `rig.tunings` when
`flyball-runner` starts (`--tunings DIR` to point elsewhere; default
`tunings/` beside the first rig file). This rig ships two:

```yaml
# tunings/gentle.yaml -- the demo's starting law
# Proportional only, on top of the blender's feedforward: settles a little
# short of the setpoint and never oscillates.
type: P
kp: 0.5
```

```yaml
# tunings/brisk.yaml
# PID for the simulated chamber: 1 s samples on a ~6 s lag at full flow, so
# the derivative gain is kept small -- at 8 it drove the pumps bang-bang on
# sensor noise. Integral time 10 s; tracking time 5 s for anti-windup.
type: PID
kp: 0.8
ki: 0.08
kd: 1.0
tt: 5
```

A `regulate` program step's `tuning` names one by its stem —
`{setpoint: 45, tuning: brisk}` swaps `blender.humidity`'s law bumplessly
before aiming — see [Humidity programs](../1-running/programs.md). Neither
file changes `rig-multi-sensor.yaml`'s own `controllers.blender.humidity.law` (the PI
gains the controller starts with); they're alternatives a program or an
operator picks at runtime.

## `--set` and multiple overlays

`--set devices.blender.config.blend_flow=1.5` overrides one value from the
command line, applied after every file. A third file (say, a noisier
plant) would overlay both `rig-multi-sensor.yaml` and `sim.yaml` the same way — later
file wins, in the order given.
