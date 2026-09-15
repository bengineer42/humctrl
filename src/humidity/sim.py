"""A humidity rig with no hardware.

Two pumps that remember their effort, a chamber whose humidity chases their
blend, and one SHT4x-shaped sensor:

    python -m humidity.sim            # serve on :8000, recording to sim.db
"""

from __future__ import annotations

import argparse
import logging
import random
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
    """Humidity chases the pumps' blend; with no flow it drifts to ambient.

    A first-order lag whose time constant shrinks as flow rises.
    """

    def __init__(
        self,
        pumps: DualPumps,
        supply: SupplyHumidities,
        clock: Clock,
        ambient: Percent = 40.0,
        tau_s: Positive = 60.0,
        temperature: float = 21.0,
        noise_rh: float = 0.3,
        noise_c: float = 0.05,
        seed: int | None = None,
    ) -> None:
        self._pumps = pumps
        self._supply = supply
        self._clock = clock
        self._ambient = ambient
        self._tau_s = tau_s
        self._temperature = temperature
        self._lag = Lag(tau_s, value=ambient)
        self._last_ns = clock.now_ns()
        # Sensor noise, one sigma: the SHT45 datasheet gives ±1 %RH / ±0.1 °C
        # accuracy, and repeatability is a few tenths of that.
        self._noise_rh = noise_rh
        self._noise_c = noise_c
        self._random = random.Random(seed)

    def sample(self, time_ns: int) -> dict[Any, float]:
        flows = self._pumps.flows
        expected = expected_humidity_from_flows(flows, self._supply)
        # Time constant falls with flow: at guaranteed max flow it is a tenth of the idle one.
        fraction = min(flows.total / self._pumps.guaranteed_max_flow, 1.0)
        self._lag.tau_s = self._tau_s / (1.0 + 9.0 * fraction)
        dt_s = max(time_ns - self._last_ns, 0) / 1e9
        self._last_ns = time_ns
        humidity = self._lag.drive(self._ambient if expected is None else expected, dt_s)
        return {
            Humidity: min(100.0, max(0.0, self._random.gauss(humidity, self._noise_rh))),
            Temperature: self._random.gauss(self._temperature, self._noise_c),
        }


class Supplies:
    """The two supply lines: steady at their nominal humidity, plus sensor noise."""

    def __init__(
        self,
        nominal: tuple[Percent, Percent],
        temperature: float = 21.0,
        noise_rh: float = 0.3,
        noise_c: float = 0.05,
        seed: int | None = None,
    ) -> None:
        self._dry, self._wet = nominal
        self._temperature = temperature
        self._noise_rh = noise_rh
        self._noise_c = noise_c
        self._random = random.Random(seed)

    def _read(self, humidity: Percent) -> dict[Any, float]:
        return {
            Humidity: min(100.0, max(0.0, self._random.gauss(humidity, self._noise_rh))),
            Temperature: self._random.gauss(self._temperature, self._noise_c),
        }

    def dry(self, time_ns: int) -> dict[Any, float]:
        return self._read(self._dry)

    def wet(self, time_ns: int) -> dict[Any, float]:
        return self._read(self._wet)


def build_simulated_rig(
    supply: tuple[Percent, Percent] = (10.0, 90.0),
    max_flows: tuple[Positive, Positive] = (5.0, 5.0),
    period_s: Positive = 1.0,
    ambient: Percent = 40.0,
    tau_s: Positive = 60.0,
    noise_rh: float = 0.3,
) -> Rig:
    """One blender on two simulated pumps, one process sensor, reading every `period_s`."""
    rig = Rig()
    pumps = DualPumps(PumpPair(dry=SimPump(), wet=SimPump()), MaxFlows(*max_flows))
    blender = DualPumpsBlender(pumps, humidities=SupplyHumidities(*supply), name="pumps")
    rig.add_actuator(blender)

    chamber = Chamber(
        pumps, blender.supply_humidities, rig.clock, ambient, tau_s, noise_rh=noise_rh
    )
    process, dry, wet = HTSource("process"), HTSource("dry"), HTSource("wet")
    supplies = Supplies(supply, noise_rh=noise_rh)
    # The blender follows the supply sensors, so its expected humidity tracks
    # what the lines actually carry rather than the configured nominal values.
    blender.set_channels(dry, wet)
    rig.attach_observer(blender)
    rig.start_reader(
        FunctionReader(
            "sht4x", {process: chamber.sample, dry: supplies.dry, wet: supplies.wet}
        ),
        period_s,
    )
    return rig


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="humidity-sim", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--period", type=float, default=1.0, help="sensor read period, seconds")
    p.add_argument("--db", type=Path, default=Path("sim.db"), help="record into this SQLite file")
    p.add_argument("--noise", type=float, default=0.3, help="humidity sensor noise, one sigma, %RH")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    import uvicorn
    from flyball.db.sqlite import SqliteStore
    from flyball.programmer.programmer import Programmer
    from flyball.server import create_app, set_rig
    from flyball.server.deps import set_programmer, set_store

    rig = build_simulated_rig(period_s=args.period, noise_rh=args.noise)
    store = SqliteStore(args.db)
    set_store(store)  # history routes read it whether or not a session is open
    if not args.no_record:
        rig.start_recording(store, hardware="simulated")
    set_rig(rig)
    set_programmer(Programmer(rig))  # programs run against the rig from the library or a document
    log.info("simulated rig on http://%s:%d", args.host, args.port)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
