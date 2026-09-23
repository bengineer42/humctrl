# First run

*Start the runner, confirm the sensor reads, drive a pump by hand, set a setpoint, stop.*

This walks through `sim.yaml` — no hardware needed — but every command
below is identical against real hardware (`flyball-runner rig-multi-sensor.yaml`
alone); only the addresses and the runner's boot line change.

## Start the runner

This rig has no runner of its own — `flyball-runner` (from `flyball`
itself) serves it directly:

```
cd examples/humidity
uv run flyball-runner rig-multi-sensor.yaml sim.yaml
```

It binds to `127.0.0.1:8000` by default — loopback only — and, beside
loading the rig, picks up `tunings/` next to the first rig file
automatically: `tunings/brisk.yaml` and `tunings/gentle.yaml` are loaded
onto `rig.tunings` as `"brisk"` and `"gentle"`, two alternative laws for
`blender.humidity` a `regulate` step can name (see [Humidity
programs](programs.md)). A `programs/` directory beside the rig file
would be imported the same way; this rig doesn't have one yet.

## Confirm the rig loaded

```
flyball devices
```

lists `hum_sensors` and `blender`. `flyball status` shows more: every
signal's latest value, the controller, and anything waiting.

## Read a signal

```
flyball read hum_sensors.chamber.humidity
flyball read hum_sensors.dry --fresh    # a namespace: one sample, forced fresh
flyball read hum_sensors               # a device: one sample per sensor
```

`GET /api/read/{address}` underneath — a signal gives a `Reading`, a
namespace gives a `Sample`, a device gives one `Sample` per namespace it
reads in its own transaction. `--fresh` forces a new hardware read instead
of the last polled value.

## Drive a pump by hand

`flows.dry`/`flows.wet` are readbacks, not directly writable — `flyball
demand blender.flows.dry 0.2` is refused (409). Drive both lines at once
with the `set_flows` command instead:

```
flyball invoke blender set_flows dry=0.2 wet=0.2
```

or by effort, 0–1 of full, with `set_efforts`:

```
flyball invoke blender set_efforts dry=0.3 wet=0.3
```

## Set a humidity setpoint

`blender.humidity` is driven by the `blender.humidity` controller, marked
`default: true` in `rig-multi-sensor.yaml` — so `humidity` itself refuses a direct
demand while the controller owns it (409: a signal a controller drives).
Aim the controller instead. The simplest way is a one-step program (see
[Humidity programs](programs.md) for the full vocabulary):

```yaml
# aim.yaml
name: aim
steps:
  - regulate: {setpoint: 45}
```

```
flyball program run aim.yaml
```

`setpoint: 45` means 45 %RH; `controllers` is omitted, so it aims the rig's
default controller. `flyball status` (or `flyball watch controllers`)
shows the chamber humidity moving towards it.

## Stop

```
flyball invoke blender stop
```

calls the blender's `stop` command directly (`POST
/api/devices/blender/commands/stop`) — both pumps off at once, bypassing whatever
demand is pending. This does **not** touch the controller's mode: it's
still regulating, and will move the pumps again on its next tick. To stop
regulating as well, put the controller in manual with a program step
(`- manual: {}`) or `POST /api/controllers/blender.humidity/manual`.
