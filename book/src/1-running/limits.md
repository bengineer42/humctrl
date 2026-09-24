# Limits

*Achievable range, flow, and the reasons for each limit — as `rig-multi-sensor.yaml` and the driver code actually declare them.*

## Humidity

`blender.humidity [W]` is clamped to `[0, 100]` by the signal's own
`limits`. Within that, what's actually achievable is bounded by the two
supply humidities — `dry: 0x45`, `wet: 0x46` in `hum_sensors`, whatever
they currently read (or, on a rig with no sensor bound, the configured
`supply.dry`/`supply.wet`). A target outside `[dry, wet]` rails to the
nearer end rather than being refused — see [the split-range
arithmetic](../3-devices/blender.md#the-split-range-arithmetic) — and the
resulting `WriteState.at_limit` says which end. With room air on the dry
line, the practical floor is ambient %RH, not zero.

`hum_sensors.chamber.humidity`'s own signal `range` is `[0, 100]`
(precision 2), and `rig-multi-sensor.yaml` adds a `warning: [20, 80]` band on top — inside
`[0, 100]` but a narrower range the UI flags outside of, independent of
any control limit.

## Flow

Each pump line is limited to its own `max_flow` (`rig-multi-sensor.yaml`: 2.0 L/min for
both `dry` and `wet`). `flows.dry`/`flows.wet [RP]` are clamped there
directly, and (being readbacks, not writable) are only ever moved by
`set_flows`, which clamps its own `dry`/`wet` arguments the same way;
`blend.flow [RP]` (the total flow a `humidity` demand mixes to, a
setting re-set only by `set_blend`) is not itself limited by a signal
range, but the resulting per-line flows still are: the blend is
allocated inside `flows.dry`/`flows.wet`'s effective limits, so a rig
file's narrower `limits` on them bound the blend too.

The achievable *total* flow depends on the blend, not just on the two
`max_flow`s: at a wet fraction `b`, `dry_flow = (1−b)·total ≤ dry_max` and
`wet_flow = b·total ≤ wet_max`, so `total ≤ min(dry_max/(1−b),
wet_max/b)`. That ceiling is *lowest* near either rail — as `b → 0` it
falls to `dry_max` alone (2.0 L/min here), since almost none of the total
may come from the wet line — and *highest* at the 50/50 blend, `2 ·
min(dry_max, wet_max)` (4.0 L/min, with this rig's equal 2.0 L/min lines —
the same number the simple sum would give here only because the two
maxima happen to match; with unequal lines they'd differ).
`MaxFlows.flows_at_blend` (`humidity/pumps/types.py`) is the exact
computation; `Absolute(flow, OnOverdrive.CLAMP)` derates to whatever the
blend can actually deliver rather than raising. A blend `commit` makes for
a `humidity` demand always derates, even with `on_overdrive: raise`, which
refuses only a command run by hand.

## Effort and deadband

`efforts.dry`/`efforts.wet [RP]` are `[0, 1]` of full PWM drive, readbacks
moved only by `set_efforts`.
`deadband` (`rig-multi-sensor.yaml`: 0.05 for both lines) is the effort below which a
line doesn't turn — `PwmPump.calculate_duty_ratio` maps `[0, 1]`
effort onto `[deadband, 1]` duty, so effort 0 is genuinely off and any
effort above 0 already clears the deadband; there's no achievable duty
between "off" and the deadband's floor.

## Sensor range and rate

`humidity [RP]`: `[0, 100]` %RH, precision 2 (`Sht4xSet`'s declared
signal spec). `temperature [RP]`: `[-40, 125]` °C, precision 2 — not
clamped further by the decode itself (see [the sensor
device](../3-devices/sht4x.md#one-i2c-transaction-command-to-decode)).

`hum_sensors.chamber` polls every 1 s (the device's own `poll_s`);
`hum_sensors.dry`/`hum_sensors.wet` every 5 s (`rig-multi-sensor.yaml`'s per-namespace
`poll_s`) — the supply lines drift slowly enough that polling them as
often as the chamber buys nothing. `blender` itself polls every 1 s, but
its `read` never touches the bus: every value it reports is computed from
the pumps' own state, so its poll rate only bounds how fresh the
readback is, not any hardware transaction rate.

## The controller

`blender.humidity`'s law is `PI, kp: 0.8, ki: 0.02, tt: 60` — no
derivative term, appropriate for a noisy %RH reading (see [Autotune on
this rig](autotune.md) for how those gains would be re-fitted). `tt: 60`
is the back-calculation tracking constant for anti-windup, in seconds —
how fast the integral unwinds once the target stops railing.
