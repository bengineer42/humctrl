# First run

*Start the daemon, confirm the sensor reads, drive a pump by hand, set a setpoint, stop.*

This walks through `sim.yaml` — no hardware needed — but every command
below is identical against real hardware (`flyball-daemon rig.yaml`
alone); only the addresses and the daemon's boot line change.

## Start the daemon

This rig has no daemon of its own — `flyball-daemon` (from `flyball`
itself) serves it directly:

```
cd examples/humidity
uv run flyball-daemon rig.yaml sim.yaml
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
signal's latest value, the controller, and anything waiting. This
package's own thin client works the same way against its own API:

```
humidity status
```

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

`dry_flow` and `wet_flow` are a `together` pair — a demand naming one
alone is refused, so a single-signal `flyball demand` won't do; put both
in one demand instead. This package's `humidity` client wraps exactly
that call (`PUT /api/devices/{name}/demand`):

```
humidity set blender dry_flow=0.2 wet_flow=0.2
```

`blend_flow`, by contrast, isn't `together` with anything, so it takes a
plain single-signal demand:

```
flyball demand blender.blend_flow 1.2
```

## Set a target humidity

`blender.humidity` is driven by the `blender.humidity` controller, marked
`default: true` in `rig.yaml` — so `humidity` itself refuses a direct
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

`setpoint: 45` means 45 %RH; `loop` is omitted, so it aims the rig's
default controller. `flyball status` (or `flyball watch controllers`)
shows the chamber humidity moving towards it.

## Stop

```
humidity stop
```

calls the blender's `stop` command directly (`POST
/api/devices/blender/commands/stop`) — both pumps off at once, bypassing whatever
demand is pending. This does **not** touch the controller's mode: it's
still regulating, and will move the pumps again on its next tick. To stop
regulating as well, put the controller in manual with a program step
(`- manual: {}`) or `POST /api/controllers/blender.humidity/manual`.
