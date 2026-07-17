"""Laser emitter, gated by the safety interlock.

The laser NEVER fires on its own. `fire()` takes a SafetyDecision and refuses
unless it permits. On real hardware, `RelayLaser` also depends on the hardware
interlock physically cutting power — this class is the software half of a
defence-in-depth pair, not the only guard.
"""
from __future__ import annotations

from .safety import SafetyDecision


class MockLaser:
    """Records fire/off events instead of driving a pin."""

    def __init__(self) -> None:
        self.is_on = False
        self.fire_count = 0
        self.blocked_count = 0

    def _set(self, on: bool) -> None:
        self.is_on = on

    def fire(self, decision: SafetyDecision) -> bool:
        if not decision.permit_fire:
            self.blocked_count += 1
            self._set(False)
            return False
        self.fire_count += 1
        self._set(True)
        return True

    def off(self) -> None:
        self._set(False)


class GpioLaser(MockLaser):
    """Real laser keyed by a GPIO line. Reuses the safety gate of MockLaser and
    only adds the pin write, so the safety logic is identical and tested."""

    def __init__(self, pin: int):
        super().__init__()
        import RPi.GPIO as GPIO  # type: ignore

        self._GPIO = GPIO
        self._pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    def _set(self, on: bool) -> None:
        super()._set(on)
        self._GPIO.output(self._pin, self._GPIO.HIGH if on else self._GPIO.LOW)
