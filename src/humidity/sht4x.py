from __future__ import annotations

import struct
import time
from threading import RLock

from flyball.core import Percent
from flyball.hardware import I2CBus

from humidity.readers import HTReading, HTSource

_TRIGGER = 0xFD  # Mode.NOHEAT_HIGHPRECISION
_CONVERSION_NS = 8_500_000  # datasheet t_meas max 8.3 ms, plus margin
SHT4X_ADDRESS = 0x44


class CrcError(Exception):
    """Raised when a sensor read fails due to CRC mismatch."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SHT4xTriggerError(Exception):
    """Raised when a sensor trigger fails."""

    def __init__(self, source: HTSource, cause: OSError) -> None:
        self.source = source
        self.cause = cause
        super().__init__(f"Trigger failed for sensor {source.name!r}: {cause}")


class SHT4xReadError(Exception):
    """Raised when a sensor read fails."""

    def __init__(self, source: HTSource, cause: OSError | CrcError) -> None:
        self.source = source
        self.cause = cause
        super().__init__(f"Read failed for sensor {source.name!r}: {cause}")


def _crc8(data: bytes) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _decode(buf: bytes) -> tuple[Percent, float]:
    if _crc8(buf[0:2]) != buf[2] or _crc8(buf[3:5]) != buf[5]:
        raise CrcError(buf.hex())
    raw_t = struct.unpack_from(">H", buf, 0)[0]
    raw_h = struct.unpack_from(">H", buf, 3)[0]
    return (
        min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0)),
        -45.0 + 175.0 * raw_t / 65535.0,
    )


class SHT4x:
    """One SHT4x on an :class:`I2CBus` -- the root bus or a mux lane, it does not care.

    Satisfies ``hardware.bank.TwoPhase``: ``trigger`` starts a conversion,
    ``collect`` waits it out and decodes. ``read`` is the two back to back.
    """

    __slots__ = ("_addr", "_buf", "_i2c", "_lock", "seq", "source")

    def __init__(self, i2c: I2CBus, source: HTSource, address: int = SHT4X_ADDRESS) -> None:
        self._i2c = i2c
        self._addr = address
        self.source = source
        self._lock = RLock()
        self.seq = 0
        self._buf = bytearray(6)

    def trigger(self) -> int:
        """Start a high-precision conversion. Returns the monotonic ns it was sent."""
        with self._lock:
            self._i2c.try_lock()
            try:
                self._i2c.writeto(self._addr, bytes((_TRIGGER,)))
            except OSError as e:
                raise SHT4xTriggerError(self.source, e) from e
            finally:
                self._i2c.unlock()
        return time.monotonic_ns()

    def collect(self, trigger_ns: int, stamp_ns: int) -> HTReading:
        """Wait for the conversion started at ``trigger_ns``, then read and decode."""
        remaining = trigger_ns + _CONVERSION_NS - time.monotonic_ns()
        if remaining > 0:
            time.sleep(remaining / 1e9)
        with self._lock:
            self._i2c.try_lock()
            try:
                self._i2c.readfrom_into(self._addr, self._buf)
                humidity, temperature = _decode(bytes(self._buf))
            except (OSError, CrcError) as e:
                raise SHT4xReadError(self.source, e) from e
            finally:
                self._i2c.unlock()
                self.seq += 1
        return HTReading.of(self.source, self.seq, stamp_ns, humidity, temperature)

    def read(self, time_ns: int) -> HTReading:
        """Trigger, wait, collect. Stamped at the trigger instant in the caller's epoch."""
        offset_ns = time_ns - time.monotonic_ns()
        trigger_ns = self.trigger()
        return self.collect(trigger_ns, trigger_ns + offset_ns)
