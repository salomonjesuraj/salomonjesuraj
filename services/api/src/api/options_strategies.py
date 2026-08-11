"""Multi-leg options strategy catalog -- Phase 13.6a.

Confirmed via repo-wide search before writing a line of this: zero
multi-leg strategy code exists anywhere in Infusion today (no
iron_condor/bull_call_spread/multi_leg hits) -- every existing options
path (market.py's _upstox_option_context, options_analytics.py) reasons
about a single naked CE or PE leg. This is the foundation Phase 13.6b's
selection logic ranks *from* -- build the catalog first, select over it
second.

Six of the most commonly useful defined-risk/defined-reward structures,
built from the SAME full-chain rows _fetch_full_option_chain() already
fetches for options_analytics.py (real quoted premiums/bid/ask/OI/greeks
straight from Upstox, not a synthetic Black-Scholes estimate -- Infusion
has the real chain, so it uses the real chain):

  - Bull Call Spread   (bullish, defined risk/reward)
  - Bear Put Spread    (bearish, defined risk/reward)
  - Iron Condor        (range-bound, defined risk/reward both sides)
  - Long Straddle      (high-volatility view, direction-agnostic)
  - Long Strangle      (high-volatility view, cheaper than straddle)
  - Covered Call       (income against a hypothetical equity holding at
                         current spot -- Infusion tracks no actual equity
                         portfolio, so this is presented as a calculator
                         against "bought at today's spot", the same
                         standard simplification any options calculator
                         uses, not a claim Infusion holds the position)

Every function takes the same (rows, spot) shape compute_max_pain() etc.
already use and returns a dict with "ready": False + a reason on any
leg-selection failure (thin chain, no strikes far enough out) rather than
guessing or crashing -- same discipline as every other options function
in this codebase.

Strike offsets are expressed in STRIKE STEPS, not a fixed rupee amount --
the real interval between consecutive strikes varies enormously across
Infusion's universe (a ~Rs50 stock vs. a ~Rs3,000 one), so a step count
derived from the chain's own actual strikes is the only version of "one
strike out" that means the same thing for every symbol.
"""

from __future__ import annotations

DEFAULT_WING_STEPS = 2          # spreads: how many strike-steps the short/far leg sits from ATM
DEFAULT_CONDOR_SHORT_STEPS = 2  # iron condor: short strikes this many steps from ATM
DEFAULT_CONDOR_WING_STEPS = 4   # iron condor: protective long strikes this many steps from ATM
DEFAULT_STRANGLE_STEPS = 2      # long strangle: OTM leg steps from ATM


def _leg_market(row: dict, leg_name: str) -> dict:
    leg = row.get(leg_name) or {}
    return leg.get("market_data") or {}


def _leg_greeks(row: dict, leg_name: str) -> dict:
    leg = row.get(leg_name) or {}
    return leg.get("option_greeks") or {}


def buy_price(row: dict, leg_name: str) -> float:
    """What it actually costs to BUY this leg right now -- ask, falling
    back to ltp when no live ask is quoted (thin book), same convention
    market.py's _score_option_leg already uses for a single leg."""
    market = _leg_market(row, leg_name)
    ask = float(market.get("ask_price") or 0)
    ltp = float(market.get("ltp") or 0)
    return ask if ask > 0 else ltp


def sell_price(row: dict, leg_name: str) -> float:
    """What you actually receive SELLING this leg right now -- bid,
    falling back to ltp."""
    market = _leg_market(row, leg_name)
    bid = float(market.get("bid_price") or 0)
    ltp = float(market.get("ltp") or 0)
    return bid if bid > 0 else ltp


def _sorted_strikes(rows: list[dict]) -> list[float]:
    strikes = sorted({float(r.get("strike_price") or 0) for r in rows if float(r.get("strike_price") or 0) > 0})
    return strikes


def _atm_index(strikes: list[float], spot: float) -> int | None:
    if not strikes or spot <= 0:
        return None
    return min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))


def _row_at_strike(rows: list[dict], strike: float) -> dict | None:
    for row in rows:
        if float(row.get("strike_price") or 0) == strike:
            return row
    return None


def _strike_at_offset(strikes: list[float], atm_idx: int, steps: int) -> float | None:
    """steps > 0 = further OTM for a call (higher strike) / further OTM for
    a put in the negative direction -- caller decides the sign. Returns
    None (not clamped to the chain edge) when the requested offset falls
    outside the available strikes -- a clamped/wrong strike is worse than
    an honest "chain too thin for this leg"."""
    idx = atm_idx + steps
    if idx < 0 or idx >= len(strikes):
        return None
    return strikes[idx]


def _leg(action: str, opt_type: str, strike: float, premium: float, iv: float, delta: float) -> dict:
    return {
        "action": action, "type": opt_type, "strike": strike,
        "premium": round(premium, 2), "iv": round(iv, 2), "delta": round(delta, 4),
    }


def _ready_result(strategy: str, **kwargs) -> dict:
    return {"strategy": strategy, "ready": True, **kwargs}


def _not_ready(strategy: str, reason: str) -> dict:
    return {"strategy": strategy, "ready": False, "reason": reason}


def bull_call_spread(rows: list[dict], spot: float, wing_steps: int = DEFAULT_WING_STEPS) -> dict:
    """Buy ATM/near-ATM call, sell a further-OTM call to fund it. Defined
    risk (the net debit paid), defined reward (strike width - net debit)."""
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None:
        return _not_ready("bull_call_spread", "No strikes in chain.")
    long_strike = strikes[atm_idx]
    short_strike = _strike_at_offset(strikes, atm_idx, wing_steps)
    if short_strike is None:
        return _not_ready("bull_call_spread", "Chain too thin for the short-call wing.")
    long_row = _row_at_strike(rows, long_strike)
    short_row = _row_at_strike(rows, short_strike)
    long_premium = buy_price(long_row, "call_options")
    short_premium = sell_price(short_row, "call_options")
    if long_premium <= 0:
        return _not_ready("bull_call_spread", "No live premium for the long call leg.")

    net_debit = long_premium - short_premium
    width = short_strike - long_strike
    max_profit = width - net_debit
    max_loss = net_debit
    breakeven = long_strike + net_debit
    g_long, g_short = _leg_greeks(long_row, "call_options"), _leg_greeks(short_row, "call_options")
    return _ready_result(
        "bull_call_spread",
        legs=[
            _leg("BUY", "CE", long_strike, long_premium, float(g_long.get("iv") or 0), float(g_long.get("delta") or 0)),
            _leg("SELL", "CE", short_strike, short_premium, float(g_short.get("iv") or 0), float(g_short.get("delta") or 0)),
        ],
        net_debit=round(net_debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=[round(breakeven, 2)],
    )


def bear_put_spread(rows: list[dict], spot: float, wing_steps: int = DEFAULT_WING_STEPS) -> dict:
    """Buy ATM/near-ATM put, sell a further-OTM put to fund it. Mirror of
    bull_call_spread for a bearish view."""
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None:
        return _not_ready("bear_put_spread", "No strikes in chain.")
    long_strike = strikes[atm_idx]
    short_strike = _strike_at_offset(strikes, atm_idx, -wing_steps)
    if short_strike is None:
        return _not_ready("bear_put_spread", "Chain too thin for the short-put wing.")
    long_row = _row_at_strike(rows, long_strike)
    short_row = _row_at_strike(rows, short_strike)
    long_premium = buy_price(long_row, "put_options")
    short_premium = sell_price(short_row, "put_options")
    if long_premium <= 0:
        return _not_ready("bear_put_spread", "No live premium for the long put leg.")

    net_debit = long_premium - short_premium
    width = long_strike - short_strike
    max_profit = width - net_debit
    max_loss = net_debit
    breakeven = long_strike - net_debit
    g_long, g_short = _leg_greeks(long_row, "put_options"), _leg_greeks(short_row, "put_options")
    return _ready_result(
        "bear_put_spread",
        legs=[
            _leg("BUY", "PE", long_strike, long_premium, float(g_long.get("iv") or 0), float(g_long.get("delta") or 0)),
            _leg("SELL", "PE", short_strike, short_premium, float(g_short.get("iv") or 0), float(g_short.get("delta") or 0)),
        ],
        net_debit=round(net_debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=[round(breakeven, 2)],
    )


def iron_condor(
    rows: list[dict], spot: float,
    short_steps: int = DEFAULT_CONDOR_SHORT_STEPS,
    wing_steps: int = DEFAULT_CONDOR_WING_STEPS,
) -> dict:
    """Sell an OTM put + OTM call (collect premium), buy further-OTM put +
    call as protection on each side. Net credit, defined risk both
    directions -- a range-bound / high-theta view, the opposite read from
    the straddle/strangle below."""
    if wing_steps <= short_steps:
        return _not_ready("iron_condor", "wing_steps must sit further out than short_steps.")
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None:
        return _not_ready("iron_condor", "No strikes in chain.")

    short_put_k = _strike_at_offset(strikes, atm_idx, -short_steps)
    long_put_k = _strike_at_offset(strikes, atm_idx, -wing_steps)
    short_call_k = _strike_at_offset(strikes, atm_idx, short_steps)
    long_call_k = _strike_at_offset(strikes, atm_idx, wing_steps)
    if None in (short_put_k, long_put_k, short_call_k, long_call_k):
        return _not_ready("iron_condor", "Chain too thin for both condor wings.")

    short_put_row = _row_at_strike(rows, short_put_k)
    long_put_row = _row_at_strike(rows, long_put_k)
    short_call_row = _row_at_strike(rows, short_call_k)
    long_call_row = _row_at_strike(rows, long_call_k)

    short_put_prem = sell_price(short_put_row, "put_options")
    long_put_prem = buy_price(long_put_row, "put_options")
    short_call_prem = sell_price(short_call_row, "call_options")
    long_call_prem = buy_price(long_call_row, "call_options")
    if short_put_prem <= 0 or short_call_prem <= 0:
        return _not_ready("iron_condor", "No live premium for one or both short legs.")

    net_credit = (short_put_prem - long_put_prem) + (short_call_prem - long_call_prem)
    put_wing_width = short_put_k - long_put_k
    call_wing_width = long_call_k - short_call_k
    max_profit = net_credit
    max_loss = max(put_wing_width, call_wing_width) - net_credit
    lower_breakeven = short_put_k - net_credit
    upper_breakeven = short_call_k + net_credit

    gp_s, gp_l = _leg_greeks(short_put_row, "put_options"), _leg_greeks(long_put_row, "put_options")
    gc_s, gc_l = _leg_greeks(short_call_row, "call_options"), _leg_greeks(long_call_row, "call_options")
    return _ready_result(
        "iron_condor",
        legs=[
            _leg("BUY", "PE", long_put_k, long_put_prem, float(gp_l.get("iv") or 0), float(gp_l.get("delta") or 0)),
            _leg("SELL", "PE", short_put_k, short_put_prem, float(gp_s.get("iv") or 0), float(gp_s.get("delta") or 0)),
            _leg("SELL", "CE", short_call_k, short_call_prem, float(gc_s.get("iv") or 0), float(gc_s.get("delta") or 0)),
            _leg("BUY", "CE", long_call_k, long_call_prem, float(gc_l.get("iv") or 0), float(gc_l.get("delta") or 0)),
        ],
        net_credit=round(net_credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=[round(lower_breakeven, 2), round(upper_breakeven, 2)],
    )


def long_straddle(rows: list[dict], spot: float) -> dict:
    """Buy the ATM call and ATM put at the SAME strike. Unlimited-ish
    upside on a big move either direction; max loss is the combined
    premium paid if the underlying pins the strike at expiry."""
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None:
        return _not_ready("long_straddle", "No strikes in chain.")
    strike = strikes[atm_idx]
    row = _row_at_strike(rows, strike)
    call_premium = buy_price(row, "call_options")
    put_premium = buy_price(row, "put_options")
    if call_premium <= 0 or put_premium <= 0:
        return _not_ready("long_straddle", "No live premium for the ATM call and/or put.")

    total_premium = call_premium + put_premium
    g_c, g_p = _leg_greeks(row, "call_options"), _leg_greeks(row, "put_options")
    return _ready_result(
        "long_straddle",
        legs=[
            _leg("BUY", "CE", strike, call_premium, float(g_c.get("iv") or 0), float(g_c.get("delta") or 0)),
            _leg("BUY", "PE", strike, put_premium, float(g_p.get("iv") or 0), float(g_p.get("delta") or 0)),
        ],
        net_debit=round(total_premium, 2),
        max_profit=None,  # theoretically unbounded on the call side
        max_loss=round(total_premium, 2),
        breakeven=[round(strike - total_premium, 2), round(strike + total_premium, 2)],
    )


def long_strangle(rows: list[dict], spot: float, wing_steps: int = DEFAULT_STRANGLE_STEPS) -> dict:
    """Buy an OTM call and an OTM put -- cheaper than a straddle for the
    same "big move either direction" view, at the cost of needing a
    bigger move to reach breakeven."""
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None:
        return _not_ready("long_strangle", "No strikes in chain.")
    call_k = _strike_at_offset(strikes, atm_idx, wing_steps)
    put_k = _strike_at_offset(strikes, atm_idx, -wing_steps)
    if call_k is None or put_k is None:
        return _not_ready("long_strangle", "Chain too thin for both OTM wings.")

    call_row = _row_at_strike(rows, call_k)
    put_row = _row_at_strike(rows, put_k)
    call_premium = buy_price(call_row, "call_options")
    put_premium = buy_price(put_row, "put_options")
    if call_premium <= 0 or put_premium <= 0:
        return _not_ready("long_strangle", "No live premium for one or both OTM legs.")

    total_premium = call_premium + put_premium
    g_c, g_p = _leg_greeks(call_row, "call_options"), _leg_greeks(put_row, "put_options")
    return _ready_result(
        "long_strangle",
        legs=[
            _leg("BUY", "CE", call_k, call_premium, float(g_c.get("iv") or 0), float(g_c.get("delta") or 0)),
            _leg("BUY", "PE", put_k, put_premium, float(g_p.get("iv") or 0), float(g_p.get("delta") or 0)),
        ],
        net_debit=round(total_premium, 2),
        max_profit=None,
        max_loss=round(total_premium, 2),
        breakeven=[round(put_k - total_premium, 2), round(call_k + total_premium, 2)],
    )


def covered_call(rows: list[dict], spot: float, wing_steps: int = DEFAULT_WING_STEPS) -> dict:
    """Sell an OTM call against a hypothetical equity holding bought at
    today's spot. Infusion tracks no actual equity portfolio -- this is
    presented as "if you held/bought at spot right now", the same
    simplification any options calculator uses, not a claim Infusion
    holds the position. Income strategy: caps upside at the strike in
    exchange for the premium collected."""
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    if atm_idx is None or spot <= 0:
        return _not_ready("covered_call", "No strikes in chain or no spot price.")
    call_k = _strike_at_offset(strikes, atm_idx, wing_steps)
    if call_k is None:
        return _not_ready("covered_call", "Chain too thin for the covered-call strike.")

    call_row = _row_at_strike(rows, call_k)
    premium = sell_price(call_row, "call_options")
    if premium <= 0:
        return _not_ready("covered_call", "No live premium for the call leg.")

    max_profit = (call_k - spot) + premium if call_k >= spot else premium
    max_loss = spot - premium  # theoretical, if the underlying went to zero
    breakeven = spot - premium
    g_c = _leg_greeks(call_row, "call_options")
    return _ready_result(
        "covered_call",
        legs=[
            {"action": "HOLD", "type": "EQUITY", "strike": None, "premium": round(spot, 2), "iv": None, "delta": None},
            _leg("SELL", "CE", call_k, premium, float(g_c.get("iv") or 0), float(g_c.get("delta") or 0)),
        ],
        assumed_equity_entry=round(spot, 2),
        net_credit=round(premium, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=[round(breakeven, 2)],
    )


CATALOG = {
    "bull_call_spread": bull_call_spread,
    "bear_put_spread": bear_put_spread,
    "iron_condor": iron_condor,
    "long_straddle": long_straddle,
    "long_strangle": long_strangle,
    "covered_call": covered_call,
}


def build_all_strategies(rows: list[dict], spot: float) -> dict[str, dict]:
    """Every catalog strategy for one chain snapshot, keyed by name. Each
    value carries its own "ready" flag -- callers must check it per
    strategy (a thin chain can starve one strategy's wings while another
    with tighter offsets still has everything it needs)."""
    return {name: fn(rows, spot) for name, fn in CATALOG.items()}


# ── Phase 13.6b: selection logic ────────────────────────────────────────
# Ranks the catalog above using inputs Infusion already computes for every
# symbol (IV Rank, PCR sentiment, Max Pain, directional bias from mtf.py's
# compute_mtf) -- no new data source, purely a fit-scoring layer over
# existing evidence. Advisory only: this ranks a shortlist for a human to
# review, it never auto-selects or auto-executes anything, consistent with
# every "propose only" pattern elsewhere in Infusion (kill-switch,
# optimizer-proposal, feature-ablation).

# Each strategy's inherent directional character, used only for scoring --
# not a claim about what it "is" beyond this ranking.
_STRATEGY_CLASS = {
    "bull_call_spread": "bullish",
    "bear_put_spread": "bearish",
    "covered_call": "neutral_bullish",
    "iron_condor": "neutral",
    "long_straddle": "volatility",
    "long_strangle": "volatility",
}
_PREMIUM_SELLING = {"iron_condor", "covered_call"}  # net credit / theta-positive structures
_BULLISH_PCR = {"strong_bullish", "neutral_bullish"}
_BEARISH_PCR = {"strong_bearish", "mild_bearish"}


def _directional_fit(strategy: str, trade_bias: str) -> tuple[float, str]:
    cls = _STRATEGY_CLASS[strategy]
    table = {
        "BUY CE": {"bullish": (40, "matches the bullish bias"), "neutral_bullish": (30, "leans the same way as the bullish bias"),
                   "neutral": (15, "no directional edge against a bullish bias"), "volatility": (20, "direction-agnostic, doesn't fight the bias"),
                   "bearish": (0, "works against the bullish bias")},
        "BUY PE": {"bearish": (40, "matches the bearish bias"), "neutral": (15, "no directional edge against a bearish bias"),
                   "volatility": (20, "direction-agnostic, doesn't fight the bias"), "neutral_bullish": (5, "works against the bearish bias"),
                   "bullish": (0, "works against the bearish bias")},
        "HOLD": {"neutral": (35, "range-bound structure fits a no-clear-bias read"), "volatility": (25, "profits from a move either way while bias is unclear"),
                  "neutral_bullish": (15, "mild directional lean without a confirmed bias"), "bullish": (10, "directional bet without a confirmed bias"),
                  "bearish": (10, "directional bet without a confirmed bias")},
    }
    score, reason = table.get(trade_bias, table["HOLD"]).get(cls, (10, "no strong read either way"))
    return float(score), reason


def _iv_rank_fit(strategy: str, iv_rank: float | None) -> tuple[float, str]:
    if iv_rank is None:
        return 17.5, "IV Rank not yet available (needs 60+ days of history) -- scored neutral"
    selling = strategy in _PREMIUM_SELLING
    if selling:
        score = iv_rank / 100 * 35
        reason = f"IV Rank {iv_rank:.0f} -- {'rich' if iv_rank >= 60 else 'not particularly rich'} premium to sell"
    else:
        score = (100 - iv_rank) / 100 * 35
        reason = f"IV Rank {iv_rank:.0f} -- {'cheap' if iv_rank <= 40 else 'not particularly cheap'} premium to buy"
    return round(score, 1), reason


def _pcr_fit(trade_bias: str, pcr_sentiment: str | None) -> tuple[float, str]:
    if not pcr_sentiment:
        return 7.5, "PCR unavailable -- scored neutral"
    if trade_bias == "BUY CE" and pcr_sentiment in _BULLISH_PCR:
        return 15.0, f"PCR sentiment ({pcr_sentiment}) agrees with the bullish bias"
    if trade_bias == "BUY CE" and pcr_sentiment in _BEARISH_PCR:
        return 0.0, f"PCR sentiment ({pcr_sentiment}) contradicts the bullish bias"
    if trade_bias == "BUY PE" and pcr_sentiment in _BEARISH_PCR:
        return 15.0, f"PCR sentiment ({pcr_sentiment}) agrees with the bearish bias"
    if trade_bias == "BUY PE" and pcr_sentiment in _BULLISH_PCR:
        return 0.0, f"PCR sentiment ({pcr_sentiment}) contradicts the bearish bias"
    return 7.5, f"PCR sentiment ({pcr_sentiment}) -- no clear agreement or conflict"


def _max_pain_fit(strategy: str, spot: float, max_pain_strike: float | None) -> tuple[float, str]:
    cls = _STRATEGY_CLASS[strategy]
    if max_pain_strike is None or spot <= 0:
        return 5.0, "Max Pain unavailable -- scored neutral"
    distance_pct = abs(spot - max_pain_strike) / spot * 100
    if cls == "neutral" and distance_pct < 1.0:
        return 10.0, f"Spot is pinned near Max Pain ({max_pain_strike:g}, {distance_pct:.1f}% away) -- favors a range-bound structure"
    if cls in ("bullish", "neutral_bullish") and max_pain_strike > spot and distance_pct >= 1.0:
        return 10.0, f"Max Pain ({max_pain_strike:g}) sits above spot -- a theoretical pull in this strategy's direction"
    if cls == "bearish" and max_pain_strike < spot and distance_pct >= 1.0:
        return 10.0, f"Max Pain ({max_pain_strike:g}) sits below spot -- a theoretical pull in this strategy's direction"
    if cls == "volatility":
        return 3.0, "Max Pain's pinning tendency is a mild headwind for a big-move thesis"
    return 2.0, f"Max Pain ({max_pain_strike:g}) doesn't support this structure's read"


def rank_strategies(
    built: dict[str, dict],
    trade_bias: str,
    iv_rank: float | None,
    pcr_sentiment: str | None,
    spot: float,
    max_pain_strike: float | None,
) -> list[dict]:
    """Score and rank every READY strategy in `built` (as returned by
    build_all_strategies). Returns a list sorted best-fit-first, each entry
    carrying its total score (0-100), the 4 component scores, and a
    reasoning bullet per component -- so a human can see WHY something
    ranked where it did, not just trust a number. Never picks one FOR the
    user; this is a ranked shortlist, not a decision.
    """
    ranked = []
    for name, result in built.items():
        if not result.get("ready"):
            continue
        d_score, d_reason = _directional_fit(name, trade_bias)
        iv_score, iv_reason = _iv_rank_fit(name, iv_rank)
        pcr_score, pcr_reason = _pcr_fit(trade_bias, pcr_sentiment)
        mp_score, mp_reason = _max_pain_fit(name, spot, max_pain_strike)
        total = round(d_score + iv_score + pcr_score + mp_score, 1)
        ranked.append({
            "strategy": name,
            "fit_score": total,
            "components": {
                "directional": {"score": d_score, "reason": d_reason},
                "iv_rank": {"score": iv_score, "reason": iv_reason},
                "pcr": {"score": pcr_score, "reason": pcr_reason},
                "max_pain": {"score": mp_score, "reason": mp_reason},
            },
            **result,
        })
    ranked.sort(key=lambda r: r["fit_score"], reverse=True)
    return ranked
