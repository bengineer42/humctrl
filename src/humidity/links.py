"""Links the humidity rig's drivers talk over: an I²C bus and a PWM chip.

Both build real hardware only inside `build()` -- the Blinka and
`linux_pwm` imports are local there, so this module (and the drivers that
only need the tag registered) imports cleanly off a Pi.
"""

from __future__ import annotations

from typing import Any

from flyball.core.config import Config
from flyball.hardware import I2CBus


class I2CLinkConfig(Config[I2CBus], tag="i2c"):
    """The Pi's I²C bus, through Adafruit Blinka."""

    bus: int = 1

    def build(self) -> I2CBus:
        import board
        import busio

        if self.bus != 1:
            # Blinka names buses by pin (board.SCL/SDA), not number; the Pi has one.
            raise ValueError(f"only I2C bus 1 is supported, not {self.bus}")
        return busio.I2C(board.SCL, board.SDA)


class PwmChipConfig(Config[Any], tag="linux_pwm"):
    """A PWM chip, through `linux_pwm`; a device names its own channel and frequency."""

    chip: int = 0

    def build(self) -> Any:
        from linux_pwm import PWMChip

        return PWMChip(self.chip)
