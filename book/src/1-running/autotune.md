# Autotune on this rig

*Which experiment to run, what a good fit looks like, and what to do with a bad one.*

This rig has no autotune program step or CLI command of its own — flyball's
generic `flyball.autotune` library (`engine/src/flyball/autotune/`) is
run as a script against the live rig, with the target controller's law
temporarily set to `OpenLoop` so a step is applied without the PI law
fighting it. Nothing here is humidity-specific.

## Which experiment

`flyball.autotune` offers two:

| experiment | measures | cost |
| --- | --- | --- |
| `StepTest` | a step response, fit to an FOPDT model | one clean step, held at each end |
| `RelayTest` | the critical gain/period, off a forced limit cycle | deliberately oscillates the rig |

Prefer `StepTest` here. The chamber is slow and the wet/dry blend is a
finite resource (pump runtime, water and desiccant consumption) — a
`RelayTest` cycles the blend back and forth for several periods to read
the oscillation, which costs more of both than one step does, and the
model a `StepTest` fits is reusable for [simulation](../2-config/index.md)
and for re-tuning later without another run.

## Sizing the experiment

`StepTest(base, size, window, band, timeout)` needs a plateau at `base`
before it steps, and treats a further `window`-second span within `band`
as the new plateau. Against this rig:

- **`base`/`size`**: keep the step within the supply span (`dry: 10.0`,
  `wet: 90.0` in `rig-multi-sensor.yaml`) and away from its ends — a step that rails
  (see [The blender device](../3-devices/blender.md#the-split-range-arithmetic))
  isn't measuring the linear part of the plant. A base near the middle of
  the working range with a size of 10–15 %RH is a reasonable start.
- **`window`**: must exceed the plant's dead time, or the flat stretch
  right after the step reads as the plateau and the fit never sees the
  response. `sim.yaml`'s `sim_humidity_chamber` plant is a pure first-order
  lag (`tau_s: 45.0`) with **no dead time term at all** — useful for
  exercising the fit code, not for sizing `window` against real transport
  delay. On real hardware, expect dead time from the tubing between the
  blend point and the chamber, and from the sensor's own response; size
  `window` generously until a real run confirms it.
- **`band`**: above the chamber sensor's noise (`sim.yaml`'s plant adds
  ±0.3 %RH Gaussian noise to the chamber reading only — the real SHT4x's
  own noise floor is unmeasured), well below `size`.
- **`timeout`**: an upper bound on how long either plateau is allowed to
  take, so a rig that never settles fails the experiment instead of
  hanging it.

## Fitting and tuning

`fit_fopdt` turns the logged `(time, reading)` pairs into an
`FOPDT`: `gain`, `tau`, `dead_time`, and an
`error` (RMS residual, in reading units — %RH here). `imc(model)` is the
preferred rule for a `StepTest` result: IMC/lambda tuning, PI by default
(`derivative=False` keeps it PI, matching `blender.humidity`'s current
law). `FOPDT.normalised_dead_time` (`θ/(θ+τ)`) says how hard the plant is:
below 0.2 most tunings work; above 0.6 no PID does well and the rig may
need detuning rather than a cleverer rule.

## What a good fit looks like

- `error` small relative to `band` and to the step `size` — the model
  tracks the logged response, not just its two endpoints.
- `dead_time` and `tau` both positive and the right order of magnitude for
  a chamber this size (seconds to low tens of seconds for `dead_time`,
  tens of seconds for `tau`, on `sim.yaml`'s numbers) — a fitted `tau`
  near zero or wildly larger than the experiment's own `window` means the
  window was too short to see the actual settling.
- `normalised_dead_time` comfortably below 0.6.

## What to do with a bad one

- **Fit never completes** (`ExperimentIncompleteError`/
  `ExperimentTimeoutError`): the plateau band was too tight for the
  sensor's noise, or `timeout` too short for the plant — widen `band` or
  `timeout` and rerun.
- **Response smaller than expected** (`ResponseTooSmallError`, from
  `RelayTest`, or a suspiciously small `error`-free fit from `StepTest`):
  `size` was too small against the noise, or the step landed near a rail
  (see [the split-range rail](../3-devices/blender.md#the-split-range-arithmetic))
  and clipped.
- **`dead_time` swallowing the whole response**: `window` was shorter
  than the true dead time, so the flat pre-response stretch was read as
  the new plateau — widen `window` and rerun rather than trusting the fit.
- **The plant is genuinely dead-time-dominated**
  (`normalised_dead_time` above ~0.6): expect any PID to be slow;
  detuning (a larger `lam` in `imc`) is the honest answer, not a better
  rule.

`Gains.of_ideal`/the fitted `Gains` give `kp`, `ki`, `kd`, `tt` in the same
parallel form `blender.humidity`'s `law: { tag: PI, kp, ki, tt }` already
uses, so a fitted result drops straight into `rig-multi-sensor.yaml` once it looks
right.
