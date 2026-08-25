"""Real HTTP contract tests against the live api container.

Structural assertions only (keys present, correct types, enum
membership) -- these hit a live, continuously-changing system, so exact
values (symbol counts, scores, precision numbers) are deliberately never
asserted. What IS asserted is the response *shape* every dashboard
consumer this session built actually depends on -- the class of
regression a typing pass or a careless refactor could introduce without
any mypy error, since JSON shape isn't type-checked.
"""

from __future__ import annotations


async def test_diagnostics_reports_a_real_loaded_universe(api_client) -> None:
    async with api_client.get("/api/diagnostics") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert isinstance(body["symbols_loaded"], int)
    assert body["symbols_loaded"] > 0
    assert "streams" in body


async def test_ebie_candidates_available_and_shaped(api_client) -> None:
    """EBIE-KNOWN-GAPS.md §1.7/§7.1's own contract -- every candidate must
    carry cache_freshness (fresh/stale/never_cached per rolling-subset
    family) and lightweight_verdict/direction_agreement (agree/disagree/
    unknown), even when there's nothing to report for a given symbol."""
    async with api_client.get("/api/ebie/candidates?limit=20&suppressed=all") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    candidates = body["candidates"]
    assert isinstance(candidates, list)

    valid_freshness_status = {"fresh", "stale", "never_cached"}
    valid_agreement = {"agree", "disagree", "unknown"}

    for c in candidates:
        assert c.get("symbol")
        assert c["direction_agreement"] in valid_agreement
        # None (Redis unavailable) is an honest, valid state -- see the
        # route's own comment; a dict of freshness statuses is the other.
        freshness = c["data_quality"]["cache_freshness"]
        if freshness is not None:
            for status in freshness.values():
                assert status["status"] in valid_freshness_status
        # market_context/option_chain/lightweight_verdict are each either
        # None (not cached right now) or a real dict -- never anything
        # else (e.g. a bare string or a fabricated placeholder value).
        for optional_field in ("market_context", "option_chain", "lightweight_verdict"):
            assert c[optional_field] is None or isinstance(c[optional_field], dict)


async def test_ebie_lightweight_verdicts_carry_market_and_futures_context(api_client) -> None:
    """EBIE-KNOWN-GAPS.md §6.5 middle ground -- market_context/
    futures_context are the two rate-limit-free enrichments added to the
    universe-wide lightweight verdict; both should be present (though
    possibly None for a symbol the sweep hasn't reached yet) on every
    entry, not just some."""
    async with api_client.get("/api/ebie/lightweight-verdicts?include_no_trade=true") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    verdicts = body["verdicts"]
    assert isinstance(verdicts, list)
    for v in verdicts:
        assert "market_context" in v
        assert "futures_context" in v
        assert v["confidence_band"] in {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}


async def test_ebie_comparison_reports_real_drift_data(api_client) -> None:
    async with api_client.get("/api/ebie/comparison?hours=24") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    assert isinstance(body["state_distribution"], list)
    assert isinstance(body["total_transitions"], int)


async def test_ebie_verdict_calibration_reports_a_real_gate_state(api_client) -> None:
    """Always either a real calibration or an honest NOT_READY -- never a
    fabricated probability (Non-Negotiable Rule #7)."""
    async with api_client.get("/api/ebie/verdict-calibration") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert "available" in body
    if not body["available"]:
        assert body.get("reason")


async def test_backtest_label_study_never_fabricates_a_recommendation(api_client) -> None:
    async with api_client.get("/api/backtest/label-study") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    if body["recommended_window_min"] is None:
        assert body["total_decided_signals_since_eb10"] < body["min_sample_for_recommendation"]


async def test_backtest_kelly_sizing_gates_on_real_sample_size(api_client) -> None:
    async with api_client.get("/api/backtest/kelly-sizing?days=180") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    for stat in body["strategies"].values():
        if not stat["reliable"]:
            assert stat["kelly_pct"] is None and stat["half_kelly_pct"] is None


async def test_backtest_summary_returns_a_real_precision_breakdown(api_client) -> None:
    async with api_client.get("/api/backtest/summary?days=30") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["available"] is True
    assert isinstance(body["total"], int)
    assert isinstance(body["by_grade"], list)


async def test_ai_query_answers_a_real_deterministic_question(api_client) -> None:
    """No LLM required for this to work -- the deterministic fallback IS
    the source of truth per ai_query.py's own module docstring."""
    async with api_client.post(
        "/api/ai/query", json={"question": "what is the market regime"}
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["source"] == "deterministic"
    assert "regime" in body["answer"].lower()
    assert "regime" in body["intents_matched"]
