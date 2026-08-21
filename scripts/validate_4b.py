"""Phase 4B offline validation — analytics engine + daily recap.

Tests analytics queries, recap formatting, alerter recap handling,
and API route registration WITHOUT live services.

Usage:
    python -X utf8 scripts/validate_4b.py
"""

import os
import sys
import time

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "archiver", "src"))
sys.path.insert(0, os.path.join(base, "services", "alerter", "src"))
sys.path.insert(0, os.path.join(base, "services", "api", "src"))

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


def main():
    print("=" * 70)
    print("INFUSION PHASE 4B — OFFLINE VALIDATION")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    # ═══════════════════════════════════════════════
    # MODULE IMPORTS
    # ═══════════════════════════════════════════════
    print("\n--- MODULE IMPORTS ---")

    try:
        from archiver.analytics import SignalAnalytics

        check("SignalAnalytics imports", True)
    except Exception as e:
        check("SignalAnalytics imports", False, str(e))
        return

    try:
        from archiver.recap import format_recap

        check("Recap imports", True)
    except Exception as e:
        check("Recap imports", False, str(e))
        return

    try:
        from alerter.engine import AlerterEngine

        check("AlerterEngine imports", True)
    except Exception as e:
        check("AlerterEngine imports", False, str(e))
        return

    try:
        from api.routes.analytics import routes as analytics_routes

        check("Analytics routes import", True)
    except Exception as e:
        check("Analytics routes import", False, str(e))
        return

    # ═══════════════════════════════════════════════
    # ANALYTICS CLASS METHODS
    # ═══════════════════════════════════════════════
    print("\n--- ANALYTICS ENGINE ---")

    check("precision method", hasattr(SignalAnalytics, "precision"))
    check("precision_by_grade method", hasattr(SignalAnalytics, "precision_by_grade"))
    check("precision_by_sector method", hasattr(SignalAnalytics, "precision_by_sector"))
    check("precision_by_session method", hasattr(SignalAnalytics, "precision_by_session"))
    check("precision_by_regime method", hasattr(SignalAnalytics, "precision_by_regime"))
    check("suppression_stats method", hasattr(SignalAnalytics, "suppression_stats"))
    check("recent_outcomes method", hasattr(SignalAnalytics, "recent_outcomes"))
    check("daily_recap_data method", hasattr(SignalAnalytics, "daily_recap_data"))

    # ═══════════════════════════════════════════════
    # RECAP FORMATTER
    # ═══════════════════════════════════════════════
    print("\n--- RECAP FORMATTER ---")

    sample_data = {
        "trade_date": "2026-05-29",
        "total_signals": 15,
        "active_signals": 8,
        "suppressed_signals": 7,
        "precision": {
            "total": 5,
            "target_hits": 3,
            "stop_hits": 1,
            "expired": 1,
            "precision_pct": 75.0,
            "avg_score": 82.5,
            "avg_rr": 2.1,
            "avg_mfe_pct": 1.85,
            "avg_mae_pct": 0.45,
            "avg_time_to_target_min": 12.5,
            "avg_time_to_stop_min": 8.3,
        },
        "by_grade": [
            {
                "grade": "A+",
                "total": 2,
                "target_hits": 2,
                "stop_hits": 0,
                "expired": 0,
                "precision_pct": 100.0,
                "avg_score": 95.0,
            },
            {
                "grade": "A",
                "total": 3,
                "target_hits": 1,
                "stop_hits": 1,
                "expired": 1,
                "precision_pct": 50.0,
                "avg_score": 82.0,
            },
        ],
        "by_session": [
            {
                "session": "opening",
                "total": 4,
                "target_hits": 3,
                "stop_hits": 1,
                "precision_pct": 75.0,
                "avg_score": 85.0,
            },
            {
                "session": "midday",
                "total": 1,
                "target_hits": 0,
                "stop_hits": 0,
                "precision_pct": None,
                "avg_score": 70.0,
            },
        ],
        "by_sector": [
            {
                "sector_id": "NIFTY_50",
                "total": 3,
                "target_hits": 2,
                "stop_hits": 1,
                "precision_pct": 66.7,
                "avg_sector_strength": 72.0,
                "avg_score": 84.0,
            },
        ],
        "by_regime": [
            {
                "regime": "risk_on",
                "total": 5,
                "target_hits": 3,
                "stop_hits": 1,
                "precision_pct": 75.0,
                "avg_score": 82.5,
            },
        ],
        "suppression": {
            "total_suppressed": 7,
            "by_reason": [
                {"reason": "cooldown_active", "count": 3, "avg_score": 75.0},
                {"reason": "regime_risk_off", "count": 2, "avg_score": 68.0},
                {"reason": "duplicate_active", "count": 2, "avg_score": 80.0},
            ],
        },
    }

    text = format_recap(sample_data)
    check("Recap text generated", len(text) > 100, f"length={len(text)}")
    check("Recap has trade_date", "2026-05-29" in text)
    check("Recap has SIGNALS count", "8 active" in text)
    check("Recap has PRECISION", "75.0%" in text)
    check("Recap has BY GRADE", "BY GRADE" in text)
    check("Recap has BY SESSION", "BY SESSION" in text)
    check("Recap has SECTORS", "SECTORS" in text)
    check("Recap has REGIME", "REGIME" in text)
    check("Recap has SUPPRESSION", "SUPPRESSION" in text)
    check("Recap has cooldown_active", "cooldown_active" in text)
    check("Recap has separator lines", "═" in text)
    check("Recap has INFUSION header", "INFUSION" in text)

    # Determinism
    text2 = format_recap(sample_data)
    check("Recap formatting deterministic", text == text2)

    # ═══════════════════════════════════════════════
    # ALERTER RECAP HANDLING
    # ═══════════════════════════════════════════════
    print("\n--- ALERTER RECAP SUPPORT ---")

    import inspect

    engine_source = inspect.getsource(AlerterEngine.process_signal)
    check("Engine has recap detection", "recap" in engine_source)
    check("Engine has _deliver_recap method", hasattr(AlerterEngine, "_deliver_recap"))

    recap_source = inspect.getsource(AlerterEngine._deliver_recap)
    check("_deliver_recap checks recap_text", "recap_text" in recap_source)

    # ═══════════════════════════════════════════════
    # TELEGRAM PARSE MODE
    # ═══════════════════════════════════════════════
    print("\n--- TELEGRAM PARSE MODE ---")

    from alerter.telegram import TelegramClient

    tg_source = inspect.getsource(TelegramClient.send_message)
    check("Telegram conditionally includes parse_mode", "if parse_mode" in tg_source)

    # ═══════════════════════════════════════════════
    # ANALYTICS API ROUTES
    # ═══════════════════════════════════════════════
    print("\n--- ANALYTICS API ROUTES ---")

    [r.resource.canonical for r in analytics_routes if hasattr(r, "resource")]
    check(
        "Route /api/analytics/precision",
        any("/api/analytics/precision" in str(r) for r in analytics_routes),
        f"routes={len(analytics_routes)}",
    )

    # Check API main imports
    import api.main as api_main_mod

    api_source = inspect.getsource(api_main_mod)
    check("API imports analytics_routes", "analytics_routes" in api_source)
    check("API imports asyncpg", "asyncpg" in api_source)
    check("API has pg_pool creation", "pg_pool" in api_source)
    check("API conditional analytics routing", "if pg_pool" in api_source)

    # ═══════════════════════════════════════════════
    # ARCHIVER MAIN — RECAP SCHEDULER
    # ═══════════════════════════════════════════════
    print("\n--- ARCHIVER RECAP SCHEDULER ---")

    import archiver.main as archiver_main

    check("_recap_scheduler function exists", hasattr(archiver_main, "_recap_scheduler"))
    check("Archiver imports analytics", "SignalAnalytics" in inspect.getsource(archiver_main))
    check(
        "Archiver imports recap", "generate_and_publish_recap" in inspect.getsource(archiver_main)
    )

    # ═══════════════════════════════════════════════
    # INFRASTRUCTURE
    # ═══════════════════════════════════════════════
    print("\n--- INFRASTRUCTURE ---")

    import pathlib

    root = pathlib.Path(base)

    check("analytics.py exists", (root / "services/archiver/src/archiver/analytics.py").exists())
    check("recap.py exists", (root / "services/archiver/src/archiver/recap.py").exists())
    check("analytics routes exists", (root / "services/api/src/api/routes/analytics.py").exists())

    # API Dockerfile includes archiver
    api_dockerfile = (root / "services/api/Dockerfile").read_text()
    check("API Dockerfile includes archiver", "services/archiver" in api_dockerfile)

    # ═══════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print(f"  Total: {passed + failed}  |  PASS: {passed}  |  FAIL: {failed}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'PHASE 4B VALIDATED' if failed == 0 else 'NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
