from dataclasses import dataclass
from typing import Self

from flyball.control import Tuning
from flyball.control.laws import OpenLoopTuning
from flyball.control.types import ControlLawLike
from flyball.core.config import Config, ConfigOr, resolve
from flyball.core.typing import Percent, Positive
from flyball.rig import HumRig
from linux_pwm import PWMChip
from linux_pwm.sysfs import PWM_PATH

from humidity.blender import DualPumpsBlender
from humidity.direct import DEFAULT_PWM_FREQUENCY, LinuxPWMPump
from humidity.pumps import (
    BlendFlow,
    DefaultBlendFlow,
    DefaultHumidities,
    DualPumps,
    PumpPair,
    SupplyHumiditiesLike,
)


@dataclass(slots=True, frozen=True)
class PumpConfig:
    channel: int | None = None
    deadband: float = 0.0


DefaultPumpConfig = PumpConfig()


class ChipConfig(Config[PWMChip]):
    index: int = 0
    path: str = PWM_PATH

    def build(self) -> PWMChip:
        return PWMChip(self.index, self.path)

    @classmethod
    def resolve(cls, config: int | ConfigOr[PWMChip]) -> PWMChip:
        return PWMChip(config) if isinstance(config, int) else resolve(config)


class LinuxPwmDualPumpsConfig(Config[PumpPair]):
    dry: PumpConfig = DefaultPumpConfig
    wet: PumpConfig = DefaultPumpConfig
    setpoint: Percent | None = None
    chip: int | ConfigOr[PWMChip] = 0
    frequency: float = DEFAULT_PWM_FREQUENCY
    timeout: float = 10.0
    flip_channels: bool = False

    def build(self) -> PumpPair:
        chip = PWMChip(self.chip) if isinstance(self.chip, int) else resolve(self.chip)
        dry_channel = self.dry.channel if self.dry.channel is not None else 0
        wet_channel = self.wet.channel if self.wet.channel is not None else 1
        if self.flip_channels:
            wet_channel, dry_channel = dry_channel, wet_channel
        dry = LinuxPWMPump(
            dry_channel, self.frequency, self.dry.deadband, chip, timeout=self.timeout
        )
        wet = LinuxPWMPump(
            wet_channel, self.frequency, self.wet.deadband, chip, timeout=self.timeout
        )
        return PumpPair(dry, wet)


class DualPumpBlenderConfig(Config[DualPumpsBlender]):
    pumps: ConfigOr[DualPumps]
    humidities: SupplyHumiditiesLike = DefaultHumidities
    flow: BlendFlow = DefaultBlendFlow
    demand: Percent | None = None

    def build(self) -> DualPumpsBlender:
        return DualPumpsBlender(
            pumps=resolve(self.pumps),
            humidities=self.humidities,
            flow=self.flow,
            demand=self.demand,
        )


@dataclass(slots=True)
class RigConfig(Config[HumRig]):
    process_interval: Positive = 1.0
    pumps: Config[DualPumpsBlender] | None = None
    tunings: list[Tuning] | None = None
    tuning: str | ControlLawLike = OpenLoopTuning

    def build(self) -> HumRig:
        return HumRig(
            pumps=resolve(self.pumps),
            tunings=self.tunings,
            tuning=self.tuning,
            process_interval=self.process_interval,
            supply_humidities=self.supply_humidities,
        )

    def add_process_time(self, time: Positive) -> Self:
        self.process_time = time
        return self

    def add_pumps(self, pumps: Config[DualPumps] | None) -> Self:
        self.pumps = pumps
        return self

    def add_tunings(self, tunings: list[Tuning] | None) -> Self:
        self.tunings = tunings
        return self

    def add_default_tuning(self, default_tuning: str | None) -> Self:
        self.default_tuning = default_tuning
        return self

    def add_dry_humidity(self, dry_humidity: Percent) -> Self:
        self.dry_humidity = dry_humidity
        return self

    def add_wet_humidity(self, wet_humidity: Percent) -> Self:
        self.wet_humidity = wet_humidity
        return self
