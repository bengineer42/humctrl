# The humidity rig

A chamber held at a target relative humidity by blending the output of two
pumps — one wet, one dry — under closed-loop control. Flyball's device
model is the substrate; this book covers only what is specific to this
rig: the hardware, its two devices, and how to operate it.

| You want to… | Read |
| --- | --- |
| build or repair the board | **Hardware** |
| understand what the software sees and drives | **The rig** |
| run it | **Operating** |

Anything about the device model, the control loop or the CLI in general is
in the flyball book, not here.

## The rig, in one sentence

Three SHT4x humidity/temperature
sensors on one I²C bus (`hum_sensors`, driver `sht4x_set`) and two PWM-driven
pumps blended by one driver (`blender`, driver `dual_pump_blender`); a PI
controller (`blender.humidity`) regulates the chamber's humidity
(`hum_sensors.chamber.humidity`) by moving the blend. `rig.yaml` is the real
rig; `sim.yaml` overlays it with no hardware attached, for development and
for this book's examples. Both are quoted in full in
[Configuration](rig/config.md).
