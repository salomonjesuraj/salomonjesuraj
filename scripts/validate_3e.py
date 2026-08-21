"""Phase 3E offline validation — alerter delivery gate, formatter, priority tiers.

Tests delivery gate logic, message formatting, priority classification,
rate limit mechanics, and price formatting — all without Redis.

Usage:
    python -X utf8 scripts/validate_3e.py
"""

import os
import sys

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "alerter", "src"))

passed = 0
failed = 0
errors = []


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}{' -- ' + detail if detail else ''}")
    else:
        failed += 1
        errors.append(label)
        print(f"  ✗ {label}{' -- ' + detail if detail else ''}")


# ═══════════════════════════════════════════════════
# 1. Priority tier mapping
# ═══════════════════════════════════════════════════
print("\n--- PRIORITY TIERS ---")
from alerter.gate import _GRADE_RANK, PRIORITY_TIERS

check("A+ → CRITICAL", PRIORITY_TIERS.get("A+") == "CRITICAL")
check("A → HIGH", PRIORITY_TIERS.get("A") == "HIGH")
check("B+ → NORMAL", PRIORITY_TIERS.get("B+") == "NORMAL")
check("B → PASSIVE", PRIORITY_TIERS.get("B") == "PASSIVE")
check("C → PASSIVE", PRIORITY_TIERS.get("C") == "PASSIVE")
check("D → PASSIVE", PRIORITY_TIERS.get("D") == "PASSIVE")

# Grade ranking
check("A+ > A", _GRADE_RANK["A+"] > _GRADE_RANK["A"])
check("A > B+", _GRADE_RANK["A"] > _GRADE_RANK["B+"])
check("B+ > B", _GRADE_RANK["B+"] > _GRADE_RANK["B"])

# ═══════════════════════════════════════════════════
# 2. Price formatting
# ═══════════════════════════════════════════════════
print("\n--- PRICE FORMATTING ---")
from alerter.formatter import _escape_md, _format_price, _pct_change

check("Simple price", _format_price(500.0) == "₹500.00", f"got={_format_price(500.0)}")
check("Thousands", _format_price(2500.50) == "₹2,500.50", f"got={_format_price(2500.50)}")
check(
    "Lakhs",
    _format_price(123456.78) == "₹1,23,456.78",
    f"got={_format_price(123456.78)}",
)
check(
    "Crores",
    _format_price(12345678.90) == "₹1,23,45,678.90",
    f"got={_format_price(12345678.90)}",
)

# Pct change
check("Positive pct", _pct_change(100, 102) == "+2.0%", f"got={_pct_change(100, 102)}")
check("Negative pct", _pct_change(100, 97) == "-3.0%", f"got={_pct_change(100, 97)}")
check("Zero pct", _pct_change(100, 100) == "+0.0%", f"got={_pct_change(100, 100)}")

# ═══════════════════════════════════════════════════
# 3. MarkdownV2 escaping
# ═══════════════════════════════════════════════════
print("\n--- MARKDOWNV2 ESCAPING ---")
check("Escapes dot", "\\." in _escape_md("test.price"))
check("Escapes dash", "\\-" in _escape_md("test-value"))
check("Escapes parens", "\\(" in _escape_md("(value)"))
check("Escapes plus", "\\+" in _escape_md("+2.0%"))

# ═══════════════════════════════════════════════════
# 4. Message formatting
# ═══════════════════════════════════════════════════
print("\n--- MESSAGE FORMATTING ---")
from alerter.formatter import format_signal

sample_signal = {
    "signal_id": "test-001",
    "symbol": "RELIANCE",
    "strategy_id": "vol_vwap_breakout",
    "signal_type": "bullish",
    "conviction_score": 85.0,
    "conviction_grade": "A",
    "risk_reward_ratio": 2.2,
    "entry_price": 2500.0,
    "invalidation_price": 2477.50,
    "target_price": 2550.0,
    "sector_id": "NIFTY_50",
    "sector_strength": 72.0,
    "market_regime": "risk_on",
    "pre_breakout_state": "coiled",
    "created_at_us": 1748425200000000,  # some timestamp
    "explanation": [
        "VWAP reclaim crossover",
        "Volume 3.5x above average",
        "RSI 62 — momentum confirmed",
    ],
    "conditions_met": {"vol_expansion": True, "vwap_crossover": True},
}

msg = format_signal(sample_signal)
check("Message is string", isinstance(msg, str))
check("Contains symbol", "RELIANCE" in msg)
check("Contains score", "85" in msg)
check("Contains grade", "A" in msg)
check("Contains entry price", "2,500" in msg or "2500" in msg)
check("Contains stop price", "2,477" in msg or "2477" in msg)
check("Contains target price", "2,550" in msg or "2550" in msg)
check("Contains sector", "NIFTY" in msg)
check("Contains regime", "risk" in msg.lower())
check("Contains explanation", "VWAP" in msg)
check("Contains conditions", "Volume" in msg)
check("Contains timestamp IST", "IST" in msg)
check("Has emoji signal indicator", "🟢" in msg)
check("Has emoji condition indicator", "✅" in msg)

# ═══════════════════════════════════════════════════
# 5. Formatting determinism
# ═══════════════════════════════════════════════════
print("\n--- FORMAT DETERMINISM ---")
msg1 = format_signal(sample_signal)
msg2 = format_signal(sample_signal)
check("Same payload → same message", msg1 == msg2)

# ═══════════════════════════════════════════════════
# 6. DeliveryOutcome / DeliveryResult structure
# ═══════════════════════════════════════════════════
print("\n--- DELIVERY STRUCTURES ---")
from alerter.gate import DeliveryResult
from alerter.telegram import DeliveryOutcome

outcome_ok = DeliveryOutcome(success=True, status_code=200, retries=0)
check("DeliveryOutcome success", outcome_ok.success)
check("DeliveryOutcome frozen", hasattr(outcome_ok, "__slots__"))

result_pass = DeliveryResult(passed=True, priority_tier="CRITICAL")
check("DeliveryResult passed", result_pass.passed)
check("DeliveryResult tier", result_pass.priority_tier == "CRITICAL")

result_fail = DeliveryResult(passed=False, reason="rate_limit", priority_tier="HIGH")
check("DeliveryResult failed", not result_fail.passed)
check("DeliveryResult reason", result_fail.reason == "rate_limit")

# ═══════════════════════════════════════════════════
# 7. Config validation
# ═══════════════════════════════════════════════════
print("\n--- CONFIG VALIDATION ---")
from alerter.config import AlerterSettings

settings = AlerterSettings()
check("Default min grade B+", settings.alert_min_grade == "B+")
check("Default cooldown 1800s", settings.alert_cooldown_sec == 1800)
check("Default rate limit 10", settings.global_rate_limit == 10)
check("Default burst limit 3", settings.burst_limit == 3)
check("Default retry max 3", settings.retry_max == 3)
check("Default delivery log max 100", settings.delivery_log_max == 100)

# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("\n" + "=" * 60)
if failed == 0:
    print(f"ALL CHECKS PASSED ({passed}) — Phase 3E offline validation complete")
else:
    print(f"FAILED: {failed} / {passed + failed}")
    print(f"  Failures: {errors}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
