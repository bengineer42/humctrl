# Failure modes

*What goes wrong, how it shows up, and what to do.*

Nothing here is humidity-specific machinery — it's flyball's generic
device offline/condition handling, and this rig's own error types, read
together.

## A sensor read fails: the device goes offline

`hum_sensors` is polled on a period (`poll_s`); an exception escaping
`Sht4xSet.read` -- a bad CRC or a short reply (`HardwareError`, raised
directly by `decode` in `flyball_chips.sht4x`), or an I2C bus error under
it -- counts toward the device's failure budget (`reads.fail_after`, 3 by
default). Below it polling carries on at its period; at it the device holds
`offline` (severity `error`, the exception's message), every humidity and
temperature it has delivered reads `stale` (reason `device_offline`) at
once, and the runtime retries with backoff until a read succeeds, which
clears `offline`. `POST /api/devices/hum_sensors/restart` reads it again
without waiting out the backoff.

Because `hum_sensors` is three atomic namespaces read in one `read`, a CRC
failure on `wet` alone counts against the *whole device* -- `read` raising
at all is what the polling loop sees, whichever namespace was
mid-transaction.

## A sensor that stops reporting, or a read that hangs

A signal that stops arriving without an error -- a namespace the driver
leaves out, a device that returns nothing -- goes `stale` at its
threshold, `max(3 × poll_s, 5 s)`: 5 s for the chamber (`poll_s: 1`),
15 s for the supplies (`poll_s: 5`). The rig pushes it on its own clock
(reason `last_read` when the device still delivers its other signals,
`silent` when it delivers nothing, `never_read` if it was never read), so
the chart breaks there, `blender.humidity` freezes on a stale chamber
reading, and a stale supply is `supply_unknown`
([the blender](../3-devices/blender.md#a-supply-sensor-with-no-value)). A
poll whose read is still in flight after 5 s is stuck in its driver: the
device holds `hung`, and what it read is `stale` (reason `device_hung`)
until the read returns.

## A read that succeeds but is slow

If a device's `read` takes longer than its own `poll_s` three reads in a
row, the same `_read` raises a `slow` condition (severity `warning`)
instead — the device keeps polling, but something (a stretched I2C
transaction, system load) is eating into the margin. Only the `read` itself
is timed, and the condition clears after five reads in a row at or under
0.8 of the period, so a read that is slow now and then raises nothing. The
device's run (`GET /api/devices/hum_sensors`) shows the last read's
duration (`read_s`) and how many reads have overrun (`missed`). Worth watching if `hum_sensors`' `poll_s:
1` starts showing this: the fixed conversion wait (§[the sensor
device](../3-devices/sht4x.md#one-i2c-transaction-command-to-decode), ~8.3 ms
at high precision) is a small fraction of a second, so a slow condition
here points at the bus or the host, not the sensor's own timing.

## A demand is refused

These are refused before anything reaches the blender — a 409 from the
API, or a raised `ConflictError` from a script — and change nothing:

- **A direct demand on a readback.** `flows.dry`/`flows.wet`,
  `efforts.dry`/`efforts.wet` and `blend.wet_fraction` are readbacks
  (`[RP]`), not writable directly — "`'…flows.dry' [rp] is not
  writable: it is a readback, moved by the command 'set_flows' (it puts a
  regulating controller in manual)`". Drive them through the command it
  names (`set_flows`/`set_efforts`/`set_fraction`). See [The blender
  device](../3-devices/blender.md).
- **`set_blend` while a controller regulates `humidity`** — "`is driven by
  controller 'blender.humidity': 'set_blend' would fight it`". Put the
  controller in manual first.
- **A demand on a signal a controller drives.** `blender.humidity` while
  `blender.humidity` (the controller) is regulating — put it in `manual`
  first (a program step, or `POST /api/controllers/blender.humidity/manual`).

## The blend can't reach the target

Not a failure the rig refuses — `commit` still runs, still writes the
pumps — but the result isn't what was asked for:

- **The target is outside `[dry, wet]`**: `calculate_wet_fraction` rails
  to the nearer supply end rather than raising, and the write state for
  `humidity` reports `at_limit: "low"` or `"high"`. Watch
  `expected_humidity` against the demanded target — a persistent gap
  between them, with `at_limit` set, means the supply lines themselves
  need attention (the dry line isn't dry enough, or the wet line isn't
  wet enough), not the controller.
- **The requested flow exceeds what the blend can deliver**:
  `humidity.pumps.errors.FlowsOverdrivenError` — raised by
  `DualPumps.set_flows`/`validate_flows` for a `set_flows` command that
  exceeds a line's `max_flow`, and by `set_blend`/`set_humidity`/`set_fraction`
  with an `Absolute(…, raise)` blend flow the mix cannot move (the
  controller, if any, is left regulating). A `humidity` demand's blend
  always scales, even with `raise`, so `commit`'s own split-range path
  derates to what's achievable rather than raising — see
  [Limits](limits.md#flow).
- **The two supply humidities aren't in order**:
  `humidity.blender.SupplyHumiditiesError` — `wet` must read (or be
  configured) greater than `dry`, or there is no span to blend across.
  Raised at `commit` time if the supplies' values cross -- numbers given
  the wrong way round in `inputs`, or bound sensors' readings that cross (the wet line reading drier than the
  dry line, say, or a swapped I2C address).

## A pump write fails

A blender `commit` that raises (a PWM write the kernel refuses) holds
`write_failed` on the blender until a commit succeeds, and its echo
demands -- `humidity` and the rest -- read `stale` (reason `write_failed`)
meanwhile. The demand it carried is kept, not dropped: it goes out with the
next commit, and the rig retries on its own clock, first after 5 s (the
blender has no `poll_s`) and then doubling up to 60 s, each re-send a
`resent` event, until it is older than `retry_max_age_s` (60 s), when it is
dropped with a `write_dropped` event.

## The pumps themselves fail

`humidity.pumps.errors.PumpHardwareError` wraps whatever the underlying
`PwmLink` raises on `configure`/`enable` — a real sysfs write can fail if
the PWM overlay isn't enabled, or the chip's gone away. `stop()`
(`PumpPair.stop`, called by the blender's `stop` command) tries both
lines even if one fails, and raises a `PumpErrorGroup` — both individual
errors, not just the first — if either does.

## Recovering

An offline device is read again on its backoff, and `POST
/api/devices/{name}/restart` reads it at once. Building the blender always disables both
PWM channels first — `PwmPump.__init__` calls `link.enable(channel,
False)` before anything else — so a fresh `flyball-runner` start never
inherits a pump left running by a process that died mid-write; it's a
property of the driver's own construction, not something the runner has
to arrange.
