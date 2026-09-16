"""A humidity chamber, as a `MultiPlant` link: no hardware needed.

One input, `wet_fraction` (0 dry to 1 wet of the blend); six named
outputs, one per leaf of the real `hum_sensors` device --
`chamber_humidity`, `chamber_temperature`, `dry_humidity`,
`dry_temperature`, `wet_humidity`, `wet_temperature` -- so `sim.yaml` can
read and drive it under the exact addresses `rig.yaml` declares (plan
§1.6). The chamber humidity settles towards the blend on a first-order lag
(`Lag`, reused from [flyball.sim.plant]); the supply lines and every
temperature are constants -- nothing here models a thermal path.
"""

from __future__ import annotations

import random
from typing import Literal

from flyball.core.config import Config
from flyball.core.typing import NonNegative, Positive
from flyball.sim.plant import Lag

from humidity.blender import SupplyHumiditiesError
from humidity.pumps import SupplyHumidities
from humidity.units import Humidity, Temperature

Port = Literal[
    "chamber_humidity",
    "chamber_temperature",
    "dry_humidity",
    "dry_temperature",
    "wet_humidity",
    "wet_temperature",
]
PORTS: tuple[Port, ...] = (
    "chamber_humidity",
    "chamber_temperature",
    "dry_humidity",
    "dry_temperature",
    "wet_humidity",
    "wet_temperature",
)


class HumidityChamber:
    """A `MultiPlant`: `wet_fraction` in, the chamber's and both supplies' RH and T out."""

    def __init__(
        self,
        dry: float,
        wet: float,
        tau_s: float,
        initial: float,
        temperature: float,
        noise: float,
        seed: int | None,
    ) -> None:
        self.inputs: dict[str, float] = {"wet_fraction": 0.0}
        self.output_names = PORTS
        self._dry = dry
        self._wet = wet
        self._temperature = temperature
        self._lag = Lag(tau_s, initial, gain=wet - dry, ambient=dry)
        self._last_ns: int | None = None
        self._noise = noise
        self._random = random.Random(seed)

    def output(self, port: str) -> float:
        if port == "chamber_humidity":
            return min(100.0, max(0.0, self._random.gauss(self._lag.value, self._noise)))
        if port == "dry_humidity":
            return self._dry
        if port == "wet_humidity":
            return self._wet
        if port in ("chamber_temperature", "dry_temperature", "wet_temperature"):
            return self._temperature
        raise ValueError(f"no output {port!r}; there are {self.output_names}")

    def advance(self, time_ns: int) -> None:
        """Step the lag to `time_ns`; a second call at the same instant does nothing."""
        if self._last_ns is not None and time_ns > self._last_ns:
            self._lag.input = self.inputs["wet_fraction"]
            self._lag.step((time_ns - self._last_ns) / 1e9)
        self._last_ns = time_ns

    def feedforward(self, port: str, demand: float) -> float:
        self._check_input(port)
        return self._lag.feedforward(demand)

    def inverse_feedforward(self, port: str, drive: float) -> float:
        self._check_input(port)
        return self._lag.inverse_feedforward(drive)

    def _check_input(self, port: str) -> None:
        if port not in self.inputs:
            raise ValueError(f"no input {port!r}; there are {tuple(self.inputs)}")


class HumidityChamberConfig(Config[HumidityChamber], tag="sim_humidity_chamber"):
    """A chamber that settles towards the blend's expected humidity.

    At `wet_fraction` 0 (all dry) it rests at `dry`; at 1 (all wet) at
    `wet`; a fraction in between at the point along that span. The supply
    lines and every temperature are steady -- there is no thermal model to
    perturb them.
    """

    dry: Humidity = 10.0
    wet: Humidity = 90.0
    tau_s: Positive = 60.0
    initial: Humidity = 40.0
    temperature: Temperature = 21.0
    noise: NonNegative = 0.3
    """Gaussian noise on the chamber's humidity reading, %RH."""
    seed: int | None = None

    def build(self) -> HumidityChamber:
        humidities = SupplyHumidities(self.dry, self.wet)
        if humidities.wet <= humidities.dry:
            raise SupplyHumiditiesError(humidities)
        return HumidityChamber(
            self.dry, self.wet, self.tau_s, self.initial, self.temperature, self.noise, self.seed
        )
