from typing import Protocol, runtime_checkable

from flyball.core.typing import Normalised

from .errors import PumpErrorGroup, PumpHardwareError
from .types import SupplyEfforts


class PumpDriver(Protocol):
    @property
    def effort(self) -> Normalised: ...

    def set_effort(self, effort: Normalised) -> Normalised: ...

    def stop(self) -> None: ...


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
