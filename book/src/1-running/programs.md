# Humidity programs

*Running this rig's programs — the general vocabulary, with a worked tour of the rig's own `programs/demo.yaml`.*

There is no humidity-specific program vocabulary: `regulate`, `ramp`,
`hold`, `arrive`, `manual`, `set`, `command` and `wait` are flyball's own
steps, and this rig uses them exactly as any other rig would — naming
`blender.humidity` (the controller) or `blender` (the device) where a
step needs one. Every step below names the controller by its field, which
is still `loop` on the wire even though the concept is a *controller* — a
writable signal has at most one controller, so naming `blender.humidity`
means "the controller driving it". Every example omits `loop` where this
rig has only one controller: the rig's `default: true` controller is
used.

## `programs/demo.yaml`

`flyball-runner` imports every file under `programs/` beside the rig file
on start, so this one is already in the library once the runner is up.
It's a tour of the rig for the simulator — manual pumps, closed-loop
regulation under each of the two stored [tunings](../2-config/index.md#tunings),
ramps paced two different ways, an unwaited ramp caught by `arrive`, and
an operator wait:

```yaml
name: demo
description: >-
  Manual pumps, P then PID regulation, ramps, an unwaited ramp caught by
  arrive, an operator wait.
steps:
  # --- by hand: the controller stays manual, the blender's commands drive the pumps ---
  - command: { device: blender, device_command: set_flows, args: { dry: 2, wet: 2 } }
  - hold: { minutes: 2, message: "2 L/min each way: the chamber heads for the midpoint of the supplies" }
  - command: { device: blender, device_command: set_efforts, args: { dry: 0.1, wet: 0.9 } }
  - hold: { minutes: 2, message: "wet-heavy efforts: humidity climbs" }
  - command: { device: blender, device_command: stop }
  - hold: { minutes: 1, message: "pumps stopped: drifting back towards ambient" }

  # --- closed loop: P first, then PID ---
  - regulate: { setpoint: 60, tuning: gentle }
  - arrive: { within: 1, readings: 5, timeout: { minutes: 8 }, message: "settling at 60 under P" }
  - hold: { minutes: 1, message: "holding 60 under P: expect a steady offset" }
  - regulate: { setpoint: 60, tuning: brisk }
  - hold: { minutes: 2, message: "same setpoint, PID: the integrator closes the offset" }

  # --- ramps: paced by rate, then by duration ---
  - ramp: { to: 30, per_minute: 10 }
  - hold: { minutes: 1, message: "dry soak at 30" }
  - ramp: { to: 75, minutes: 3, wait: false }
  - hold: { minutes: 1, message: "ramp to 75 running unattended" }
  - arrive: { within: 1.5, readings: 3, timeout: { minutes: 8 }, message: "catching the ramp" }
  - hold: { minutes: 1, message: "wet soak at 75" }

  # --- the operator ---
  - wait:
      message: "Kick the chamber if you like (a disturbance, a supply change), then press go"
      timeout: { minutes: 5 }
  - arrive: { within: 1, readings: 5, timeout: { minutes: 8 }, message: "recovering" }

  # --- back to manual, pumps off ---
  - manual: blender.humidity
  - command: { device: blender, device_command: stop }
```

A few things worth noticing:

- **`hold`'s `duration` is the primary field, folded flat**: `hold:
  {minutes: 2, message: "…"}` sets `duration` directly from `minutes`
  (`Duration` takes any of its unit keys — `seconds`, `minutes`, `hours`,
  … — or a bare number of seconds) rather than nesting `duration:
  {minutes: 2}`. `ramp`'s `pace` folds the same way when it's a
  `Duration` (`minutes: 3` alongside `to`) or a `Speed` (`per_minute: 10`)
  — either reads directly onto the one pace field.
- **`arrive`'s `timeout` doesn't fold**, because `loop` — not `timeout` —
  is `arrive`'s primary field, so `timeout: {minutes: 8}` stays nested.
- **`manual: blender.humidity`** is the bare-scalar form: `manual`'s only
  field, `loop`, is primary, so naming just a controller (with nothing
  else to set) can be the step's whole value, the same way `wait:
  "message"` is short for `wait: {message: "message"}`.
- **`command`'s `device_command`**, not `command` — the step's own tag
  already uses that word — calls one of `blender`'s own commands exactly
  as `POST /api/devices/blender/commands/{tag}` would. `flows.dry`/`wet`
  and `efforts.dry`/`wet` are readbacks, not writable signals, so driving
  them by hand goes through `set_flows`/`set_efforts` (a `command` step),
  not `set` — a `set` step only reaches a device's writable (`[RPW]`)
  signals.
- Every `tuning: gentle`/`tuning: brisk` swaps `blender.humidity`'s law
  bumplessly before aiming, by the stem of a file under `tunings/`.

## Running it

```
flyball program check programs/demo.yaml           # validates against the live rig: names a missing tuning or controller
flyball program run programs/demo.yaml
flyball program status
flyball program stop                                # interrupt
```

`flyball program run FILE [--interrupt]` sends the file's document to
`POST /api/programs/run`; the rig normalises it and applies the first
step before answering. `--interrupt` stops whatever is already running
first — without it, a program already running refuses a second. At real
time this program takes roughly 25 rig-minutes; `flyball-runner rig-multi-sensor.yaml
sim.yaml --set clock.speed=6` runs the simulated clock at 6×, about four
minutes of wall time, to watch the whole tour.
