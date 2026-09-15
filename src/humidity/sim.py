"""A humidity rig with no hardware.

Two pumps that remember their effort, a chamber whose humidity chases their
blend, and one SHT4x-shaped sensor:

    python -m humidity.sim            # serve on :8000, recording to sim.db
"""

from __future__ import annotations

import argparse
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flyball.core.clock import Clock
from flyball.core.device import Device, DeviceSettings, DeviceState, command
from flyball.core.typing import NonNegative, Normalised, Percent, Positive
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import Simulation
from flyball.sim import FunctionReader, Lag, ScaledClock

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

    # region Knobs: what a simulation panel turns

    @property
    def tau_s(self) -> Positive:
        """The idle time constant; with flow it shrinks (see `sample`)."""
        return self._tau_s

    @tau_s.setter
    def tau_s(self, tau_s: Positive) -> None:
        if tau_s <= 0:
            raise ValueError("tau_s must be positive")
        self._tau_s = tau_s

    @property
    def ambient(self) -> Percent:
        return self._ambient

    @ambient.setter
    def ambient(self, ambient: Percent) -> None:
        self._ambient = ambient

    @property
    def noise(self) -> tuple[NonNegative, NonNegative]:
        """One sigma of the sensor's noise: (%RH, °C)."""
        return self._noise_rh, self._noise_c

    def set_noise(self, rh: NonNegative | None = None, c: NonNegative | None = None) -> None:
        if rh is not None:
            self._noise_rh = rh
        if c is not None:
            self._noise_c = c

    @property
    def humidity(self) -> Percent:
        """What the chamber is actually at, before the sensor's noise."""
        return self._lag.value

    @property
    def target(self) -> Percent | None:
        """What the humidity is chasing now: the blend, or ambient with no flow."""
        return expected_humidity_from_flows(self._pumps.flows, self._supply)

    @property
    def effective_tau_s(self) -> Positive:
        return self._lag.tau_s

    def reset(self, humidity: Percent | None = None) -> None:
        """Put the chamber at `humidity` (default ambient) now, as if the door had been opened."""
        self._lag.value = self._ambient if humidity is None else humidity
        self._last_ns = self._clock.now_ns()

    # endregion

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

    @property
    def nominal(self) -> tuple[Percent, Percent]:
        """(dry, wet): what each line carries."""
        return self._dry, self._wet

    def set_nominal(self, dry: Percent | None = None, wet: Percent | None = None) -> None:
        if dry is not None:
            self._dry = dry
        if wet is not None:
            self._wet = wet

    def set_noise(self, rh: NonNegative | None = None, c: NonNegative | None = None) -> None:
        if rh is not None:
            self._noise_rh = rh
        if c is not None:
            self._noise_c = c

    def _read(self, humidity: Percent) -> dict[Any, float]:
        return {
            Humidity: min(100.0, max(0.0, self._random.gauss(humidity, self._noise_rh))),
            Temperature: self._random.gauss(self._temperature, self._noise_c),
        }

    def dry(self, time_ns: int) -> dict[Any, float]:
        return self._read(self._dry)

    def wet(self, time_ns: int) -> dict[Any, float]:
        return self._read(self._wet)


@dataclass(frozen=True, slots=True, kw_only=True)
class HumiditySimulationSettings(DeviceSettings):
    tau_s: Positive = 60.0
    """The chamber's idle time constant, s; it shrinks as flow rises."""
    ambient: Percent = 40.0
    """Where the chamber drifts with no flow, %RH."""
    noise_rh: NonNegative = 0.3
    """Sensor noise, one sigma, %RH; every SHT4x-shaped source."""
    noise_c: NonNegative = 0.05
    """Sensor noise, one sigma, °C."""
    supply: tuple[Percent, Percent] = (10.0, 90.0)
    """What the (dry, wet) lines carry, %RH."""


@dataclass(frozen=True, slots=True, kw_only=True)
class HumiditySimulationState(DeviceState):
    humidity: Percent
    """The chamber's true humidity, before the sensor's noise."""
    target: Percent | None
    """What it is chasing: the pumps' blend, or ambient with no flow."""
    effective_tau_s: Positive
    """The time constant at the current flow."""
    speed: float
    """Rig seconds per wall second; `PUT /api/sim/clock` changes it, `/api/sim` has the clock."""


class HumiditySimulation(Device):
    """What exists only because the chamber is simulated: its physics and its sensors' noise.

    The rig's speed lives on `/api/sim` with every other simulation's; this
    device carries what a rig file cannot name. Served at `/api/sim/device`.
    """

    def __init__(
        self, rig: Rig, chamber: Chamber, supplies: Supplies, name: str = "simulation"
    ) -> None:
        super().__init__(name)
        self.rig = rig
        self.chamber = chamber
        self.supplies = supplies

    @property
    def settings(self) -> HumiditySimulationSettings:
        noise_rh, noise_c = self.chamber.noise
        return HumiditySimulationSettings(
            tau_s=self.chamber.tau_s,
            ambient=self.chamber.ambient,
            noise_rh=noise_rh,
            noise_c=noise_c,
            supply=self.supplies.nominal,
        )

    @property
    def state(self) -> HumiditySimulationState:
        return HumiditySimulationState(
            humidity=self.chamber.humidity,
            target=self.chamber.target,
            effective_tau_s=self.chamber.effective_tau_s,
            speed=float(getattr(self.rig.clock, "speed", 1.0)),
        )

    @command
    def set_chamber(
        self, tau_s: Positive | None = None, ambient: Percent | None = None
    ) -> HumiditySimulationSettings:
        """Change the chamber's physics: its idle time constant and where it rests with no flow."""
        if tau_s is not None:
            self.chamber.tau_s = tau_s
        if ambient is not None:
            self.chamber.ambient = ambient
        return self.settings

    @command
    def set_noise(
        self, rh: NonNegative | None = None, c: NonNegative | None = None
    ) -> HumiditySimulationSettings:
        """Sensor noise, one sigma, on every source: humidity in %RH, temperature in °C."""
        self.chamber.set_noise(rh, c)
        self.supplies.set_noise(rh, c)
        return self.settings

    @command
    def set_supply(
        self, dry: Percent | None = None, wet: Percent | None = None
    ) -> HumiditySimulationSettings:
        """What the supply lines carry; the blender follows their sensors, so it sees the change."""
        if dry is not None and wet is not None and dry >= wet:
            raise ValueError("the dry line must be drier than the wet one")
        self.supplies.set_nominal(dry, wet)
        return self.settings

    @command
    def reset(self, humidity: Percent | None = None) -> HumiditySimulationState:
        """Put the chamber at `humidity` (default ambient) at once: a door opened and closed."""
        self.chamber.reset(humidity)
        return self.state


def build_simulated_rig(
    supply: tuple[Percent, Percent] = (10.0, 90.0),
    max_flows: tuple[Positive, Positive] = (5.0, 5.0),
    period_s: Positive = 1.0,
    ambient: Percent = 40.0,
    tau_s: Positive = 60.0,
    noise_rh: float = 0.3,
    speed: Positive = 1.0,
) -> tuple[Rig, HumiditySimulation]:
    """One blender on two simulated pumps, one process sensor, reading every `period_s`.

    The rig runs on a [ScaledClock][flyball.sim.clock.ScaledClock] at `speed`,
    so its time can be run faster while it serves. Returns the rig and the
    device that holds the simulation's own knobs.
    """
    rig = Rig()
    rig.clock = ScaledClock(speed)  # as `RigConfig.build` does: swapped in before anything reads it
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
        FunctionReader("sht4x", {process: chamber.sample, dry: supplies.dry, wet: supplies.wet}),
        period_s,
    )
    return rig, HumiditySimulation(rig, chamber, supplies)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="humidity-sim", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--period", type=float, default=1.0, help="sensor read period, seconds")
    p.add_argument("--db", type=Path, default=Path("sim.db"), help="record into this SQLite file")
    p.add_argument("--noise", type=float, default=0.3, help="humidity sensor noise, one sigma, %RH")
    p.add_argument("--speed", type=float, default=1.0, help="rig seconds per wall second")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    import uvicorn
    from flyball.db.sqlite import SqliteStore
    from flyball.programmer.programmer import Programmer
    from flyball.runtime.config import RigConfig
    from flyball.server import create_app, set_rig, set_simulation
    from flyball.server.deps import set_programmer, set_simulation_device, set_store

    rig, simulation = build_simulated_rig(
        period_s=args.period, noise_rh=args.noise, speed=args.speed
    )
    store = SqliteStore(args.db)
    set_store(store)  # history routes read it whether or not a session is open
    if not args.no_record:
        rig.start_recording(store, hardware="simulated")
    set_rig(rig)
    set_programmer(Programmer(rig))  # programs run against the rig from the library or a document
    # No rig file built this, so the simulation has no plants to list and nothing
    # to save; the clock's speed is what `/api/sim` controls here.
    set_simulation(Simulation(rig, RigConfig(name="humidity-sim")))
    set_simulation_device(simulation)  # the chamber's knobs, at /api/sim/device
    log.info("simulated rig on http://%s:%d", args.host, args.port)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
