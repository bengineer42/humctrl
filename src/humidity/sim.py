"""A humidity rig with no hardware.

Two pumps that remember their effort, a chamber whose humidity chases the
blend the pumps are delivering, and one SHT4x-shaped sensor reading it.
Enough for the UI, the programmer and the loop to be exercised on a laptop::

    python -m humidity.sim            # serve on :8000, recording to sim.db
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from flyball.core.clock import Clock
from flyball.core.typing import Normalised, Percent, Positive
from flyball.runtime.rig import Rig
from flyball.sim import FunctionReader, Lag

from humidity.blender import DualPumpsBlender, expected_humidity_from_flows
from humidity.pumps import DualPumps, PumpPair
from humidity.pumps.drivers import PumpDriver
from humidity.pumps.types import MaxFlows, SupplyHumidities
from humidity.readers import HTSource, Humidity, Temperature

log = logging.getLogger("humidity.sim")


class SimPump(PumpDriver):
    """A pump that only remembers what it was told."""

    def __init__(self) -> None:
        self._effort: Normalised = 0.0

    @property
    def effort(self) -> Normalised:
        return self._effort

    def set_effort(self, effort: Normalised) -> Normalised:
        self._effort = effort
        return effort

    def stop(self) -> None:
        self._effort = 0.0


class Chamber:
    """Humidity chases what the pumps are blending; with no flow it drifts to ambient.

    A first-order lag with a time constant that shrinks as flow rises, so a
    bigger blend flow settles the chamber faster, as it does in the real one.
    """

    def __init__(
        self,
        pumps: DualPumps,
        supply: SupplyHumidities,
        clock: Clock,
        ambient: Percent = 40.0,
        tau_s: Positive = 60.0,
        temperature: float = 21.0,
    ) -> None:
        self._pumps = pumps
        self._supply = supply
        self._clock = clock
        self._ambient = ambient
        self._tau_s = tau_s
        self._temperature = temperature
        self._lag = Lag(tau_s, value=ambient)
        self._last_ns = clock.now_ns()

    def sample(self, time_ns: int) -> dict[Any, float]:
        flows = self._pumps.flows
        expected = expected_humidity_from_flows(flows, self._supply)
        # Time constant falls with flow: at guaranteed max flow it is a tenth of the idle one.
        fraction = min(flows.total / self._pumps.guaranteed_max_flow, 1.0)
        self._lag.tau_s = self._tau_s / (1.0 + 9.0 * fraction)
        dt_s = max(time_ns - self._last_ns, 0) / 1e9
        self._last_ns = time_ns
        humidity = self._lag.step(self._ambient if expected is None else expected, dt_s)
        return {Humidity: humidity, Temperature: self._temperature}


def build_simulated_rig(
    supply: tuple[Percent, Percent] = (10.0, 90.0),
    max_flows: tuple[Positive, Positive] = (5.0, 5.0),
    period_s: Positive = 1.0,
    ambient: Percent = 40.0,
    tau_s: Positive = 60.0,
) -> Rig:
    """One blender on two simulated pumps, one process sensor, reading every ``period_s``."""
    rig = Rig()
    pumps = DualPumps(PumpPair(dry=SimPump(), wet=SimPump()), MaxFlows(*max_flows))
    blender = DualPumpsBlender(pumps, humidities=supply, name="pumps")
    rig.add_actuator(blender)

    chamber = Chamber(pumps, blender.supply_humidities, rig.clock, ambient, tau_s)
    process = HTSource("process")
    rig.start_reader(FunctionReader("chamber", {process: chamber.sample}), period_s)
    return rig


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="humidity-sim", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--period", type=float, default=1.0, help="sensor read period, seconds")
    p.add_argument("--db", type=Path, default=Path("sim.db"), help="record into this SQLite file")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    import uvicorn
    from flyball.db.sqlite import SqliteStore
    from flyball.server import create_app, set_rig

    rig = build_simulated_rig(period_s=args.period)
    if not args.no_record:
        rig.start_recording(SqliteStore(args.db), hardware="simulated")
    set_rig(rig)
    log.info("simulated rig on http://%s:%d", args.host, args.port)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
