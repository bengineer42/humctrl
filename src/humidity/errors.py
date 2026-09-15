"""Domain errors, grouped by the answer they give a caller.

The four bases below carry the whole classification: what a caller (or an HTTP
client) can do about a failure, rather than which subsystem raised it. Each also
mixes in the builtin a library consumer would reach for, so ``except LookupError``
and ``except RuntimeError`` behave as expected without importing anything here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.core.errors import ConflictError, HardwareError, NotReadyError

if TYPE_CHECKING:
    from humidity.readers import HTReaderSource


class RecorderNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Recorder not set. Use set_recorder() to set a recorder before starting recording."
        )


class ReadersNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Readers not set. Use set_readers() to set readers before reading sensor data."
        )


class PumpsNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("Pumps not set. Use set_pumps() to set pumps before using them.")


class ProcessReadingNotAvailableError(NotReadyError):
    """The sensor is fitted but has not been read yet. The next loop tick fixes it."""

    def __init__(self) -> None:
        super().__init__(
            "Process reading not available. Use read_process() to read process data before "
            "accessing it."
        )


# endregion

# region Wrong state


class RigNotRunningError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Rig not running. Use start() to start the rig before calling this method."
        )


class TargetHumidityNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Target humidity not set. Use start_regulating() to set a target humidity before "
            "reading regulated humidity."
        )


class TargetStreamNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Target stream not set. Use start_stream() to set a target stream before "
            "reading regulated humidity."
        )


class CurrentWetFractionNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Current wet fraction not set. Use set_flows() or set_blend() to set the current wet "
            "fraction before calling this method."
        )


# region Hardware


class ReaderError(HardwareError):
    def __init__(self, reader: HTReaderSource, error: Exception) -> None:
        super().__init__(f"Error reading from {reader}: {error}")


# endregion
