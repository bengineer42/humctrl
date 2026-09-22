# The humidity rig

A chamber held at a target relative humidity by blending the output of two
pumps -- one wet, one dry -- under closed-loop control, on a Raspberry Pi.
It is an application built *on* flyball: this book covers only what is
particular to this rig -- its hardware, its two devices, its files and how
to operate it. Everything general -- the device model, the UI, the CLI, the
API, programs, sessions -- is [the flyball book](https://bengineer42.github.io/flyball/latest/), and this book
links there rather than repeating it.

## Getting started

**I'm operating the rig.** It is built and wired; someone has started the
daemon, or [First run](1-running/first-run.md) shows how. Then the
[UI](https://bengineer42.github.io/flyball/latest/1-running/ui/) or the [CLI](https://bengineer42.github.io/flyball/latest/1-running/cli/) as for any
rig; what is particular here is in [Running the rig](1-running/index.md):
the demo program, autotuning the blend, what its failures look like, its
limits.

**I'm building or repairing it.** [Hardware](4-hardware/index.md): the Pi,
the wiring, the pumps and the driver, the parts.

**I'm changing it.** The files are [Configuration](2-config/index.md); the
two devices it adds to flyball are [The rig's devices](3-devices/index.md).

## The rig, in one sentence

Three SHT4x humidity/temperature sensors on one I²C bus (`hum_sensors`,
driver `sht4x_set`) and two PWM-driven pumps blended by one driver
(`blender`, driver `dual_pump_blender`); a PI controller
(`blender.humidity`) regulates the chamber's humidity
(`hum_sensors.chamber.humidity`) by moving the blend. `rig-multi-sensor.yaml` is the real
rig; `sim.yaml` overlays it with no hardware attached, for development and
for this book's examples. Both are quoted in full in
[Configuration](2-config/index.md).

## Where things are

| | |
| --- | --- |
| this book | `examples/humidity/book/` -- published beside the flyball book at `/humidity/` |
| the package | `examples/humidity/src/humidity/`: the blender (`blender.py`), the simulated chamber (`sim.py`), units, a small CLI |
| the files | `examples/humidity/rig-multi-sensor.yaml`, `sim.yaml`, `programs/`, `tunings/` |
| the drivers it relies on | `sht4x_set` from `flyball-chips`, `i2c`/`pwm` from `flyball-linux` -- [Raspberry Pi and Linux buses](https://bengineer42.github.io/flyball/latest/5-integrations/linux/) |
