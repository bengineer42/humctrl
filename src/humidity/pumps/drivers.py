from typing import TYPE_CHECKING, Protocol, runtime_checkable

from flyball.foundation.typing import Normalised, Positive

from .errors import PumpErrorGroup, PumpHardwareError
from .types import SupplyEfforts

if TYPE_CHECKING:
    from flyball_linux.links.pwm import PwmLink


class PumpDriver(Protocol):
    @property
    def effort(self) -> Normalised: ...

    def set_effort(self, effort: Normalised) -> Normalised: ...

    def stop(self) -> None: ...


class PwmPump:
    """One `PwmLink` channel, driven 0-1 of full with an optional deadband.

    Real hardware (`flyball_linux.links.pwm.PwmConfig`, tag `pwm`) and its
    fake (`fake_pwm`) share this protocol, so this driver -- and the
    blender that uses it -- never touches a real chip in a test.
    """

    __slots__ = ("_deadband", "_effort", "_link", "channel", "period_ns")

    def __init__(
        self, link: "PwmLink", channel: int, frequency_hz: Positive, deadband: Normalised = 0.0
    ) -> None:
        self._link = link
        self.channel = channel
        self.period_ns = round(1e9 / frequency_hz)
        self._deadband = deadband
        self._effort: Normalised = 0.0
        self._link.enable(channel, False)

    @property
    def deadband(self) -> float:
        return self._deadband

    @property
    def effort(self) -> Normalised:
        return self._effort

    def calculate_duty_ratio(self, effort: float) -> float:
        return (1.0 - self._deadband) * max(0.0, min(effort, 1.0)) + self._deadband

    def set_effort(self, effort: Normalised) -> Normalised:
        duty_ns = round(self.calculate_duty_ratio(effort) * self.period_ns)
        self._link.configure(self.channel, self.period_ns, duty_ns)
        self._link.enable(self.channel, effort > 0.0)
        self._effort = effort
        return self._effort

    def stop(self) -> None:
        self.set_effort(0.0)


@runtime_checkable
class DualPumpDriver(Protocol):
    @property
    def dry_effort(self) -> Normalised: ...

    @property
    def wet_effort(self) -> Normalised: ...

    @property
    def efforts(self) -> SupplyEfforts:
        return SupplyEfforts(self.dry_effort, self.wet_effort)

    def set_efforts(self, efforts: SupplyEfforts) -> SupplyEfforts: ...

    def stop(self) -> SupplyEfforts: ...


class PumpPair(DualPumpDriver):
    def __init__(self, dry: PumpDriver, wet: PumpDriver) -> None:
        self.dry = dry
        self.wet = wet

    @property
    def dry_effort(self) -> Normalised:
        return self.dry.effort

    @property
    def wet_effort(self) -> Normalised:
        return self.wet.effort

    def set_efforts(self, efforts: SupplyEfforts) -> SupplyEfforts:
        dry_effort = self.set_dry_effort(efforts.dry)
        wet_effort = self.set_wet_effort(efforts.wet)
        return SupplyEfforts(dry_effort, wet_effort)

    def set_dry_effort(self, effort: Normalised) -> Normalised:
        try:
            return self.dry.set_effort(effort)
        except Exception as e:
            raise PumpHardwareError(f"Dry pump error: {e.__class__.__name__}: {e}") from e

    def set_wet_effort(self, effort: Normalised) -> Normalised:
        try:
            return self.wet.set_effort(effort)
        except Exception as e:
            raise PumpHardwareError(f"Wet pump error: {e.__class__.__name__}: {e}") from e

    def stop_dry(self) -> None:
        try:
            self.dry.stop()
        except Exception as e:
            raise PumpHardwareError(f"Dry pump failed to stop: {e.__class__.__name__}: {e}") from e

    def stop_wet(self) -> None:
        try:
            self.wet.stop()
        except Exception as e:
            raise PumpHardwareError(f"Wet pump failed to stop: {e.__class__.__name__}: {e}") from e

    def stop(self) -> SupplyEfforts:
        errors: list[PumpHardwareError] = []
        for stop_one in (self.stop_dry, self.stop_wet):
            try:
                stop_one()
            except PumpHardwareError as e:
                errors.append(e)
        if errors:
            raise PumpErrorGroup("Failed to stop pumps", errors)
        return self.efforts
