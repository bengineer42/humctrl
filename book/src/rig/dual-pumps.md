# The blender device: dual pumps

*How a demand becomes two pump flows: blend policy, supply humidities, limits.*

`DualPumpBlender` (`examples/humidity/src/humidity/blender.py`, driver tag
`dual_pump_blender`) is a composite device: a controller drives its
`humidity` demand, and `commit` does the split-range arithmetic once per
delivery, however many of a new target, a changed supply reading and a
new blend flow arrived together — one pump write per delivery, not one
per input. People run its commands, which drive the lines at once.

## The signals, and what drives them

| signal | role | what it is |
| --- | --- | --- |
| `humidity` | demand | the split-range target; a controller usually owns this |
| `flows.dry`, `flows.wet` | demand | each line's flow, L/min: the readback, set by `set_flows` |
| `efforts.dry`, `efforts.wet` | demand | each line's effort, 0–1 of full: the readback, set by `set_efforts` |
| `expected_humidity` | output | what the current pump outputs should actually deliver |
| `mode` | output | `blend`, `flows`, `efforts` or `stopped`: what is driving the pumps |
| `blend.flow` | setting | the flow a `humidity` demand mixes to; `set_blend` |
| `blend.wet_fraction` | demand | the share drawn from the wet line: the readback while blending; `set_fraction` sets it directly |
| `max_flows.dry`, `max_flows.wet` | config | each line's maximum, the limit of its flow demand |

The mode decides what a delivery does. A `humidity` demand puts the
blender in `blend`, where a moved supply reading re-blends; `set_flows`,
`set_efforts` and `stop` drive the lines at once and change the mode, so
a manual flow is not undone by the next supply reading. The `dry`/`wet`
sections tag each line's signals across `flows`, `efforts` and
`max_flows`, so a UI can show the tree by line as well as by kind. The
blender is never polled: it pushes its readbacks from `commit` and from
its commands.

## The split-range arithmetic

`calculate_wet_fraction(humidities, target)` turns a target `%RH` into a
wet fraction 0–1, given the supply humidities:

```python
def calculate_wet_fraction(humidities: SupplyHumidities, target: float) -> Normalised | Limit:
    if humidities.wet <= humidities.dry:
        raise SupplyHumiditiesError(humidities)
    if target < humidities.dry:
        return Limit.LOW
    if target > humidities.wet:
        return Limit.HIGH
    return (target - humidities.dry) / humidities.difference
```

A target outside `[dry, wet]` doesn't raise — it rails to the nearer end
(`flyball.core.signal.Limit`, the same enum the rig's clamp reports), and
the delivery's write state for `humidity` carries it as `at_limit: "low"`
or `"high"`. Only a wet-humidity-not-greater-than-dry configuration raises
(`SupplyHumiditiesError`): with no span there is nothing to blend.

The fraction, `blend` (the setting) and `pumps.set_blend(...)` then
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

The blender declares two inputs, `dry` and `wet` (`supply.input(...)` in
the class body); the rig file binds them to the sensors. Whenever a bound
signal publishes, the rig commits the blender, and `commit` reads the
supply's newest values from the router (`self.dry_supply.value`) — there
is no callback and no copy on the device. While blending, that re-blends;
in any other mode it does nothing. A rig with no sensor bound uses
`config.supply` (the `supply_defaults` config signals) instead.
`expected_humidity` is pushed after every pump write from the pumps'
actual output and the current supply humidities.

## Limits

- `humidity` is clamped to `[0, 100]` by the signal's own `limits`, then
  railed against the two supplies as above — so a demand can be within
  `[0, 100]` and still rail if it's outside what the current dry/wet
  supply can reach.
- `flows.dry`/`flows.wet` are each limited to their pump's `max_flow`
  (`rig.yaml`: 2.0 L/min per line) — the limit *is* the `max_flows.dry`
  config signal, so the command form shows it and the rig clamps to it.
- `efforts.dry`/`efforts.wet` are limited to `[0, 1]`.
- `set_flows` and `set_efforts` take one value per line; a line left out
  keeps its current value. They are refused while a controller drives
  `humidity` (put it in manual, or detach it); `stop` is exempt and stops
  both pumps at once, whatever is driving them.

## Config

```yaml
blender:
  driver: dual_pump_blender
  label: Pump blender
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
