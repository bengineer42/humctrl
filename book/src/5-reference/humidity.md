# `humidity`

!!! abstract "Where you are: Reference (the humidity rig)"
    The `humidity` package's API, from its docstrings.

    | if instead you want to… | go to |
    | --- | --- |
    | operate it | [Running the rig](../1-running/index.md) |
    | describe it in a file | [Configuration](../2-config/index.md) |
    | understand or change its two devices | [The rig's devices](../3-devices/index.md) |
    | build or repair it | [Hardware](../4-hardware/index.md) |
    | anything about flyball itself -- the device model, the UI, the CLI, the API | [the flyball book](https://bengineer42.github.io/flyball/latest/) |

*API reference, generated from the source. This package now carries only its own arithmetic and glue — the hardware drivers live in two dependencies (`sht4x`/`sht4x_set` in `flyball-chips`, `i2c`/`pwm` in `flyball-linux`), and the runner is the generic `flyball-runner`; none of that is documented here.*

::: humidity
    options:
      members: false
      show_root_heading: false

::: humidity.blender

::: humidity.cli

::: humidity.sim

::: humidity.units
