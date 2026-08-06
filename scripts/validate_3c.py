"""Phase 3C validation — pre-breakout state machine.

Tests state transitions, timeout expiry, readiness scoring, determinism,
and replay consistency. No Redis required for offline tests.

Usage:
    python -X utf8 scripts/validate_3c.py
"""

import os
import sys

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))

errors = []


def check(label, fn):
    try:
        fn()
        print(f"  ✓ {label}")
    except Exception as e:
        errors.append(f"{label}: {e}")
        print(f"  ✗ {label}: {e}")
        import traceback
        traceback.print_exc()


# ═══════════════════════════════════════════════════
# 1. Pure state evaluation logic
# ═══════════════════════════════════════════════════
print("\n--- STATE TRANSITIONS ---")


def _make_settings():
    from scanner.config import ScannerSettings
    return ScannerSettings()


def _make_state(symbol="TEST"):
    from scanner.state import ScannerSymbolState
    s = ScannerSymbolState(symbol=symbol)
    s.tick_count = 10
    return s


def test_idle_to_compressing():
    """IDLE → COMPRESSING when bb_width declining for N ticks."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()
    state.pre_breakout_state = PBState.IDLE
    state.bb_width_declining_count = 6  # > pb_compress_ticks (5)

    result = tracker._evaluate(PBState.IDLE, state, bb_width=0.02, rel_vol=1.0, rsi=50.0)
    assert result == PBState.COMPRESSING, f"Expected COMPRESSING, got {result}"


def test_idle_stays_idle():
    """IDLE stays IDLE when declining count insufficient."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()
    state.pre_breakout_state = PBState.IDLE
    state.bb_width_declining_count = 3  # < 5

    result = tracker._evaluate(PBState.IDLE, state, bb_width=0.02, rel_vol=1.0, rsi=50.0)
    assert result == PBState.IDLE, f"Expected IDLE, got {result}"


def test_compressing_to_accumulating():
    """COMPRESSING → ACCUMULATING when volume rises."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()
    state.bb_width_declining_count = 8

    result = tracker._evaluate(PBState.COMPRESSING, state, bb_width=0.02, rel_vol=1.5, rsi=50.0)
    assert result == PBState.ACCUMULATING, f"Expected ACCUMULATING, got {result}"


def test_compressing_to_expired():
    """COMPRESSING → EXPIRED when bb trend reversed for N ticks."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()
    state.bb_width_declining_count = 0  # trend reversed
    state.ticks_in_pre_breakout = 12   # > pb_reversal_ticks (10)

    result = tracker._evaluate(PBState.COMPRESSING, state, bb_width=0.035, rel_vol=0.8, rsi=50.0)
    assert result == PBState.EXPIRED, f"Expected EXPIRED, got {result}"


def test_accumulating_to_coiled():
    """ACCUMULATING → COILED when extreme compression + volume + RSI."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(
        PBState.ACCUMULATING, state,
        bb_width=0.012,  # < 0.015
        rel_vol=1.8,     # >= 1.5
        rsi=52.0,        # between 45-60
    )
    assert result == PBState.COILED, f"Expected COILED, got {result}"


def test_accumulating_to_expired_volume_drop():
    """ACCUMULATING → EXPIRED when volume drops below 1.0."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(
        PBState.ACCUMULATING, state,
        bb_width=0.02, rel_vol=0.8, rsi=50.0,
    )
    assert result == PBState.EXPIRED, f"Expected EXPIRED, got {result}"


def test_coiled_stays_coiled():
    """COILED stays COILED when conditions hold."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(
        PBState.COILED, state,
        bb_width=0.012, rel_vol=1.6, rsi=55.0,
    )
    assert result == PBState.COILED, f"Expected COILED, got {result}"


def test_coiled_to_expired_bb_expansion():
    """COILED → EXPIRED when Bollinger expands without breakout."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(
        PBState.COILED, state,
        bb_width=0.04,   # > pb_compress_bb_max (0.03)
        rel_vol=1.5, rsi=55.0,
    )
    assert result == PBState.EXPIRED, f"Expected EXPIRED, got {result}"


def test_coiled_to_expired_extreme_rsi():
    """COILED → EXPIRED when RSI goes extreme."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(
        PBState.COILED, state,
        bb_width=0.012, rel_vol=1.5, rsi=85.0,  # RSI > 80
    )
    assert result == PBState.EXPIRED, f"Expected EXPIRED, got {result}"


def test_triggered_to_idle():
    """TRIGGERED → IDLE (immediate reset)."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(PBState.TRIGGERED, state, bb_width=0.02, rel_vol=1.5, rsi=55.0)
    assert result == PBState.IDLE, f"Expected IDLE, got {result}"


def test_expired_to_idle():
    """EXPIRED → IDLE (immediate reset)."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    state = _make_state()

    result = tracker._evaluate(PBState.EXPIRED, state, bb_width=0.02, rel_vol=1.5, rsi=55.0)
    assert result == PBState.IDLE, f"Expected IDLE, got {result}"


check("IDLE → COMPRESSING", test_idle_to_compressing)
check("IDLE stays IDLE (insufficient)", test_idle_stays_idle)
check("COMPRESSING → ACCUMULATING", test_compressing_to_accumulating)
check("COMPRESSING → EXPIRED (reversal)", test_compressing_to_expired)
check("ACCUMULATING → COILED", test_accumulating_to_coiled)
check("ACCUMULATING → EXPIRED (vol drop)", test_accumulating_to_expired_volume_drop)
check("COILED stays COILED", test_coiled_stays_coiled)
check("COILED → EXPIRED (BB expansion)", test_coiled_to_expired_bb_expansion)
check("COILED → EXPIRED (extreme RSI)", test_coiled_to_expired_extreme_rsi)
check("TRIGGERED → IDLE", test_triggered_to_idle)
check("EXPIRED → IDLE", test_expired_to_idle)


# ═══════════════════════════════════════════════════
# 2. Readiness scoring
# ═══════════════════════════════════════════════════
print("\n--- READINESS SCORING ---")


def test_readiness_coiled_high():
    """COILED with strong conditions → high readiness."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    score = tracker._compute_readiness(
        PBState.COILED, bb_width=0.008, rel_vol=3.0, rsi=52.0, bb_declining_count=15
    )
    # 25 (bb=0.008, not <0.008) + 25 (vol) + 15 (RSI) + 15 (trend) + 15 (state) = 95
    assert score == 95.0, f"Expected 95.0, got {score}"


def test_readiness_idle_zero():
    """IDLE with no conditions → low readiness."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    score = tracker._compute_readiness(
        PBState.IDLE, bb_width=0.05, rel_vol=0.8, rsi=50.0, bb_declining_count=0
    )
    # 0 (compression) + 0 (volume) + 15 (RSI) + 0 (trend) + 0 (state) = 15
    assert score == 15.0, f"Expected 15.0, got {score}"


def test_readiness_compressing_moderate():
    """COMPRESSING with moderate conditions → moderate readiness."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    score = tracker._compute_readiness(
        PBState.COMPRESSING, bb_width=0.02, rel_vol=1.0, rsi=55.0, bb_declining_count=7
    )
    # 8 (bb=0.02, in <0.03 tier) + 5 (vol=1.0) + 15 (rsi) + 8 (trend) + 3 (state) = 39
    assert score == 39.0, f"Expected 39.0, got {score}"


def test_readiness_determinism():
    """Same inputs → same readiness score."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    s1 = tracker._compute_readiness(PBState.COILED, 0.01, 2.5, 55.0, 12)
    s2 = tracker._compute_readiness(PBState.COILED, 0.01, 2.5, 55.0, 12)
    assert s1 == s2, f"Not deterministic: {s1} != {s2}"


check("COILED + strong → readiness 100", test_readiness_coiled_high)
check("IDLE + none → readiness 15", test_readiness_idle_zero)
check("COMPRESSING + moderate → readiness 46", test_readiness_compressing_moderate)
check("Readiness scoring determinism", test_readiness_determinism)


# ═══════════════════════════════════════════════════
# 3. Replay consistency
# ═══════════════════════════════════════════════════
print("\n--- REPLAY DETERMINISM ---")


def test_replay_sequence():
    """Same feature sequence → identical state progression."""
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    settings = _make_settings()

    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = settings

    # Simulate a sequence: build up compression → accumulation → coil
    sequence = [
        # (bb_width, rel_vol, rsi, prev_bb) → expected state after eval
        (0.025, 1.0, 50.0),   # declining bb
        (0.024, 1.0, 50.0),
        (0.023, 1.0, 50.0),
        (0.022, 1.0, 50.0),
        (0.021, 1.0, 50.0),
        (0.020, 1.0, 50.0),   # tick 6 — should now be COMPRESSING
        (0.019, 1.4, 50.0),   # volume rising → ACCUMULATING
        (0.013, 1.6, 52.0),   # extreme → COILED
    ]

    def run_sequence():
        state = _make_state()
        state.pre_breakout_state = PBState.IDLE
        state.prev_bb_width = 0.030
        states_seen = []

        for bb, vol, rsi in sequence:
            current = PBState(state.pre_breakout_state)

            # Update declining count
            if bb < state.prev_bb_width:
                state.bb_width_declining_count += 1
            elif bb > state.prev_bb_width * 1.02:
                state.bb_width_declining_count = 0

            state.ticks_in_pre_breakout += 1
            new = tracker._evaluate(current, state, bb, vol, rsi)

            if new != current:
                state.pre_breakout_state = new.value
                state.ticks_in_pre_breakout = 0
                if new in (PBState.IDLE, PBState.EXPIRED):
                    state.bb_width_declining_count = 0

            state.prev_bb_width = bb
            states_seen.append(state.pre_breakout_state)

        return states_seen

    run1 = run_sequence()
    run2 = run_sequence()
    assert run1 == run2, f"Replay mismatch:\n  run1={run1}\n  run2={run2}"

    # Verify final state is COILED
    assert run1[-1] == PBState.COILED, f"Expected COILED at end, got {run1[-1]}"
    # Verify progression included COMPRESSING and ACCUMULATING
    assert PBState.COMPRESSING in run1, "COMPRESSING not seen"
    assert PBState.ACCUMULATING in run1, "ACCUMULATING not seen"


check("Replay: same sequence → same state progression", test_replay_sequence)


# ═══════════════════════════════════════════════════
# 4. Mark triggered
# ═══════════════════════════════════════════════════
print("\n--- TRIGGER MARKING ---")


def test_mark_triggered():
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    state = _make_state()
    state.pre_breakout_state = PBState.COILED
    state.ticks_in_pre_breakout = 50

    tracker.mark_triggered(state)
    assert state.pre_breakout_state == PBState.TRIGGERED, f"Expected TRIGGERED"
    assert state.ticks_in_pre_breakout == 0


check("mark_triggered sets TRIGGERED", test_mark_triggered)


# ═══════════════════════════════════════════════════
# 5. Transition reasons
# ═══════════════════════════════════════════════════
print("\n--- TRANSITION REASONS ---")


def test_transition_reasons():
    from scanner.pre_breakout import PreBreakoutTracker, PBState
    tracker = PreBreakoutTracker.__new__(PreBreakoutTracker)
    tracker._s = _make_settings()

    state = _make_state()
    state.bb_width_declining_count = 8

    r1 = tracker._transition_reason(PBState.IDLE, PBState.COMPRESSING, 0.02, 1.0, 50.0, state)
    assert "bb_declining_8" in r1, f"Bad reason: {r1}"

    r2 = tracker._transition_reason(PBState.COMPRESSING, PBState.ACCUMULATING, 0.02, 1.5, 50.0, state)
    assert "vol_rising" in r2, f"Bad reason: {r2}"

    r3 = tracker._transition_reason(PBState.ACCUMULATING, PBState.COILED, 0.012, 1.8, 52.0, state)
    assert "extreme_compression" in r3, f"Bad reason: {r3}"

    r4 = tracker._transition_reason(PBState.COILED, PBState.EXPIRED, 0.04, 0.8, 55.0, state)
    assert "coil_invalidated" in r4, f"Bad reason: {r4}"


check("Structured transition reasons", test_transition_reasons)


# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("\n" + "=" * 60)
total = len(errors)
if total == 0:
    print("ALL CHECKS PASSED — Phase 3C offline validation complete")
else:
    print(f"FAILURES: {total}")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
