"""Real hardware, imported only when actually built -- so a Pi is not required to import a driver.

`LinuxPWMPump` drives one PWM channel through `linux_pwm`; `blender.py`'s
`DualPumpBlenderConfig.build` imports it locally, on a real `linux_pwm`
link (`humidity.links.PwmChipConfig`).
"""

from __future__ import annotations

from typing import Any

from flyball.core.typing import Normalised

from humidity.pumps.drivers import PumpDriver

DEFAULT_PWM_FREQUENCY: float = 20_000.0  # Hz


class LinuxPWMPump(PumpDriver):
    """One `linux_pwm` channel, driven 0-1 of full with an optional deadband."""

    def __init__(
        self,
        channel: int,
        frequency: float,
        deadband: float = 0.0,
        chip: Any = 0,
        timeout: float = 10,
    ) -> None:
        from linux_pwm import PWMChannel

        self._deadband = deadband
        self._effort: Normalised = 0.0
        self.pwm = PWMChannel(channel=channel, chip=chip, timeout=timeout)
        self.pwm.set_frequency(frequency)

    @property
    def deadband(self) -> float:
        return self._deadband

    @property
    def effort(self) -> Normalised:
        return self._effort

    def calculate_duty_ratio(self, effort: float) -> float:
        return (1.0 - self._deadband) * max(0.0, min(effort, 1.0)) + self._deadband

    def set_effort(self, effort: Normalised) -> Normalised:
        self.pwm.set_duty_ratio(self.calculate_duty_ratio(effort))
        if effort > 0.0 and not self.pwm.enabled:
            self.pwm.enable()
        self._effort = effort
        return self._effort

    def stop(self) -> None:
        self.pwm.stop()
        self._effort = 0.0
