# Failure modes

*What goes wrong, how it shows up, and what to do.*

Nothing here is humidity-specific machinery — it's flyball's generic
device offline/condition handling, and this rig's own error types, read
together.

## A sensor read fails: the device goes offline

`hum_sensors` is polled on a period (`poll_s`); if any exception escapes
`Sht4xSet.read` — a bad CRC or a short reply
(`HardwareError`, raised directly by `decode` in
`flyball_linux.devices.chips.sht4x`), or an I2C bus error under it — the
runtime's polling loop (`flyball.runtime.polling.Polling._read`) doesn't
retry: it records an `offline` `Condition` (`Level.ERROR`, the exception's
message) against the device, stops polling it, and emits an event. The
device stays offline — no more reads, no more samples, nothing publishing
— until explicitly restarted (`POST /api/devices/hum_sensors/restart`),
which clears the condition and resumes polling on the same period.

Because `hum_sensors` is three atomic namespaces read independently, a
CRC failure on `wet` alone still takes the *whole device* offline, not
just that namespace — `read` raising at all is what the polling loop
sees, whichever namespace was mid-transaction.

## A read that succeeds but is slow

If a device's `read` takes longer than its own `poll_s`, the same
`_read` records a `slow` `Condition` (`Level.WARNING`) instead — the
device keeps polling, but something (a stretched I2C transaction, system
load) is eating into the margin. Worth watching if `hum_sensors`' `poll_s:
1` starts showing this: the fixed conversion wait (§[the sensor
device](../rig/sht4x.md#one-i2c-transaction-command-to-decode), ~8.3 ms
at high precision) is a small fraction of a second, so a slow condition
here points at the bus or the host, not the sensor's own timing.

## A demand is refused

These are refused before anything reaches the blender — a 409 from the
API, or a raised `ConflictError` from a script — and change nothing:

- **A `together` pair set in part.** `dry_flow` and `wet_flow` (and
  `dry_effort`/`wet_effort`) must arrive in the same demand — "`'…dry_flow'
  is set with wet_flow`". See [The blender device](../rig/dual-pumps.md).
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
  `DualPumps.set_flows`/`validate_flows` for a direct `dry_flow`/`wet_flow`
  demand that exceeds a line's `max_flow`. A `humidity` demand instead
  goes through `Absolute(flow, OnOverdrive.CLAMP)`, so `commit`'s own
  split-range path derates to what's achievable rather than raising — see
  [Limits](limits.md#flow).
- **The two supply humidities aren't in order**:
  `humidity.blender.SupplyHumiditiesError` — `wet` must read (or be
  configured) greater than `dry`, or there is no span to blend across.
  Raised at build time from a configured `supply:`, or at `commit` time if
  the bound sensors' readings cross (the wet line reading drier than the
  dry line, say, or a swapped I2C address).

## The pumps themselves fail

`humidity.pumps.errors.PumpHardwareError` wraps whatever the underlying
`PwmLink` raises on `configure`/`enable` — a real sysfs write can fail if
the PWM overlay isn't enabled, or the chip's gone away. `stop()`
(`PumpPair.stop`, called by the blender's `stop` command) tries both
lines even if one fails, and raises a `PumpErrorGroup` — both individual
errors, not just the first — if either does.

## Recovering

A device stays offline until `POST /api/devices/{name}/restart`, or the
daemon is restarted outright. Building the blender always disables both
PWM channels first — `PwmPump.__init__` calls `link.enable(channel,
False)` before anything else — so a fresh `flyball-daemon` start never
inherits a pump left running by a process that died mid-write; it's a
property of the driver's own construction, not something the daemon has
to arrange.
