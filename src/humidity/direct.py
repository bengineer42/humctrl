from __future__ import annotations

from collections.abc import Generator

from flyball.core import Channel, Normalised
from flyball.core.errors import UnachievableError
from flyball.hardware import TCA9548_ADDRESS, Bank, I2CBus, I2CMux
from linux_pwm import PWMChannel, PWMChip

from humidity.pumps.drivers import PumpDriver
from humidity.pumps.errors import PumpError
from humidity.readers import HTReaderSource, HTReading, HTSetReader, HTSource
from humidity.sht4x import SHT4X_ADDRESS, SHT4x

DEFAULT_PWM_FREQUENCY: float = 20_000.0  # Hz


class PumpFlowError(PumpError, UnachievableError):
    """A requested flow or fraction cannot be applied."""

    def __init__(self, flow: float, max_flow: float, name: str | None = None) -> None:
        self.flow = flow
        self.max_flow = max_flow
        self.name = name
        super().__init__(
            f"flow {flow} exceeds max_flow {max_flow}"
            + (f" for {name}" if name else "")
        )


class LinuxPWMPump(PumpDriver):
    pwm: PWMChannel
    _frequency: float
    _deadband: float
    _effort: float = 0.0

    def __init__(
        self,
        channel: int,
        frequency: float,
        deadband: float = 0.0,
        chip: PWMChip | int = 0,
        timeout: float = 10,
    ) -> None:
        self._frequency = frequency
        self._deadband = deadband
        self.pwm = PWMChannel(channel=channel, chip=chip, timeout=timeout)
        self.pwm.set_frequency(frequency)

    @property
    def deadband(self) -> float:
        return self._deadband

    @property
    def effort(self) -> float:
        return self._effort

    def calculate_duty_ratio(self, effort: float) -> float:
        return (1.0 - self._deadband) * max(0.0, min(effort, 1.0)) + self._deadband

    def set_effort(self, effort: float) -> Normalised:
        self.pwm.set_duty_ratio(self.calculate_duty_ratio(effort))
        if effort > 0.0 and not self.pwm.enabled:
            self.pwm.enable()
        self._effort = effort
        return self._effort

    def stop(self) -> None:
        self.pwm.stop()


def labelled[T](
    dry: T, wet: T, process: T
) -> Generator[tuple[HTReaderSource, T], None, None]:
    yield HTReaderSource.DRY, dry
    yield HTReaderSource.WET, wet
    yield HTReaderSource.PROCESS, process


def muxed_sht4x_readers(
    i2c: I2CBus,
    process_port: int | None = None,
    dry_port: int | None = None,
    wet_port: int | None = None,
    mux_address: int = TCA9548_ADDRESS,
    sensor_address: int = SHT4X_ADDRESS,
) -> Bank[HTReaderSource, HTReading]:
    """Up to three SHT4x behind a TCA9548, read together so their samples share an instant."""
    mux = I2CMux(i2c, mux_address)
    return Bank(
        {
            name: SHT4x(mux.lane(port), HTSource(name), sensor_address)
            for name, port in labelled(dry_port, wet_port, process_port)
            if port is not None
        }
    )


def sht4x_reader(
    i2c: I2CBus,
    source: HTReaderSource = HTReaderSource.PROCESS,
    address: int = SHT4X_ADDRESS,
) -> SHT4x:
    """One SHT4x directly on the bus."""
    return SHT4x(i2c, HTSource(source), address)


class MuxedI2CSHT4xReaders(HTSetReader):
    bank: Bank[HTReaderSource, HTReading]

    sources: dict[HTReaderSource, HTSource]

    def __init__(
        self,
        i2c: I2CBus,
        process_port: int | None = None,
        dry_port: int | None = None,
        wet_port: int | None = None,
        mux_address: int = TCA9548_ADDRESS,
        sensor_address: int = SHT4X_ADDRESS,
    ) -> None:
        mux = I2CMux(i2c, mux_address)
        self.sources = {s: HTSource(s) for s in HTReaderSource}
        self.bank = Bank(
            {
                name: SHT4x(mux.lane(port), HTSource(name), sensor_address)
                for name, port in labelled(dry_port, wet_port, process_port)
                if port is not None
            }
        )

    @property
    def channels(self) -> set[Channel]:
        return {
            channel for source in self.sources.values() for channel in source.channels
        }

    def read_process(self, time_ns: int) -> HTReading | Exception | None:
        return self.bank.read_device(time_ns, HTReaderSource.PROCESS)

    def read_dry(self, time_ns: int) -> HTReading | Exception | None:
        return self.bank.read_device(time_ns, HTReaderSource.DRY)

    def read_wet(self, time_ns: int) -> HTReading | Exception | None:
        return self.bank.read_device(time_ns, HTReaderSource.WET)

    def read(self, time_ns: int) -> list[HTReading | Exception]:
        return list(
            self.bank.read_devices(
                time_ns,
                [HTReaderSource.DRY, HTReaderSource.WET, HTReaderSource.PROCESS],
            ).values()
        )
