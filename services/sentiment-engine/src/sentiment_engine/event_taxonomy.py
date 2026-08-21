"""Deterministic event taxonomy -- EBIE EB-7 (increment 2).

Per docs/EBIE-BLUEPRINT.md Section 4.10: "Do not use generic positive/
negative keywords. Financial language is contextual" -- the event
TAXONOMY below is deliberately not a sentiment classifier (FinBERT in
classifier.py does that job); it only answers "what kind of corporate
event is this article about," a much narrower and more reliable
keyword-matching problem than judging tone. Priority-ordered, most-
specific-first, same "first match wins" pattern this codebase already
uses for breakout_type classification (api/routes/ticks.py) -- falls
through to 'other' when nothing in the fixed taxonomy matches, never a
fabricated specific category.

Severity is a static v1 heuristic (how consequential this class of
event tends to be, not how positive/negative), explicitly disclosed as
not yet calibrated against real outcome data -- a real severity model
would need historical price-reaction evidence per event_type, which
doesn't exist yet this early in EB-7. Revisit once enough classified,
outcome-linked articles have accumulated.
"""

from __future__ import annotations

# (event_type, [phrases to match against lowercased heading+summary], severity)
# Ordered most-specific/highest-signal first.
_TAXONOMY: list[tuple[str, list[str], float]] = [
    (
        "regulatory_investigation",
        [
            "regulatory probe",
            "sebi probe",
            "sebi investigation",
            "raided",
            "cbi raid",
            "ed raid",
            "income tax raid",
            "fraud investigation",
            "show cause notice",
            "under investigation",
        ],
        0.9,
    ),
    (
        "regulatory_approval",
        [
            "regulatory approval",
            "gets approval",
            "receives approval",
            "usfda approval",
            "drug approval",
            "clearance from",
            "nod from sebi",
            "environmental clearance",
        ],
        0.6,
    ),
    (
        "acquisition",
        [
            "to acquire",
            "acquires",
            "acquisition of",
            "merger with",
            "to merge",
            "takeover",
            "buyout",
        ],
        0.75,
    ),
    (
        "stake_sale_purchase",
        [
            "stake sale",
            "sells stake",
            "buys stake",
            "stake purchase",
            "block deal",
            "bulk deal",
            "divests stake",
            "raises stake",
        ],
        0.55,
    ),
    (
        "promoter_pledge",
        [
            "promoter pledge",
            "pledged shares",
            "shares pledged",
            "pledge of shares",
        ],
        0.7,
    ),
    (
        "credit_rating_change",
        [
            "rating upgraded",
            "rating downgraded",
            "credit rating",
            "rating outlook",
            "crisil rating",
            "icra rating",
            "care ratings",
        ],
        0.65,
    ),
    (
        "debt_refinancing",
        [
            "debt refinancing",
            "refinances debt",
            "restructures debt",
            "loan restructuring",
        ],
        0.5,
    ),
    (
        "management_change",
        [
            "resigns",
            "resignation",
            "steps down",
            "appoints new ceo",
            "new md",
            "new chairman",
            "management change",
            "appointed as director",
        ],
        0.6,
    ),
    (
        "lawsuit",
        [
            "lawsuit",
            "sues",
            "sued by",
            "legal notice",
            "court case",
            "litigation",
        ],
        0.55,
    ),
    (
        "plant_shutdown",
        [
            "plant shutdown",
            "shuts plant",
            "halts production",
            "temporary shutdown",
            "suspends operations",
        ],
        0.65,
    ),
    (
        "production_increase",
        [
            "capacity expansion",
            "ramps up production",
            "new plant",
            "commissions plant",
            "expands capacity",
        ],
        0.5,
    ),
    (
        "order_win",
        [
            "wins order",
            "bags order",
            "secures order",
            "receives order",
            "order win",
            "contract win",
            "wins contract",
        ],
        0.55,
    ),
    (
        "earnings_beat",
        [
            "beats estimates",
            "beat estimates",
            "profit jumps",
            "profit surges",
            "record profit",
            "results beat",
        ],
        0.7,
    ),
    (
        "earnings_miss",
        [
            "misses estimates",
            "profit falls",
            "profit declines",
            "posts loss",
            "results miss",
            "widens loss",
        ],
        0.7,
    ),
    (
        "guidance_change",
        [
            "raises guidance",
            "cuts guidance",
            "revises guidance",
            "guidance revised",
            "outlook raised",
            "outlook cut",
        ],
        0.65,
    ),
    (
        "commodity_input_shock",
        [
            "crude oil",
            "input cost",
            "raw material cost",
            "commodity prices",
            "rising crude",
            "oil prices",
        ],
        0.45,
    ),
    (
        "government_policy",
        [
            "government policy",
            "budget announcement",
            "import duty",
            "export duty",
            "tariff",
            "gst rate",
            "policy change",
        ],
        0.5,
    ),
    (
        "sector_policy",
        [
            "rbi policy",
            "sebi circular",
            "sector regulation",
            "irdai",
            "trai",
        ],
        0.5,
    ),
]

DEFAULT_EVENT_TYPE = "other"
DEFAULT_SEVERITY = 0.2  # generic market commentary / roundup, not a distinct corporate event

EVENT_SEVERITY: dict[str, float] = {name: sev for name, _, sev in _TAXONOMY} | {
    DEFAULT_EVENT_TYPE: DEFAULT_SEVERITY,
}


def classify_event_type(heading: str, summary: str | None) -> str:
    """First-match-wins keyword classification. Never fabricates a
    specific category -- returns DEFAULT_EVENT_TYPE ('other') when
    nothing in the fixed taxonomy matches."""
    text = f" {heading} {summary or ''} ".lower()
    for event_type, phrases, _severity in _TAXONOMY:
        for phrase in phrases:
            if phrase in text:
                return event_type
    return DEFAULT_EVENT_TYPE


def event_severity(event_type: str) -> float:
    return EVENT_SEVERITY.get(event_type, DEFAULT_SEVERITY)
