"""Step-test autotune for blender.humidity, run against a live rig over HTTP.

    uv run scripts/autotune_step.py --url http://127.0.0.1:8000 --base 50 --size 12

Holds at `base`, steps to `base + size`, waits for both plateaus, fits an
FOPDT, prints IMC gains. Nothing here writes a tuning to the rig -- copy the
printed `law:` block into the rig file by hand, or use `--apply` to register
it as a named tuning over the API without touching the file.

See book/src/1-running/autotune.md (this repo) and the engine's own
book/src/1-running/autotune.md for how to choose base/size/window/band/timeout
for this rig specifically -- keep the step within the supply span
(rig-multi-sensor.yaml's dry/wet humidities) and away from its ends.
"""

from __future__ import annotations

import argparse
import sys
import time

from flyball.autotune import ExperimentTimeoutError, StepTest, imc
from flyball.interfaces.client import Rig


def run(
    rig: Rig,
    controller: str,
    source: str,
    base: float,
    size: float,
    window: float,
    band: float,
    timeout: float | None,
    poll_s: float,
) -> None:
    print(f"regulating {controller!r} open-loop at {base}, watching {source!r}")
    rig.post(
        f"/api/controllers/{controller}/regulate",
        {"at": base, "tuning": {"type": "open_loop"}},
    )

    test = StepTest(base=base, size=size, window=window, band=band, timeout=timeout)
    while not test.done:
        reading = rig.read(source)["reading"]["value"]
        target = test.step(time.monotonic(), reading)
        rig.put(f"/api/controllers/{controller}/reference", {"at": target})
        state = "stepped, watching for response" if test.stepped else "settling at base"
        print(f"  {reading:7.2f} -> target {target:7.2f}  ({state})")
        time.sleep(poll_s)

    model = test.result
    print()
    print(f"gain={model.gain:.4g}  tau={model.tau:.4g}s  dead_time={model.dead_time:.4g}s")
    print(f"normalised_dead_time={model.normalised_dead_time:.3f}  error={model.error:.4g}")
    if model.normalised_dead_time > 0.6:
        print("warning: dead-time-dominated -- expect any PID to be slow; detune (larger lam).")

    # derivative=False: PI, matching blender.humidity's current law (a noisy
    # %RH reading gives up little by dropping D, per book/src/1-running/autotune.md).
    gains = imc(model, derivative=False)
    print()
    print(f"IMC gains: kp={gains.kp:.4g}  ki={gains.ki:.4g}  tt={gains.tt:.4g}")
    print("law:")
    print("  type: PI")
    print(f"  kp: {gains.kp:.4g}")
    print(f"  ki: {gains.ki:.4g}")
    print(f"  tt: {gains.tt:.4g}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--token", default=None, help="defaults to $FLYBALL_TOKEN")
    p.add_argument("--controller", default="blender.humidity")
    p.add_argument("--source", default="hum_sensors.chamber.humidity")
    p.add_argument("--base", type=float, required=True, help="%%RH to hold at before stepping")
    p.add_argument("--size", type=float, required=True, help="signed step size, %%RH")
    p.add_argument("--window", type=float, default=120.0, help="seconds a plateau must hold")
    p.add_argument("--band", type=float, default=0.5, help="%%RH the plateau may move within window")
    p.add_argument("--timeout", type=float, default=1800.0, help="seconds per plateau, 0 = forever")
    p.add_argument("--poll-s", type=float, default=2.0, help="seconds between readings")
    args = p.parse_args(argv)

    rig = Rig(args.url, token=args.token)
    try:
        run(
            rig,
            args.controller,
            args.source,
            args.base,
            args.size,
            args.window,
            args.band,
            args.timeout or None,
            args.poll_s,
        )
    except ExperimentTimeoutError as e:
        print(f"timed out: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
