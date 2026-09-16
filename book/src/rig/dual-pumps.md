# The blender device: dual pumps

*How a demand becomes two pump flows: blend policy, supply humidities, limits.*

`DualPumpBlender` (`examples/humidity/src/humidity/blender.py`, driver tag
`dual_pump_blender`) is a composite device: it does its split-range
arithmetic once per delivery in `commit`, however many of a new target, a
changed supply reading and a new blend flow arrived together — one pump
write per delivery, not one per input.

## The signals, and what drives them

| signal | access | drives |
| --- | --- | --- |
| `humidity` | `[W]` | the split-range target; a controller usually owns this |
| `dry_flow`, `wet_flow` | `[RPW]`, `together` | manual flow, L/min; bypasses the arithmetic |
| `dry_effort`, `wet_effort` | `[RPW]`, `together` | manual effort, 0–1 of full; bypasses the arithmetic |
| `blend_flow` | `[RW]` | a setting: the total flow a `humidity` demand mixes to |
| `expected_humidity` | `[RP]` | what the current pump outputs should actually deliver |

`commit` picks one of three things to do with whatever arrived in the
delivery, in this order: a `dry_flow`/`wet_flow` pair wins over a
`dry_effort`/`wet_effort` pair, which wins over a `humidity` (or
`blend_flow`) demand — so a manual flow always overrides the split-range
target for that delivery. `read` never touches the bus: it reports the
setting and every readback computed from the pumps' own state.

## The split-range arithmetic

`calculate_wet_fraction(humidities, target)` turns a target `%RH` into a
wet fraction 0–1, given the supply humidities:

```python
def calculate_wet_fraction(humidities: SupplyHumidities, target: float) -> Normalised | Rail:
    if humidities.wet <= humidities.dry:
        raise SupplyHumiditiesError(humidities)
    if target < humidities.dry:
        return Rail.DRY
    if target > humidities.wet:
        return Rail.WET
    return (target - humidities.dry) / humidities.difference
```

A target outside `[dry, wet]` doesn't raise — it rails to the nearer end,
and the delivery's `WriteState` for `humidity` reports `at_limit: "low"` or
`"high"`. Only a wet-humidity-not-greater-than-dry configuration raises
(`SupplyHumiditiesError`): with no span there is nothing to blend.

The fraction, `blend_flow` (the setting) and `pumps.set_blend(...)` then
give the actual dry/wet flows, which each line's `PwmPump.set_effort`
turns into a PWM duty ratio (`calculate_duty_ratio`, honouring that line's
`deadband`) and writes straight to `flyball-linux`'s `PwmLink` protocol —
`configure(channel, period_ns, duty_ns)` then `enable(channel, ...)`. The
blender owns both channels directly rather than wrapping a `pwm_channel`
device each: the split-range maths is its own, so one PWM chip link
(`config.link`) is enough.

## Following the supply lines

```yaml
bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }
```

`blender.observe` is called whenever a bound signal publishes: it records
the new supply humidity but does no I/O. The next `commit` — triggered by
*any* delivery that touches this device, not necessarily the one that
carried the new supply reading — picks it up. `expected_humidity` is
recomputed every `commit` from the pumps' actual output and the current
supply humidities, so it reflects reality even between controller moves.

## Limits

- `humidity` is clamped to `[0, 100]` by the signal's own `limits`, then
  railed against the two supplies as above — so a demand can be within
  `[0, 100]` and still rail if it's outside what the current dry/wet
  supply can reach.
- `dry_flow`/`wet_flow` are each limited to their pump's `max_flow`
  (`rig.yaml`: 2.0 L/min per line).
- `dry_effort`/`wet_effort` are limited to `[0, 1]`.
- `stop` (a command, not a demand: `blender.stop()`, or `POST
  /api/devices/blender/stop`) stops both pumps at once, bypassing whatever
  is pending.

## Config

```yaml
blender:
  driver: dual_pump_blender
  label: Pump blender
  poll_s: 1
  config:
    link: pwm0
    dry: { channel: 0, deadband: 0.05, max_flow: 2.0 }   # L/min
    wet: { channel: 1, deadband: 0.05, max_flow: 2.0 }
    blend_flow: 1.0
  bound: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }
```

`link` names a PWM chip link (`pwm0: { tag: pwm, chip: 0 }`, from
`flyball-linux` — sysfs `/sys/class/pwm/pwmchip0`, no extra library);
`dry`/`wet` are each a channel number, a deadband (0–1, below which the
pump doesn't turn) and a max flow. `config` also takes a `frequency_hz`
(default 20 000 Hz), the PWM carrier both lines share. A rig with no
sensor bound to `dry`/`wet` may give a starting `supply: { dry: …, wet: …
}` in `config` instead of `bound` — see `DualPumpBlenderConfig.supply` in
`blender.py`.
