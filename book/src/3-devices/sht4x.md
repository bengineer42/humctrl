# The sensor device: SHT4x

*How the sensor is read, at what rate, in what units, and what it reports when it fails.*

This rig has no sensor driver of its own: `sht4x`/`sht4x_set` live in
`flyball-chips` (`extensions/chips/src/flyball_chips/sht4x.py`), a
dependency of this package (`examples/humidity/pyproject.toml`:
`flyball-chips`). Both are built on `Sht4xSensor` — one chip on an
`I2cLink` (`flyball.hardware.i2c`: register reads/writes at an address,
plus raw `write`/`read` for a chip with no register map, like this one):

| driver | class | tree |
| --- | --- | --- |
| `sht4x` | `Sht4x` | one chip on the device root: `humidity`, `temperature [RP]` |
| `sht4x_set` | `Sht4xSet` | several chips, one atomic namespace each |

This rig uses `sht4x_set` for `hum_sensors` — see [Devices and
signals](index.md) for the three namespaces it declares.

## One I2C transaction, command to decode

`Sht4xSensor.read` sends the measure command, waits the conversion time,
and reads back six bytes, in one call:

```python
COMMANDS: dict[str, tuple[int, float]] = {
    "high": (0xFD, 0.0083),
    "medium": (0xF6, 0.0045),
    "low": (0xE0, 0.0016),
}

def read(self) -> tuple[float, float]:
    command, wait_s = COMMANDS[self.precision]
    self.link.write(self.address, [command])
    if self.sleep:
        time.sleep(wait_s)
    return decode(self.link.read(self.address, 6))
```

`precision` (`"high"`/`"medium"`/`"low"`, default `"high"`) picks the
command byte and the datasheet's own maximum conversion time for it; this
rig doesn't override it, so every sensor reads at high precision, no
on-die heater. `decode` checks two CRC-8 bytes (polynomial `0x31`, initial
`0xFF`) and returns `(temperature, humidity)`:

```python
def decode(frame: bytes) -> tuple[float, float]:
    if len(frame) != 6:
        raise HardwareError(f"SHT4x reply is {len(frame)} bytes, not 6")
    for word, crc in ((frame[0:2], frame[2]), (frame[3:5], frame[5])):
        if crc8(word) != crc:
            raise HardwareError(f"SHT4x CRC mismatch in {frame.hex()}")
    raw_t = int.from_bytes(frame[0:2], "big")
    raw_h = int.from_bytes(frame[3:5], "big")
    temperature = -45.0 + 175.0 * raw_t / 65535.0
    humidity = min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0))
    return temperature, humidity
```

A short reply or a CRC mismatch raises `HardwareError` directly — no
sensor-specific error type — so the polling loop takes the device offline
rather than delivering a bad sample (see [Failure
modes](../1-running/failures.md)). Humidity is clamped to `[0, 100]` in
the decode; temperature is not — a sensor can read below 0 °C or above
the datasheet's usual span without being clipped.

## `Sht4xSet`: several sensors, one namespace each

Each sensor is its own `NodeSpec(atomic=True, children=(humidity,
temperature))`, so `hum_sensors.dry` is read as one `Sample` — one I2C
transaction — never mixed with `chamber`'s or `wet`'s. `read(time_ns,
node)`:

- given no node (the periodic poll), reads only the namespaces **due** —
  at least `0.9 × poll_s` since the last read, on each namespace's own
  period: `chamber` roughly every second, `dry`/`wet` every five, per
  `rig-multi-sensor.yaml`'s `signals:` metadata;
- given a node directly (`rig.read(hum_sensors.dry, fresh=True)`), reads it
  regardless of when it was last due.

At least one sensor is required — an `sht4x_set` with an empty `sensors:`
map is a config error, not an empty device.

## Config

```yaml
hum_sensors:
  driver: sht4x_set
  label: Humidity sensors
  poll_s: 1
  link: i2c1
  sensors:
    chamber: { address: 0x44 }
    dry: { address: 0x45 }
    wet: { address: 0x46 }
  signals:
    chamber: { signals: { humidity: { warning: [20, 80] } } }
    dry: { poll_s: 5 }
    wet: { poll_s: 5 }
```

`link` names an I2C bus link (`i2c1: { type: i2c, bus: 1 }`,
`/dev/i2c-1` through `smbus2` — `flyball_linux.links.i2c.I2cConfig`); each
sensor in `sensors` is just its address (`precision` would
set `"medium"`/`"low"` for all of them, if this rig ever needed to trade
accuracy for speed — it doesn't). `signals:` in the envelope carries
metadata only — `chamber.humidity`'s warning band, and the two supply
lines' slower poll — never a new tree; the tree itself (which namespaces
exist) is declared by `sensors`, not by `signals:`.
