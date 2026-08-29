"""Unit tests for api.routes.screener's pure Order Block/FVG proximity
helper -- "Unified Omni-Screener & Deep-Dive Interactivity" sprint
(2026-08-28) -- plus a real route-level test of GET /api/screener/fno's
composite merge -- "Full Universe Batch Hydration Engine" sprint
(2026-08-29), Phase 3. Real request/response cycle via
aiohttp.test_utils with a _FakeRedis, matching test_auth_routes.py's own
established pattern for this codebase; the other bulk Redis-pipeline
routes here remain untested at the route level (only their pure helpers
are), consistent with this file's own prior scope.
"""

from __future__ import annotations

import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from api.routes.screener import (
    OPTIONS_UNIVERSE_KEY,
    SMC_UNIVERSE_KEY,
    _nearest_ob_fvg_either_direction,
)
from api.routes.screener import routes as screener_routes


def _features(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


class _FakeRedis:
    """Only the two methods screener_fno actually calls -- hgetall on
    each universe key, both already real-JSON-encoded per symbol,
    matching exactly what api.screener_hydrator's own hset(mapping=...)
    writes there."""

    def __init__(self, hashes: dict[str, dict[str, str]]) -> None:
        self.hashes = hashes

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})


def _make_app(hashes: dict[str, dict[str, str]]) -> web.Application:
    app = web.Application()
    app.router.add_routes(screener_routes)
    app["redis"] = _FakeRedis(hashes)
    return app


def test_picks_the_bullish_zone_when_only_it_is_validated() -> None:
    features = _features(order_block_bullish_validated=True, order_block_bullish_high=100.0)
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 100.0


def test_picks_the_bearish_zone_when_only_it_is_validated() -> None:
    features = _features(order_block_bearish_validated=True, order_block_bearish_low=110.0)
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 110.0


def test_picks_whichever_real_zone_is_closer_to_ltp() -> None:
    features = _features(
        order_block_bullish_validated=True,
        order_block_bullish_high=95.0,  # 10 away from ltp
        order_block_bearish_validated=True,
        order_block_bearish_low=108.0,  # 3 away from ltp
    )
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 108.0


def test_is_honestly_none_when_neither_zone_is_validated() -> None:
    assert _nearest_ob_fvg_either_direction(_features(), ltp=105.0) is None


async def test_fno_merges_a_symbol_present_in_both_universes() -> None:
    smc_row = {
        "symbol": "RELIANCE",
        "ltp": 1287.0,
        "squeeze_readiness": 62.5,
        "rvol": 1.8,
        "rvol_session": "last_close",
        "oi_buildup": "LONG_BUILDUP",
        "ob_fvg_level": 1300.0,
        "ob_fvg_distance_pct": 1.01,
        "bar_count": 21,
    }
    options_row = {
        "symbol": "RELIANCE",
        "pcr": {"pcr": 1.12, "sentiment": "bearish"},
        "max_pain": {"max_pain_strike": 1280.0},
        "iv_rank": 42.0,
        "iv_rank_history_count": 60,
        "updated_at": 1787980497.0,
    }
    app = _make_app(
        {
            SMC_UNIVERSE_KEY: {"RELIANCE": json.dumps(smc_row)},
            OPTIONS_UNIVERSE_KEY: {"RELIANCE": json.dumps(options_row)},
        }
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/screener/fno")
        assert resp.status == 200
        body = await resp.json()

    assert body["count"] == 1
    assert body["options_recent_count"] == 1
    row = body["rows"]["RELIANCE"]
    # Every real SMC field survives the merge untouched.
    assert row["squeeze_readiness"] == 62.5
    assert row["rvol"] == 1.8
    assert row["oi_buildup"] == "LONG_BUILDUP"
    # Options fields are folded in from the second hash.
    assert row["pcr"] == {"pcr": 1.12, "sentiment": "bearish"}
    assert row["max_pain"] == {"max_pain_strike": 1280.0}
    assert row["iv_rank"] == 42.0
    assert row["iv_rank_history_count"] == 60
    assert row["options_updated_at"] == 1787980497.0


async def test_fno_leaves_options_fields_honestly_null_outside_the_candidate_subset() -> None:
    """A symbol the SMC hydrator covers but option_chain_queue.py's own
    rotating ~28-candidate sweep hasn't reached recently -- the real,
    disclosed, common case for most of the 208-symbol universe. Must
    read as an honest null, never a fabricated/zeroed options block."""
    smc_row = {"symbol": "KAYNES", "squeeze_readiness": 88.0, "rvol": 3.2, "oi_buildup": None}
    app = _make_app({SMC_UNIVERSE_KEY: {"KAYNES": json.dumps(smc_row)}})

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/screener/fno")
        body = await resp.json()

    assert body["count"] == 1
    assert body["options_recent_count"] == 0
    row = body["rows"]["KAYNES"]
    assert row["squeeze_readiness"] == 88.0
    assert row["pcr"] is None
    assert row["max_pain"] is None
    assert row["iv_rank"] is None
    assert row["iv_rank_history_count"] == 0
    assert row["options_updated_at"] is None


async def test_fno_is_empty_but_well_formed_when_no_universe_has_hydrated_yet() -> None:
    app = _make_app({})
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/screener/fno")
        body = await resp.json()

    assert body == {"count": 0, "options_recent_count": 0, "rows": {}}
