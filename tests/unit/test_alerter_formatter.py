"""Unit tests for alerter.formatter's Telegram message formatting --
"Telegram Redesign & Token Modal" sprint (2026-08-27). First test
coverage this module has ever had (services/alerter had zero unit tests
before this sprint)."""

from __future__ import annotations

from alerter.formatter import _escape_pre, _option_dte_text, _row, format_signal


def test_escape_pre_only_touches_backslash_and_backtick() -> None:
    # Telegram's own narrower rule for text inside a pre/code entity --
    # every other MarkdownV2-reserved character (., -, (, ), !, etc.) is
    # literal there and must NOT come back escaped.
    assert _escape_pre("Entry Rs 1,255.10 (BUY-CE)!") == "Entry Rs 1,255.10 (BUY-CE)!"
    assert _escape_pre("a\\b`c") == "a\\\\b\\`c"


def test_row_pads_the_label_to_a_fixed_width() -> None:
    row = _row("T1", "Rs 265.05")
    assert row.startswith("T1")
    assert row == "T1         Rs 265.05"  # 11-char label column


def test_option_dte_text_is_an_honest_dash_with_no_chain_context() -> None:
    assert _option_dte_text({}) == "-"
    assert _option_dte_text({"option_chain": {}}) == "-"
    assert _option_dte_text({"option_chain": {"expiry_days": None}}) == "-"


def test_option_dte_text_reads_the_real_expiry_days_field() -> None:
    assert _option_dte_text({"option_chain": {"expiry_days": 21.0}}) == "21d"


def test_format_signal_wraps_the_table_in_a_markdownv2_code_fence() -> None:
    message = format_signal(
        {
            "symbol": "RELIANCE",
            "strategy_id": "options_first_hybrid",
            "signal_type": "bullish",
            "conviction_score": 92,
            "conviction_grade": "A+",
            "entry_price": 1255.10,
            "invalidation_price": 1247.57,
            "target_price": 1262.41,
            "risk_reward_ratio": 1.71,
            "features_snapshot": {
                "t2_price": 1270.0,
                "t3_price": 1280.0,
                "rel_vol_20d": 2.3,
                "chaseable": True,
                "mtf_dots": {"1M": "G", "5M": "G", "15M": "Y", "1H": "G", "4H": "G", "1D": "R"},
            },
            "option_chain": {"expiry_days": 21},
            "created_at_us": 0,
        }
    )
    # Bold headline outside the fence, exactly one fence pair, real
    # numbers present in the monospace body.
    assert message.startswith("*")
    assert message.count("```") == 2
    assert "RELIANCE" in message
    assert "Rs 1,255.10" in message  # ENTRY
    assert "Rs 1,247.57" in message  # STOP LOSS
    assert "Rs 1,270.00" in message  # T2
    assert "1:1.71" in message  # R:R, unescaped inside the fence
    assert "2.3x" in message  # volume multiplier
    assert "21d" in message  # DTE


def test_format_signal_is_honest_about_missing_rr_volume_and_dte() -> None:
    message = format_signal(
        {
            "symbol": "TESTSYM",
            "strategy_id": "vvb",
            "signal_type": "bullish",
            "conviction_score": 60,
            "conviction_grade": "B",
            "entry_price": 100.0,
            "invalidation_price": 95.0,
            "target_price": 110.0,
        }
    )
    # No fabricated ratio/multiplier/DTE when the upstream fields are
    # genuinely absent -- a bare "-" in each row, not an invented 0.
    lines = message.split("\n")
    rr_line = next(line for line in lines if line.startswith("R:R"))
    vol_line = next(line for line in lines if line.startswith("VOL"))
    dte_line = next(line for line in lines if line.startswith("DTE"))
    assert rr_line.strip().endswith("-")
    assert vol_line.strip().endswith("-")
    assert dte_line.strip().endswith("-")
