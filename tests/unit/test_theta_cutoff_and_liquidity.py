"""Unit tests for pipeline-audit fixes B3 (late-session theta cutoff),
B4 (option liquidity/spread defaults), B1 (static OI-wall proximity),
and B5 (RejectionCode taxonomy), 2026-08-25.
"""

from __future__ import annotations

import json
from datetime import time as dt_time
from types import SimpleNamespace
from typing import Any

from api.routes.market import _liquidity_thresholds, _score_option_leg
from infusion_models.rejection import RejectionCode
from infusion_models.signal import ScanSignalV2
from scanner.suppression import SuppressionGate, _parse_hhmm
from scanner.verdict_engine import OI_WALL_PROXIMITY_PCT, _oi_wall_reason, compute_verdict

# ── B5: RejectionCode enum serialization ────────────────────────────────


def test_rejection_code_is_a_plain_string_everywhere_it_matters() -> None:
    """StrEnum -- must behave as a plain str for JSON encoding (Postgres
    TEXT column, HTTP JSON response) with no custom encoder needed."""
    code = RejectionCode.REJECTED_OI_WALL
    assert code == "REJECTED_OI_WALL"
    assert isinstance(code, str)
    assert json.dumps({"code": code}) == '{"code": "REJECTED_OI_WALL"}'


def test_rejection_code_has_exactly_the_ten_specified_members() -> None:
    assert {member.value for member in RejectionCode} == {
        "REJECTED_OI_WALL",
        "REJECTED_OPTION_SPREAD",
        "REJECTED_IV_CRUSH",
        "REJECTED_INDEX_DIVERGENCE",
        "REJECTED_LIQUIDITY",
        "REJECTED_DELTA_BAND",
        "REJECTED_THETA_CUTOFF",
        "REJECTED_LOW_CONVICTION",
        "REJECTED_SECTOR_WEAK",
        "REJECTED_CHASING_OB",
    }


def test_scan_signal_v2_carries_suppression_code_as_a_plain_string() -> None:
    """ScanSignalV2.suppression_code is a plain str field (not the enum
    type itself) -- matches suppression_reason's own existing shape and
    keeps the Pydantic model decoupled from the enum's exact identity."""
    signal = ScanSignalV2(
        signal_id="11111111-1111-1111-1111-111111111111",
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        signal_type="bullish",
        suppressed=True,
        suppression_reason="late_session_theta_decay_after_14:30",
        suppression_code=RejectionCode.REJECTED_THETA_CUTOFF.value,
    )
    assert signal.suppression_code == "REJECTED_THETA_CUTOFF"
    dumped = json.loads(signal.model_dump_json())
    assert dumped["suppression_code"] == "REJECTED_THETA_CUTOFF"


def test_scan_signal_v2_suppression_code_defaults_to_empty_not_none() -> None:
    """An unsuppressed (or not-yet-code-mapped) signal must carry ''"""
    signal = ScanSignalV2(
        signal_id="22222222-2222-2222-2222-222222222222",
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        signal_type="bullish",
    )
    assert signal.suppression_code == ""


class _FakeRedis:
    """Minimal fake covering exactly the calls SuppressionGate.evaluate()
    makes -- always reports "nothing active" (no ban, no duplicate, no
    cooldown, no sector data) so a real test can walk straight through
    to the gate under test."""

    async def exists(self, key: str) -> int:
        return 0

    async def sismember(self, key: str, member: str) -> bool:
        return False

    async def zscore(self, key: str, member: str) -> float | None:
        return None

    async def hget(self, key: str, field: str) -> None:
        return None


def _settings(**overrides: Any) -> SimpleNamespace:
    base = dict(
        min_conviction_score=80.0,
        min_sector_strength=30.0,
        theta_cutoff_enabled=True,
        theta_cutoff_time="14:30",
        theta_cutoff_strategy_ids="options_first_hybrid,vol_vwap_breakout",
        precision_guard_enabled=False,
        precision_guard_min_score=80.0,
        precision_guard_min_rr=1.2,
        precision_guard_sessions="",
        precision_guard_strategy_ids="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── B3: late-session theta cutoff ───────────────────────────────────────


def test_parse_hhmm_reads_a_real_configured_time() -> None:
    assert _parse_hhmm("14:30") == dt_time(14, 30)
    assert _parse_hhmm("09:05") == dt_time(9, 5)


def test_parse_hhmm_falls_back_to_the_safe_default_on_garbage() -> None:
    assert _parse_hhmm("not-a-time") == dt_time(14, 30)
    assert _parse_hhmm("") == dt_time(14, 30)


async def test_theta_cutoff_suppresses_a_configured_strategy_after_cutoff() -> None:
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        conviction_score=95.0,
        now=dt_time(14, 45),
    )
    assert result.passed is False
    assert result.gate == "theta_cutoff"
    assert result.code == RejectionCode.REJECTED_THETA_CUTOFF


async def test_theta_cutoff_allows_the_same_strategy_before_cutoff() -> None:
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        conviction_score=95.0,
        now=dt_time(11, 0),
    )
    assert result.passed is True


async def test_theta_cutoff_exempts_a_strategy_not_in_the_configured_list() -> None:
    """The strategy target list is configurable per B3's own ask --
    a strategy left out of theta_cutoff_strategy_ids must not be
    suppressed by this gate at all, e.g. a pure equity-only setup with
    no option-buying/theta exposure."""
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="some_equity_only_strategy",
        conviction_score=95.0,
        now=dt_time(15, 0),
    )
    assert result.passed is True


async def test_theta_cutoff_can_be_disabled_entirely() -> None:
    gate = SuppressionGate(_FakeRedis(), _settings(theta_cutoff_enabled=False))  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        conviction_score=95.0,
        now=dt_time(15, 0),
    )
    assert result.passed is True


async def test_low_conviction_is_rejected_before_theta_cutoff_is_even_checked() -> None:
    """Gate ordering: a weak setup should be rejected for being weak,
    not relabeled as a theta-cutoff rejection just because it's also
    late in the day."""
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        conviction_score=10.0,
        now=dt_time(15, 0),
    )
    assert result.passed is False
    assert result.gate == "conviction"
    assert result.code == RejectionCode.REJECTED_LOW_CONVICTION


async def test_sector_weak_carries_the_rejection_code() -> None:
    class _SectorWeakRedis(_FakeRedis):
        async def hget(self, key: str, field: str) -> str:
            return "10.0"  # below min_sector_strength=30.0

    gate = SuppressionGate(_SectorWeakRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        conviction_score=95.0,
        sector_id="NIFTY_IT",
        now=dt_time(11, 0),
    )
    assert result.passed is False
    assert result.gate == "sector"
    assert result.code == RejectionCode.REJECTED_SECTOR_WEAK


# ── SMC Inception Conviction Model: institutional anti-chase gate ──────
# (2026-08-27 -- see scanner/scoring.py's own module docstring for the
# model this gate is the hard-rejection complement to.)


async def test_chasing_ob_is_rejected_beyond_the_configured_distance() -> None:
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="options_first_hybrid",
        conviction_score=95.0,
        ob_fvg_distance_pct=1.2,  # > CHASING_OB_MAX_DISTANCE_PCT (0.75)
        now=dt_time(11, 0),
    )
    assert result.passed is False
    assert result.gate == "institutional_anti_chase"
    assert result.code == RejectionCode.REJECTED_CHASING_OB


async def test_price_at_the_base_of_the_ob_is_not_rejected() -> None:
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="options_first_hybrid",
        conviction_score=95.0,
        ob_fvg_distance_pct=0.3,  # inside the 0.75% line
        now=dt_time(11, 0),
    )
    assert result.passed is True


async def test_no_ob_or_fvg_at_all_does_not_trigger_the_anti_chase_gate() -> None:
    """Absence of a zone to chase is not the same thing as chasing
    one -- None must no-op, not be treated as infinitely far away."""
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="options_first_hybrid",
        conviction_score=95.0,
        ob_fvg_distance_pct=None,
        now=dt_time(11, 0),
    )
    assert result.passed is True


async def test_low_conviction_is_rejected_before_the_anti_chase_gate_is_even_checked() -> None:
    """Same gate-ordering discipline as theta_cutoff's own test above --
    a weak setup is rejected for being weak first."""
    gate = SuppressionGate(_FakeRedis(), _settings())  # type: ignore[arg-type]
    result = await gate.evaluate(
        symbol="RELIANCE",
        strategy_id="options_first_hybrid",
        conviction_score=10.0,
        ob_fvg_distance_pct=5.0,
        now=dt_time(11, 0),
    )
    assert result.passed is False
    assert result.gate == "conviction"


# ── B1: static OI-wall proximity ────────────────────────────────────────


def test_oi_wall_reason_none_when_far_from_the_wall() -> None:
    dynamics = {"wall": {"call_wall": [{"strike": 3000.0, "oi": 500_000}]}}
    assert _oi_wall_reason(bullish=True, spot=2500.0, options_dynamics_cache=dynamics) is None


def test_oi_wall_reason_fires_within_the_configured_proximity_ce() -> None:
    spot = 2500.0
    strike = spot * (1 + OI_WALL_PROXIMITY_PCT / 200)  # well inside 0.5%
    dynamics = {"wall": {"call_wall": [{"strike": strike, "oi": 500_000}]}}
    result = _oi_wall_reason(bullish=True, spot=spot, options_dynamics_cache=dynamics)
    assert result is not None
    reason, code = result
    assert code == RejectionCode.REJECTED_OI_WALL.value
    assert "Call wall" in reason


def test_oi_wall_reason_fires_within_the_configured_proximity_pe() -> None:
    spot = 1000.0
    strike = spot * (1 - OI_WALL_PROXIMITY_PCT / 200)
    dynamics = {"wall": {"put_wall": [{"strike": strike, "oi": 250_000}]}}
    result = _oi_wall_reason(bullish=False, spot=spot, options_dynamics_cache=dynamics)
    assert result is not None
    reason, code = result
    assert code == RejectionCode.REJECTED_OI_WALL.value
    assert "Put wall" in reason


def test_oi_wall_reason_never_fabricates_without_real_spot_or_wall_data() -> None:
    assert _oi_wall_reason(bullish=True, spot=None, options_dynamics_cache={}) is None
    assert _oi_wall_reason(bullish=True, spot=2500.0, options_dynamics_cache={}) is None
    assert _oi_wall_reason(bullish=True, spot=0.0, options_dynamics_cache={"wall": {}}) is None


def _verdict_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        bullish=True,
        ml={},
        mtf_cache={},
        sentiment_cache={},
        futures_cache={},
        options_dynamics_cache={},
        market_context_cache={},
        rel_vol_20d=None,
        option_chain_context={},
        ma_regime=None,
        donchian=None,
        wyckoff_sos_sow=None,
        atr_trend="",
        candle_pattern="",
        entry_price=100.0,
        invalidation_price=95.0,
        fo_banned=False,
        data_quality_score=100.0,
        tick_lag_ms=0.0,
        session_gap_ms=0.0,
        chaseable=True,
    )
    base.update(overrides)
    return base


def test_compute_verdict_hard_blocks_on_oi_wall_proximity() -> None:
    spot = 100.0
    strike = spot * 1.001  # 0.1% away, well inside the 0.5% gate
    verdict = compute_verdict(
        **_verdict_kwargs(
            entry_price=spot,
            options_dynamics_cache={"wall": {"call_wall": [{"strike": strike, "oi": 100_000}]}},
            option_chain_context={"spot": spot},
        )
    )
    assert verdict["verdict"] == "HARD_BLOCKED"
    assert RejectionCode.REJECTED_OI_WALL.value in verdict["hard_gate_codes"]
    assert any("OI wall proximity" in reason for reason in verdict["hard_gates"])


# ── B4: option liquidity/spread defaults ────────────────────────────────


def _option_row(*, ltp: float, bid: float, ask: float, oi: float, iv: float, delta: float) -> dict:
    return {
        "strike_price": 100.0,
        "expiry": "2026-09-25",
        "call_options": {
            "instrument_key": "NSE_FO|TEST25SEP100CE",
            "lot_size": 500,
            "market_data": {
                "ltp": ltp,
                "bid_price": bid,
                "ask_price": ask,
                "oi": oi,
                "prev_oi": oi,
                "volume": 1000,
            },
            "option_greeks": {"iv": iv, "delta": delta},
        },
    }


def test_liquidity_thresholds_defaults_are_the_raised_baseline(monkeypatch) -> None:
    monkeypatch.delenv("INFUSION_OPTION_MIN_OI", raising=False)
    monkeypatch.delenv("INFUSION_OPTION_MIN_VOLUME", raising=False)
    monkeypatch.delenv("INFUSION_OPTION_MAX_SPREAD_PCT", raising=False)
    thresholds = _liquidity_thresholds()
    assert thresholds == {"min_oi": 1000.0, "min_volume": 500.0, "max_spread_pct": 1.5}


def test_liquidity_thresholds_still_env_overridable(monkeypatch) -> None:
    monkeypatch.setenv("INFUSION_OPTION_MIN_OI", "42")
    thresholds = _liquidity_thresholds()
    assert thresholds["min_oi"] == 42.0


def test_score_option_leg_spread_gate_uses_the_same_configurable_threshold(monkeypatch) -> None:
    """Pipeline audit bonus finding: gates["spread"] used to be a bare
    hardcoded 3.0%, entirely independent of max_spread_pct -- so at the
    new 1.5% default, a leg with a 2% spread (comfortably under the OLD
    hardcoded 3.0%) must now fail gates["spread"] and carry
    REJECTED_OPTION_SPREAD, not silently pass on the stale threshold."""
    monkeypatch.setenv("INFUSION_OPTION_MAX_SPREAD_PCT", "1.5")
    row = _option_row(ltp=100.0, bid=99.0, ask=101.0, oi=5000, iv=30.0, delta=0.45)  # ~2% spread
    score, gates, _contract, _metrics, _reasons, _blockers, _hard_blockers = _score_option_leg(
        row,
        "call_options",
        spot=100.0,
        bias="CE",
        expiry_days=10,
        levels={},
        iv_rank=40.0,
        iv_history_count=100,
        symbol="TEST",
        event_risk={"entry_allowed": True},
    )
    assert gates["spread"] is False
    assert any("Wide spread" in b or "acceptable but not ideal" in b for b in _blockers)
    # ~2% is inside the 2x soft band (<=3.0%) at the new 1.5% baseline,
    # so it's a soft blocker, not (yet) a hard one at this exact spread --
    # confirm the *threshold* moved, via the gate flag above, without
    # asserting a specific hard/soft split that depends on the exact
    # ~2% spread landing on one side of that band.
    assert score is not None


def test_score_option_leg_wide_spread_is_hard_blocked_with_its_code(monkeypatch) -> None:
    monkeypatch.setenv("INFUSION_OPTION_MAX_SPREAD_PCT", "1.5")
    row = _option_row(ltp=100.0, bid=95.0, ask=105.0, oi=5000, iv=30.0, delta=0.45)  # ~10% spread
    _score, gates, _contract, metrics, _reasons, _blockers, hard_blockers = _score_option_leg(
        row,
        "call_options",
        spot=100.0,
        bias="CE",
        expiry_days=10,
        levels={},
        iv_rank=40.0,
        iv_history_count=100,
        symbol="TEST",
        event_risk={"entry_allowed": True},
    )
    assert gates["spread"] is False
    assert any("Wide spread" in b for b in hard_blockers)
    assert RejectionCode.REJECTED_OPTION_SPREAD.value in metrics["hard_blocker_codes"]


def test_score_option_leg_below_liquidity_whitelist_carries_its_code(monkeypatch) -> None:
    monkeypatch.setenv("INFUSION_OPTION_MIN_OI", "1000")
    monkeypatch.setenv("INFUSION_OPTION_MIN_VOLUME", "500")
    row = _option_row(
        ltp=100.0, bid=99.5, ask=100.5, oi=50, iv=30.0, delta=0.45
    )  # OI far below 1000
    _score, _gates, _contract, metrics, _reasons, _blockers, hard_blockers = _score_option_leg(
        row,
        "call_options",
        spot=100.0,
        bias="CE",
        expiry_days=10,
        levels={},
        iv_rank=40.0,
        iv_history_count=100,
        symbol="TEST",
        event_risk={"entry_allowed": True},
    )
    assert any("liquidity whitelist" in b for b in hard_blockers)
    assert RejectionCode.REJECTED_LIQUIDITY.value in metrics["hard_blocker_codes"]


def test_compute_verdict_reuses_the_option_chains_own_specific_code() -> None:
    """When option_chain_context already flags AVOID_CONTRACT with its
    own specific hard_blocker_codes (e.g. from a wide spread), the
    verdict's hard_gate_codes should carry that same specific code, not
    a generic/guessed one."""
    verdict = compute_verdict(
        **_verdict_kwargs(
            option_chain_context={
                "execution_status": "AVOID_CONTRACT",
                "hard_blockers": ["Wide spread 9.0%"],
                "hard_blocker_codes": [RejectionCode.REJECTED_OPTION_SPREAD.value],
            }
        )
    )
    assert verdict["verdict"] == "HARD_BLOCKED"
    assert verdict["hard_gate_codes"] == [RejectionCode.REJECTED_OPTION_SPREAD.value]
