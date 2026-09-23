# Devices and signals

!!! abstract "Where you are: The rig's devices (the humidity rig)"
    For the **developer**: the two devices this rig adds to flyball, what they declare and how they work inside.

    | if instead you want to… | go to |
    | --- | --- |
    | operate it | [Running the rig](../1-running/index.md) |
    | describe it in a file | [Configuration](../2-config/index.md) |
    | build or repair it | [Hardware](../4-hardware/index.md) |
    | look the package up | [Reference](../5-reference/humidity.md) |
    | anything about flyball itself -- the device model, the UI, the CLI, the API | [the flyball book](https://bengineer42.github.io/flyball/latest/) |

*Which devices this rig declares, which quantities each signal carries, and which signal the controller drives.*

The rig has two devices — everything named in `rig-multi-sensor.yaml` is one of these two,
or the controller that binds them:

| device | driver | what it is |
| --- | --- | --- |
| `hum_sensors` | `sht4x_set` | three SHT4x sensors, each its own namespace |
| `blender` | `dual_pump_blender` | two PWM pumps, blended to a target humidity |

## `hum_sensors`

One `sht4x_set` device, one namespace per sensor: `chamber` (the process
value), `dry` and `wet` (the two supply lines). Each namespace is read in
its own I²C transaction, and each declares the same two signals:

| signal | access | quantity | unit |
| --- | --- | --- | --- |
| `humidity` | `[RP]` | humidity | %RH |
| `temperature` | `[RP]` | temperature | °C |

So the full address list is `hum_sensors.chamber.humidity`,
`hum_sensors.chamber.temperature`, `hum_sensors.dry.humidity`, …,
`hum_sensors.wet.temperature` — six signals, three atomic namespaces. The
rig file polls `chamber` every second and the two supply lines every five
(`rig-multi-sensor.yaml`'s `signals:` overrides, since the lines drift slowly and don't
need the chamber's rate).

## `blender`

One `dual_pump_blender` device, no namespaces — every signal is on its
root. `rig-multi-sensor.yaml`'s own comment gives the tree the driver declares:

```yaml
    # signals the driver declares (no poll: it pushes from commit and its commands):
    #   humidity                 [RPW]  %RH, limits [0, 100]  the split-range target
    #   flows.dry, flows.wet     [RP]   L/min                 readbacks; set only by set_flows
    #   efforts.dry, efforts.wet [RP]   0-1 of full            readbacks; set only by set_efforts
    #   blend.flow                [RP]  L/min                  a setting: re-set only by set_blend
    #   blend.wet_fraction        [RP]  0-1                    readback; set only by set_fraction
    #   expected_humidity         [RP]  %RH                    what the lines actually deliver
```

`humidity` is the only signal a controller may drive — it's the split-range
demand, the controller's output, and `blender.humidity` is exactly that
controller's name. The rest
are readbacks, `[RP]` not `[RPW]`: a direct demand on `flows.dry`,
`efforts.wet`, `blend.wet_fraction` or `blend.flow` is refused ("not
writable") rather than silently dropped, because `commit` only ever reads
`humidity`'s pending value. Drive the lines through `set_flows`,
`set_efforts`, `set_fraction` or `set_blend` instead — see [The blender
device](blender.md). `expected_humidity` is what `commit` computes the
blend should be delivering, published alongside the readbacks.

`blender.bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }`
means the blender *follows* the two supply sensors: whenever either
publishes, `blender.observe` records the new supply humidity, ready for the
next `commit` — no bus poll of its own for that half of the picture.

## The controller

One controller, `blender.humidity`, named by the demand it drives (its output):

```yaml
controllers:
  blender.humidity:
    measured: hum_sensors.chamber.humidity
    law: { tag: PI, kp: 0.8, ki: 0.02, tt: 60 }
    default: true
```

It regulates the chamber's published humidity (its `measured` signal,
`[P]`) by writing the blender's `humidity` demand (its output, `[RPW]`)
through a PI law, and is the rig's
default — the one a program step or a `flyball` command uses when it names
no controller at all.
