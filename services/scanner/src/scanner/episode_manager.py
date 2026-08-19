"""Shared watch-episode manager — EBIE EB-1.

Generalizes Phase W's watch-episode-freeze mechanism (previously
duplicated near-verbatim between strategies/options_first_hybrid.py and
strategies/vol_vwap_breakout.py) into one shared component, per the
authorized migration plan's own instruction (docs/EBIE-IMPLEMENTATION-
ANSWERS.md Q3.2): "Extend and generalize the existing proven freeze
mechanism. Do not build a second independent freeze system."

Behavior is UNCHANGED from the two strategies' existing inline logic —
this is a pure extraction, not a redesign. Both strategies always called
`compute_pine_decision(features, bullish, entry, invalidation)` exactly
once, downstream of deciding entry/invalidation (reused from a frozen
episode, or freshly computed off live price) — this module preserves
that exact two-step shape (resolve_ladder_basis -> caller runs pine ->
finalize_episode) rather than collapsing it into one call, specifically
so pine confidence is never computed twice per strategy per tick.

A strategy still only ever READS through this (matches strategies/
base.py's "must not mutate state" contract) — the caller (engine.py)
remains the one that writes state.watch_episodes[episode_key] = snapshot
after evaluate() returns, exactly as before.

EBIE context: this is also the component EB-1's canonical state machine
will build its own episode notion on top of once that work starts — see
the "Suggested initial derived features"/Section 28 "Episode Freezing"
of docs/EBIE-BLUEPRINT.md and Section 3.2 of docs/EBIE-IMPLEMENTATION-
ANSWERS.md, which asks for a shared EpisodeManager generalized beyond
just Phase W's watch-tier ladders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class LadderBasis:
    """Entry/invalidation this cycle should use, and where it came from.

    target/target2/target3/effective_risk/target_method are deliberately
    NOT decided here — for a reused episode they come from the episode's
    own frozen values; for a fresh one they come from whatever the
    caller's pine-confidence call produces off this entry/invalidation.
    Both branches need exactly one compute_pine_decision() call using
    this basis's entry/invalidation, same as the original strategies.
    """

    entry_price: float
    invalidation_price: float
    reused: bool                    # True if resolved from a still-open, valid episode
    first_seen_us: int              # preserved from the reused episode, or "now" if fresh
    frozen_episode: dict | None     # the reused episode dict, or None if fresh


def resolve_ladder_basis(
    *,
    episode: dict | None,
    now_us: int,
    ttl_us: int,
    invalidated: Callable[[float], bool],
    compute_fresh_entry_invalidation: Callable[[], tuple[float, float]],
) -> LadderBasis:
    """Decide whether to reuse an existing watch episode's frozen
    entry/invalidation or compute a fresh one off live price.

    `invalidated(frozen_stop)` and `compute_fresh_entry_invalidation()`
    are injected because they're genuinely strategy-specific — e.g.
    options_first_hybrid's invalidation check is bidirectional (CE vs PE),
    vol_vwap_breakout's is bullish-only; each strategy's stop-buffer math
    differs. This function owns only the shared TTL-check / invalidation-
    check / reuse-vs-fresh sequencing, not the price math itself.
    """
    episode_valid = bool(episode) and (
        now_us <= 0 or now_us - int(episode.get("first_seen_us") or 0) <= ttl_us
    )
    if episode_valid:
        frozen_stop = float(episode["invalidation_price"])
        # Invalidated: price closed beyond the frozen stop without ever
        # confirming entry — the watched setup failed, not "the same
        # opportunity, just later." Falls through to a fresh ladder below,
        # same as a brand-new setup.
        if invalidated(frozen_stop):
            episode_valid = False

    if episode_valid:
        return LadderBasis(
            entry_price=float(episode["entry_price"]),
            invalidation_price=float(episode["invalidation_price"]),
            reused=True,
            first_seen_us=int(episode.get("first_seen_us") or 0),
            frozen_episode=episode,
        )

    entry, invalidation = compute_fresh_entry_invalidation()
    return LadderBasis(
        entry_price=entry,
        invalidation_price=invalidation,
        reused=False,
        first_seen_us=now_us,
        frozen_episode=None,
    )


def finalize_episode(
    basis: LadderBasis, pine, chaseable: bool
) -> tuple[bool, dict, float, float, float, float, str]:
    """Decide suppress-vs-publish and compute the final ladder to use.

    Returns (suppress, snapshot, target, target2, target3, effective_risk,
    target_method).

    suppress=True means the strategy should return None this cycle —
    same still-open episode, nothing changed since the last alert (or it
    was already chaseable last cycle too). This is the actual Phase W
    fix: stay silent instead of re-publishing/re-alerting/re-archiving a
    structurally identical candidate.
    """
    if basis.reused:
        episode = basis.frozen_episode
        target = float(episode["target_price"])
        target2 = float(episode["target2_price"])
        target3 = float(episode["target3_price"])
        effective_risk = abs(basis.entry_price - basis.invalidation_price)
        target_method = str(episode.get("target_method") or "frozen_watch_episode")
        was_chaseable = bool(episode.get("alerted_chaseable"))
        newly_chaseable = chaseable and not was_chaseable
        if bool(episode.get("alerted_watch")) and not newly_chaseable:
            return True, episode, target, target2, target3, effective_risk, target_method
        snapshot = {
            **episode,
            "alerted_watch": True,
            "alerted_chaseable": was_chaseable or chaseable,
        }
        return False, snapshot, target, target2, target3, effective_risk, target_method

    # Fresh episode: pine already computed T1/T2/T3/risk/target_method
    # with these exact same entry/invalidation inputs — reuse instead of
    # recomputing (matches the original strategies' own comment on this).
    target, target2, target3 = pine.t1_price, pine.t2_price, pine.t3_price
    effective_risk, target_method = pine.risk_per_share, pine.target_method
    snapshot = {
        "entry_price": basis.entry_price,
        "invalidation_price": basis.invalidation_price,
        "target_price": target,
        "target2_price": target2,
        "target3_price": target3,
        "target_method": target_method,
        "first_seen_us": basis.first_seen_us,
        "alerted_watch": True,
        "alerted_chaseable": chaseable,
    }
    return False, snapshot, target, target2, target3, effective_risk, target_method
