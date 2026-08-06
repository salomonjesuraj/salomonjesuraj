# Phase 5 — Live Market Validation Dashboard

## System Philosophy

> **Signal quality > alert quantity.**
> **Statistical proof before feature expansion.**
> **AI summarizes validated intelligence, not compensates for lack of data.**

This document defines the measurement framework for evaluating the Infusion trading intelligence platform under real NSE market conditions. No new features, strategies, or AI layers are introduced until these criteria are met.

---

## Current System State

| Component | Status | Strategy |
|-----------|--------|----------|
| Signal Scanner | ✅ Operational | `vol_vwap_breakout` (1 strategy only) |
| Conviction Scoring | ✅ Deterministic | A+ / A / B+ / B / C / D grades |
| Sector Intelligence | ✅ Active | Dynamic sector ranking + conviction adjustment |
| Regime Awareness | ✅ Active | RISK_ON / RISK_OFF / NEUTRAL suppression |
| Pre-Breakout Watchlist | ✅ Active | COILED → TRIGGERED → ACTIVE lifecycle |
| Suppression Gate | ✅ Active | Cooldown, duplicate, regime, sector filters |
| Signal Archiver | ✅ Persisting | Postgres + outcome tracking |
| Analytics Engine | ✅ Computing | Precision by grade/sector/session/regime |
| Daily Recap | ✅ Delivering | 15:35 IST via Telegram |
| Analytics API | ✅ Serving | 8 endpoints |

---

## Data Collection Requirements

| Metric | Minimum | Target | Current |
|--------|---------|--------|---------|
| Live market sessions | 10 | 20+ | 0 |
| Active signals tracked | 50 | 100+ | 0 |
| Outcome-tracked signals | 30 | 75+ | 0 |
| Suppressed signals | 100 | 200+ | 0 |
| Sectors with signal history | 3+ | 5+ | 0 |
| Regime transitions observed | 2+ | 5+ | 0 |

> [!IMPORTANT]
> No strategy expansion or AI layer until minimum thresholds are met across ALL rows.

---

## KPI Definitions

### 1. Signal Precision by Grade

**Definition:** Of all signals that reached a decided outcome (TARGET_HIT or STOP_HIT), what percentage hit target?

```
Precision = TARGET_HIT / (TARGET_HIT + STOP_HIT) × 100
```

**Computed per grade:** A+, A, B+, B (C/D are passive — not delivered)

**API:** `GET /api/analytics/precision/grade`

**Success Threshold:**

| Grade | Minimum Precision | Expected Precision | Fail Threshold |
|-------|-------------------|-------------------|----------------|
| A+ | 65% | 75%+ | < 55% |
| A | 55% | 65%+ | < 45% |
| B+ | 45% | 55%+ | < 35% |

> [!WARNING]
> If A+ precision < A precision over 20+ signals, the conviction scoring model needs recalibration.

**Validation Query:**
```sql
SELECT conviction_grade, 
       COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as hits,
       COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stops,
       ROUND(COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::numeric / 
             NULLIF(COUNT(*) FILTER (WHERE outcome_label IN ('TARGET_HIT','STOP_HIT')), 0) * 100, 1) as precision_pct
FROM signals
WHERE NOT suppressed AND outcome_tracked
GROUP BY conviction_grade
ORDER BY AVG(conviction_score) DESC;
```

---

### 2. Signal Precision by Sector

**Definition:** Precision breakdown by sector_id — identifies which sectors the strategy performs best/worst in.

**API:** `GET /api/analytics/precision/sector`

**Success Threshold:**

| Condition | Status |
|-----------|--------|
| At least 1 sector with precision > 60% over 10+ signals | ✅ Strategy viable in sector |
| All sectors below 40% precision over 10+ signals | ❌ Strategy needs sector-specific tuning |
| Strong-sector signals outperform weak-sector signals | ✅ Sector intelligence validated |

**Key Question:** Do signals in sectors with `sector_strength > 60` outperform signals in sectors with `sector_strength < 40`?

**Validation Query:**
```sql
SELECT 
    CASE WHEN sector_strength >= 60 THEN 'strong_sector'
         WHEN sector_strength >= 40 THEN 'neutral_sector'
         ELSE 'weak_sector' END as sector_tier,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as hits,
    ROUND(COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::numeric / 
          NULLIF(COUNT(*) FILTER (WHERE outcome_label IN ('TARGET_HIT','STOP_HIT')), 0) * 100, 1) as precision_pct
FROM signals
WHERE NOT suppressed AND outcome_tracked AND sector_strength > 0
GROUP BY 1 ORDER BY 1;
```

---

### 3. Signal Precision by Regime

**Definition:** Precision in RISK_ON vs NEUTRAL vs RISK_OFF market conditions.

**API:** `GET /api/analytics/precision/regime`

**Success Threshold:**

| Condition | Status |
|-----------|--------|
| RISK_ON precision > RISK_OFF precision | ✅ Regime filtering validated |
| RISK_ON precision > 55% over 15+ signals | ✅ Strategy aligned with regime |
| RISK_OFF suppression rate > 80% | ✅ Suppression protecting capital |

**Key Question:** Does regime filtering actually prevent bad signals from being delivered?

---

### 4. Watchlist Conversion Rate

**Definition:** Of all symbols that entered COILED state in the pre-breakout watchlist, what percentage progressed to TRIGGERED → ACTIVE (signal emitted)?

```
Conversion Rate = ACTIVE_signals / COILED_entries × 100
```

**Success Threshold:**

| Metric | Expected | Concerning |
|--------|----------|------------|
| Conversion rate | 5-20% | > 40% (too loose) or < 2% (too tight) |
| Avg COILED duration | 10-60 min | < 5 min (noise) or > 4 hrs (stale) |
| COILED → expired (no signal) | 70-90% | < 50% (too many false breakouts) |

**Key Question:** Is the watchlist successfully filtering noise, or is it missing valid setups?

---

### 5. Suppression Effectiveness

**Definition:** Measures whether suppressed signals would have performed worse than delivered signals.

**API:** `GET /api/analytics/suppression`

**Success Threshold:**

| Condition | Status |
|-----------|--------|
| Suppressed signal avg_score < delivered signal avg_score | ✅ Suppression is selective |
| Suppressed signals: hypothetical precision < 40% | ✅ Suppression preventing losses |
| Suppression rate: 40-70% of total evaluations | ✅ Balanced filtering |
| Suppression rate < 20% | ⚠️ Too permissive |
| Suppression rate > 85% | ⚠️ Too aggressive |

**Suppression by Reason Breakdown:**

| Reason | Expected % of Suppressions |
|--------|----------------------------|
| `cooldown_active` | 30-50% |
| `duplicate_active` | 10-20% |
| `regime_risk_off` | 10-30% |
| `sector_weak` | 5-15% |
| `below_min_grade` | 10-25% |

---

### 6. Time-to-Target Analysis

**Definition:** How long (in minutes) from signal emission to target hit.

**Success Threshold:**

| Session | Expected Time-to-Target | Concerning |
|---------|------------------------|------------|
| Opening (9:15-10:00) | 10-30 min | > 90 min |
| Mid-morning (10-12) | 15-45 min | > 120 min |
| Midday (12-14) | 20-60 min | > 120 min |
| Closing (14-15:30) | 10-30 min | > 60 min |

**Key Question:** Are signals producing timely results, or are they "eventually right" (which is operationally useless)?

**Validation Query:**
```sql
SELECT session_hour,
       ROUND(AVG(time_to_target_min)::numeric, 1) as avg_time,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY time_to_target_min)::numeric, 1) as median_time,
       COUNT(*) as count
FROM signals
WHERE outcome_label = 'TARGET_HIT' AND time_to_target_min IS NOT NULL
GROUP BY session_hour;
```

---

### 7. Time-to-Failure Analysis

**Definition:** How long from signal emission to stop hit.

**Success Threshold:**

| Metric | Good | Concerning |
|--------|------|------------|
| Avg time-to-stop > avg time-to-target | ✅ Winners are faster | ❌ Losers hit faster |
| Median time-to-stop > 15 min | ✅ Not immediate failures | ⚠️ Rapid failures |
| Time-to-stop < 5 min (count) | < 10% of stops | ⚠️ False breakout problem |

**Key Question:** Are stop-hits happening immediately (false breakouts) or after a reasonable attempt?

---

### 8. False Breakout Analysis

**Definition:** Signals that hit stop within 5 minutes of emission — indicating the breakout was false.

```
False Breakout Rate = (STOP_HIT within 5 min) / (all STOP_HIT) × 100
```

**Success Threshold:**

| Metric | Target | Fail |
|--------|--------|------|
| False breakout rate | < 25% | > 50% |
| False breakouts in opening session | < 30% | > 60% |
| A+ false breakout rate | < 15% | > 35% |

---

### 9. Session Quality Analysis

**Definition:** Which market sessions produce the highest precision signals?

**API:** `GET /api/analytics/precision/session`

**Success Threshold:**

| Session | Expected Precision | Signal Volume |
|---------|-------------------|---------------|
| Opening (9:15-10:00) | 50-65% | Highest volume |
| Mid-morning (10-12) | 55-70% | Moderate |
| Midday (12-14) | 40-55% | Lowest volume |
| Closing (14-15:30) | 50-60% | Moderate |

**Key Question:** Should midday signals be suppressed or downgraded?

---

### 10. Daily Performance Drift

**Definition:** Is precision stable across days or trending in one direction?

**Monitoring Approach:**
- Track 5-day rolling precision
- Track 10-day rolling precision
- Alert if 5-day precision drops below 35% (3+ sessions)

**Validation Query:**
```sql
SELECT created_at::date as trade_date,
       COUNT(*) as signals,
       COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as hits,
       COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stops,
       ROUND(COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::numeric /
             NULLIF(COUNT(*) FILTER (WHERE outcome_label IN ('TARGET_HIT','STOP_HIT')), 0) * 100, 1) as precision_pct
FROM signals
WHERE NOT suppressed AND outcome_tracked
GROUP BY 1
ORDER BY 1 DESC
LIMIT 20;
```

---

## Strategy Promotion Criteria

### Gate 1: Data Volume (required before ANY evaluation)

- [ ] >= 10 live market sessions completed
- [ ] >= 50 active (non-suppressed) signals emitted
- [ ] >= 30 outcome-tracked signals (TARGET_HIT or STOP_HIT)
- [ ] >= 100 suppressed signals archived

### Gate 2: Precision Validation

- [ ] Overall precision >= 50% (decided signals only)
- [ ] A+ precision >= 60%
- [ ] A+ precision > A precision (grade ordering validated)
- [ ] A precision > B+ precision (grade ordering validated)

### Gate 3: Intelligence Validation

- [ ] Strong-sector signals outperform weak-sector signals
- [ ] RISK_ON precision > NEUTRAL precision
- [ ] RISK_OFF suppression rate > 70%
- [ ] Suppression avg_score < delivered avg_score

### Gate 4: Operational Validation

- [ ] False breakout rate < 30%
- [ ] Median time-to-target < 60 min
- [ ] Time-to-stop > 10 min (median)
- [ ] Daily precision standard deviation < 25%

> [!CAUTION]
> ALL four gates must pass before introducing a second strategy or AI layer.

---

## AI Layer Gate

The AI layer (Gemini integration, natural language summaries) is NOT approved until:

- [ ] Gate 1-4 all pass
- [ ] >= 20 live market sessions completed
- [ ] >= 100 outcome-tracked signals
- [ ] Analytics data has been manually reviewed and confirmed trustworthy
- [ ] No systematic false breakout pattern identified

**AI should summarize validated intelligence, not compensate for lack of data.**

---

## Monitoring Checklist (Per Session)

### Pre-Market (before 9:15 IST)
- [ ] All services running: `docker compose ps`
- [ ] Redis connected
- [ ] Postgres connected
- [ ] Archiver consuming streams
- [ ] Alerter consuming streams

### During Market (9:15 - 15:30 IST)
- [ ] Monitor Telegram for signal alerts
- [ ] Note signal quality subjectively
- [ ] Check for false breakouts (immediate stop-hits)
- [ ] Monitor suppression rate (should be 40-70%)

### Post-Market (after 15:35 IST)
- [ ] Verify daily recap delivered to Telegram
- [ ] Check `GET /api/analytics/recap`
- [ ] Review precision by grade
- [ ] Review suppression breakdown
- [ ] Log observations

---

## Data Access Quick Reference

| What | How |
|------|-----|
| Overall precision | `GET /api/analytics/precision` |
| Precision by grade | `GET /api/analytics/precision/grade` |
| Precision by sector | `GET /api/analytics/precision/sector` |
| Precision by session | `GET /api/analytics/precision/session` |
| Precision by regime | `GET /api/analytics/precision/regime` |
| Suppression stats | `GET /api/analytics/suppression` |
| Recent outcomes | `GET /api/analytics/outcomes?limit=20` |
| Daily recap (JSON) | `GET /api/analytics/recap?date=YYYY-MM-DD` |
| Raw SQL | `docker compose exec postgres psql -U infusion -d infusion` |

---

## Session Log Template

```
Session: YYYY-MM-DD
Market Regime: [RISK_ON / NEUTRAL / RISK_OFF]
Session Type: [trend / chop / rotation / gap]

Signals Emitted: X
Signals Suppressed: X
Target Hits: X
Stop Hits: X
Precision: X%

Observations:
- 
- 
- 

Issues:
- 
- 
```
