"""The SHT4x driver: one bare sensor, and several namespaced (`sht4x_set`)."""

from __future__ import annotations

from typing import Any

import pytest
from flyball.core.errors import HardwareError
from flyball.core.signal import Access

from humidity.sht4x import (
    SensorEntry,
    Sht4x,
    Sht4xConfig,
    Sht4xReadError,
    Sht4xSet,
    Sht4xSetConfig,
    _crc8,
)


def _encode(temperature: float, humidity: float) -> bytes:
    """The six raw bytes the chip would send for these values."""
    raw_t = round((temperature + 45.0) / 175.0 * 65535.0)
    raw_h = round((humidity - -6.0) / 125.0 * 65535.0)
    t = raw_t.to_bytes(2, "big")
    h = raw_h.to_bytes(2, "big")
    return t + bytes([_crc8(t)]) + h + bytes([_crc8(h)])


class FakeI2CBus:
    """Replies from a table by address; remembers every trigger byte written."""

    def __init__(self, replies: dict[int, bytes]) -> None:
        self.replies = dict(replies)
        self.written: list[tuple[int, bytes]] = []
        self.reads = 0

    def try_lock(self) -> bool:
        return True

    def unlock(self) -> None:
        pass

    def writeto(self, address: int, buffer: bytes) -> None:
        self.written.append((address, bytes(buffer)))

    def readfrom_into(self, address: int, buffer: bytearray) -> None:
        self.reads += 1
        data = self.replies[address]
        buffer[: len(data)] = data


@pytest.fixture
def i2c() -> FakeI2CBus:
    return FakeI2CBus({
        0x44: _encode(21.9, 45.0),
        0x45: _encode(20.0, 4.1),
        0x46: _encode(22.0, 95.0),
    })


class TestSht4x:
    def test_tree_and_one_transaction_read(self, i2c: FakeI2CBus, fresh: Any) -> None:
        device = Sht4x(fresh("chamber"), i2c, address=0x44)
        assert {s.name: s.access for s in device.signals.values()} == {
            "humidity": Access.RP,
            "temperature": Access.RP,
        }
        (sample,) = list(device.read(0))
        values = sample.by_name()
        assert values["humidity"] == pytest.approx(45.0, abs=0.01)
        assert values["temperature"] == pytest.approx(21.9, abs=0.01)
        assert i2c.written == [(0x44, bytes((0xFD,)))]
        assert i2c.reads == 1, "one trigger, one collect"

    def test_a_bad_crc_is_a_hardware_error(self, fresh: Any) -> None:
        bad = FakeI2CBus({0x44: bytes(6)})  # all zero: CRC mismatches
        device = Sht4x(fresh("chamber"), bad, address=0x44)
        with pytest.raises(Sht4xReadError):
            list(device.read(0))
        assert issubclass(Sht4xReadError, HardwareError)

    def test_config_builds_from_the_envelope(self, i2c: FakeI2CBus, fresh: Any) -> None:
        config = Sht4xConfig(link=i2c, address=0x44)
        device = config.build(fresh("chamber"))
        humidity = next(iter(device.read(0))).by_name()["humidity"]
        assert humidity == pytest.approx(45.0, abs=0.01)


class TestSht4xSet:
    def test_one_namespace_per_sensor_each_atomic(self, i2c: FakeI2CBus, fresh: Any) -> None:
        device = Sht4xSet(fresh("hum"), i2c, {"chamber": 0x44, "dry": 0x45, "wet": 0x46})
        assert set(device.nodes) == {"chamber", "dry", "wet"}
        assert all(device.nodes[n].atomic for n in ("chamber", "dry", "wet"))
        samples = {s.node.name: s for s in device.read(0)}
        dry_values, wet_values = samples["dry"].by_name(), samples["wet"].by_name()
        assert dry_values["humidity"] == pytest.approx(4.1, abs=0.01)
        assert dry_values["temperature"] == pytest.approx(20.0, abs=0.01)
        assert wet_values["humidity"] == pytest.approx(95.0, abs=0.01)
        assert wet_values["temperature"] == pytest.approx(22.0, abs=0.01)
        assert i2c.reads == 3, "one transaction per sensor"

    def test_a_namespace_not_yet_due_is_skipped_on_a_whole_device_read(
        self, i2c: FakeI2CBus, fresh: Any
    ) -> None:
        device = Sht4xSet(fresh("hum"), i2c, {"chamber": 0x44, "dry": 0x45})
        device.nodes["dry"].override(poll_s=5.0)
        device.nodes["chamber"].override(poll_s=1.0)
        first = {s.node.name for s in device.read(0)}
        assert first == {"chamber", "dry"}, "never read before: both due"
        second = {s.node.name for s in device.read(1_000_000_000)}  # +1 s: dry (5 s) not due yet
        assert second == {"chamber"}
        third = {s.node.name for s in device.read(5_000_000_000)}  # +5 s: dry due again
        assert third == {"chamber", "dry"}

    def test_a_direct_node_read_ignores_its_own_due_time(self, i2c: FakeI2CBus, fresh: Any) -> None:
        """`rig.read(node, fresh=True)` must always transact, not wait out the period."""
        device = Sht4xSet(fresh("hum"), i2c, {"dry": 0x45})
        device.nodes["dry"].override(poll_s=5.0)
        list(device.read(0, device.nodes["dry"]))
        again = list(
            device.read(1_000_000_000, device.nodes["dry"])
        )  # +1 s: not due, but named directly
        assert len(again) == 1

    def test_at_least_one_sensor_is_required(self, i2c: FakeI2CBus, fresh: Any) -> None:
        with pytest.raises(ValueError, match="at least one sensor"):
            Sht4xSet(fresh("hum"), i2c, {})

    def test_config_builds_the_namespaces(self, i2c: FakeI2CBus, fresh: Any) -> None:
        config = Sht4xSetConfig(
            link=i2c,
            sensors={"chamber": SensorEntry(address=0x44), "dry": SensorEntry(address=0x45)},
        )
        device = config.build(fresh("hum"))
        assert set(device.nodes) == {"chamber", "dry"}
