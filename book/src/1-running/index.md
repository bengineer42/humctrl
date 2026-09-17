# Running the rig

!!! abstract "Where you are: Running the rig (the humidity rig)"
    For the **operator**: the rig is built and wired; you start it, drive it, run its programs, tune it, and know what its failures look like.

    | if instead you want to… | go to |
    | --- | --- |
    | describe it in a file | [Configuration](../2-config/index.md) |
    | understand or change its two devices | [The rig's devices](../3-devices/index.md) |
    | build or repair it | [Hardware](../4-hardware/index.md) |
    | look the package up | [Reference](../5-reference/humidity.md) |
    | anything about flyball itself -- the device model, the UI, the CLI, the API | [the flyball book](https://bengineer42.github.io/flyball/latest/) |

| page | |
| --- | --- |
| [First run](first-run.md) | start the daemon, confirm the rig loaded, read a signal, drive a pump, set a target, stop |
| [Humidity programs](programs.md) | the rig's own `programs/demo.yaml`, step by step |
| [Autotune on this rig](autotune.md) | a relay test on `blender.humidity` and what it writes |
| [Failure modes](failures.md) | what a stuck pump, a dead sensor or a railed blend look like, and what to do |
| [Limits](limits.md) | the achievable humidity range and flows, and where each limit comes from |

Everything general -- the UI's pages, the CLI's commands, programs, sessions
-- is the flyball book's [Running a rig](https://bengineer42.github.io/flyball/latest/1-running/); this part only
says what is particular here.
