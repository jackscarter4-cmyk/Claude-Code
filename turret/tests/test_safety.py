from turret.config import SafetyConfig
from turret.safety import SafetyInputs, SafetyInterlock, State


def _all_good():
    return SafetyInputs(
        key_on=True, enclosure_closed=True, estop_pressed=False,
        backstop_present=True, target_in_range=True,
        target_size_px=12.0, wingbeat_ok=True,
    )


def test_permits_when_everything_ok():
    s = SafetyInterlock(SafetyConfig())
    d = s.evaluate(_all_good())
    assert d.permit_fire and d.state is State.ARMED


def test_each_missing_condition_blocks():
    cfg = SafetyConfig()
    for field, val, needle in [
        ("key_on", False, "key"),
        ("enclosure_closed", False, "enclosure"),
        ("backstop_present", False, "backstop"),
        ("target_in_range", False, "working volume"),
        ("wingbeat_ok", False, "wing-beat"),
    ]:
        inp = _all_good()
        setattr(inp, field, val)
        d = SafetyInterlock(cfg).evaluate(inp)
        assert not d.permit_fire
        assert any(needle in r for r in d.reasons), (field, d.reasons)


def test_oversized_target_blocks():
    inp = _all_good()
    inp.target_size_px = 200.0
    d = SafetyInterlock(SafetyConfig()).evaluate(inp)
    assert not d.permit_fire
    assert any("too large" in r for r in d.reasons)


def test_estop_latches_until_reset():
    s = SafetyInterlock(SafetyConfig())
    inp = _all_good()
    inp.estop_pressed = True
    d = s.evaluate(inp)
    assert d.state is State.FAULT and not d.permit_fire

    # releasing the button alone must NOT re-arm
    d = s.evaluate(_all_good())
    assert d.state is State.FAULT and not d.permit_fire

    # explicit reset clears it
    s.reset()
    d = s.evaluate(_all_good())
    assert d.permit_fire and d.state is State.ARMED
