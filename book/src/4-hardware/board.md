# Board and wiring

Three things hang off the Pi's header: the sensors on the I²C bus, the
motor driver on the two PWM pins, and power for the pumps. `rig.yaml`
names them as the `i2c1` and `pwm0` links; the board profile
(`flyball_linux/boards/rpi4.toml`, [Boards](https://bengineer42.github.io/flyball/latest/2-config/boards/)) says which pins
those are.

## Sensors: I²C

| Pi header | signal | to |
| --- | --- | --- |
| pin 1 | 3V3 | sensor VDD |
| pin 3 | GPIO2 / SDA1 | sensor SDA |
| pin 5 | GPIO3 / SCL1 | sensor SCL |
| pin 6 | GND | sensor GND |

Three SHT4x breakouts share the one bus at three addresses -- `0x44` in the
chamber, `0x45` on the dry line, `0x46` on the wet line -- which is what
`sensors:` under `hum_sensors` in `rig.yaml` says. Boards with a
Qwiic / STEMMA QT connector daisy-chain with no soldering; three distinct
addresses need address-variant parts (or a multiplexer -- see the
[bill of materials](bom.md)). Keep the leads short; I²C over a metre of
cable in a humid chamber is where intermittent `offline` conditions come
from.

## Pumps: PWM and the motor driver

| Pi header | signal | to (TB6612) | |
| --- | --- | --- | --- |
| pin 12 | GPIO18 / PWM0 | PWMA | the dry pump's speed |
| pin 35 | GPIO19 / PWM1 | PWMB | the wet pump's speed |
| any GPIO or 3V3 | | STBY | high, or the driver sleeps |
| 3V3 / GND | | AIN1 / AIN2, BIN1 / BIN2 | one high, one low per channel: the direction, fixed |
| pin 1 | 3V3 | VCC | the driver's logic supply |
| pin 6 | GND | GND | common with the pump supply |

The TB6612FNG is a dual H-bridge: each channel takes a PWM on `PWMx` and a
direction on `xIN1`/`xIN2`, and switches the motor supply `VM` to the pump.
Pumps only ever run one way, so the direction pins are wired once. Channel
A is `dry`, channel B `wet`, matching `dry: { channel: 0 }` and
`wet: { channel: 1 }` in `rig.yaml`; swap the wires or the numbers, not both.

## Power

The pumps run from their own supply into `VM` (6 V for the pumps listed in
the [bill of materials](bom.md)) -- never from the Pi's 5 V rail, which
cannot source a motor's start-up current and browns out the Pi when asked.
Grounds are common. The TB6612 is rated 1.2 A continuous, 3.2 A peak per
channel; the pumps draw about 0.5 A running and up to 2 A starting, so the
start-up spike is inside the driver's peak rating but the two pumps must not
be started at the same instant from a supply that cannot give 4 A -- the
blender's `deadband` and the driver's ramp keep starts apart in practice.

The schematics of the two breakout boards this was built with are in the
repository under `docs/` (Adafruit TB6612 and SparkFun TB6612FNG, KiCad and
Eagle).
