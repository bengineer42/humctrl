# Hardware

!!! abstract "Where you are: Hardware (the humidity rig)"
    For whoever **builds or repairs** the rig: the Pi, the board, the pumps, the parts.

    | if instead you want to… | go to |
    | --- | --- |
    | operate it | [Running the rig](../1-running/index.md) |
    | describe it in a file | [Configuration](../2-config/index.md) |
    | understand or change its two devices | [The rig's devices](../3-devices/index.md) |
    | look the package up | [Reference](../5-reference/humidity.md) |
    | anything about flyball itself -- the device model, the UI, the CLI, the API | [the flyball book](https://bengineer42.github.io/flyball/latest/) |

| page | |
| --- | --- |
| [Raspberry Pi setup](pi.md) | the OS, I²C and PWM enabled, the setup script, installing flyball and this package |
| [Board and wiring](board.md) | what connects to what: the sensors on I²C, the motor driver on the PWM pins, power |
| [Pumps and PWM](pumps.md) | why two DC pumps, how a duty becomes a flow, the dead zone and hysteresis, the driver chip |
| [Bill of materials](bom.md) | the parts |

The physics of blending two flows is [The blender device](../3-devices/blender.md);
what the software expects of the board is the `pwm0` and `i2c1` links in
[Configuration](../2-config/index.md).
