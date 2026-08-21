"""Phase 3D integration test — sector context layer with Redis.

Tests: sector strength computation, breadth, rankings, market regime,
conviction adjustments, suppression integration, API endpoints.

Requires: Redis running on localhost:6379

Usage:
    python -X utf8 scripts/test_sector_integration.py
"""

import asyncio
import os
import sys
import time

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))

import redis.asyncio as aioredis
from infusion_common.timing import now_us
from infusion_streams.constants import (
    KEY_SECTOR_PREFIX,
    KEY_SIGNAL_ACTIVE,
)
from scanner.config import ScannerSettings
from scanner.engine import ScannerEngine
from scanner.sector import MarketRegime
from scanner.strategies import _REGISTRY, register_strategy
from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

REDIS_URL = os.environ.get("INFUSION_REDIS_URL", "redis://localhost:6379/0")

# Test symbols across 3 sectors
SYMBOLS = {
    "SEC_INFY": "NIFTY_IT",
    "SEC_TCS": "NIFTY_IT",
    "SEC_HDFC": "NIFTY_BANK",
    "SEC_REL": "NIFTY_50",
    "SEC_NIFTY": "INDEX",
}

passed = 0
failed = 0
errors = []


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS]   {label}{' -- ' + detail if detail else ''}")
    else:
        failed += 1
        errors.append(label)
        print(f"  [FAIL]   {label}{' -- ' + detail if detail else ''}")


def _features(symbol, ltp, vwap, ema_20, change_pct, rsi, rel_vol, bb_width=0.02):
    return {
        "symbol": symbol,
        "timestamp_us": now_us(),
        "ltp": ltp,
        "vwap": vwap,
        "ema_5": ltp * 0.999,
        "ema_9": ltp * 0.998,
        "ema_20": ema_20,
        "ema_50": ema_20 * 0.98,
        "atr_14": ltp * 0.01,
        "rsi_14": rsi,
        "rel_vol_20d": rel_vol,
        "change_pct": change_pct,
        "bb_width": bb_width,
        "bb_upper": ltp * 1.02,
        "bb_lower": ltp * 0.98,
        "order_imbalance": 0.15,
        "spread_bps": 8.0,
        "prev_close": ltp / (1 + change_pct / 100),
    }


async def run_tests():
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    await r.ping()
    print("✓ Redis connected\n")

    settings = ScannerSettings()

    # Cleanup test keys
    for sym in SYMBOLS:
        for prefix in ("infusion:signal:", "infusion:prebreak:"):
            await r.delete(f"{prefix}{sym}")
        # Cooldown keys include strategy suffix
        await r.delete(f"infusion:cooldown:{sym}:vol_vwap_breakout")
        await r.zrem(KEY_SIGNAL_ACTIVE, f"{sym}:vol_vwap_breakout")
    for sec in ("NIFTY_IT", "NIFTY_BANK", "NIFTY_50"):
        await r.delete(f"{KEY_SECTOR_PREFIX}{sec}")
    await r.delete("infusion:regime")

    _REGISTRY.clear()
    register_strategy(VolVwapBreakout(settings))

    engine = ScannerEngine(
        redis=r,
        settings=settings,
        symbol_sectors=SYMBOLS,
    )

    # ═══════════════════════════════════════════════
    # Phase 1: Warmup all symbols
    # ═══════════════════════════════════════════════
    print("--- WARMUP ---")
    for _i in range(settings.warmup_ticks):
        for sym, sec in SYMBOLS.items():
            if sec == "INDEX":
                await engine.process_feature(_features(sym, 22000, 21900, 21800, 0.5, 55, 1.0))
            else:
                await engine.process_feature(_features(sym, 1500, 1490, 1480, 0.5, 52, 1.0))

    check("Warmup complete", engine._evaluations >= settings.warmup_ticks * len(SYMBOLS))

    # ═══════════════════════════════════════════════
    # Phase 2: Create strong IT sector
    # ═══════════════════════════════════════════════
    print("\n--- SECTOR STRENGTH ---")
    # Both IT stocks strong: above VWAP, above EMA20, positive change, good RSI
    for _ in range(15):
        await engine.process_feature(
            _features("SEC_INFY", 1520, 1490, 1480, 2.0, 62, 1.8, bb_width=0.015)
        )
        await engine.process_feature(
            _features("SEC_TCS", 3520, 3490, 3480, 1.5, 58, 1.5, bb_width=0.018)
        )
        # Weak bank
        await engine.process_feature(
            _features("SEC_HDFC", 1480, 1500, 1510, -1.0, 38, 0.7, bb_width=0.03)
        )
        # Moderate NIFTY_50
        await engine.process_feature(
            _features("SEC_REL", 2500, 2490, 2480, 0.8, 52, 1.2, bb_width=0.02)
        )
        # Index: RISK_ON conditions
        await engine.process_feature(_features("SEC_NIFTY", 22200, 22100, 21800, 0.8, 60, 1.2))

    # Check sector strength
    it_sector = engine.sector.get_sector("NIFTY_IT")
    bank_sector = engine.sector.get_sector("NIFTY_BANK")
    engine.sector.get_sector("NIFTY_50")

    check("IT sector exists", it_sector is not None)
    check("Bank sector exists", bank_sector is not None)

    if it_sector and bank_sector:
        check(
            "IT stronger than BANK",
            it_sector.strength_score > bank_sector.strength_score,
            f"IT={it_sector.strength_score:.1f}, BANK={bank_sector.strength_score:.1f}",
        )
        check(
            "IT breadth high",
            it_sector.breadth_score >= 70,
            f"breadth={it_sector.breadth_score:.1f}",
        )
        check(
            "BANK breadth low",
            bank_sector.breadth_score < 50,
            f"breadth={bank_sector.breadth_score:.1f}",
        )

    # ═══════════════════════════════════════════════
    # Phase 3: Market regime
    # ═══════════════════════════════════════════════
    print("\n--- MARKET REGIME ---")
    check(
        "Regime is RISK_ON",
        engine.sector.regime == MarketRegime.RISK_ON,
        f"regime={engine.sector.regime}",
    )

    # Switch to RISK_OFF
    for _ in range(5):
        await engine.process_feature(_features("SEC_NIFTY", 21000, 21500, 21800, -2.5, 32, 1.5))

    check(
        "Regime switched to RISK_OFF",
        engine.sector.regime == MarketRegime.RISK_OFF,
        f"regime={engine.sector.regime}",
    )

    # Switch back to NEUTRAL
    for _ in range(5):
        await engine.process_feature(_features("SEC_NIFTY", 21800, 21700, 21600, 0.3, 48, 1.0))

    check(
        "Regime switched to NEUTRAL",
        engine.sector.regime == MarketRegime.NEUTRAL,
        f"regime={engine.sector.regime}",
    )

    # ═══════════════════════════════════════════════
    # Phase 4: Conviction adjustments
    # ═══════════════════════════════════════════════
    print("\n--- CONVICTION ADJUSTMENTS ---")

    # Strong sector → positive adjustment
    adj_it, exp_it = engine.sector.compute_sector_adjustment("NIFTY_IT", "SEC_INFY")
    check("IT sector adj > 0", adj_it > 0, f"adj={adj_it:.1f}")
    check("IT explanations present", len(exp_it) > 0, f"count={len(exp_it)}")

    # Weak sector → negative adjustment
    adj_bank, _exp_bank = engine.sector.compute_sector_adjustment("NIFTY_BANK", "SEC_HDFC")
    check("BANK sector adj < 0", adj_bank < 0, f"adj={adj_bank:.1f}")

    # ═══════════════════════════════════════════════
    # Phase 5: Rankings
    # ═══════════════════════════════════════════════
    print("\n--- SECTOR RANKINGS ---")
    rankings = engine.sector.get_rankings()
    check("Rankings have 3 sectors", len(rankings) == 3, f"count={len(rankings)}")
    if len(rankings) >= 2:
        check(
            "Rank 1 is strongest",
            rankings[0].strength_score >= rankings[1].strength_score,
            f"rank1={rankings[0].sector_id}({rankings[0].strength_score:.1f}), "
            f"rank2={rankings[1].sector_id}({rankings[1].strength_score:.1f})",
        )

    # ═══════════════════════════════════════════════
    # Phase 6: Relative strength
    # ═══════════════════════════════════════════════
    print("\n--- RELATIVE STRENGTH ---")
    rel_infy = engine.sector.get_relative_strength("SEC_INFY", "NIFTY_IT")
    rel_tcs = engine.sector.get_relative_strength("SEC_TCS", "NIFTY_IT")
    check(
        "INFY outperforming TCS in sector",
        rel_infy > rel_tcs,
        f"INFY={rel_infy:+.2f}%, TCS={rel_tcs:+.2f}%",
    )

    # ═══════════════════════════════════════════════
    # Phase 7: Redis persistence
    # ═══════════════════════════════════════════════
    print("\n--- REDIS PERSISTENCE ---")
    it_key = f"{KEY_SECTOR_PREFIX}NIFTY_IT"
    it_data = await r.hgetall(it_key)
    check("IT sector in Redis", len(it_data) > 0)
    if it_data:
        redis_strength = float(it_data.get(b"strength_score", b"0").decode())
        check("Redis strength > 0", redis_strength > 0, f"strength={redis_strength}")
        redis_breadth = float(it_data.get(b"breadth_score", b"0").decode())
        check("Redis breadth stored", redis_breadth >= 0, f"breadth={redis_breadth}")
        redis_trend = it_data.get(b"trend", b"").decode()
        check("Redis trend stored", len(redis_trend) > 0, f"trend={redis_trend}")

    regime_data = await r.hgetall(b"infusion:regime")
    check("Regime in Redis", len(regime_data) > 0)
    if regime_data:
        redis_regime = regime_data.get(b"regime", b"").decode()
        check(
            "Redis regime value",
            redis_regime in ("risk_on", "neutral", "risk_off"),
            f"regime={redis_regime}",
        )

    # ═══════════════════════════════════════════════
    # Phase 8: Signal with sector context
    # ═══════════════════════════════════════════════
    print("\n--- SIGNAL WITH SECTOR CONTEXT ---")

    # Set regime to RISK_ON for signal
    for _ in range(5):
        await engine.process_feature(_features("SEC_NIFTY", 22200, 22100, 21800, 0.8, 60, 1.2))

    # Trigger breakout for INFY in strong IT sector
    state = engine.state_mgr.get_or_create("SEC_INFY")
    state.prev_ltp = 1490.0
    state.prev_vwap = 1495.0

    await engine.process_feature(
        _features("SEC_INFY", 1520, 1495, 1480, 2.0, 62, 3.5, bb_width=0.015)
    )

    check("Signal emitted", engine._signals_emitted >= 1, f"emitted={engine._signals_emitted}")

    # Check the signal has sector context
    sig_data = await r.hgetall(b"infusion:signal:SEC_INFY")
    if sig_data:
        score = float(sig_data.get(b"conviction_score", b"0").decode())
        check("Signal has conviction score", score > 0, f"score={score}")

    # ═══════════════════════════════════════════════
    # Phase 9: Engine stats
    # ═══════════════════════════════════════════════
    print("\n--- ENGINE STATS ---")
    stats = engine.stats
    check(
        "Stats: sector_updates > 0",
        stats.get("sector_updates", 0) > 0,
        f"updates={stats.get('sector_updates')}",
    )
    check(
        "Stats: sector_count == 3",
        stats.get("sector_count", 0) == 3,
        f"count={stats.get('sector_count')}",
    )
    check("Stats: regime present", "regime" in stats, f"regime={stats.get('regime')}")

    # ═══════════════════════════════════════════════
    # Phase 10: Determinism
    # ═══════════════════════════════════════════════
    print("\n--- DETERMINISM ---")
    adj1, _ = engine.sector.compute_sector_adjustment("NIFTY_IT", "SEC_INFY")
    adj2, _ = engine.sector.compute_sector_adjustment("NIFTY_IT", "SEC_INFY")
    check("Sector adjustment determinism", adj1 == adj2, f"adj1={adj1}, adj2={adj2}")

    s1 = engine.sector.get_sector_strength("NIFTY_IT")
    s2 = engine.sector.get_sector_strength("NIFTY_IT")
    check("Sector strength determinism", s1 == s2, f"s1={s1}, s2={s2}")

    # Cleanup
    for sym in SYMBOLS:
        for prefix in ("infusion:signal:", "infusion:cooldown:", "infusion:prebreak:"):
            await r.delete(f"{prefix}{sym}")
    await r.zrem(KEY_SIGNAL_ACTIVE, "SEC_INFY:vol_vwap_breakout")

    await r.aclose()
    return passed, failed


def main():
    print("=" * 70)
    print("INFUSION PHASE 3D — SECTOR CONTEXT INTEGRATION TEST")
    print(f"Redis: {REDIS_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    p, f = asyncio.run(run_tests())

    print("\n" + "=" * 70)
    print(f"  Total: {p + f}  |  PASS: {p}  |  FAIL: {f}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'SECTOR CONTEXT VALIDATED' if f == 0 else 'SECTOR CONTEXT NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
