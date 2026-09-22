# Bill of materials

What the rig is built from, as far as the configuration and the design
notes record it. The original build's parts list, with the pumps' measured
duty-to-flow curves, was in a handover note that has been lost; part
numbers below are the families the design specifies, not verified orders.

| part | qty | notes |
| --- | --- | --- |
| Raspberry Pi 4 (any RAM), SD card, PSU | 1 | runs the daemon; [Raspberry Pi setup](pi.md) |
| Sensirion SHT45 humidity / temperature sensor on a breakout with Qwiic / STEMMA QT | 3 | one in the chamber (`0x44`), one per supply line (`0x45`, `0x46`); three addresses means address-variant parts, or a Qwiic I²C multiplexer with three `0x44` boards and a matching change to `sensors:` |
| Qwiic / STEMMA QT cables, and a splitter or hub | as needed | daisy-chain the three sensors on the one bus |
| TB6612FNG dual motor driver breakout (Adafruit or SparkFun) | 1 | both pumps; schematics under `docs/` |
| 6 V DC diaphragm air pump, ~0.5 A running, ≤ 2 A starting | 2 | one dry line, one wet line; measure duty → flow for each |
| 6 V supply, ≥ 4 A | 1 | the pumps' `VM`; not the Pi's rail |
| Tubing, a wet bottle (bubbler) and a desiccant column | 1 each | the wet and dry supplies |
| The chamber, with three sensor ports and two inlets | 1 | volume sets the time constant: `sim.yaml` models 3 L |

Anything that reads absolute humidity over I²C can replace the sensors
(the earlier DHT22 needed an Arduino in between; the SHT45 does not), and
any driver that meets the [pump page](pumps.md)'s three checks can replace
the TB6612. Both are a change to `rig-multi-sensor.yaml` and, for a new chip, a driver:
[the flyball book's Extending](https://bengineer42.github.io/flyball/latest/3-extending/).
