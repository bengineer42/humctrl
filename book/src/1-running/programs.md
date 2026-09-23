# Humidity programs

*Running this rig's programs — the general vocabulary, with a worked tour of the rig's own `programs/demo.yaml`.*

There is no humidity-specific program vocabulary: `regulate`, `ramp`,
`wait`, `settle`, `manual`, `set`, `command` and `prompt` are flyball's own
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
ramps paced two different ways, an unwaited ramp caught by `settle`, and
an operator prompt:

```yaml
name: demo
description: >-
  Manual pumps, P then PID regulation, ramps, an unwaited ramp caught by
  settle, an operator prompt.
steps:
  # --- by hand: the controller stays manual, the blender's commands drive the pumps ---
  - command: { device: blender, device_command: set_flows, args: { dry: 2, wet: 2 } }
  - wait: { duration: { minutes: 2 }, message: "2 L/min each way: the chamber heads for the midpoint of the supplies" }
  - command: { device: blender, device_command: set_efforts, args: { dry: 0.1, wet: 0.9 } }
  - wait: { duration: { minutes: 2 }, message: "wet-heavy efforts: humidity climbs" }
  - command: { device: blender, device_command: stop }
  - wait: { duration: { minutes: 1 }, message: "pumps stopped: drifting back towards ambient" }

  # --- closed loop: P first, then PID ---
  - regulate: { setpoint: 60, tuning: gentle }
  - settle: { within: 1, count: 5, timeout: { minutes: 8 }, message: "settling at 60 under P" }
  - wait: { duration: { minutes: 1 }, message: "holding 60 under P: expect a steady offset" }
  - regulate: { setpoint: 60, tuning: brisk }
  - wait: { duration: { minutes: 2 }, message: "same setpoint, PID: the integrator closes the offset" }

  # --- ramps: paced by rate, then by duration ---
  - ramp: { to: 30, per_minute: 10 }
  - wait: { duration: { minutes: 1 }, message: "dry soak at 30" }
  - ramp: { to: 75, minutes: 3, wait: false }
  - wait: { duration: { minutes: 1 }, message: "ramp to 75 running unattended" }
  - settle: { within: 1.5, count: 3, timeout: { minutes: 8 }, message: "catching the ramp" }
  - wait: { duration: { minutes: 1 }, message: "wet soak at 75" }

  # --- the operator ---
  - prompt:
      message: "Kick the chamber if you like (a disturbance, a supply change), then press go"
      timeout: { minutes: 5 }
  - settle: { within: 1, count: 5, timeout: { minutes: 8 }, message: "recovering" }

  # --- back to manual, pumps off ---
  - manual: blender.humidity
  - command: { device: blender, device_command: stop }
```

A few things worth noticing:

- **`wait`'s `duration` folds flat only when the step has no `message`.**
  Bare, `wait: {minutes: 2}` (or plain `wait: 120`) sets `duration`
  directly from `minutes` (`Duration` takes any of its unit keys —
  `seconds`, `minutes`, `hours`, … — or a bare number of seconds). The
  demo's waits all carry a `message`, so each writes `duration:` out
  explicitly instead: `wait: {duration: {minutes: 2}, message: "…"}`.
  `wait: {minutes: 2, message: "…"}` is refused — "a prompt is now
  `prompt:`; for a timed wait with a message write `duration:`
  explicitly" — because that flat spelling is exactly what an old
  operator prompt with a flat timeout used to look like. `ramp`'s `pace`
  has no such exception: it folds the same way whether or not other
  arguments are given (`minutes: 3` alongside `to`, or a `Speed`
  `per_minute: 10`) — either reads directly onto the one pace field.
- **`timeout` never folds, in any step.** It is always written nested,
  `timeout: {minutes: 8}`, whatever the step's own primary field is: it is
  set aside before folding is even considered. `wait`'s `duration` is the
  one time field left once `timeout` is set aside (and `message` is
  absent); `settle` has no other foldable time field at all — `loop` is
  its primary — so its `timeout` stays nested the same way `prompt`'s
  does.
- **`manual: blender.humidity`** is the bare-scalar form: `manual`'s only
  field, `loop`, is primary, so naming just a controller (with nothing
  else to set) can be the step's whole value, the same way `prompt:
  "message"` is short for `prompt: {message: "message"}`.
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
