"""Safety interlock state machine.

A steered laser is a moving beam of unpredictable direction, so firing must be
gated by an explicit AND of hardware and software conditions (design doc S6).
Model of trust:

* Hardware conditions (key, enclosure, e-stop) are authoritative. In the real
  build these physically gate laser *power*; software can only ADD inhibits,
  never override a hardware SAFE.
* E-stop latches a FAULT until an explicit reset() — you cannot clear it by just
  releasing the button, matching real e-stop behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import SafetyConfig


class State(Enum):
    SAFE = "SAFE"        # not permitted, no fault
    ARMED = "ARMED"      # all conditions met, firing permitted
    FAULT = "FAULT"      # latched (e.g. e-stop); needs reset()


@dataclass
class SafetyInputs:
    key_on: bool = False
    enclosure_closed: bool = False
    estop_pressed: bool = False
    backstop_present: bool = False   # solid, in-range backstop seen behind target
    target_in_range: bool = False    # depth within calibrated working volume
    target_size_px: float = 0.0      # bbox size of the candidate
    wingbeat_ok: bool = False        # wing-beat gate confirmed an insect


@dataclass
class SafetyDecision:
    permit_fire: bool
    state: State
    reasons: list[str] = field(default_factory=list)   # why firing is blocked


class SafetyInterlock:
    def __init__(self, cfg: SafetyConfig):
        self.cfg = cfg
        self._fault_latched = False

    def reset(self) -> None:
        """Clear a latched fault. Only call after the operator has cleared the
        physical cause (e-stop released, etc.)."""
        self._fault_latched = False

    def evaluate(self, s: SafetyInputs) -> SafetyDecision:
        c = self.cfg

        # E-stop latches a fault immediately and dominates everything.
        if s.estop_pressed:
            self._fault_latched = True
        if self._fault_latched:
            return SafetyDecision(False, State.FAULT, ["e-stop latched (reset required)"])

        reasons: list[str] = []
        if c.require_key and not s.key_on:
            reasons.append("key switch off")
        if c.require_enclosure and not s.enclosure_closed:
            reasons.append("enclosure open")
        if c.require_backstop and not s.backstop_present:
            reasons.append("no solid backstop behind target")
        if not s.target_in_range:
            reasons.append("target outside working volume")
        if s.target_size_px > c.max_target_px:
            reasons.append(f"target too large ({s.target_size_px:.0f}px > {c.max_target_px:.0f}px)")
        if not s.wingbeat_ok:
            reasons.append("wing-beat not confirmed")

        if reasons:
            return SafetyDecision(False, State.SAFE, reasons)
        return SafetyDecision(True, State.ARMED, [])
