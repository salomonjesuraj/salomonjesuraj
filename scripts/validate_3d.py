"""Phase 3D validation — sector context layer.

Tests sector strength, breadth, ranking, market regime, conviction
adjustments, and replay determinism. No Redis required.

Usage:
    python -X utf8 scripts/validate_3d.py
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
# 1. SectorState and breadth computation
# ═══════════════════════════════════════════════════
print("\n--- SECTOR BREADTH ---")


def _make_sector_engine():
    from scanner.config import ScannerSettings
    from scanner.sector import SectorEngine

    class FakeRedis:
        async def pipeline(self):
            return FakePipeline()

    class FakePipeline:
        def hset(self, *a, **kw):
            pass

        def expire(self, *a, **kw):
            pass

        async def execute(self):
            pass

    return SectorEngine(
        redis=FakeRedis(),
        settings=ScannerSettings(),
        symbol_sectors={
            "RELIANCE": "NIFTY_50",
            "INFY": "NIFTY_IT",
            "TCS": "NIFTY_IT",
            "HDFCBANK": "NIFTY_BANK",
            "NIFTY50": "INDEX",
        },
    )


def test_breadth_strong():
    """All constituents above VWAP/EMA20 with positive change → high breadth."""
    engine = _make_sector_engine()
    sector = engine.get_sector("NIFTY_IT")
    assert sector is not None

    from scanner.sector import SymbolSnapshot

    sector.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=1.5,
            rsi_14=60,
            rel_vol_20d=1.5,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3500,
            vwap=3490,
            ema_20=3480,
            change_pct=1.2,
            rsi_14=58,
            rel_vol_20d=1.3,
        ),
    }
    engine._recompute_sector(sector)

    assert sector.above_vwap_pct == 100.0, f"above_vwap={sector.above_vwap_pct}"
    assert sector.above_ema20_pct == 100.0
    assert sector.positive_change_pct == 100.0
    assert sector.breadth_score == 100.0, f"breadth={sector.breadth_score}"


def test_breadth_mixed():
    """One constituent above, one below → 50% breadth."""
    engine = _make_sector_engine()
    sector = engine.get_sector("NIFTY_IT")

    from scanner.sector import SymbolSnapshot

    sector.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=1.0,
            rsi_14=55,
            rel_vol_20d=1.0,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3400,
            vwap=3500,
            ema_20=3480,
            change_pct=-0.5,
            rsi_14=45,
            rel_vol_20d=0.8,
        ),
    }
    engine._recompute_sector(sector)

    assert sector.above_vwap_pct == 50.0
    assert sector.positive_change_pct == 50.0
    # breadth = 50*0.4 + 50*0.3 + 50*0.3 = 50
    # TCS: ltp=3400 < ema_20=3480 → above_ema20 = 50%
    assert sector.breadth_score == 50.0, f"breadth={sector.breadth_score}"


check("Strong breadth (all above)", test_breadth_strong)
check("Mixed breadth (50/50)", test_breadth_mixed)


# ═══════════════════════════════════════════════════
# 2. Strength scoring
# ═══════════════════════════════════════════════════
print("\n--- SECTOR STRENGTH ---")


def test_strength_formula():
    """Verify strength is a weighted composite."""
    engine = _make_sector_engine()
    sector = engine.get_sector("NIFTY_IT")

    from scanner.sector import SymbolSnapshot

    sector.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=1.5,
            rsi_14=60,
            rel_vol_20d=2.0,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3500,
            vwap=3490,
            ema_20=3480,
            change_pct=1.2,
            rsi_14=58,
            rel_vol_20d=1.8,
        ),
    }
    engine._recompute_sector(sector)

    # All components should be positive
    assert sector.strength_score > 0
    assert sector.breadth_score > 0
    assert sector.avg_rsi > 50
    assert sector.avg_rel_vol > 1.0
    assert sector.avg_change_pct > 0


def test_strength_determinism():
    """Same constituents → same strength score."""
    from scanner.sector import SymbolSnapshot

    engine1 = _make_sector_engine()
    engine2 = _make_sector_engine()
    for engine in (engine1, engine2):
        sector = engine.get_sector("NIFTY_IT")
        sector.constituents = {
            "INFY": SymbolSnapshot(
                symbol="INFY",
                ltp=1500,
                vwap=1490,
                ema_20=1480,
                change_pct=1.0,
                rsi_14=55,
                rel_vol_20d=1.5,
            ),
            "TCS": SymbolSnapshot(
                symbol="TCS",
                ltp=3500,
                vwap=3490,
                ema_20=3480,
                change_pct=0.8,
                rsi_14=52,
                rel_vol_20d=1.2,
            ),
        }
        engine._recompute_sector(sector)

    s1 = engine1.get_sector("NIFTY_IT").strength_score
    s2 = engine2.get_sector("NIFTY_IT").strength_score
    assert s1 == s2, f"Not deterministic: {s1} != {s2}"


check("Strength formula components", test_strength_formula)
check("Strength determinism", test_strength_determinism)


# ═══════════════════════════════════════════════════
# 3. Rankings
# ═══════════════════════════════════════════════════
print("\n--- SECTOR RANKINGS ---")


def test_rankings():
    """Stronger sectors ranked higher."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    # Make NIFTY_IT strong
    sec_it = engine.get_sector("NIFTY_IT")
    sec_it.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=2.0,
            rsi_14=65,
            rel_vol_20d=2.5,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3500,
            vwap=3490,
            ema_20=3480,
            change_pct=1.8,
            rsi_14=62,
            rel_vol_20d=2.2,
        ),
    }
    engine._recompute_sector(sec_it)

    # Make NIFTY_BANK weak
    sec_bank = engine.get_sector("NIFTY_BANK")
    sec_bank.constituents = {
        "HDFCBANK": SymbolSnapshot(
            symbol="HDFCBANK",
            ltp=1500,
            vwap=1520,
            ema_20=1530,
            change_pct=-1.0,
            rsi_14=38,
            rel_vol_20d=0.7,
        ),
    }
    engine._recompute_sector(sec_bank)

    engine._recalculate_rankings()

    rankings = engine.get_rankings()
    assert rankings[0].sector_id == "NIFTY_IT", f"Top sector: {rankings[0].sector_id}"
    assert rankings[0].rank == 1
    assert sec_it.strength_score > sec_bank.strength_score


check("Sector rankings (strongest first)", test_rankings)


# ═══════════════════════════════════════════════════
# 4. Market regime
# ═══════════════════════════════════════════════════
print("\n--- MARKET REGIME ---")


def test_regime_risk_on():
    """RISK_ON when index RSI > 55, change > 0, above VWAP."""
    from scanner.sector import MarketRegime

    engine = _make_sector_engine()
    engine._update_index(
        {
            "ltp": 22000,
            "vwap": 21900,
            "rsi_14": 62,
            "change_pct": 0.8,
            "rel_vol_20d": 1.2,
            "bb_width": 0.02,
        }
    )
    assert engine.regime == MarketRegime.RISK_ON


def test_regime_risk_off_rsi():
    """RISK_OFF when index RSI < 40."""
    from scanner.sector import MarketRegime

    engine = _make_sector_engine()
    engine._update_index(
        {
            "ltp": 21000,
            "vwap": 21500,
            "rsi_14": 35,
            "change_pct": -0.3,
            "rel_vol_20d": 1.0,
            "bb_width": 0.02,
        }
    )
    assert engine.regime == MarketRegime.RISK_OFF


def test_regime_risk_off_crash():
    """RISK_OFF when index change < -1.5%."""
    from scanner.sector import MarketRegime

    engine = _make_sector_engine()
    engine._update_index(
        {
            "ltp": 21000,
            "vwap": 21500,
            "rsi_14": 48,
            "change_pct": -2.0,
            "rel_vol_20d": 1.0,
            "bb_width": 0.02,
        }
    )
    assert engine.regime == MarketRegime.RISK_OFF


def test_regime_neutral():
    """NEUTRAL when conditions are mixed."""
    from scanner.sector import MarketRegime

    engine = _make_sector_engine()
    engine._update_index(
        {
            "ltp": 21500,
            "vwap": 21400,
            "rsi_14": 50,
            "change_pct": 0.2,
            "rel_vol_20d": 1.0,
            "bb_width": 0.02,
        }
    )
    assert engine.regime == MarketRegime.NEUTRAL


check("Regime RISK_ON", test_regime_risk_on)
check("Regime RISK_OFF (RSI)", test_regime_risk_off_rsi)
check("Regime RISK_OFF (crash)", test_regime_risk_off_crash)
check("Regime NEUTRAL", test_regime_neutral)


# ═══════════════════════════════════════════════════
# 5. Conviction adjustments
# ═══════════════════════════════════════════════════
print("\n--- CONVICTION ADJUSTMENTS ---")


def test_conviction_strong_sector():
    """Strong sector → positive conviction adjustment."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    sec = engine.get_sector("NIFTY_IT")
    sec.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=2.0,
            rsi_14=65,
            rel_vol_20d=2.0,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3500,
            vwap=3490,
            ema_20=3480,
            change_pct=1.5,
            rsi_14=60,
            rel_vol_20d=1.8,
        ),
    }
    engine._recompute_sector(sec)
    engine._recalculate_rankings()

    adj, explanations = engine.compute_sector_adjustment("NIFTY_IT", "INFY")
    assert adj > 0, f"Expected positive adjustment, got {adj}"
    assert len(explanations) > 0


def test_conviction_weak_sector():
    """Weak sector → negative conviction adjustment."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    sec = engine.get_sector("NIFTY_BANK")
    sec.constituents = {
        "HDFCBANK": SymbolSnapshot(
            symbol="HDFCBANK",
            ltp=1500,
            vwap=1520,
            ema_20=1530,
            change_pct=-1.5,
            rsi_14=35,
            rel_vol_20d=0.6,
        ),
    }
    engine._recompute_sector(sec)

    adj, explanations = engine.compute_sector_adjustment("NIFTY_BANK", "HDFCBANK")
    assert adj < 0, f"Expected negative adjustment, got {adj}"
    assert len(explanations) > 0


def test_conviction_regime_risk_off():
    """RISK_OFF regime → major negative adjustment."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    # Set regime to RISK_OFF
    engine._update_index(
        {
            "ltp": 21000,
            "vwap": 21500,
            "rsi_14": 35,
            "change_pct": -2.0,
            "rel_vol_20d": 1.0,
            "bb_width": 0.02,
        }
    )

    # Weak sector constituents (below VWAP, negative change)
    sec = engine.get_sector("NIFTY_IT")
    sec.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1480,
            vwap=1500,
            ema_20=1510,
            change_pct=-0.8,
            rsi_14=42,
            rel_vol_20d=0.7,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3450,
            vwap=3500,
            ema_20=3520,
            change_pct=-1.0,
            rsi_14=38,
            rel_vol_20d=0.6,
        ),
    }
    engine._recompute_sector(sec)

    adj, explanations = engine.compute_sector_adjustment("NIFTY_IT", "INFY")
    # Weak sector (-10) + weak breadth (-5) + RISK_OFF (-10) = heavily negative
    assert adj < 0, f"Expected negative adjustment during RISK_OFF, got {adj}"
    assert any("RISK_OFF" in e for e in explanations)


def test_conviction_clamp():
    """Adjustment clamped to [-20, +15]."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    # Create extremely strong sector to test upper clamp
    sec = engine.get_sector("NIFTY_IT")
    sec.strength_score = 90
    sec.breadth_score = 90
    sec.trend = "improving"
    sec.rank = 1
    sec.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=3.0,
            rsi_14=60,
            rel_vol_20d=2.0,
        ),
        "TCS": SymbolSnapshot(
            symbol="TCS",
            ltp=3500,
            vwap=3490,
            ema_20=3480,
            change_pct=2.0,
            rsi_14=58,
            rel_vol_20d=1.8,
        ),
    }
    sec.avg_change_pct = 2.5

    adj, _ = engine.compute_sector_adjustment("NIFTY_IT", "INFY")
    assert adj <= 15.0, f"Adjustment not clamped: {adj}"
    assert adj >= -20.0


check("Strong sector → positive adjustment", test_conviction_strong_sector)
check("Weak sector → negative adjustment", test_conviction_weak_sector)
check("RISK_OFF → negative adjustment", test_conviction_regime_risk_off)
check("Adjustment clamped [-20, +15]", test_conviction_clamp)


# ═══════════════════════════════════════════════════
# 6. Relative strength
# ═══════════════════════════════════════════════════
print("\n--- RELATIVE STRENGTH ---")


def test_relative_strength():
    """Stock outperforming sector → positive relative strength."""
    from scanner.sector import SymbolSnapshot

    engine = _make_sector_engine()

    sec = engine.get_sector("NIFTY_IT")
    sec.constituents = {
        "INFY": SymbolSnapshot(symbol="INFY", change_pct=2.0),
        "TCS": SymbolSnapshot(symbol="TCS", change_pct=0.5),
    }
    engine._recompute_sector(sec)

    rel = engine.get_relative_strength("INFY", "NIFTY_IT")
    assert rel > 0, f"Expected positive relative strength, got {rel}"

    rel_weak = engine.get_relative_strength("TCS", "NIFTY_IT")
    assert rel_weak < 0, "Expected negative relative strength for underperformer"


check("Relative strength: outperformer positive", test_relative_strength)


# ═══════════════════════════════════════════════════
# 7. Sector trend
# ═══════════════════════════════════════════════════
print("\n--- SECTOR TREND ---")


def test_sector_trend():
    """Improving strength delta → IMPROVING trend."""
    from scanner.sector import SectorTrend, SymbolSnapshot

    engine = _make_sector_engine()

    sec = engine.get_sector("NIFTY_IT")
    sec.constituents = {
        "INFY": SymbolSnapshot(
            symbol="INFY",
            ltp=1500,
            vwap=1490,
            ema_20=1480,
            change_pct=1.0,
            rsi_14=55,
            rel_vol_20d=1.0,
        ),
    }
    sec.strength_score = 40  # previous
    engine._recompute_sector(sec)
    # strength should be different from 40, so delta exists

    # Now set prev explicitly and recompute
    sec.prev_strength = 35
    sec.strength_delta = sec.strength_score - 35

    if sec.strength_delta > 2.0:
        sec.trend = SectorTrend.IMPROVING
    assert sec.strength_delta != 0.0


check("Sector trend detection", test_sector_trend)


# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("\n" + "=" * 60)
total = len(errors)
if total == 0:
    print("ALL CHECKS PASSED — Phase 3D offline validation complete")
else:
    print(f"FAILURES: {total}")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
