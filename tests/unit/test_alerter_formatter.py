"""Unit tests for alerter.formatter's Telegram message formatting --
"Blended HUD" redesign (2026-08-28)."""

from __future__ import annotations

from alerter.formatter import _escape_html, _pct_move, format_signal


def test_escape_html_only_touches_amp_lt_gt() -> None:
    assert _escape_html("M&M < RELIANCE > 100%") == "M&amp;M &lt; RELIANCE &gt; 100%"
    # Everything MarkdownV2 would have reserved (., -, (, ), +, !) is
    # untouched -- HTML has no opinion about any of it.
    assert _escape_html("A+ (88%) -1.20%!") == "A+ (88%) -1.20%!"


def test_pct_move_is_signed_and_direction_agnostic() -> None:
    assert _pct_move(100.0, 90.0) == "-10.00%"
    assert _pct_move(100.0, 110.0) == "+10.00%"


def test_pct_move_is_an_honest_n_a_when_either_price_is_missing() -> None:
    assert _pct_move(0.0, 110.0) == "N/A"
    assert _pct_move(100.0, 0.0) == "N/A"


def test_format_signal_renders_the_bullish_call_template() -> None:
    message = format_signal(
        {
            "symbol": "RELIANCE",
            "signal_type": "bullish",
            "option_bias": "BUY CE",
            "conviction_score": 88,
            "conviction_grade": "A+",
            "entry_price": 1420.50,
            "invalidation_price": 1408.30,
            "target_price": 1445.00,
            "features_snapshot": {"t2_price": 1460.00, "rel_vol_20d": 2.3},
            "mtf_structure": {"blocker_down_level": 1400.0, "blocker_up_level": 1480.0},
        }
    )
    lines = message.split("\n")
    assert lines[0] == "🚨 <b>BUY CALL: RELIANCE @ Rs 1,420.50</b>"
    assert lines[1] == "Grade: A+ (88%) | Vol: 2.3x"
    assert lines[3] == "📍 <b>Execution Blueprint</b>"
    assert lines[4] == "• SL : Rs 1,408.30 (-0.86%)"
    assert lines[5] == "• T1 : Rs 1,445.00 (+1.72%)"
    assert lines[6] == "• T2 : Rs 1,460.00 (+2.78%)"
    assert lines[8] == "🛡️ <b>Structural Anchor</b>"
    # Bullish -> the real cached SUPPORT (blocker_down_level), a close
    # BELOW it invalidates the long.
    assert lines[9] == "• Support : Rs 1,400.00"
    assert lines[10] == "• Invalidation: 1m Close &lt; Rs 1,400.00"


def test_format_signal_renders_the_bearish_put_template_with_resistance() -> None:
    """A PUT's structural anchor is the real resistance above it, not
    the same "Support" label the bullish mockup example used -- labeling
    a resistance level "Support" would be a real mislabeling, not a
    cosmetic one."""
    message = format_signal(
        {
            "symbol": "TCS",
            "signal_type": "bearish",
            "option_bias": "BUY PE",
            "conviction_score": 75,
            "conviction_grade": "A",
            "entry_price": 3850.00,
            "invalidation_price": 3875.00,
            "target_price": 3800.00,
            "features_snapshot": {"t2_price": 3775.00, "rel_vol_20d": 1.5},
            "mtf_structure": {"blocker_down_level": 3700.0, "blocker_up_level": 3900.0},
        }
    )
    lines = message.split("\n")
    assert lines[0] == "🚨 <b>BUY PUT: TCS @ Rs 3,850.00</b>"
    assert lines[4] == "• SL : Rs 3,875.00 (+0.65%)"
    assert lines[5] == "• T1 : Rs 3,800.00 (-1.30%)"
    assert lines[9] == "• Resistance : Rs 3,900.00"
    assert lines[10] == "• Invalidation: 1m Close &gt; Rs 3,900.00"


def test_format_signal_is_honest_about_missing_optional_fields() -> None:
    """No KeyError, no fabricated numbers -- an "N/A" for whatever the
    upstream source genuinely doesn't have yet (Phase 4's own ask)."""
    message = format_signal(
        {
            "symbol": "TESTSYM",
            "signal_type": "bullish",
            "entry_price": 100.0,
            "invalidation_price": 95.0,
            "target_price": 110.0,
        }
    )
    lines = message.split("\n")
    assert lines[1] == "Grade: N/A (0%) | Vol: N/A"
    assert lines[9] == "• Support : N/A"
    assert lines[10] == "• Invalidation: 1m Close &lt; N/A"
