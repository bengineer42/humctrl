# The blender device: dual pumps

*How a demand becomes two pump flows: blend policy, supply humidities, limits.*

`DualPumpBlender` (`examples/humidity/src/humidity/blender.py`, driver type
`dual_pump_blender`) is a composite device: a controller drives its
`humidity` demand, and `commit` does the split-range arithmetic once per
delivery, however many of a new target, a changed supply reading and a
new blend flow arrived together — one pump write per delivery, not one
per input. People run its commands, which drive the lines at once.

## The signals, and what drives them

| signal | role | what it is |
| --- | --- | --- |
| `humidity` | demand | the split-range target; a controller usually owns this. In `flows` mode its readback is what the flows deliver (see below) |
| `flows.dry`, `flows.wet` | demand, read-only | each line's flow, L/min: the readback, set only by `set_flows` -- not directly writable |
| `efforts.dry`, `efforts.wet` | demand, read-only | each line's effort, 0–1 of full: the readback, set only by `set_efforts` -- not directly writable |
| `expected_humidity` | output | what the current pump outputs should actually deliver; no value with no flow |
| `mode` | readout | which demand is in control: `humidity` (a humidity demand, or `set_humidity`) or `flows` (`set_flows`, `set_efforts`, `set_fraction`, `stop`) |
| `blend.flow` | setting | the flow a `humidity` demand mixes to; `set_blend` (refused while a controller regulates `humidity`) |
| `blend.wet_fraction` | demand, read-only | the share drawn from the wet line: the readback while blending, set only by `set_fraction` -- not directly writable; no value with no flow |

Each line's maximum flow is not a signal: it is the top of that flow
demand's `limits`, set from the pump's `max_flow` when the blender is
built, so the device page and the command form show it with the flow.

Its two inputs, `dry` and `wet`, are the supply lines' humidity (below).

`flows.*`, `efforts.*` and `blend.wet_fraction` are declared `access=Access.RP`
(readable and published, not writable): `commit` only ever reads `humidity`'s
`.staged`, so a direct write to one of these would be accepted and
silently dropped -- the pumps would never move. The generic signal editor
reads a signal's access from its spec, so declaring them this way is
enough to stop it offering a write control for them; drive the lines
through `set_flows`/`set_efforts`/`set_fraction` instead. The rig's
refusal names the command: "`'blender.flows.dry' [rp] is not writable: it
is a readback, moved by the command 'set_flows' (it puts a regulating
controller in manual)`".

In `flows` mode `humidity`'s readback is what the lines deliver from the
supplies now -- the model run backwards, the same number as
`expected_humidity` -- not the last target. Every flows command pushes it,
and a moved supply reading moves it again. So a controller in manual
tracks what is delivered, and the card shows it. With no flow (after
`stop`) it and `blend.wet_fraction` have no value (`not_applicable`,
reason `no_flow`): a `set_fraction` then needs its `wet_fraction` given,
since there is none to fill it from.

The mode, named after the demand in control, decides what a delivery does.
A `humidity` demand puts the blender in `humidity` mode, where a moved
supply reading re-blends; `set_flows`, `set_efforts`, `set_fraction` and
`stop` drive the lines at once and put it in `flows` mode, where a supply
reading changes nothing, so a flow set by hand is not undone by the next
reading. A `line: dry`/`line: wet`
tag groups each line's signals across `flows`, `efforts` and
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
(`flyball.foundation.device.signal.Limit`, the same enum the rig's clamp reports), and
the delivery's write state for `humidity` carries it as `at_limit: "low"`
or `"high"`. Only a wet-humidity-not-greater-than-dry configuration raises
(`SupplyHumiditiesError`): with no span there is nothing to blend.

The fraction, `blend` (the setting) and `pumps.set_blend(...)` then
give the actual dry/wet flows, allocated inside each line's *effective*
flow limit (`flows.dry`/`flows.wet`'s limits after a rig file's `limits`
narrowing, not only the pump's `max_flow`). Each line's
`PwmPump.set_effort` turns its flow into a PWM duty ratio (`calculate_duty_ratio`, honouring that line's
`deadband`) and writes straight to `flyball-linux`'s `PwmLink` protocol —
`configure(channel, period_ns, duty_ns)` then `enable(channel, ...)`. The
blender owns both channels directly rather than wrapping a `pwm_channel`
device each: the split-range maths is its own, so one PWM chip link
(`link`) is enough.

## Following the supply lines

```yaml
inputs: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }   # rig-multi-sensor.yaml
inputs: { dry: 36.5, wet: 88.5 }                                           # rig-single-sensor.yaml
```

The blender declares two inputs, `dry` and `wet` (`humidities.input(...)` in
the class body), and the rig file binds each one to a sensor's address or to
a number. There is no default: a rig file that leaves one out does not load
(`flyball rig check` says which), so `blender.yaml` is not a rig on its own.
A number has its value from the start. Whenever a bound sensor publishes,
the rig commits the blender, and `commit` reads the supply's newest values
through the input's binding (`self.dry_supply.value`) — there is no callback
and no copy on the device. In `humidity` mode that re-blends; in `flows`
mode it does nothing. Nothing is blended before a humidity demand (or
`set_humidity`): until one there is no target. `expected_humidity` is
pushed after every pump write from the pumps' actual output and the current
supply humidities. With no flow it has no value (`not_applicable`, reason
`no_flow`: a chart breaks there rather than drawing the last blend), and
while a supply has none it carries the supply's own quality and reason
(`stale: device_offline`, `invalid: crc`), so the chart and the readout say
why.

### A supply sensor with no value

A supply sensor that has not been read yet (`pending`, up to one
`poll_s` after start: 5 s on `rig-multi-sensor.yaml`), or whose reading has
no value (`invalid`, or `stale`: its device offline or hung, or nothing read
within its threshold -- `max(3 × poll_s, 5 s)`, 15 s for the supply
sensors' `poll_s: 5`), is never replaced by a number: in `humidity` mode
nothing is blended, and the pumps keep what they are doing. On
`rig-multi-sensor.yaml` a supply sensor outage therefore holds humidity
control; it used to fall back to constants. The blender holds the `supply_unknown` condition (a
warning) until the sensor reads again, when it blends at once and the
condition clears. It is not a failed write, so the blender's demands stay
as they were. Meanwhile a `humidity` demand is refused, naming the input
(its limits follow the supplies, which are not known: "`its limit follows
'dry' (pending)`"), a controller driving it holds `limit_unknown` -- an
`info` while the sensor is only pending, a `warning` once it is stale or
invalid -- and `set_humidity` refuses with 503.

## Limits

- `humidity` is clamped to `[0, 100]` by the signal's own `limits`, then
  railed against the two supplies as above — so a demand can be within
  `[0, 100]` and still rail if it's outside what the current dry/wet
  supply can reach.
- `flows.dry`/`flows.wet` are each limited to their pump's `max_flow`
  (`rig-multi-sensor.yaml`: 2.0 L/min per line) — the top of the flow's own
  `limits`, so the command form shows it and the rig clamps to it.
- `efforts.dry`/`efforts.wet` are limited to `[0, 1]`.
- A rig file may narrow `flows.dry`/`flows.wet` (`limits: [0, 1.5]`): the
  blend is then allocated inside the narrower limit, and each line's effort
  is still worked out against its pump's `max_flow`.
- `set_humidity(humidity, blend_flow)` blends to a target by hand, at a
  blend flow, in one go — what a controller does through the `humidity`
  demand; `set_flows` and `set_efforts` take one value per line. An
  argument left out is filled from its linked signal's current reading and
  re-applied. These commands, `set_fraction` and `stop` put a controller
  regulating `humidity` in manual -- once the command has succeeded, so one
  that is refused (an overdrive) leaves it regulating -- and the response's
  `interrupted` names it. `stop` stops both pumps at once, whatever is
  driving them.
- `stop` is the blender's stop (`@command(stops=True)`): flyball's
  software stop, the runner's shutdown and a controller's `on_fault: stop`
  or `stop_device` run it rather than writing values. A rig file's `stop:`
  values are refused on the blender -- `commit` ignores the flows and
  efforts, so a value would never reach the pumps. If `stop` itself
  raises, the blender is reported `failed` with no fallback ("fallback:
  none effective"): the flows and efforts declare `off` at 0 but are
  readbacks a stop cannot write, and `humidity` declares none. The
  pumps' own error handling is in [When things fail](../1-running/failures.md).
- `set_blend` is refused while a controller regulates `humidity`: it moves
  the pumps under the controller (a zero blend flow would wind it up against
  no air). Put the controller in manual first.

## Blend flow

`blend.flow` is one of:

| value | wire | means |
| --- | --- | --- |
| `Absolute(flow, on_overdrive)` | `{flow, on_overdrive: "raise" \| "clamp"}` | this many L/min. `clamp` scales both lines down, keeping the mix, when the mix cannot move that much; `raise` refuses -- but only a command run by hand (`set_blend`, `set_humidity`, `set_fraction`). A blend a humidity demand or a moved supply makes always scales, so a controller's write is never refused |
| `OfBlendMax(blend_fraction)` | `{blend_fraction}` | a share of the most this mix can move |
| `OfGuaranteedMax(guaranteed_max_fraction)` | `{guaranteed_max_fraction}` | a share of the flow every mix can move (the smaller line's limit) |
| `KeepTotal(fallback)` | `{keep: true, fallback?}` | keep the total flow the pumps move when the blender enters `humidity` mode |

`KeepTotal` is opt-in. It resolves once on each entry into `humidity` mode
(a humidity demand from `flows` mode, or `set_humidity`) to
`Absolute(<the total then>, clamp)`, held for that episode and not worked
out again at each blend, so a supply reading does not ratchet it down. At
or below 1 % of the guaranteed maximum flow (the pumps stopped, or just
started) it uses `fallback` instead; none means the configured
`blend_flow`, scaling. A `set_blend(KeepTotal)` in `humidity` mode
resolves at once, to the total now. `blend.flow` keeps showing `KeepTotal`;
the resolved total is `set_humidity`'s result and a `blend_flow_kept`
event (`details: {total, fallback, delivered, clamped}`; a warning when
the new mix cannot deliver the kept total). Switching into `humidity`
mode is bumpless in total flow only within what the new mix can move.

## Config

```yaml
blender:
  driver: dual_pump_blender
  label: Pump blender
  link: pwm0
  dry: { channel: 0, deadband: 0.05, max_flow: 2.0 }   # L/min
  wet: { channel: 1, deadband: 0.05, max_flow: 2.0 }
  blend_flow: 1.0          # or { keep: true, fallback: 1.0 }
  inputs: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }
```

`blend_flow` is L/min, scaling to what the mix can move, or `{keep: true,
fallback: <L/min>}` for a `KeepTotal` (there is no bare `keep`, so the
fallback is always visible).

`link` names a PWM chip link (`pwm0: { type: pwm, chip: 0 }`, from
`flyball-linux` — sysfs `/sys/class/pwm/pwmchip0`, no extra library);
`dry`/`wet` are each a channel number, a deadband (0–1, below which the
pump doesn't turn) and a max flow. The driver also takes a `frequency_hz`
(default 20 000 Hz), the PWM carrier both lines share. `inputs` gives each
supply line's humidity: a sensor's address, or a number for a line with
none (`inputs: { dry: 36.5, wet: 88.5 }`).
