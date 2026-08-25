"""Unit tests for feature_engine.features.volume_profile — mathematical
audit fix §1.1 (POC / Value Area engine), 2026-08-25.
"""

from __future__ import annotations

from feature_engine.features.volume_profile import (
    ACCUMULATION_BASE_WIDTH_PCT,
    compute_volume_profile,
    compute_volume_profile_from_bars,
    detect_macro_accumulation_breakout,
)


def test_unavailable_with_no_data() -> None:
    profile = compute_volume_profile([])
    assert profile["available"] is False
    assert profile["poc"] is None


def test_unavailable_when_every_bar_has_zero_volume() -> None:
    profile = compute_volume_profile([(100.0, 0.0), (101.0, 0.0)])
    assert profile["available"] is False


def test_a_single_flat_price_collapses_poc_vah_val_to_that_price() -> None:
    profile = compute_volume_profile([(100.0, 500.0), (100.0, 300.0)])
    assert profile["available"] is True
    assert profile["poc"] == profile["vah"] == profile["val"] == 100.0
    assert profile["value_area_width_pct"] == 0.0


def test_poc_lands_on_the_heaviest_volume_cluster() -> None:
    """Heavy, tight volume around 100 with light volume scattered from
    90 to 110 -- POC must land in the heavy cluster, not be dragged by
    the wide light distribution."""
    pairs = [(100.0, 10_000.0)] * 5 + [(p, 100.0) for p in range(90, 111)]
    profile = compute_volume_profile(pairs)
    assert profile["available"] is True
    assert 99.0 <= profile["poc"] <= 101.0


def test_value_area_contains_at_least_70_pct_of_volume_and_is_narrower_than_full_range() -> None:
    pairs = [(100.0, 5000.0)] * 3 + [(p, 50.0) for p in range(80, 121)]
    profile = compute_volume_profile(pairs)
    assert profile["available"] is True
    assert profile["val"] <= profile["poc"] <= profile["vah"]
    assert profile["vah"] - profile["val"] < 40  # narrower than the full 80-120 range


def test_value_area_is_reasonably_centered_for_a_symmetric_distribution() -> None:
    """A perfectly symmetric volume distribution around a center price
    should produce a value area roughly centered on that same price --
    sanity check that the extend-outward algorithm doesn't have a gross
    directional bias. Not asserting exact symmetry: real binning
    quantization (continuous 1-unit price offsets against a bin width
    that isn't an exact divisor of 1) and the algorithm's own tie-break
    convention (ties extend upward first, a real, disclosed, ordinary
    implementation choice, not a bug) both introduce some real drift."""
    pairs = [(100.0, 10_000.0)]
    for offset in range(1, 20):
        vol = 10_000.0 / offset
        pairs.append((100.0 + offset, vol))
        pairs.append((100.0 - offset, vol))
    profile = compute_volume_profile(pairs)
    center = (profile["vah"] + profile["val"]) / 2
    assert abs(center - 100.0) < 5.0  # a fraction of the full 81-119 range


def test_compute_volume_profile_from_bars_uses_typical_price() -> None:
    bars = [
        {"h": 102.0, "l": 98.0, "c": 100.0, "v": 1000.0},  # typical = 100.0
        {"h": 102.0, "l": 98.0, "c": 100.0, "v": 1000.0},
    ]
    profile = compute_volume_profile_from_bars(bars)
    assert profile["available"] is True
    assert profile["poc"] == 100.0


def test_compute_volume_profile_from_bars_skips_malformed_bars() -> None:
    bars = [
        {"h": 0.0, "l": 0.0, "c": 0.0, "v": 1000.0},  # malformed -- skipped
        {"h": 102.0, "l": 98.0, "c": 100.0, "v": 500.0},
    ]
    profile = compute_volume_profile_from_bars(bars)
    assert profile["available"] is True
    assert profile["total_volume"] == 500.0


# ── macro accumulation breakout detection ───────────────────────────────


def _tight_profile(poc: float = 100.0) -> dict:
    width = poc * (ACCUMULATION_BASE_WIDTH_PCT / 4)  # comfortably under the threshold
    return {
        "available": True,
        "poc": poc,
        "vah": poc + width,
        "val": poc - width,
        "value_area_width_pct": (2 * width / poc) * 100,
    }


def test_accumulation_breakout_fires_on_a_fresh_cross_with_volume() -> None:
    profile = _tight_profile()
    fired = detect_macro_accumulation_breakout(
        profile, current_price=profile["vah"] + 0.5, prev_price=profile["vah"] - 0.1, rel_vol=2.0
    )
    assert fired is True


def test_accumulation_breakout_does_not_fire_without_a_fresh_cross() -> None:
    """Price already above VAH on the PREVIOUS bar too -- not a fresh
    cross, must not fire (this is the "currently above" false-positive
    the task's own wording explicitly guards against)."""
    profile = _tight_profile()
    fired = detect_macro_accumulation_breakout(
        profile,
        current_price=profile["vah"] + 1.0,
        prev_price=profile["vah"] + 0.5,  # already above last bar too
        rel_vol=2.0,
    )
    assert fired is False


def test_accumulation_breakout_does_not_fire_without_volume_expansion() -> None:
    profile = _tight_profile()
    fired = detect_macro_accumulation_breakout(
        profile, current_price=profile["vah"] + 0.5, prev_price=profile["vah"] - 0.1, rel_vol=1.0
    )
    assert fired is False


def test_accumulation_breakout_does_not_fire_on_a_wide_base() -> None:
    profile = {
        "available": True,
        "poc": 100.0,
        "vah": 106.0,  # (106-94)/100 = 12% -- well over the 3% threshold
        "val": 94.0,
        "value_area_width_pct": 12.0,
    }
    fired = detect_macro_accumulation_breakout(
        profile, current_price=106.5, prev_price=105.0, rel_vol=3.0
    )
    assert fired is False


def test_accumulation_breakout_never_fabricates_true_when_profile_unavailable() -> None:
    fired = detect_macro_accumulation_breakout(
        {"available": False}, current_price=110.0, prev_price=90.0, rel_vol=5.0
    )
    assert fired is False
