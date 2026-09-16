"""The SHT4x humidity/temperature sensor, as a device: one bare, or several namespaced.

`Sht4x` is one sensor on the device root (`humidity`/`temperature [RP]`, one
I²C transaction). `Sht4xSet` is several, each its own atomic namespace, for
a rig with a process sensor beside the two supply lines
(`hum_sensors.dry.humidity`); each namespace is read in its own transaction,
on its own `poll_s`.

The transceiver (`Sht4xSensor`, CRC and decode) has no flyball dependency
beyond [I2CBus][flyball.hardware.I2CBus] -- a `Protocol`, so a fake stands in
for hardware in tests.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Iterator, Mapping
from typing import Any

from flyball.core.device import Device, DriverConfig
from flyball.core.errors import HardwareError
from flyball.core.signal import Access, Node, NodeSpec, Sample, SignalSpec
from flyball.hardware import I2CBus
from pydantic import BaseModel, ConfigDict

from humidity.units import HUMIDITY, TEMPERATURE

_TRIGGER = 0xFD  # Mode.NOHEAT_HIGHPRECISION
_CONVERSION_NS = 8_500_000  # datasheet t_meas max 8.3 ms, plus margin
SHT4X_ADDRESS = 0x44


class CrcError(HardwareError):
    """A sensor read failed its CRC."""


class Sht4xTriggerError(HardwareError):
    """A sensor's conversion could not be triggered."""

    def __init__(self, name: str, cause: OSError) -> None:
        self.cause = cause
        super().__init__(f"{name}: trigger failed: {cause}")


class Sht4xReadError(HardwareError):
    """A sensor's conversion could not be collected."""

    def __init__(self, name: str, cause: OSError | CrcError) -> None:
        self.cause = cause
        super().__init__(f"{name}: read failed: {cause}")


def _crc8(data: bytes) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _decode(buf: bytes) -> tuple[float, float]:
    """(humidity %RH, temperature °C) from the sensor's six raw bytes."""
    if _crc8(buf[0:2]) != buf[2] or _crc8(buf[3:5]) != buf[5]:
        raise CrcError(buf.hex())
    raw_t = struct.unpack_from(">H", buf, 0)[0]
    raw_h = struct.unpack_from(">H", buf, 3)[0]
    return (
        min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0)),
        -45.0 + 175.0 * raw_t / 65535.0,
    )


class Sht4xSensor:
    """One SHT4x on an [I2CBus][flyball.hardware.I2CBus]: trigger, wait, collect, in one `read`."""

    __slots__ = ("_addr", "_buf", "_i2c", "_lock", "name")

    def __init__(self, i2c: I2CBus, address: int, name: str) -> None:
        self._i2c = i2c
        self._addr = address
        self.name = name
        self._buf = bytearray(6)

    def trigger(self) -> int:
        """Start a high-precision conversion. Returns the monotonic ns it was sent."""
        self._i2c.try_lock()
        try:
            self._i2c.writeto(self._addr, bytes((_TRIGGER,)))
        except OSError as e:
            raise Sht4xTriggerError(self.name, e) from e
        finally:
            self._i2c.unlock()
        return time.monotonic_ns()

    def collect(self, trigger_ns: int) -> tuple[float, float]:
        """Wait for the conversion started at `trigger_ns`, then read and decode."""
        remaining = trigger_ns + _CONVERSION_NS - time.monotonic_ns()
        if remaining > 0:
            time.sleep(remaining / 1e9)
        self._i2c.try_lock()
        try:
            self._i2c.readfrom_into(self._addr, self._buf)
            return _decode(bytes(self._buf))
        except (OSError, CrcError) as e:
            raise Sht4xReadError(self.name, e) from e
        finally:
            self._i2c.unlock()

    def read(self, time_ns: int) -> tuple[float, float]:
        """Trigger, wait, collect: (humidity %RH, temperature °C), one I²C transaction."""
        return self.collect(self.trigger())


def _tree() -> tuple[SignalSpec, ...]:
    return (
        SignalSpec(
            name="humidity", quantity=HUMIDITY, access=Access.RP, range=(0.0, 100.0), precision=1
        ),
        SignalSpec(
            name="temperature",
            quantity=TEMPERATURE,
            access=Access.RP,
            range=(-40.0, 125.0),
            precision=2,
        ),
    )


class Sht4x(Device):
    """One SHT4x on the device root: `humidity`, `temperature [RP]`, one I²C transaction."""

    TREE = _tree()

    def __init__(
        self, name: str, i2c: I2CBus, address: int = SHT4X_ADDRESS, label: str | None = None
    ) -> None:
        super().__init__(name, label)
        self._sensor = Sht4xSensor(i2c, address, name)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        humidity, temperature = self._sensor.read(time_ns)
        yield Sample(
            self.root,
            time_ns,
            {self.signals["humidity"]: humidity, self.signals["temperature"]: temperature},
        )


class Sht4xConfig(DriverConfig[Sht4x], tag="sht4x"):
    """One SHT4x by its I²C address."""

    link: Any = None
    """An [I2CBus][flyball.hardware.I2CBus], by link name."""
    address: int = SHT4X_ADDRESS

    def build(self, name: str, label: str | None = None) -> Sht4x:
        return Sht4x(name, self.link, self.address, label)


class SensorEntry(BaseModel):
    """One sensor of an `sht4x_set`: its I²C address."""

    model_config = ConfigDict(extra="forbid")

    address: int = SHT4X_ADDRESS


class Sht4xSet(Device):
    """Several SHT4x, each its own atomic namespace, read one transaction each when due."""

    def __init__(
        self, name: str, i2c: I2CBus, sensors: Mapping[str, int], label: str | None = None
    ) -> None:
        super().__init__(name, label)
        if not sensors:
            raise ValueError(f"{name}: an sht4x_set reads at least one sensor")
        self.bind(
            tuple(
                NodeSpec(name=sensor_name, atomic=True, children=_tree()) for sensor_name in sensors
            )
        )
        self._sensors = {
            sensor_name: Sht4xSensor(i2c, address, f"{name}.{sensor_name}")
            for sensor_name, address in sensors.items()
        }
        self._last_read_ns: dict[str, int] = {}

    def _due(self, node: Node, time_ns: int) -> bool:
        last = self._last_read_ns.get(node.name)
        if last is None:
            return True
        period_s = node.poll_s
        return period_s is None or (time_ns - last) >= period_s * 1e9

    def _sample(self, node: Node, time_ns: int) -> Sample:
        humidity, temperature = self._sensors[node.name].read(time_ns)
        self._last_read_ns[node.name] = time_ns
        return Sample(
            node,
            time_ns,
            {node.signals["humidity"]: humidity, node.signals["temperature"]: temperature},
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One sample per due namespace, each its own I²C transaction.

        `node` names one namespace: always read, `fresh` or not. `None` (or
        the root, the periodic poll): only the namespaces due on their own
        `poll_s`.
        """
        if node is not None and node is not self.root:
            yield self._sample(node, time_ns)
            return
        for child in self.root.children.values():
            if self._due(child, time_ns):
                yield self._sample(child, time_ns)


class Sht4xSetConfig(DriverConfig[Sht4xSet], tag="sht4x_set"):
    """Several SHT4x on one bus, one namespace per sensor."""

    link: Any = None
    """An [I2CBus][flyball.hardware.I2CBus], by link name."""
    sensors: dict[str, SensorEntry]

    def build(self, name: str, label: str | None = None) -> Sht4xSet:
        return Sht4xSet(name, self.link, {n: s.address for n, s in self.sensors.items()}, label)
