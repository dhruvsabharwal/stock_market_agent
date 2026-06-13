"""Stage analysis (Weinstein-style 4-stage state machine).

See `stage_analysis_spec.md` for the full specification. This module is the
source of truth for the implementation; the spec is the source of truth for
the rules.

Public API
----------
- `compute_stage_analysis(weekly, benchmark_weekly=None, **config) -> StageAnalysis`
- `StageAnalysis` (dataclass) — exposes every derived metric used by the state
  machine (`sma30`, `slope30_pct`, `slope30_category`, `vol_avg`, pivots,
  segments, optional `relative_strength`) so results can be validated.
- `WeeklyPivot`, `StageSegment`, `RelativeStrength` (dataclasses).

Inputs are weekly OHLCV. Caller is responsible for daily→weekly resampling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import math
import numpy as np
import pandas as pd


__all__ = [
    "WeeklyPivot",
    "PriorStageInfo",
    "StageSegment",
    "RelativeStrength",
    "StageAnalysis",
    "RangeWithStage",
    "compute_stage_analysis",
    "build_etf_stage_cache",
    "enrich_with_etf_stages",
    "find_ranges_with_stage",
    "DEFAULT_CONFIG",
]


DEFAULT_CONFIG: dict = {
    "pivot_window": 5,         # weeks on each side for two-sided pivot detection
    "slope_lookback": 3,       # weeks for SMA30 slope calc
    "slope_flat_pct": 0.3,     # |slope%| ≤ this = Flat
    "breakout_buffer_pct": 0.0,  # required % above ceiling for Stage 2
                                 # (0 = close just above ceiling is enough;
                                 #  S2→S1 revert handles false breakouts)
    "vol_mult": 1.5,           # volume multiple — spike path (single week ≥ 1.5×)
    "vol_step_mult": 1.3,      # volume multiple — step path (≥ 1.3× AND
                               # vol[t] > vol[t-1] > vol[t-2], institutions
                               # spreading buys across consecutive weeks)
    "vol_lookback": 10,        # weeks to average volume over (breakout week excluded)
    "osc_window": 4,           # weeks used in Stage 3 trigger C
    "trendline_tolerance_pct": 5.0,  # max % deviation of a middle anchor from
                                     # the (oldest, newest) endpoint line to be
                                     # considered colinear with it
    "s2_confirmation_pct": 10.0,     # Stage 2 is "unconfirmed" until its max
                                     # weekly High exceeds the original Stage 1
                                     # ceiling by this %. While unconfirmed, a
                                     # weekly Low back below the ceiling
                                     # triggers a failed-breakout revert to
                                     # Stage 1.
    "s4_revert_window": 5,           # weeks after a Stage 3→4 breakdown during
                                     # which a close back above the S3 floor
                                     # cancels the breakdown and restores Stage 3.
    "collapse_min_pct": 5.0,         # both higher-high and higher-low must exceed
                                     # prior Stage 2 reference by at least this %
                                     # to qualify as a retroactive collapse
    "s1_ceiling_max_age_weeks": 78,  # Stage 1 ceiling expires if the pivot high
                                     # that set it is older than this many weeks
                                     # (~1.5 years). Stale ceiling resets to None;
                                     # a fresh pivot high must form to re-establish.
}


# ── Verbosity control ────────────────────────────────────────────────────────
# Set stage_analysis.VERBOSE = True before calling compute_stage_analysis to
# enable all debug prints (S3 weekly, S1 ceiling, ETF context downloads, etc.)
# Set it back to False (the default) for silent batch runs.

VERBOSE: bool = False

def _log(*args, **kwargs) -> None:
    """Print only when VERBOSE is enabled."""
    if VERBOSE:
        print(*args, **kwargs)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _normalize_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Accept yfinance-style frames (MultiIndex cols, extra cols) and return a
    clean OHLCV frame with a DatetimeIndex.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"weekly missing columns: {missing}")
    out = df[required].dropna()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    return out


def _ascending_lows(pivots: list) -> list:
    """Return the strictly-ascending subsequence of pivot lows in chronological
    order. These are the "trend-defining lows" — pullback bottoms that form
    the rising lower envelope of the uptrend. A pivot low that fails to be
    higher than the prior kept one is NOT added (it's a candidate trend break).
    """
    out: list = []
    for p in pivots:
        if not out or p.price > out[-1].price:
            out.append(p)
    return out


def _ascending_toward_newest(pivots: list) -> list:
    """Return the largest ascending subsequence that ends at the newest pivot.

    Walk backward from the newest pivot, keeping only lows that are strictly
    lower than the current earliest kept pivot. The result is a sequence that
    ascends toward the most recent low.

    This is the right filter for trendline seeding (S1→2, S3→2) and for
    retroactive collapse merges. Unlike _ascending_lows (oldest→newest), which
    can drop recent actual-support lows in favour of older temporary highs,
    this filter anchors at the most recent proven support and extends backward
    only through lows that are genuinely lower (i.e. the sequence ascends
    toward the breakout).

    Example: [106, 109, 107, 113]
      _ascending_lows        → [106, 109, 113]  (drops 107, keeps older 109)
      _ascending_toward_newest → [106, 107, 113]  (drops 109, keeps newer 107)
    """
    if not pivots:
        return []
    kept = [pivots[-1]]          # always anchor at the newest
    for p in reversed(pivots[:-1]):
        if p.price < kept[0].price:
            kept.insert(0, p)
    return kept


def _line_from_endpoints(anchors: list) -> tuple[Optional[float], Optional[float]]:
    """Define a straight line from the first and last anchor (endpoints).

    This is NOT a regression. The line passes exactly through the oldest and
    newest anchor in the list. Middle anchors are expected to be near the
    line (verified by `_select_colinear_anchors`) but do not influence its
    slope.
    """
    if len(anchors) < 2:
        return None, None
    oldest, newest = anchors[0], anchors[-1]
    if newest.week_index == oldest.week_index:
        return None, None
    m = (newest.price - oldest.price) / (newest.week_index - oldest.week_index)
    b = newest.price - m * newest.week_index
    return float(m), float(b)


def _select_colinear_anchors(anchors: list, tolerance_pct: float) -> list:
    """From a chronological list of ascending pivot lows, return the largest
    *recent* tail that is colinear within `tolerance_pct`%.

    Algorithm — greedy backward extension:
      1. Start with the last 2 anchors. They define an exact line.
      2. Walk backwards. For each candidate older anchor, redefine the line
         as the connection between (candidate, latest). Verify every middle
         anchor (those between candidate and latest) is within tolerance%
         of the new line. If all are, expand the kept set; if any fails,
         stop — the prior set is the answer.
      3. The returned list is contiguous from some index to the end.

    More kept anchors → higher confidence (the trend has held across more
    pullbacks). The line is always defined by the first and last of the
    returned list (see `_line_from_endpoints`).
    """
    if len(anchors) < 2:
        return list(anchors)
    latest = anchors[-1]
    best = anchors[-2:]  # at minimum, the last two

    for i in range(len(anchors) - 3, -1, -1):
        oldest = anchors[i]
        if latest.week_index == oldest.week_index:
            break
        m = (latest.price - oldest.price) / (latest.week_index - oldest.week_index)
        b = latest.price - m * latest.week_index
        # Verify every middle anchor sits within tolerance of this line.
        all_within = True
        for p in anchors[i + 1:-1]:
            line_y = m * p.week_index + b
            denom = abs(p.price) if abs(p.price) > 1e-9 else 1e-9
            if abs(p.price - line_y) / denom * 100.0 > tolerance_pct:
                all_within = False
                break
        if all_within:
            best = anchors[i:]
        else:
            break  # stop on first failure (recency must be contiguous)
    return list(best)


def _categorize_slope(slope_pct: Optional[float], flat_pct: float) -> str:
    if slope_pct is None:
        return "Unknown"
    if slope_pct > flat_pct:
        return "Rising"
    if slope_pct < -flat_pct:
        return "Declining"
    return "Flat"


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class WeeklyPivot:
    """A weekly pivot low or high.

    Two-sided swing definition: week t is a pivot if its Low/High is strictly
    less/greater than the Low/High of the prior `window` weeks AND the next
    `window` weeks. For the last `window` weeks of data we have less than
    `window` forward bars — those pivots are tentative (`is_confirmed=False`).
    """
    date: pd.Timestamp
    price: float
    kind: str               # 'low' or 'high'
    week_index: int         # integer position in the weekly DataFrame
    is_confirmed: bool = True  # False = forward window incomplete (tail-only)

    def to_dict(self) -> dict:
        return {
            "date": self.date.strftime("%Y-%m-%d"),
            "price": round(self.price, 4),
            "kind": self.kind,
            "week_index": self.week_index,
            "is_confirmed": self.is_confirmed,
        }


@dataclass
class PriorStageInfo:
    """Summary of the most recent prior occurrence of a given stage."""
    stage: int
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    duration_weeks: int    # number of weekly bars the prior stage lasted
    weeks_ago: int         # weekly bars between prior stage end and current stage start

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "start_date": self.start_date.strftime("%Y-%m-%d"),
            "end_date": self.end_date.strftime("%Y-%m-%d"),
            "duration_weeks": self.duration_weeks,
            "weeks_ago": self.weeks_ago,
        }


@dataclass
class StageSegment:
    """One contiguous span of weeks in a single stage (0..4)."""
    state: int
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    entry_trigger: Optional[str] = None

    # State 1 specifics
    stage1_floor: Optional[float] = None
    stage1_ceiling: Optional[float] = None
    stage1_ceiling_locked: bool = False

    # State 2 specifics
    stage2_line_m: Optional[float] = None
    stage2_line_b: Optional[float] = None
    stage2_anchor_pivots: list = field(default_factory=list)  # WeeklyPivots used for line

    # State 3 specifics
    stage3_floor: Optional[float] = None
    stage3_ceiling: Optional[float] = None

    # ── Enrichment fields (populated by _enrich_segments) ────────────────────
    duration_weeks: int = 0             # how many weekly bars this segment spans
    stage_iteration: int = 0            # 1st, 2nd, 3rd... time the stock is in this stage
    prior_s1: Optional[PriorStageInfo] = None  # most recent prior Stage 1
    prior_s2: Optional[PriorStageInfo] = None  # most recent prior Stage 2
    prior_s3: Optional[PriorStageInfo] = None  # most recent prior Stage 3
    prior_s4: Optional[PriorStageInfo] = None  # most recent prior Stage 4

    # ── ETF context (populated by enrich_with_etf_stages) ────────────────────
    # All ETF stage segments whose date range overlaps this stock segment.
    # There may be multiple if the ETF changed stage during the stock's stage.
    industry_segments: list = field(default_factory=list)  # e.g. SMH segments
    sector_segments:   list = field(default_factory=list)  # e.g. XLK segments
    industry_etf: Optional[str] = None   # symbol used (e.g. "SMH")
    sector_etf:   Optional[str] = None   # symbol used (e.g. "XLK")

    def line_at(self, week_index: int) -> Optional[float]:
        """Project Stage2_Line forward to a given week index. Returns None if undefined."""
        if self.stage2_line_m is None or self.stage2_line_b is None:
            return None
        return self.stage2_line_m * week_index + self.stage2_line_b

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "start_date": self.start_date.strftime("%Y-%m-%d"),
            "end_date": self.end_date.strftime("%Y-%m-%d"),
            "entry_trigger": self.entry_trigger,
            "stage1_floor": self.stage1_floor,
            "stage1_ceiling": self.stage1_ceiling,
            "stage1_ceiling_locked": self.stage1_ceiling_locked,
            "stage2_line_m": self.stage2_line_m,
            "stage2_line_b": self.stage2_line_b,
            "stage2_anchor_pivots": [p.to_dict() for p in self.stage2_anchor_pivots],
            "stage3_floor": self.stage3_floor,
            "stage3_ceiling": self.stage3_ceiling,
            "duration_weeks": self.duration_weeks,
            "stage_iteration": self.stage_iteration,
            "prior_s1": self.prior_s1.to_dict() if self.prior_s1 else None,
            "prior_s2": self.prior_s2.to_dict() if self.prior_s2 else None,
            "prior_s3": self.prior_s3.to_dict() if self.prior_s3 else None,
            "prior_s4": self.prior_s4.to_dict() if self.prior_s4 else None,
            "industry_etf": self.industry_etf,
            "industry_segments": [s.to_dict() for s in self.industry_segments],
            "sector_etf": self.sector_etf,
            "sector_segments": [s.to_dict() for s in self.sector_segments],
        }


@dataclass
class RelativeStrength:
    """Weekly RS line vs a benchmark. Computed but NOT used by the state machine."""
    benchmark_symbol: str
    rs_line: pd.Series          # stock_close / benchmark_close, rebased to 100
    rs_slope_5w: pd.Series      # % change over last 5 weeks
    rs_new_high: pd.Series      # bool, RS at all-time-high through that week

    def to_dict(self) -> dict:
        def _ser(s: pd.Series, cast=float):
            out = {}
            for d, v in s.items():
                if pd.isna(v):
                    continue
                out[d.strftime("%Y-%m-%d")] = cast(v)
            return out
        return {
            "benchmark_symbol": self.benchmark_symbol,
            "rs_line": _ser(self.rs_line),
            "rs_slope_5w": _ser(self.rs_slope_5w),
            "rs_new_high": _ser(self.rs_new_high, cast=bool),
        }


@dataclass
class StageAnalysis:
    """Top-level container — all inputs and derived series the state machine uses."""
    weekly: pd.DataFrame
    config: dict

    # Derived weekly metrics (each indexed by weekly DatetimeIndex)
    sma30: pd.Series
    slope30_pct: pd.Series
    slope30_category: pd.Series
    vol_avg: pd.Series           # spike baseline: prior 10w avg (excludes current week)
    vol_avg_step: pd.Series      # step baseline: prior 10w avg shifted 3w (excludes t, t-1, t-2)

    # Pivots in chronological order
    pivot_lows: list = field(default_factory=list)
    pivot_highs: list = field(default_factory=list)

    # State timeline
    segments: list = field(default_factory=list)
    state_series: Optional[pd.Series] = None  # int per week, indexed by weekly date

    # Auxiliary (not used by rules)
    relative_strength: Optional[RelativeStrength] = None

    # ── Lookups ──────────────────────────────────────────────────────────────

    def state_at(self, date) -> Optional[StageSegment]:
        """Return the StageSegment covering the week containing `date`.

        `date` may be any daily/weekly date; it is mapped to the containing
        weekly bar — the last weekly bar whose start date is <= date.

        Weekly bars are Monday-dated (yfinance convention), so for a Thursday
        like Apr 24 this correctly resolves to the Apr 21 bar (the week that
        contains Apr 24), not the Apr 28 bar (the following week).
        If date is before all data, returns the first bar's segment.
        """
        ts = pd.Timestamp(date)
        idx = self.weekly.index
        if len(idx) == 0:
            return None
        past = idx[idx <= ts]
        if len(past) > 0:
            week_ts = past[-1]
        else:
            week_ts = idx[0]  # date is before all weekly data — use first bar
        for seg in self.segments:
            if seg.start_date <= week_ts <= seg.end_date:
                return seg
        return None

    def to_dict(self) -> dict:
        def _ser(s: pd.Series, cast=float):
            out = {}
            for d, v in s.items():
                if pd.isna(v):
                    out[d.strftime("%Y-%m-%d")] = None
                else:
                    out[d.strftime("%Y-%m-%d")] = cast(v)
            return out
        return {
            "config": self.config,
            "sma30": _ser(self.sma30),
            "slope30_pct": _ser(self.slope30_pct),
            "slope30_category": _ser(self.slope30_category, cast=str),
            "vol_avg": _ser(self.vol_avg),
            "pivot_lows": [p.to_dict() for p in self.pivot_lows],
            "pivot_highs": [p.to_dict() for p in self.pivot_highs],
            "segments": [s.to_dict() for s in self.segments],
            "state_series": _ser(self.state_series, cast=int) if self.state_series is not None else {},
            "relative_strength": self.relative_strength.to_dict() if self.relative_strength else None,
        }


# ── Derived metrics ──────────────────────────────────────────────────────────

def _compute_weekly_metrics(weekly: pd.DataFrame, config: dict):
    sma30 = weekly["Close"].rolling(30).mean()
    lb = config["slope_lookback"]
    slope30_pct = (sma30 / sma30.shift(lb) - 1.0) * 100.0
    flat = config["slope_flat_pct"]
    slope30_cat = slope30_pct.apply(lambda x: _categorize_slope(None if pd.isna(x) else float(x), flat))
    # Shift by 1: baseline for spike path — excludes breakout week only.
    vol_avg = weekly["Volume"].rolling(config["vol_lookback"]).mean().shift(1)
    # Shift by 3: baseline for step path — excludes t, t-1, t-2 so the three
    # rising weeks being tested don't inflate the average they're compared to.
    vol_avg_step = weekly["Volume"].rolling(config["vol_lookback"]).mean().shift(3)
    return sma30, slope30_pct, slope30_cat, vol_avg, vol_avg_step


def _find_pivots_two_sided(weekly: pd.DataFrame, window: int):
    """Two-sided swing pivots with tie handling.

    Week t is a PivotLow if:
      - L[t] is **≤** L[t-k] for k=1..window (lookback non-strict — ties allowed)
      - L[t] is **≤** L[t+k] for k=1..window (lookforward non-strict — ties allowed)

    Same logic mirrored for PivotHigh on H.

    Both lookback and lookforward allow ties. A week is only disqualified if a
    surrounding week is **strictly** lower (for lows) or **strictly** higher
    (for highs). This means a double-bottom retest at the same price correctly
    registers as a second pivot low.

    Without non-strict lookback, a retest week is blocked by a tie with its
    predecessor — even if that predecessor was itself rejected (e.g. it sat
    above an earlier valid low). Example: Jun-21 L=6.21, Jul-5 L=6.27
    (rejected — higher than Jun-21), Aug-9 L=6.27. With strict lookback Aug-9
    ties Jul-5 and is silently dropped; with non-strict lookback Aug-9 is
    correctly detected as a valid double-bottom pivot.

    For the last `window` weeks, only as many forward bars as exist are
    checked. Tail pivots are flagged `is_confirmed=False`.
    """
    L = weekly["Low"].values.astype(float)
    H = weekly["High"].values.astype(float)
    n = len(weekly)
    lows: list[WeeklyPivot] = []
    highs: list[WeeklyPivot] = []
    for t in range(window, n):
        is_low = True
        is_high = True
        # Lookback — non-strict (ties allowed; only strictly higher/lower disqualifies)
        # A week that ties with a prior week is allowed to be a pivot — it may
        # be a valid double-bottom/top test. Only a STRICTLY lower prior low
        # (or higher prior high) disqualifies. This prevents a rejected
        # intermediate week (e.g. a tie with a non-pivot) from silently
        # blocking a later valid pivot at the same price.
        for k in range(1, window + 1):
            if L[t] > L[t - k]:
                is_low = False
            if H[t] < H[t - k]:
                is_high = False
            if not is_low and not is_high:
                break
        # Lookforward — non-strict (ties allowed; only strictly worse disqualifies)
        forward_avail = min(window, n - 1 - t)
        for k in range(1, forward_avail + 1):
            if not is_low and not is_high:
                break
            if L[t] > L[t + k]:
                is_low = False
            if H[t] < H[t + k]:
                is_high = False
        confirmed = (forward_avail == window)
        if is_low:
            lows.append(WeeklyPivot(
                date=weekly.index[t], price=float(L[t]),
                kind="low", week_index=t, is_confirmed=confirmed,
            ))
        if is_high:
            highs.append(WeeklyPivot(
                date=weekly.index[t], price=float(H[t]),
                kind="high", week_index=t, is_confirmed=confirmed,
            ))
    return lows, highs


# ── State machine ────────────────────────────────────────────────────────────

def _run_state_machine(
    weekly: pd.DataFrame,
    sma30: pd.Series,
    slope_cat: pd.Series,
    slope_pct: pd.Series,
    vol_avg: pd.Series,
    vol_avg_step: pd.Series,
    pivot_lows: list,
    pivot_highs: list,
    config: dict,
):
    """Walk forward week by week, emitting StageSegments and a per-week state series."""
    n = len(weekly)
    dates = weekly.index
    closes = weekly["Close"].values.astype(float)
    highs_arr = weekly["High"].values.astype(float)
    lows_arr = weekly["Low"].values.astype(float)
    volumes = weekly["Volume"].values.astype(float)

    low_at = {p.week_index: p for p in pivot_lows}
    high_at = {p.week_index: p for p in pivot_highs}

    segments: list[StageSegment] = []
    state_series = np.zeros(n, dtype=int)

    # Persistent across segments: max High of the most recently *completed*
    # Stage 2 segment. Used as a fallback Stage 1 ceiling when the regular
    # pivot-derived ceiling hasn't locked yet — closing above the prior S2
    # high is a structurally valid breakout regardless.
    last_completed_s2_max_high: Optional[float] = None

    # Working memory for the *current* segment
    cur = _new_seg_memory(0, 0, "init")

    def close_segment(end_idx: int):
        if end_idx < cur["start_idx"]:
            return  # nothing to emit
        anchors = list(cur.get("s2_ascending_lows") or [])
        segments.append(StageSegment(
            state=cur["state"],
            start_date=dates[cur["start_idx"]],
            end_date=dates[end_idx],
            entry_trigger=cur["entry_trigger"],
            stage1_floor=cur["s1_floor"],
            stage1_ceiling=cur["s1_ceiling"],
            stage1_ceiling_locked=cur["s1_ceiling_locked"],
            stage2_line_m=cur["s2_line_m"],
            stage2_line_b=cur["s2_line_b"],
            stage2_anchor_pivots=anchors,
            stage3_floor=cur["s3_floor"],
            stage3_ceiling=cur["s3_ceiling"],
        ))

    def transition(new_state: int, t: int, trigger: str):
        nonlocal cur, last_completed_s2_max_high
        # If we're leaving a Stage 2 segment, persist its max-high as the
        # fallback ceiling for the next Stage 1.
        if cur["state"] == 2 and cur.get("s2_max_high") is not None:
            last_completed_s2_max_high = cur["s2_max_high"]
        # Close the prior segment ending at t-1 (if it had any extent)
        if t > cur["start_idx"]:
            close_segment(t - 1)
        else:
            # zero-length segment — discard, don't emit
            pass
        cur = _new_seg_memory(new_state, t, trigger)

    for t in range(n):
        pl = low_at.get(t)
        ph = high_at.get(t)
        c = float(closes[t])
        sma_val = sma30.iloc[t]
        sma = None if pd.isna(sma_val) else float(sma_val)
        sc = slope_cat.iloc[t]
        spct_val = slope_pct.iloc[t]
        spct = None if pd.isna(spct_val) else float(spct_val)
        vavg_val = vol_avg.iloc[t]
        vavg = None if pd.isna(vavg_val) else float(vavg_val)
        vavg_step_val = vol_avg_step.iloc[t]
        vavg_step = None if pd.isna(vavg_step_val) else float(vavg_step_val)
        vol = None if pd.isna(volumes[t]) else float(volumes[t])

        # ── Volume confirmation paths (computed once; used in S1→2 and S3→2) ─
        # Path A — spike: vol[t] ≥ vol_mult × vavg  (default 1.5×)
        #   baseline = prior 10w avg excluding breakout week
        # Path B — step:  vol[t] ≥ vol_step_mult × vavg_step (default 1.3×)
        #                 AND vol[t] > vol[t-1] > vol[t-2]
        #   baseline = prior 10w avg excluding t, t-1, t-2 (so the rising
        #   weeks don't inflate the average they're compared against)
        _vol_spike = (vol is not None and vavg is not None
                      and vol >= config["vol_mult"] * vavg)
        if (vol is not None and vavg_step is not None
                and vol >= config["vol_step_mult"] * vavg_step
                and t >= 2
                and not math.isnan(float(volumes[t - 1]))
                and not math.isnan(float(volumes[t - 2]))):
            _vol_step = (vol > float(volumes[t - 1]) > float(volumes[t - 2]))
        else:
            _vol_step = False
        _vol_ok = _vol_spike or _vol_step
        _vol_path = ("spike" if _vol_spike else "step" if _vol_step else "none")

        st = cur["state"]
        moved = False

                # ── TEMP DEBUG ──────────────────────────────────────────────────────
        _dbg_date = dates[t]
        if (pd.Timestamp("2006-10-01") <= _dbg_date <= pd.Timestamp("2006-11-15")
                and cur["state"] == 3):
            _snap_dbg = cur.get("prior_s2_snapshot")
            _s2_max = _snap_dbg.get("s2_max_high") if _snap_dbg else None
            _buf = config["breakout_buffer_pct"] / 100.0
            _ref = cur["s3_ceiling"] if cur["s3_ceiling"] is not None else _s2_max
            _log(
                f"{_dbg_date.date()} | "
                f"high={float(highs_arr[t]):.4f}  close={c:.4f}  "
                f"sma={sma:.4f}  sc={sc}  "
                f"vol={vol:.0f}  vavg={vavg:.0f}  vol_ok={vol is not None and vavg is not None and vol >= config['vol_mult']*vavg}  "
                f"s3_ceil={cur['s3_ceiling']}  s2_max_high={_s2_max}  ref_ceil={_ref}  "
                f"ref_ceil*(1+buf)={(_ref*(1+_buf)) if _ref else None}  "
                f"close>=ref*(1+buf)={(_ref is not None and c >= _ref*(1+_buf))}  "
                f"floor_set={cur.get('s3_floor_set_idx') is not None}  ph={ph}"
            )
        # ── END DEBUG ────────────────────────────────────────────────────────

        # ── State 0 → 2 direct (high-volume breakout, no Stage 1 base) ─────
        # A stock can break out directly from Stage 0 into Stage 2 without
        # forming a Stage 1 base — e.g. V-shaped recoveries or stocks re-
        # emerging from long declines on institutional volume. Conditions are
        # identical to S1→2 (ceiling buffer + SMA above + Rising slope + vol).
        # The most recent confirmed pivot high serves as the reference ceiling.
        if not moved and st == 0:
            _recent_ph = next(
                (p for p in reversed(pivot_highs) if p.week_index < t), None
            )
            if _recent_ph is not None:
                buf = config["breakout_buffer_pct"] / 100.0
                _ceil_ok  = c >= _recent_ph.price * (1 + buf)
                # SMA30 condition intentionally omitted — Stage 0 stocks don't
                # yet have 30 weeks of history; volume is the sole confirmation.
                # Print any week close is within 25% of the pivot high ceiling
                if _ceil_ok or c >= _recent_ph.price * 0.75:
                    _log(
                        f"[S0 NEAR PH] {dates[t].date()} | "
                        f"close={c:.4f}  pivot_high={_recent_ph.price:.4f} ({_recent_ph.date.date()})  "
                        f"ceil_ok={_ceil_ok}  vol_path={_vol_path}  vol_ok={_vol_ok}"
                    )
                if _ceil_ok and _vol_ok:
                    _log(
                        f"[S0→S2] {dates[t].date()} | "
                        f"pivot_high={_recent_ph.price:.4f} ({_recent_ph.date.date()})  "
                        f"close={c:.4f}  buf={buf*100:.1f}%  "
                        f"vol_path={_vol_path}"
                    )
                    transition(2, t, "breakout_above_pivot_high_from_s0")
                    cur["s2_entry_ref_ceiling"] = _recent_ph.price
                    # Seed Stage 2 with the most recent confirmed pivot low
                    _prior_pl = next(
                        (p for p in reversed(pivot_lows) if p.week_index <= t), None
                    )
                    if _prior_pl is not None:
                        cur["s2_pivot_lows"].append(_prior_pl)
                    cur["s2_max_high"] = float(highs_arr[t])
                    moved = True

        # ── State 0 / 4 → 1 ──────────────────────────────────────────────
        if not moved and st in (0, 4) and pl is not None:
            prior = [p for p in pivot_lows if p.week_index < t]
            if not prior:
                _log(
                    f"[S{st} PIVOT LOW — no prior] {dates[t].date()} | "
                    f"pivot low={pl.price:.4f}  (first pivot low in dataset — no comparison possible, stay in S{st})"
                )
            if prior and pl.price >= prior[-1].price:
                # Floor = the most recent prior pivot low (the down-leg low).
                floor = prior[-1].price
                # Save Stage 4 state before transitioning so we can revert
                # if the next pivot low in Stage 1 turns out to be lower
                # (stock still declining — higher pivot low was a dead-cat).
                # Only saved for S4→1 (not S0→1 where there's nothing to revert to).
                s4_snap = None
                if st == 4:
                    s4_snap = {
                        "start_idx": cur["start_idx"],
                        "entry_trigger": cur["entry_trigger"],
                        "prior_s3_snapshot": cur.get("prior_s3_snapshot"),
                    }
                # If the S4 was entered via a dead-cat S1 revert, the prior S1
                # ceiling (e.g. 34.89) is the real resistance to watch — NOT the
                # original S2 high (e.g. 42.41) which may be far overhead.
                # Extract it before transition() resets cur.
                prior_s1_ceiling = cur.get("prior_s1_ceiling") if st == 4 else None
                prior_s1_ceiling_week = cur.get("prior_s1_ceiling_week") if st == 4 else None
                _log(
                    f"[S{st}→S1] {dates[t].date()} | "
                    f"new pivot low={pl.price:.4f}  prior pivot low={prior[-1].price:.4f} ({prior[-1].date.date()})  "
                    f"floor={floor:.4f}  "
                    f"close={c:.4f}  sma={f'{sma:.4f}' if sma is not None else 'N/A'}  sc={sc}"
                    + (f"  restored_s1_ceil={prior_s1_ceiling:.4f}" if prior_s1_ceiling is not None else "")
                )
                transition(1, t, "higher_pivot_low")
                cur["s1_floor"] = floor
                cur["s1_entry_pivot_low"] = pl.price
                cur["prior_s4_snapshot"] = s4_snap  # None for S0→1
                # Restore the prior S1 ceiling so the new S1 uses it as the
                # reference instead of falling back to the old S2 high.
                if prior_s1_ceiling is not None:
                    cur["s1_ceiling"] = prior_s1_ceiling
                    cur["s1_ceiling_week"] = prior_s1_ceiling_week
                moved = True
            elif prior and pl.price < prior[-1].price:
                _log(
                    f"[S{st} LOWER LOW — no transition] {dates[t].date()} | "
                    f"new pivot low={pl.price:.4f}  prior pivot low={prior[-1].price:.4f}  "
                    f"(still declining)"
                )

        # ── State 4: false breakdown revert → 3 ──────────────────────────
        # If within s4_revert_window weeks of the Stage 3→4 breakdown the
        # close recovers back above the Stage 3 floor, the breakdown was a
        # shakeout. Erase the Stage 4 segment and resume Stage 3 with the
        # original floor and ceiling intact.
        if not moved and cur["state"] == 4:
            snap3 = cur.get("prior_s3_snapshot")
            if snap3 is not None:
                s4_start = cur["start_idx"]
                if t - s4_start <= config["s4_revert_window"]:
                    s3_floor = snap3.get("s3_floor")
                    _s4_trigger = cur.get("entry_trigger", "")
                    # Revert condition depends on how Stage 4 was entered:
                    # • floor break (A): recover above the floor is sufficient
                    # • SMA break  (B): must recover above BOTH floor AND SMA30
                    #   (stock was below a declining SMA — need it back above
                    #   SMA to confirm the SMA break has genuinely reversed)
                    if _s4_trigger == "breakdown_below_sma_declining":
                        _revert_ok = (s3_floor is not None
                                      and c >= s3_floor
                                      and sma is not None and c > sma)
                    else:
                        _revert_ok = (s3_floor is not None and c >= s3_floor)
                    if _revert_ok:
                        _log(
                            f"[S4→S3 REVERT] {dates[t].date()} | "
                            f"close={c:.4f}  s3_floor={s3_floor:.4f}  "
                            f"trigger={_s4_trigger}  "
                            f"sma={f'{sma:.4f}' if sma is not None else 'N/A'}  "
                            f"weeks_in_s4={t - s4_start}  window={config['s4_revert_window']}  "
                            f"s4_started={dates[s4_start].date()}  "
                            f"s3_started={dates[snap3['start_idx']].date()}"
                        )
                        if (segments
                                and segments[-1].state == 3
                                and segments[-1].start_date == dates[snap3["start_idx"]]):
                            segments.pop()
                        cur = {**snap3,
                               "s2_pivot_lows": list(snap3.get("s2_pivot_lows") or []),
                               "s2_ascending_lows": list(snap3.get("s2_ascending_lows") or [])}
                        cur["state"] = 3
                        for tt in range(s4_start, t + 1):
                            state_series[tt] = 3
                        moved = True
                        continue
                    elif s3_floor is not None:
                        # Still in window but not yet recovered — log proximity
                        _log(
                            f"[S4→S3 WATCH] {dates[t].date()} | "
                            f"close={c:.4f}  s3_floor={s3_floor:.4f}  "
                            f"gap={((c - s3_floor) / s3_floor * 100):.2f}%  "
                            f"trigger={_s4_trigger}  "
                            f"weeks_in_s4={t - s4_start}/{config['s4_revert_window']}"
                        )

        # ── State 1: ceiling evolution + → 2 ────────────────────────────
        if not moved and cur["state"] == 1:
            # Failed S4→1 revert: if Stage 1 was entered from Stage 4 and a new
            # confirmed pivot low arrives that is LOWER than the entry pivot low,
            # the stock is still declining — the higher pivot low that triggered
            # S4→1 was a dead-cat bounce. Revert to Stage 4 and erase Stage 1.
            # Once a pivot low arrives that is >= s1_entry_pivot_low, Stage 1 is
            # confirmed and the revert is no longer available.
            if pl is not None and cur.get("prior_s4_snapshot") is not None:
                entry_pl = cur.get("s1_entry_pivot_low")
                snap4 = cur["prior_s4_snapshot"]
                s1_floor = cur.get("s1_floor")
                # Revert only if new pivot low breaks BELOW the Stage 1 floor
                # (the prior S4 low that anchored the base). A pullback that
                # stays above the floor — even if below the entry pivot low —
                # is a normal Stage 1 dip, not a dead-cat reversal.
                if entry_pl is not None and s1_floor is not None and pl.price < s1_floor:
                    # Lower low below floor — revert to Stage 4.
                    if (segments and segments[-1].state == 4
                            and segments[-1].start_date == dates[snap4["start_idx"]]):
                        segments.pop()
                    s1_start_idx = cur["start_idx"]
                    _log(
                        f"[S1→S4 REVERT] {dates[t].date()} | "
                        f"new pivot low={pl.price:.4f}  floor={s1_floor:.4f}  "
                        f"entry pivot low={entry_pl:.4f}  "
                        f"(broke below floor — still declining)  "
                        f"s4_started={dates[snap4['start_idx']].date()}"
                    )
                    # Preserve the S1 ceiling so the *next* S4→S1 transition
                    # can restore it instead of falling back to the old S2 high.
                    saved_s1_ceiling = cur.get("s1_ceiling")
                    saved_s1_ceiling_week = cur.get("s1_ceiling_week")
                    cur = _new_seg_memory(4, snap4["start_idx"], snap4["entry_trigger"])
                    cur["prior_s3_snapshot"] = snap4.get("prior_s3_snapshot")
                    cur["prior_s1_ceiling"] = saved_s1_ceiling
                    cur["prior_s1_ceiling_week"] = saved_s1_ceiling_week
                    for tt in range(s1_start_idx, t + 1):
                        state_series[tt] = 4
                    state_series[t] = 4
                    moved = True
                elif entry_pl is not None and pl.price >= entry_pl:
                    # Higher low above entry pivot — Stage 1 confirmed, revert
                    # no longer available.
                    _log(
                        f"[S1 CONFIRMED from S4] {dates[t].date()} | "
                        f"new pivot low={pl.price:.4f}  entry pivot low={entry_pl:.4f}  "
                        f"(ascending lows confirmed)"
                    )
                    cur["prior_s4_snapshot"] = None
                # If pivot low is between floor and entry_pl: normal S1 dip,
                # do nothing — revert stays available but doesn't fire yet.

            # S1→S4: close below Stage 1 floor.
            # The floor (prior pivot low that defined the base) breaking is
            # sufficient — no volume requirement. The base has structurally
            # failed; the stock is back in decline.
            if not moved and cur["s1_floor"] is not None:
                if c < cur["s1_floor"]:
                    _log(
                        f"[S1→S4] {dates[t].date()} | "
                        f"close={c:.4f} < floor={cur['s1_floor']:.4f}"
                    )
                    transition(4, t, "breakdown_below_stage1_floor")
                    moved = True

            # Ceiling = running max of confirmed pivot highs in Stage 1.
            # Normal rule: ceiling only moves UP on each new higher pivot high.
            # Staleness rule: when a new pivot high arrives, if the pivot high
            # that last set the ceiling is older than s1_ceiling_max_age_weeks,
            # the new pivot high replaces it unconditionally — even if it is
            # lower. The old resistance is no longer relevant; the new swing
            # high defines the current base ceiling.
            if ph is not None:
                _max_age = config["s1_ceiling_max_age_weeks"]
                _ceiling_stale = (
                    cur["s1_ceiling_week"] is not None
                    and t - cur["s1_ceiling_week"] > _max_age
                )
                if cur["s1_ceiling"] is None or ph.price > cur["s1_ceiling"]:
                    # Normal: new higher high — always update.
                    cur["s1_ceiling"] = ph.price
                    cur["s1_ceiling_week"] = t
                elif _ceiling_stale:
                    # Stale ceiling + lower pivot high → replace with fresh level.
                    _log(
                        f"[S1 CEILING RESET] {dates[t].date()} | "
                        f"old={cur['s1_ceiling']:.4f} (set {dates[cur['s1_ceiling_week']].date()}, "
                        f"age={t - cur['s1_ceiling_week']}w > {_max_age}w)  "
                        f"→ new={ph.price:.4f}"
                    )
                    cur["s1_ceiling"] = ph.price
                    cur["s1_ceiling_week"] = t

            # Stage 2 trigger: two independent paths (either fires).
            # (A) Breakout above the Stage 1 ceiling. If the ceiling was set
            #     more than s1_ceiling_max_age_weeks ago and no new pivot high
            #     has arrived yet, it still stands until replaced on next pivot.
            # (B) Breakout above the previous completed Stage 2 max-high
            #     (fallback when no pivot high formed in S1 before the breakout,
            #     e.g. NVDA 2023-05 AI gap-up).
            # (B) Breakout above the previous completed Stage 2 max-high
            #     (fallback when no pivot high formed in S1 before the breakout,
            #     e.g. NVDA 2023-05 AI gap-up).
            if sma is not None:
                buf = config["breakout_buffer_pct"] / 100.0
                ceiling_used = None
                trig_label = None
                if (cur["s1_ceiling"] is not None
                        and c >= cur["s1_ceiling"] * (1 + buf)):
                    ceiling_used = cur["s1_ceiling"]
                    trig_label = "breakout_above_stage1_ceiling"
                elif (cur["s1_ceiling"] is None  # fallback only when no S1 ceiling exists
                        and last_completed_s2_max_high is not None
                        and c >= last_completed_s2_max_high * (1 + buf)):
                    ceiling_used = last_completed_s2_max_high
                    trig_label = "breakout_above_prev_s2_high"
                # ── S1 proximity debug ────────────────────────────────────
                # Print every week the close is within 25% below any ceiling
                # reference — shows which gate is blocking the breakout.
                _any_ref = ceiling_used or cur["s1_ceiling"] or last_completed_s2_max_high
                if (_any_ref is not None
                        and c >= _any_ref * 0.75
                        and ceiling_used is None):   # only when not yet firing
                    _ref_lbl = ("s1_ceil" if cur["s1_ceiling"] is not None
                                else "prev_s2_high")
                    _ref_val = cur["s1_ceiling"] or last_completed_s2_max_high
                    _gap_pct = (c / _ref_val - 1) * 100 if _ref_val else None
                    _sma_ok_dbg = sma is not None and c > sma
                    _slope_ok_dbg = sc == "Rising"
                    _vol_str_dbg = (
                        f"vol={vol:.0f}  vavg={vavg:.0f}  "
                        f"spike_thresh={config['vol_mult']*vavg:.0f}({_vol_spike})  "
                        f"step_thresh={config['vol_step_mult']*vavg_step:.0f}({_vol_step})"
                        if vol is not None and vavg is not None and vavg_step is not None
                        else "vol=N/A"
                    )
                    _log(
                        f"[S1 NEAR CEIL] {dates[t].date()} | "
                        f"close={c:.4f}  {_ref_lbl}={_ref_val:.4f}  "
                        f"gap={_gap_pct:.2f}%  buf_needed={buf*100:.1f}%  "
                        f"sma_ok={_sma_ok_dbg}  slope={sc}({_slope_ok_dbg})  "
                        f"{_vol_str_dbg}"
                    )
                # If ceiling is breached but other gates fail, show why
                if ceiling_used is not None and not (c > sma and sc == "Rising" and _vol_ok):
                    _sma_ok  = c > sma
                    _slop_ok = sc == "Rising"
                    _vol_str2 = (
                        f"vol={vol:.0f}  vavg={vavg:.0f}  "
                        f"spike_thresh={config['vol_mult']*vavg:.0f}({_vol_spike})  "
                        f"step_thresh={config['vol_step_mult']*vavg_step:.0f}({_vol_step})"
                        if vol is not None and vavg is not None and vavg_step is not None
                        else "vol=N/A"
                    )
                    _log(
                        f"[S1 CEIL BREACHED — BLOCKED] {dates[t].date()} | "
                        f"close={c:.4f}  ceiling={ceiling_used:.4f}  "
                        f"sma_ok={_sma_ok}  slope={sc}({_slop_ok})  vol_ok={_vol_ok}  "
                        f"{_vol_str2}"
                    )
                # ── END S1 proximity debug ────────────────────────────────

                if (ceiling_used is not None
                        and c > sma
                        and sc == "Rising"
                        and _vol_ok):
                    _s1_vol_str = (
                        f"vol={vol:.0f}  vavg={vavg:.0f}  "
                        f"spike_thresh={config['vol_mult']*vavg:.0f}  "
                        f"step_thresh={config['vol_step_mult']*vavg_step:.0f}"
                        if vol is not None and vavg is not None and vavg_step is not None else "vol=N/A"
                    )
                    _log(
                        f"[S1→2] {dates[t].date()} | trigger={trig_label}  "
                        f"close={c:.4f}  ceiling={ceiling_used:.4f}  "
                        f"vol_path={_vol_path}  {_s1_vol_str}"
                    )
                    # Snapshot Stage 1 state for possible failed-breakout
                    # revert. Use the ceiling that fired the breakout.
                    s1_snapshot = {
                        "start_idx": cur["start_idx"],
                        "entry_trigger": cur["entry_trigger"],
                        "s1_floor": cur["s1_floor"],
                        "s1_ceiling": ceiling_used,
                        "s1_ceiling_locked": True,
                    }
                    transition(2, t, trig_label)
                    cur["prior_s1_snapshot"] = s1_snapshot
                    cur["s2_entry_ref_ceiling"] = ceiling_used
                    # ── Stage 1 trendline seed ────────────────────────────
                    # Collect all confirmed pivot lows from the entire Stage 1
                    # period. These ascending base lows give Stage 2 a real
                    # trendline from day one rather than waiting for a second
                    # pivot low to form during Stage 2.
                    # Use s1_snapshot (captured before transition reset cur).
                    _s1_start_idx = s1_snapshot["start_idx"]
                    _s1_all_pls = [
                        p for p in pivot_lows
                        if _s1_start_idx <= p.week_index <= t
                    ]
                    _s1_period_pls = [p for p in _s1_all_pls if p.is_confirmed]
                    _s1_unconfirmed = [p for p in _s1_all_pls if not p.is_confirmed]
                    # One seed from before Stage 1 (the Stage 4/0 bottom).
                    _pre_s1 = [p for p in pivot_lows if p.week_index < _s1_start_idx]
                    _s1_seed_pl = _pre_s1[-1] if _pre_s1 else None
                    # Build ascending + colinear set.
                    _s1_seed_lows = (([_s1_seed_pl] if _s1_seed_pl else []) + _s1_period_pls)
                    _s1_seed_asc = _ascending_toward_newest(_s1_seed_lows)
                    _s1_seed_kept = _select_colinear_anchors(
                        _s1_seed_asc, config["trendline_tolerance_pct"]
                    )
                    _s1_seed_m, _s1_seed_b = _line_from_endpoints(_s1_seed_kept)
                    cur["s2_pivot_lows"] = list(_s1_seed_asc)
                    cur["s2_ascending_lows"] = list(_s1_seed_kept)
                    cur["s2_line_m"] = _s1_seed_m
                    cur["s2_line_b"] = _s1_seed_b
                    _s1_removed_asc = [p for p in _s1_seed_lows if p not in _s1_seed_asc]
                    _s1_removed_col = [p for p in _s1_seed_asc if p not in _s1_seed_kept]
                    _log(
                        f"[S1→2 SEED] {dates[t].date()} | "
                        f"s1_start={dates[_s1_start_idx].date()}  "
                        f"all_pls={[(p.date.date(), round(p.price,4), 'conf' if p.is_confirmed else 'UNCONF') for p in _s1_all_pls]}  "
                        f"anchors={[(p.date.date(), round(p.price,4)) for p in _s1_seed_kept]}  "
                        f"trendline={'m='+str(round(_s1_seed_m,6)) if _s1_seed_m else 'not yet (need ≥2 anchors)'}"
                    )
                    if _s1_unconfirmed:
                        _log(f"  → unconfirmed (too recent): {[p.date.date() for p in _s1_unconfirmed]}")
                    if _s1_removed_asc:
                        _log(f"  → removed by ascending filter: {[(p.date.date(), round(p.price,4)) for p in _s1_removed_asc]}")
                    if _s1_removed_col:
                        _log(f"  → removed by colinear filter: {[(p.date.date(), round(p.price,4)) for p in _s1_removed_col]}")
                    # ── END Stage 1 trendline seed ────────────────────────
                    cur["s2_max_high"] = float(highs_arr[t])
                    moved = True

        # ── State 2: maintain line + → 3 ────────────────────────────────
        if not moved and cur["state"] == 2:
            # Track max weekly High over the segment (used by Stage 3
            # retroactive-collapse logic to spot misclassified break-outs).
            hh = float(highs_arr[t])
            if cur["s2_max_high"] is None or hh > cur["s2_max_high"]:
                cur["s2_max_high"] = hh

            # Update Stage 2 confirmation status. Stage 2 is "confirmed" once
            # EITHER (a) max High has exceeded the original Stage 1 ceiling
            # by `s2_confirmation_pct`% OR (b) a confirmed pivot low has
            # formed above the original Stage 1 ceiling. Once confirmed, the
            # snapshot is cleared and the failed-breakout revert (applied at
            # S2→S3 transition time below) is no longer possible.
            snap1 = cur.get("prior_s1_snapshot")
            if snap1 is not None and snap1.get("s1_ceiling") is not None:
                conf_threshold = snap1["s1_ceiling"] * (1 + config["s2_confirmation_pct"] / 100.0)
                confirmed_by_magnitude = (cur.get("s2_max_high") is not None
                                          and cur["s2_max_high"] >= conf_threshold)
                confirmed_by_pivot = (pl is not None and pl.price > snap1["s1_ceiling"])
                if confirmed_by_magnitude or confirmed_by_pivot:
                    cur["prior_s1_snapshot"] = None
            # S3→2 confirmation: Stage 2 is confirmed once max High exceeds
            # the S3 reference ceiling by s2_confirmation_pct, OR a confirmed
            # pivot low forms above it. Until confirmed, failed-breakout revert
            # to Stage 3 is available.
            snap3 = cur.get("prior_s3_snapshot")
            if snap3 is not None:
                _ref_ceil3 = cur.get("s2_entry_ref_ceiling")
                if _ref_ceil3 is not None:
                    conf_threshold3 = _ref_ceil3 * (1 + config["s2_confirmation_pct"] / 100.0)
                    confirmed_by_magnitude3 = (cur.get("s2_max_high") is not None
                                               and cur["s2_max_high"] >= conf_threshold3)
                    confirmed_by_pivot3 = (pl is not None and pl.price > _ref_ceil3)
                    if confirmed_by_magnitude3 or confirmed_by_pivot3:
                        _conf_reason = (f"magnitude: max_high={round(cur['s2_max_high'], 4)}"
                                        if confirmed_by_magnitude3
                                        else f"pivot: pl={round(pl.price, 4)}")
                        _log(
                            f"[S2 CONFIRMED from S3] {dates[t].date()} | "
                            f"ref_ceil={_ref_ceil3:.4f}  {_conf_reason}"
                        )
                        cur["prior_s3_snapshot"] = None

            triggered = None

            # Trigger B (trendline break) — checked FIRST, against the line
            # built from PRIOR pivots only. A new pivot low this week must
            # earn its anchor status by closing above the existing line; if
            # the close breaks the line, the "higher low" is actually the
            # start of distribution, not a trend continuation.
            if cur["s2_line_m"] is not None:
                line_y = cur["s2_line_m"] * t + cur["s2_line_b"]
                if c < line_y:
                    triggered = "trendline_break"

            # Only if no break: incorporate this week's new pivot data.
            if triggered is None:
                if pl is not None:
                    prior_low = cur["s2_pivot_lows"][-1] if cur["s2_pivot_lows"] else None
                    if prior_low is not None and pl.price < prior_low.price:
                        # Lower pivot low — Stage 2 requires ascending lows.
                        # First pivot low (only seed present): check if it fell
                        # back into the prior stage's trading range (below the
                        # breakout reference ceiling) → failed breakout, revert.
                        # Any other lower low → new Stage 3.
                        is_first = len(cur["s2_pivot_lows"]) == 1  # only seed so far
                        ref_ceil = cur.get("s2_entry_ref_ceiling")
                        if is_first and ref_ceil is not None and pl.price < ref_ceil:
                            triggered = "failed_breakout_lower_low"
                        else:
                            triggered = "lower_pivot_low"
                        _log(
                            f"[S2 LOWER LOW] {dates[t].date()} | "
                            f"pivot_low={pl.price:.4f}  prior_low={prior_low.price:.4f}  "
                            f"is_first={is_first}  ref_ceil={f'{ref_ceil:.4f}' if ref_ceil else 'N/A'}  "
                            f"trigger={triggered}"
                        )
                    else:
                        # Ascending pivot low (or first pivot low with no prior to compare).
                        # All lows in s2_pivot_lows are guaranteed ascending — any lower
                        # low triggers exit before being added — so _ascending_lows is
                        # redundant; pass directly to _select_colinear_anchors.
                        cur["s2_pivot_lows"].append(pl)
                        kept = _select_colinear_anchors(
                            cur["s2_pivot_lows"], config["trendline_tolerance_pct"],
                        )
                        m, b = _line_from_endpoints(kept)
                        cur["s2_line_m"] = m
                        cur["s2_line_b"] = b
                        cur["s2_ascending_lows"] = kept
                        # ── DEBUG S2 pivot lows + trendline ──────────────────
                        _log(f"[S2 TRENDLINE] {dates[t].date()} | new pivot low: {pl.price:.4f}")
                        _log(f"  all pivot lows : {[(p.date.date(), round(p.price,4)) for p in cur['s2_pivot_lows']]}")
                        _log(f"  anchor lows    : {[(p.date.date(), round(p.price,4)) for p in kept]}")
                        _log(f"  trendline      : m={m:.6f}  b={b:.6f}" if m is not None else "  trendline      : not yet set (need ≥2 anchors)")
                        # ── END DEBUG ─────────────────────────────────────────
                if ph is not None:
                    # Trigger A (failed_higher_high) is disabled per earlier
                    # decision; we just track the latest high for inspection.
                    cur["s2_last_high"] = ph

                # Trigger C (sma_flat_oscillation) removed — fired too eagerly
                # after retroactive collapses and on legitimate Stage 2
                # continuation bases. Trendline break (B) is sufficient.

            if triggered is not None:
                snap1_now = cur.get("prior_s1_snapshot")
                snap3_now = cur.get("prior_s3_snapshot")

                # ── Failed breakout → revert to Stage 1 ──────────────────
                # Stage 2 came from Stage 1 and never confirmed. Merge the
                # failed Stage 2 back into Stage 1 with a raised ceiling.
                if snap1_now is not None and snap1_now.get("s1_ceiling") is not None:
                    if (segments and segments[-1].state == 1
                            and segments[-1].start_date == dates[snap1_now["start_idx"]]):
                        segments.pop()
                    s2_start_idx = cur["start_idx"]
                    new_ceiling = snap1_now["s1_ceiling"]
                    if cur.get("s2_max_high") is not None and cur["s2_max_high"] > new_ceiling:
                        new_ceiling = cur["s2_max_high"]
                    _log(
                        f"[S2→S1 REVERT] {dates[t].date()} | trigger={triggered}  "
                        f"old_ceiling={snap1_now['s1_ceiling']:.4f}  "
                        f"new_ceiling={new_ceiling:.4f}  "
                        f"s1_started={dates[snap1_now['start_idx']].date()}"
                    )
                    cur = _new_seg_memory(1, snap1_now["start_idx"], snap1_now["entry_trigger"])
                    cur["s1_floor"] = snap1_now["s1_floor"]
                    cur["s1_ceiling"] = new_ceiling
                    cur["s1_ceiling_week"] = t  # staleness clock starts from now
                    cur["s1_ceiling_locked"] = False  # ceiling keeps tracking higher highs
                    for tt in range(s2_start_idx, t + 1):
                        state_series[tt] = 1
                    state_series[t] = 1
                    moved = True
                    continue  # skip Stage 3 logic for this week

                # ── Failed breakout → revert to Stage 3 ──────────────────
                # Stage 2 came from Stage 3 and never confirmed. Merge the
                # failed Stage 2 back into Stage 3 with a raised ceiling.
                elif snap3_now is not None:
                    if (segments and segments[-1].state == 3
                            and segments[-1].start_date == dates[snap3_now["start_idx"]]):
                        segments.pop()
                    s2_start_idx = cur["start_idx"]
                    new_s3_ceiling = snap3_now.get("s3_ceiling")
                    if cur.get("s2_max_high") is not None:
                        if new_s3_ceiling is None or cur["s2_max_high"] > new_s3_ceiling:
                            new_s3_ceiling = cur["s2_max_high"]
                    _old_ceil_str = (f"{snap3_now['s3_ceiling']:.4f}"
                                     if snap3_now.get("s3_ceiling") is not None else "None")
                    _log(
                        f"[S2→S3 REVERT] {dates[t].date()} | trigger={triggered}  "
                        f"old_s3_ceiling={_old_ceil_str}  "
                        f"new_s3_ceiling={f'{new_s3_ceiling:.4f}' if new_s3_ceiling is not None else 'N/A'}  "
                        f"s3_started={dates[snap3_now['start_idx']].date()}"
                    )
                    cur = _new_seg_memory(3, snap3_now["start_idx"], snap3_now["entry_trigger"])
                    cur["s3_floor"] = snap3_now.get("s3_floor")
                    cur["s3_floor_provisional"] = snap3_now.get("s3_floor_provisional", False)
                    cur["s3_ceiling"] = new_s3_ceiling
                    cur["s3_floor_set_idx"] = snap3_now.get("s3_floor_set_idx")
                    cur["pending_s3_floor"] = snap3_now.get("pending_s3_floor", False)
                    cur["s3_trigger_idx"] = snap3_now.get("s3_trigger_idx")
                    cur["prior_s2_snapshot"] = snap3_now.get("prior_s2_snapshot")
                    cur["s2_pivot_lows"] = list(snap3_now.get("s2_pivot_lows") or [])
                    cur["s2_ascending_lows"] = list(snap3_now.get("s2_ascending_lows") or [])
                    for tt in range(s2_start_idx, t + 1):
                        state_series[tt] = 3
                    state_series[t] = 3
                    moved = True
                    continue  # skip remaining Stage 3 logic for this week

                # ── Otherwise: legitimate Stage 2 → Stage 3 transition ───
                # ── DEBUG S2→S3 ──────────────────────────────────────────
                _line_y_dbg = (cur["s2_line_m"] * t + cur["s2_line_b"]) if cur["s2_line_m"] is not None else None
                _gap_str = f"  gap={((c - _line_y_dbg) / _line_y_dbg * 100):.2f}% below line" if _line_y_dbg is not None else ""
                _log(
                    f"[S2→S3] {dates[t].date()} | trigger={triggered}  "
                    f"close={c:.4f}  "
                    f"trendline_expected={f'{_line_y_dbg:.4f}' if _line_y_dbg is not None else 'N/A'}"
                    + _gap_str
                )
                # ── END DEBUG ─────────────────────────────────────────────
                # Snapshot Stage 2 state for the (separate) S3 retroactive-
                # collapse mechanism (see Stage 3 logic below).
                s2_snapshot = {
                    **cur,
                    "s2_pivot_lows": list(cur["s2_pivot_lows"]),
                    "s2_ascending_lows": list(cur.get("s2_ascending_lows") or []),
                }
                transition(3, t, triggered)
                cur["pending_s3_floor"] = True
                cur["s3_trigger_idx"] = t
                cur["prior_s2_snapshot"] = s2_snapshot
                # If the trigger week itself is a confirmed pivot low, its
                # low is the natural Stage 3 floor — no need to wait for a
                # later pivot. Below this floor → Stage 4.
                if pl is not None:
                    cur["s3_floor"] = pl.price
                    cur["s3_floor_set_idx"] = t
                    cur["pending_s3_floor"] = False
                    cur["s3_floor_provisional"] = False
                else:
                    # Provisional floor: last confirmed S2 pivot low. This
                    # ensures S3→4 has a floor reference even if Stage 3
                    # never produces a confirmed pivot low. Overwritten (and
                    # marked non-provisional) when the first real S3 pivot
                    # low arrives.
                    _last_s2_pl = (s2_snapshot["s2_pivot_lows"][-1]
                                   if s2_snapshot.get("s2_pivot_lows") else None)
                    if _last_s2_pl is not None:
                        cur["s3_floor"] = _last_s2_pl.price
                        cur["s3_floor_set_idx"] = None  # not a real pivot week
                        cur["s3_floor_provisional"] = True
                        _log(
                            f"[S3 FLOOR PROVISIONAL] {dates[t].date()} | "
                            f"floor={_last_s2_pl.price:.4f}  "
                            f"source=last_s2_pivot_low({_last_s2_pl.date.date()})"
                        )
                moved = True

        # ── State 3: lock box + → 2 (recovery) or → 4 (breakdown) ───────
        if not moved and cur["state"] == 3:
            # Retroactive collapse: if any weekly High in Stage 3 exceeds
            # the prior Stage 2 max-high, the trendline was drawn too
            # aggressively — the stock is still making higher highs and the
            # trend is intact. Erase this Stage 3 segment and resume Stage 2
            # with the misclassified weeks merged in.
            #
            # Retroactive collapse rule: fires only when BOTH the first
            # confirmed S3 PivotHigh AND the S3 floor pass their checks:
            #   • higher high: first S3 PivotHigh ≥ s2_max_high × (1 + min_pct)
            #   • higher low:  s3_floor is None (no lower low was ever made —
            #                  strongest possible case) OR s3_floor ≥ last
            #                  confirmed S2 PivotLow × (1 + min_pct)
            # Using the FIRST confirmed S3 pivot high (s3_ceiling is None)
            # keeps the check tight; later pivot highs are part of an
            # established S3.
            snap = cur.get("prior_s2_snapshot")
            _last_s2_pl = (snap["s2_pivot_lows"][-1].price
                           if snap and snap.get("s2_pivot_lows") else None)
            _cmin = 1.0 + config["collapse_min_pct"] / 100.0
            _higher_low_ok = (
                cur["s3_floor"] is None                                # no lower low at all
                or (_last_s2_pl is not None
                    and cur["s3_floor"] >= _last_s2_pl * _cmin)        # higher low ≥ 5%
            )
            _collapse = (
                snap is not None
                and snap.get("s2_max_high") is not None
                and ph is not None
                and cur["s3_ceiling"] is None                          # first confirmed S3 pivot high
                and ph.price >= snap["s2_max_high"] * _cmin            # higher high ≥ 5%
                and _higher_low_ok
            )
            if _collapse:
                # ── DEBUG retroactive collapse ────────────────────────────
                _hl_str = (
                    "s3_floor=None (no lower low)"
                    if cur["s3_floor"] is None
                    else (f"s3_floor={cur['s3_floor']:.4f}  last_s2_pl={_last_s2_pl:.4f}  "
                          f"hl_pct={((cur['s3_floor'] / _last_s2_pl - 1) * 100):.2f}%"
                          if _last_s2_pl is not None else f"s3_floor={cur['s3_floor']:.4f}  last_s2_pl=N/A")
                )
                _log(
                    f"[S3→S2 COLLAPSE] {dates[t].date()} | "
                    f"pivot_high={ph.price:.4f}  s2_max_high={snap['s2_max_high']:.4f}  "
                    f"hh_pct={((ph.price/snap['s2_max_high']-1)*100):.2f}%  "
                    f"{_hl_str}  "
                    f"min_pct={config['collapse_min_pct']}%  "
                    f"s3_started={dates[cur['start_idx']].date()}"
                )
                # ── END DEBUG ─────────────────────────────────────────────
                # Pop the just-closed Stage 2 segment from the timeline
                # (it ends one week before this Stage 3 started).
                if (segments
                        and segments[-1].state == 2
                        and segments[-1].start_date == dates[snap["start_idx"]]):
                    segments.pop()
                s3_start_idx = cur["start_idx"]
                # Restore Stage 2 working state.
                cur = snap
                cur["state"] = 2
                # Replay weeks that were misclassified as Stage 3 (s3_start_idx..t)
                # back through Stage 2 logic — pivots and max-high.
                for tt in range(s3_start_idx, t + 1):
                    h_tt = float(highs_arr[tt])
                    if cur["s2_max_high"] is None or h_tt > cur["s2_max_high"]:
                        cur["s2_max_high"] = h_tt
                    pl_tt = low_at.get(tt)
                    if pl_tt is not None:
                        cur["s2_pivot_lows"].append(pl_tt)
                    ph_tt = high_at.get(tt)
                    if ph_tt is not None:
                        cur["s2_last_high"] = ph_tt
                # Rebuild trendline from merged pivot history. Anchor at the
                # newest pivot low and extend backward through genuinely lower
                # lows — this preserves recent actual-support lows over older
                # temporary highs in the merged S2+S3 sequence.
                ascending = _ascending_toward_newest(cur["s2_pivot_lows"])
                kept = _select_colinear_anchors(
                    ascending, config["trendline_tolerance_pct"],
                )
                m, b = _line_from_endpoints(kept)
                cur["s2_line_m"] = m
                cur["s2_line_b"] = b
                cur["s2_ascending_lows"] = kept
                # Rewrite state_series for the misclassified span.
                for tt in range(s3_start_idx, t + 1):
                    state_series[tt] = 2
                cur["prior_s2_snapshot"] = None  # consumed
                state_series[t] = 2
                continue  # this week is now Stage 2; skip remaining S3 logic

            # Set Stage3_Floor on first pivot low after the trigger week.
            # Also fires when floor is provisional (from last S2 pivot low) —
            # replace it with the real confirmed S3 pivot low.
            if (pl is not None
                    and pl.week_index > (cur["s3_trigger_idx"] or -1)
                    and (cur["pending_s3_floor"] or cur.get("s3_floor_provisional"))):
                _was_provisional = cur.get("s3_floor_provisional", False)
                cur["s3_floor"] = pl.price
                cur["s3_floor_set_idx"] = t
                cur["pending_s3_floor"] = False
                cur["s3_floor_provisional"] = False
                if _was_provisional:
                    _log(
                        f"[S3 FLOOR CONFIRMED] {dates[t].date()} | "
                        f"floor={pl.price:.4f}  (was provisional)"
                    )
            # Stage 3 → 2 (recovery before floor breaks)
            # IMPORTANT: S3 ceiling is checked BEFORE updating it with this
            # week's new pivot high — same principle as S2 trendline check.
            # A close above the OLD ceiling on the same week a new pivot high
            # forms is a valid breakout against the prior resistance level.
            # The ceiling update happens AFTER this check (see below).
            # When no confirmed S3 pivot high exists yet, use the prior S2
            # max-high as the fallback breakout reference.
            _s3_ref_ceiling = cur["s3_ceiling"]
            if _s3_ref_ceiling is None:
                _snap = cur.get("prior_s2_snapshot")
                if _snap is not None:
                    _s3_ref_ceiling = _snap.get("s2_max_high")
            if _s3_ref_ceiling is not None and sma is not None:
                buf = config["breakout_buffer_pct"] / 100.0
                _ceil_ok  = c >= _s3_ref_ceiling * (1 + buf)
                _sma_ok   = c > sma
                _slope_ok = sc == "Rising"
                # Print any week where at least the ceiling is breached.
                if _ceil_ok:
                    _snap_dbg  = cur.get("prior_s2_snapshot")
                    _fl_dbg    = cur.get("s3_floor")
                    _lpl_dbg   = (_snap_dbg["s2_pivot_lows"][-1].price
                                  if _snap_dbg and _snap_dbg.get("s2_pivot_lows") else None)
                    _cmin_dbg  = 1.0 + config["collapse_min_pct"] / 100.0
                    _hh_dbg    = (_snap_dbg and _snap_dbg.get("s2_max_high") and c >= _snap_dbg["s2_max_high"] * _cmin_dbg)
                    _hl_dbg    = (_fl_dbg is None  # no lower low — passes
                                  or (_lpl_dbg is not None and _fl_dbg >= _lpl_dbg * _cmin_dbg))
                    _would_collapse = _hh_dbg and _hl_dbg
                    _outcome = ("COLLAPSE→unbroken S2" if _would_collapse
                                else f"new S2 [vol:{_vol_path}]" if (_ceil_ok and _sma_ok and _slope_ok and _vol_ok)
                                else "BLOCKED")
                    _vol_str = (f"vol={vol:.0f}  vavg={vavg:.0f}  "
                                f"spike_thresh={config['vol_mult']*vavg:.0f}  "
                                f"step_thresh={config['vol_step_mult']*vavg_step:.0f}"
                                if vavg is not None and vavg_step is not None and vol is not None else "vol=N/A")
                    _log(
                        f"[S3→2 CHECK] {dates[t].date()} | "
                        f"close={c:.4f}  ref_ceil={_s3_ref_ceiling:.4f}  ceil_ok={_ceil_ok}  "
                        f"sma_ok={_sma_ok}  slope={sc}({f'{spct:.2f}%' if spct is not None else 'N/A'})({_slope_ok})  "
                        f"{_vol_str}  vol_path={_vol_path}  vol_ok={_vol_ok}  "
                        f"higher_high={_hh_dbg}  higher_low={_hl_dbg}  "
                        f"→ {_outcome}"
                    )
                if _ceil_ok and _sma_ok and _slope_ok and _vol_ok:
                    # Check if this qualifies as a retroactive collapse:
                    # close > s2_max_high (higher high) AND s3_floor > last S2
                    # pivot low (higher low). If both → erase Stage 3, extend
                    # Stage 2 unbroken. If not → normal new Stage 2.
                    _snap = cur.get("prior_s2_snapshot")
                    _s3_floor = cur.get("s3_floor")
                    _last_s2_pl = (_snap["s2_pivot_lows"][-1].price
                                   if _snap and _snap.get("s2_pivot_lows") else None)
                    _cmin = 1.0 + config["collapse_min_pct"] / 100.0
                    _vol_higher_high = (_snap is not None
                                        and _snap.get("s2_max_high") is not None
                                        and c >= _snap["s2_max_high"] * _cmin)
                    # Higher low: passes if s3_floor is None (no lower low made)
                    # OR s3_floor ≥ last S2 pivot low × (1 + min_pct).
                    _vol_higher_low  = (
                        _s3_floor is None
                        or (_last_s2_pl is not None and _s3_floor >= _last_s2_pl * _cmin)
                    )
                    _confirm_path = _vol_path

                    if _vol_higher_high and _vol_higher_low:
                        # Retroactive collapse — same outcome as pivot-high-based
                        # collapse but triggered at the breakout week.
                        _hl_str_v = (
                            "s3_floor=None (no lower low)"
                            if _s3_floor is None
                            else (f"s3_floor={_s3_floor:.4f}  last_s2_pl={_last_s2_pl:.4f}"
                                  if _last_s2_pl is not None else f"s3_floor={_s3_floor:.4f}")
                        )
                        _log(
                            f"[S3→S2 COLLAPSE via VOL] {dates[t].date()} | "
                            f"close={c:.4f}  s2_max_high={_snap['s2_max_high']:.4f}  "
                            f"{_hl_str_v}  "
                            f"confirm_path={_confirm_path}  "
                            f"s3_started={dates[cur['start_idx']].date()}"
                        )
                        if (segments
                                and segments[-1].state == 2
                                and segments[-1].start_date == dates[_snap["start_idx"]]):
                            segments.pop()
                        s3_start_idx = cur["start_idx"]
                        cur = _snap
                        cur["state"] = 2
                        for tt in range(s3_start_idx, t + 1):
                            h_tt = float(highs_arr[tt])
                            if cur["s2_max_high"] is None or h_tt > cur["s2_max_high"]:
                                cur["s2_max_high"] = h_tt
                            pl_tt = low_at.get(tt)
                            if pl_tt is not None:
                                cur["s2_pivot_lows"].append(pl_tt)
                            ph_tt = high_at.get(tt)
                            if ph_tt is not None:
                                cur["s2_last_high"] = ph_tt
                        ascending = _ascending_toward_newest(cur["s2_pivot_lows"])
                        kept = _select_colinear_anchors(
                            ascending, config["trendline_tolerance_pct"],
                        )
                        m, b = _line_from_endpoints(kept)
                        cur["s2_line_m"] = m
                        cur["s2_line_b"] = b
                        cur["s2_ascending_lows"] = kept
                        for tt in range(s3_start_idx, t + 1):
                            state_series[tt] = 2
                        cur["prior_s2_snapshot"] = None
                        state_series[t] = 2
                        moved = True
                        continue
                    else:
                        _log(
                            f"[S3→2 BREAKOUT] {dates[t].date()} | normal new S2  "
                            f"confirm_path={_confirm_path}  "
                            f"higher_high={_vol_higher_high}  higher_low={_vol_higher_low}  "
                            f"s3_floor={f'{_s3_floor:.4f}' if _s3_floor else 'N/A'}  "
                            f"last_s2_pl={f'{_last_s2_pl:.4f}' if _last_s2_pl else 'N/A'}  "
                            f"s2_max_high={round(_snap['s2_max_high'], 4) if _snap and _snap.get('s2_max_high') else 'N/A'}"
                        )
                        # Save Stage 3 working state for possible failed-breakout
                        # revert. If this S3→2 breakout never confirms (first
                        # pullback low drops back below the S3 reference ceiling),
                        # we restore Stage 3 with the ceiling raised to the max
                        # High reached during the failed Stage 2.
                        s3_snapshot = {
                            "start_idx": cur["start_idx"],
                            "entry_trigger": cur["entry_trigger"],
                            "s3_floor": cur.get("s3_floor"),
                            "s3_ceiling": cur.get("s3_ceiling"),
                            "s3_floor_set_idx": cur.get("s3_floor_set_idx"),
                            "pending_s3_floor": cur.get("pending_s3_floor", False),
                            "s3_trigger_idx": cur.get("s3_trigger_idx"),
                            "prior_s2_snapshot": cur.get("prior_s2_snapshot"),
                            "s2_pivot_lows": list(cur.get("s2_pivot_lows") or []),
                            "s2_ascending_lows": list(cur.get("s2_ascending_lows") or []),
                        }
                        transition(2, t, "stage3_breakout_to_stage2")
                        cur["prior_s3_snapshot"] = s3_snapshot
                        cur["s2_entry_ref_ceiling"] = _s3_ref_ceiling
                        # ── Stage 3 trendline seed ────────────────────────────
                        # Collect all confirmed pivot lows from the entire Stage 3
                        # period. The ref_ceiling can change mid-Stage 3 (e.g. a
                        # new S3 pivot high raises it), so scanning backward with
                        # the current threshold misses early coiling. Using the
                        # full S3 window captures all ascending support pivots
                        # regardless of which ceiling level was active.
                        # Use the snapshot (captured before transition reset cur)
                        # to get the actual Stage 3 start index.
                        _s3_start_idx = (s3_snapshot.get("s3_trigger_idx")
                                         or s3_snapshot.get("start_idx")
                                         or t)
                        # All pivot lows in the Stage 3 window (confirmed + unconfirmed)
                        # — printed for diagnostics so we can see which ones exist but
                        # are too recent to be confirmed by the breakout week.
                        _s3_all_pls = [
                            p for p in pivot_lows
                            if _s3_start_idx <= p.week_index <= t
                        ]
                        _s3_period_pls = [p for p in _s3_all_pls if p.is_confirmed]
                        _s3_unconfirmed = [p for p in _s3_all_pls if not p.is_confirmed]
                        _log(
                            f"[S3→2 SEED DBG] {dates[t].date()} | "
                            f"s3_start={dates[_s3_start_idx].date()}  "
                            f"all_pls={[(p.date.date(), round(p.price,4), 'conf' if p.is_confirmed else 'UNCONF') for p in _s3_all_pls]}"
                        )
                        if _s3_unconfirmed:
                            _log(
                                f"  → {len(_s3_unconfirmed)} unconfirmed (too recent, within {config['pivot_window']}w of breakout): "
                                f"{[p.date.date() for p in _s3_unconfirmed]}"
                            )
                        # One seed anchor from just before Stage 3 began.
                        _pre_s3 = [p for p in pivot_lows if p.week_index < _s3_start_idx]
                        _seed_pl = _pre_s3[-1] if _pre_s3 else None
                        # Build ascending + colinear set as the initial S2 trendline.
                        _s2_seed_lows = (([_seed_pl] if _seed_pl else []) + _s3_period_pls)
                        _s2_seed_asc = _ascending_toward_newest(_s2_seed_lows)
                        _s2_seed_removed = [p for p in _s2_seed_lows if p not in _s2_seed_asc]
                        _s2_seed_kept = _select_colinear_anchors(
                            _s2_seed_asc, config["trendline_tolerance_pct"]
                        )
                        _s2_colinear_removed = [p for p in _s2_seed_asc if p not in _s2_seed_kept]
                        _seed_m, _seed_b = _line_from_endpoints(_s2_seed_kept)
                        if _s2_seed_removed:
                            _log(
                                f"  → removed by ascending filter (lower than prior): "
                                f"{[(p.date.date(), round(p.price,4)) for p in _s2_seed_removed]}"
                            )
                        if _s2_colinear_removed:
                            _log(
                                f"  → removed by colinear filter (broke from trendline): "
                                f"{[(p.date.date(), round(p.price,4)) for p in _s2_colinear_removed]}"
                            )
                        cur["s2_pivot_lows"] = list(_s2_seed_asc)
                        cur["s2_ascending_lows"] = list(_s2_seed_kept)
                        cur["s2_line_m"] = _seed_m
                        cur["s2_line_b"] = _seed_b
                        _log(
                            f"[S3→2 SEED] {dates[t].date()} | "
                            f"anchors={[(p.date.date(), round(p.price,4)) for p in _s2_seed_kept]}  "
                            f"trendline={'m='+str(round(_seed_m,6))+' b='+str(round(_seed_b,4)) if _seed_m else 'not yet (need ≥2 anchors)'}"
                        )
                        # ── END Stage 3 trendline seed ────────────────────────
                        cur["s2_max_high"] = float(highs_arr[t])
                        moved = True

            # Stage3_Ceiling update — runs AFTER S3→2 check so the close is
            # compared against the resistance that was in place at the start
            # of the week, not a level just set by this week's pivot high.
            if not moved and cur["state"] == 3:
                fi = cur.get("s3_floor_set_idx")
                if (fi is not None and ph is not None
                        and ph.week_index >= fi):
                    if cur["s3_ceiling"] is None or ph.price > cur["s3_ceiling"]:
                        _prev_ceil = cur["s3_ceiling"]
                        cur["s3_ceiling"] = ph.price
                        _log(
                            f"[S3 CEILING] {dates[t].date()} | pivot high={ph.price:.4f}  "
                            f"prev_ceiling={f'{_prev_ceil:.4f}' if _prev_ceil is not None else 'None'}  "
                            f"new_ceiling={cur['s3_ceiling']:.4f}"
                        )
                elif ph is not None and fi is None:
                    _log(f"[S3 CEILING] {dates[t].date()} | pivot high={ph.price:.4f}  skipped (floor not yet set)")

            # ── DEBUG S3 weekly ──────────────────────────────────────────
            if not moved:
                _floor      = cur.get("s3_floor")
                _prov       = cur.get("s3_floor_provisional", False)
                _floor_break = _floor is not None and c < _floor
                _sma_break  = sma is not None and c < sma and sc == "Declining"
                _blocked_by_rising = sc == "Rising" and (_floor_break or _sma_break)
                _floor_str  = (f"{_floor:.4f}{'(prov)' if _prov else ''}"
                               if _floor is not None else "None")
                _ceil_str   = (f"{cur['s3_ceiling']:.4f}"
                               if cur.get("s3_ceiling") is not None else "None")
                _sma_str    = f"{sma:.4f}" if sma is not None else "N/A"
                _spct_str   = f"{spct:.2f}%" if spct is not None else "N/A"
                _why = ("floor_break" if _floor_break
                        else "sma_break" if _sma_break
                        else "rising_sma_blocked" if _blocked_by_rising
                        else "holding")
                _log(
                    f"[S3 WEEKLY] {dates[t].date()} | "
                    f"close={c:.4f}  sma={_sma_str}  "
                    f"sc={sc}({_spct_str})  "
                    f"floor={_floor_str}  ceil={_ceil_str}  "
                    f"→ {_why}"
                )
            # ── END DEBUG ────────────────────────────────────────────────

            # Stage 3 → 4 (breakdown)
            # A rising SMA30 means the underlying trend is still healthy;
            # dips in that context are shakeouts, not breakdowns.
            if not moved and sc != "Rising":
                if cur["s3_floor"] is not None and c < cur["s3_floor"]:
                    _s3_snap = {**cur, "s2_pivot_lows": list(cur.get("s2_pivot_lows") or []), "s2_ascending_lows": list(cur.get("s2_ascending_lows") or [])}
                    transition(4, t, "breakdown_below_stage3_floor")
                    cur["prior_s3_snapshot"] = _s3_snap
                    moved = True
                elif sma is not None and c < sma and sc == "Declining":
                    _s3_snap = {**cur, "s2_pivot_lows": list(cur.get("s2_pivot_lows") or []), "s2_ascending_lows": list(cur.get("s2_ascending_lows") or [])}
                    transition(4, t, "breakdown_below_sma_declining")
                    cur["prior_s3_snapshot"] = _s3_snap
                    moved = True

        state_series[t] = cur["state"]

    # Close final segment
    close_segment(n - 1)

    state_ser = pd.Series(state_series, index=dates, name="state")
    return segments, state_ser


def _new_seg_memory(state: int, start_idx: int, trigger: str) -> dict:
    return {
        "state": state,
        "start_idx": start_idx,
        "entry_trigger": trigger,
        "s1_floor": None,
        "s1_ceiling": None,
        "s1_ceiling_week": None,     # week_index of the pivot high that last raised s1_ceiling
        "s1_ceiling_locked": False,
        "s1_entry_pivot_low": None,  # price of the pivot low that triggered S4→1
                                     # (used to detect a reverting lower low)
        "prior_s4_snapshot": None,   # snapshot of the Stage 4 for failed-S1 revert
        "prior_s1_ceiling": None,    # S1 ceiling saved during S1→S4 dead-cat revert;
                                     # restored as the starting ceiling on next S4→S1
        "prior_s1_ceiling_week": None,  # week_index matching prior_s1_ceiling
        "s2_pivot_lows": [],
        "s2_ascending_lows": [],
        "s2_last_high": None,
        "s2_line_m": None,
        "s2_line_b": None,
        "s2_max_high": None,            # max weekly High during this Stage 2
        "s2_entry_ref_ceiling": None,   # breakout reference ceiling when Stage 2 was entered
                                        # (S1 ceiling or prev S2 max-high for S1→2;
                                        #  S3 ceiling or prev S2 max-high for S3→2)
        "s3_floor": None,
        "s3_floor_provisional": False,  # True while floor comes from last S2 pivot low,
                                        # not yet from a real confirmed S3 pivot low
        "s3_ceiling": None,
        "s3_floor_set_idx": None,
        "pending_s3_floor": False,
        "s3_trigger_idx": None,
        "prior_s2_snapshot": None,      # set when entering Stage 3 from Stage 2
        "prior_s1_snapshot": None,      # set when entering Stage 2 from Stage 1
        "prior_s3_snapshot": None,      # set when entering Stage 4 from Stage 3
    }


# ── Relative Strength (auxiliary; not used by rules) ─────────────────────────

def _compute_relative_strength(
    weekly: pd.DataFrame,
    benchmark_weekly: pd.DataFrame,
    benchmark_symbol: str,
    slope_lookback: int,
) -> RelativeStrength:
    aligned = weekly[["Close"]].join(
        benchmark_weekly[["Close"]].rename(columns={"Close": "BenchClose"}),
        how="inner",
    )
    if len(aligned) == 0:
        empty = pd.Series(dtype=float)
        return RelativeStrength(
            benchmark_symbol=benchmark_symbol,
            rs_line=empty,
            rs_slope_5w=empty,
            rs_new_high=pd.Series(dtype=bool),
        )
    raw = aligned["Close"] / aligned["BenchClose"]
    rs_line = raw / raw.iloc[0] * 100.0
    rs_slope = (rs_line / rs_line.shift(slope_lookback) - 1.0) * 100.0
    rs_new_high = rs_line == rs_line.cummax()
    # Reindex back to the original weekly index so dates align with the rest.
    rs_line = rs_line.reindex(weekly.index)
    rs_slope = rs_slope.reindex(weekly.index)
    rs_new_high = rs_new_high.reindex(weekly.index).fillna(False).astype(bool)
    return RelativeStrength(
        benchmark_symbol=benchmark_symbol,
        rs_line=rs_line,
        rs_slope_5w=rs_slope,
        rs_new_high=rs_new_high,
    )


# ── Public entry point ──────────────────────────────────────────────────────

def build_etf_stage_cache(
    etf_weekly_dict: dict,
    **config_overrides,
) -> dict:
    """Pre-compute StageAnalysis for a set of ETFs.

    Build this once and reuse across all stocks — avoids re-running the state
    machine for the same ETF for every stock that belongs to that industry.

    Parameters
    ----------
    etf_weekly_dict : dict[str, pd.DataFrame]
        ``{"SMH": smh_weekly_df, "XLK": xlk_weekly_df, ...}``
        Each DataFrame must be weekly OHLCV with a DatetimeIndex.
    **config_overrides
        Forwarded to ``compute_stage_analysis`` for every ETF.

    Returns
    -------
    dict[str, StageAnalysis]
        Keyed by ETF symbol.  Safe to pickle / cache to disk.

    Example
    -------
        etf_cache = build_etf_stage_cache({"SMH": smh_df, "XLK": xlk_df})
        # save:  import pickle; pickle.dump(etf_cache, open("etf_cache.pkl","wb"))
        # load:  etf_cache = pickle.load(open("etf_cache.pkl","rb"))
    """
    cache = {}
    for symbol, weekly_df in etf_weekly_dict.items():
        cache[symbol] = compute_stage_analysis(weekly_df, **config_overrides)
    return cache


def enrich_with_etf_stages(
    stage_result: "StageAnalysis",
    industry_etf: Optional[str],
    sector_etf: Optional[str],
    etf_cache: dict,
) -> None:
    """Attach overlapping ETF stage segments to each stock segment in-place.

    For each segment in ``stage_result``, finds all ETF stage segments whose
    date range overlaps with the stock segment's [start_date, end_date].
    Multiple ETF segments may attach if the ETF changed stage mid-way through
    the stock's stage.

    Parameters
    ----------
    stage_result : StageAnalysis
        Output of ``compute_stage_analysis`` for the stock.
    industry_etf : str or None
        ETF symbol for the stock's industry (e.g. ``"SMH"`` for semiconductors).
        Looked up in ``etf_cache``.  Pass None to skip industry enrichment.
    sector_etf : str or None
        ETF symbol for the stock's broad sector (e.g. ``"XLK"`` for technology).
        Pass None to skip sector enrichment.
    etf_cache : dict[str, StageAnalysis]
        Built by ``build_etf_stage_cache``.

    Example
    -------
        from industry_mapping import INDUSTRY_MAP, SECTOR_MAP

        industry_etf = INDUSTRY_MAP.get(stock_info["industry"])
        sector_etf   = SECTOR_MAP.get(stock_info["sector"])

        enrich_with_etf_stages(result, industry_etf, sector_etf, etf_cache)

        # Each segment now has:
        #   seg.industry_etf       → "SMH"
        #   seg.industry_segments  → [StageSegment(state=1,...), StageSegment(state=2,...)]
        #   seg.sector_etf         → "XLK"
        #   seg.sector_segments    → [StageSegment(state=2,...)]
    """
    def _overlapping(etf_segs: list, start: pd.Timestamp, end: pd.Timestamp) -> list:
        """Return ETF segments whose date range overlaps [start, end]."""
        return [
            s for s in etf_segs
            if s.start_date <= end and s.end_date >= start
        ]

    ind_analysis = etf_cache.get(industry_etf) if industry_etf else None
    sec_analysis = etf_cache.get(sector_etf)   if sector_etf   else None

    for seg in stage_result.segments:
        seg.industry_etf = industry_etf
        seg.sector_etf   = sector_etf

        if ind_analysis is not None:
            seg.industry_segments = _overlapping(
                ind_analysis.segments, seg.start_date, seg.end_date
            )
        if sec_analysis is not None:
            seg.sector_segments = _overlapping(
                sec_analysis.segments, seg.start_date, seg.end_date
            )


def _enrich_segments(segments: list, weekly_idx: pd.DatetimeIndex) -> None:
    """Populate duration_weeks, stage_iteration, and prior_sX fields in-place.

    Runs in a single forward pass over the completed segments list.
    `weekly_idx` is the DatetimeIndex of the weekly DataFrame — used to count
    bars between dates rather than relying on calendar arithmetic.
    """
    def _count_bars(start: pd.Timestamp, end: pd.Timestamp) -> int:
        """Number of weekly bars whose date falls in [start, end]."""
        return int(((weekly_idx >= start) & (weekly_idx <= end)).sum())

    def _bars_between(date_a: pd.Timestamp, date_b: pd.Timestamp) -> int:
        """Weekly bars strictly between date_a and date_b (exclusive both ends)."""
        return int(((weekly_idx > date_a) & (weekly_idx < date_b)).sum())

    # iteration counters per stage
    iteration_count: dict[int, int] = {}
    # most recent completed segment per stage (1-4 only)
    last_seen: dict[int, StageSegment] = {}

    for seg in segments:
        # duration
        seg.duration_weeks = _count_bars(seg.start_date, seg.end_date)

        # iteration (only track 1-4; stage 0 is initialisation)
        if seg.state in (1, 2, 3, 4):
            iteration_count[seg.state] = iteration_count.get(seg.state, 0) + 1
            seg.stage_iteration = iteration_count[seg.state]

        # prior stage info — look up last_seen for each of 1-4
        for s in (1, 2, 3, 4):
            prior = last_seen.get(s)
            if prior is None:
                continue
            weeks_ago = _bars_between(prior.end_date, seg.start_date)
            info = PriorStageInfo(
                stage          = s,
                start_date     = prior.start_date,
                end_date       = prior.end_date,
                duration_weeks = prior.duration_weeks,
                weeks_ago      = weeks_ago,
            )
            if s == 1:   seg.prior_s1 = info
            elif s == 2: seg.prior_s2 = info
            elif s == 3: seg.prior_s3 = info
            elif s == 4: seg.prior_s4 = info

        # register this segment as the new last_seen for its stage
        if seg.state in (1, 2, 3, 4):
            last_seen[seg.state] = seg


def compute_stage_analysis(
    weekly: pd.DataFrame,
    symbol: Optional[str] = None,
    include_etf_context: bool = False,
    benchmark_weekly: Optional[pd.DataFrame] = None,
    benchmark_symbol: str = "SPX",
    etf_cache: Optional[dict] = None,
    **config_overrides,
) -> StageAnalysis:
    """Compute Weinstein-style stage analysis on weekly data.

    Parameters
    ----------
    weekly : pd.DataFrame
        Weekly OHLCV with DatetimeIndex. Must contain Open, High, Low, Close, Volume.
    symbol : str, optional
        The ticker symbol (e.g. ``"NVDA"``).  Required when
        ``include_etf_context=True`` so the function can look up the stock's
        industry and sector from yfinance and resolve the corresponding ETFs.
    include_etf_context : bool, default False
        When True the function automatically:

        1. Fetches ``yf.Ticker(symbol).info`` to get the yfinance ``industry``
           and ``sector`` strings.
        2. Resolves them to ETF symbols via ``INDUSTRY_MAP`` / ``SECTOR_MAP``
           in ``industry_mapping.py``.
        3. Downloads weekly data for those ETFs (same date range as ``weekly``)
           if not already present in ``etf_cache``.
        4. Runs ``compute_stage_analysis`` on each ETF.
        5. Attaches overlapping ETF segments to every stock segment via
           ``enrich_with_etf_stages``.

        For single-stock calls no other setup is needed.  For batch runs,
        pass a shared ``etf_cache`` dict so ETF data is downloaded and
        analysed only once per ETF.
    benchmark_weekly : pd.DataFrame, optional
        Weekly OHLCV for a benchmark (e.g. SPX). If supplied, an auxiliary
        `RelativeStrength` is computed and attached. NOT used by the state machine.
    benchmark_symbol : str
        Label for the benchmark in the RelativeStrength output.
    etf_cache : dict, optional
        Shared dict mapping ETF symbol → ``StageAnalysis``.  Pass the same
        dict across multiple ``compute_stage_analysis`` calls to avoid
        re-downloading / re-computing ETF analyses.  The dict is mutated
        in-place when new ETFs are fetched.  You can pre-populate it with
        ``build_etf_stage_cache`` for maximum control, or leave it empty
        (``{}``) and let the function fill it lazily.
    **config_overrides
        Override any key in DEFAULT_CONFIG.

    Returns
    -------
    StageAnalysis
        With `segments`, `state_series`, all derived metrics, and (optionally)
        `relative_strength` and ETF context populated.

    Examples
    --------
    Single stock — fully automatic::

        result = compute_stage_analysis(nvda_weekly, symbol="NVDA",
                                        include_etf_context=True)

    Batch — share one cache across many stocks::

        cache = {}
        for sym, df in weekly_frames.items():
            results[sym] = compute_stage_analysis(df, symbol=sym,
                                                  include_etf_context=True,
                                                  etf_cache=cache)
    """
    weekly = _normalize_weekly(weekly)
    if benchmark_weekly is not None:
        benchmark_weekly = _normalize_weekly(benchmark_weekly)

    config = {**DEFAULT_CONFIG, **config_overrides}

    sma30, slope30_pct, slope30_cat, vol_avg, vol_avg_step = _compute_weekly_metrics(weekly, config)
    pivot_lows, pivot_highs = _find_pivots_two_sided(weekly, config["pivot_window"])
    segments, state_series = _run_state_machine(
        weekly, sma30, slope30_cat, slope30_pct, vol_avg, vol_avg_step, pivot_lows, pivot_highs, config,
    )
    _enrich_segments(segments, weekly.index)

    rs = None
    if benchmark_weekly is not None:
        rs = _compute_relative_strength(
            weekly, benchmark_weekly, benchmark_symbol, config["slope_lookback"],
        )

    result = StageAnalysis(
        weekly=weekly,
        config=config,
        sma30=sma30,
        slope30_pct=slope30_pct,
        slope30_category=slope30_cat,
        vol_avg=vol_avg,
        vol_avg_step=vol_avg_step,
        pivot_lows=pivot_lows,
        pivot_highs=pivot_highs,
        segments=segments,
        state_series=state_series,
        relative_strength=rs,
    )

    # ── ETF context (industry + sector stages) ────────────────────────────────
    if include_etf_context:
        if not symbol:
            raise ValueError(
                "include_etf_context=True requires a ticker symbol. "
                "Pass symbol='NVDA' (or whichever ticker) so the function "
                "can look up the industry and sector from yfinance."
            )

        import yfinance as yf  # lazy import — not required for core analysis
        from industry_mapping import INDUSTRY_MAP, SECTOR_MAP

        info         = yf.Ticker(symbol).info
        yf_industry  = info.get("industry")
        yf_sector    = info.get("sector")
        industry_etf = INDUSTRY_MAP.get(yf_industry) if yf_industry else None
        sector_etf   = SECTOR_MAP.get(yf_sector)     if yf_sector   else None

        if industry_etf or sector_etf:
            # Use a shared cache or create a throw-away one for this call.
            _cache = etf_cache if etf_cache is not None else {}

            # Date range of the stock's weekly data (use full history).
            _start = weekly.index[0].strftime("%Y-%m-%d")
            _end   = weekly.index[-1].strftime("%Y-%m-%d")

            for etf_sym in filter(None, {industry_etf, sector_etf}):
                if etf_sym not in _cache:
                    _log(f"[ETF CONTEXT] downloading {etf_sym} weekly "
                          f"({_start} → {_end})…")
                    etf_df = yf.download(
                        etf_sym, start=_start, end=_end,
                        interval="1wk", progress=False,
                    )
                    if etf_df.empty:
                        _log(f"[ETF CONTEXT] ⚠ no data for {etf_sym}, skipping.")
                        continue
                    # Recursive call — ETF itself doesn't need ETF context.
                    _cache[etf_sym] = compute_stage_analysis(etf_df, **config_overrides)

            enrich_with_etf_stages(result, industry_etf, sector_etf, _cache)

    return result


# ── Range × Stage join ───────────────────────────────────────────────────────

@dataclass
class RangeWithStage:
    """A daily price range enriched with the Weinstein stage active at its end date."""

    # ── Range fields (mirrors base_identification.Range) ─────────────────────
    range_start_date: pd.Timestamp
    range_end_date: pd.Timestamp
    range_high: float
    range_low: float
    range_height_pct: float
    range_length_days: int
    price_mode: str
    expansion: Optional[object]          # RangeExpansion or None
    trade: Optional[object]              # RangeExpansionTrade or None
    priming_patterns: list
    above_50dma: bool
    above_21ema: bool
    slope_200dma: Optional[float]
    slope_50dma: Optional[float]
    slope_21ema: Optional[float]

    # ── Stage context at range end_date ──────────────────────────────────────
    stage_segment: Optional[StageSegment]  # None if no segment covers the date
    stock_stage_weeks_elapsed: Optional[int]  # weeks stock has been in that stage as of end_date

    # ── Industry / sector ETF stage at range end_date ────────────────────────
    industry_etf: Optional[str]                    # e.g. "SMH" for Semiconductors
    industry_stage_segment: Optional[StageSegment] # ETF's stage at range end_date
    industry_stage_weeks_elapsed: Optional[int]    # weeks ETF has been in that stage as of end_date
    sector_etf: Optional[str]                      # e.g. "XLK" for Technology
    sector_stage_segment: Optional[StageSegment]   # ETF's stage at range end_date
    sector_stage_weeks_elapsed: Optional[int]      # weeks ETF has been in that stage as of end_date

    # ── Momentum from expansion date ─────────────────────────────────────────
    momentum: Optional[object]  # MomentumResult or None (None if no up expansion)

    def to_dict(self) -> dict:
        d = {
            "range_start_date": self.range_start_date.strftime("%Y-%m-%d"),
            "range_end_date": self.range_end_date.strftime("%Y-%m-%d"),
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_height_pct": self.range_height_pct,
            "range_length_days": self.range_length_days,
            "price_mode": self.price_mode,
            "expansion": self.expansion.to_dict() if self.expansion is not None and hasattr(self.expansion, "to_dict") else self.expansion,
            "trade": self.trade.to_dict() if self.trade is not None and hasattr(self.trade, "to_dict") else self.trade,
            "priming_patterns": self.priming_patterns,
            "above_50dma": self.above_50dma,
            "above_21ema": self.above_21ema,
            "slope_200dma": self.slope_200dma,
            "slope_50dma": self.slope_50dma,
            "slope_21ema": self.slope_21ema,
            "stage_segment": self.stage_segment.to_dict() if self.stage_segment is not None else None,
            "stock_stage_weeks_elapsed": self.stock_stage_weeks_elapsed,
            "industry_etf": self.industry_etf,
            "industry_stage": self.industry_stage_segment.state if self.industry_stage_segment is not None else None,
            "industry_stage_weeks_elapsed": self.industry_stage_weeks_elapsed,
            "sector_etf": self.sector_etf,
            "sector_stage": self.sector_stage_segment.state if self.sector_stage_segment is not None else None,
            "sector_stage_weeks_elapsed": self.sector_stage_weeks_elapsed,
            "momentum": self.momentum.to_dict() if self.momentum is not None and hasattr(self.momentum, "to_dict") else self.momentum,
        }
        return d


def find_ranges_with_stage(
    daily: pd.DataFrame,
    stage_result: "StageAnalysis",
    *,
    box_pct: float = 3.0,
    min_days: int = 3,
    expansion_pct: float = 4.0,
    max_expansion_days: int = 1,
    close_threshold_pct: float = 10.0,
    price_mode: str = "high_low",
    trade_ma_type: str = "21ema",
    allow_rising_close_exception: bool = True,
    stop_type: str = "expansion_open",
    stop_buffer_pct: float = 0.02,
    stop_constant_pct: float = 0.03,
    max_loss_pct: float = 0.04,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
    momentum_drawdown_pct: float = 4.0,
    sell_strategy: str = "weakness",
) -> list:
    """Find daily price ranges and join each one with the Weinstein stage
    active at the range's end date, plus momentum from the expansion date.

    Parameters
    ----------
    daily : pd.DataFrame
        Daily OHLCV with DatetimeIndex.
    stage_result : StageAnalysis
        Output of ``compute_stage_analysis`` (already contains the weekly data
        and all stage segments).
    momentum_drawdown_pct : float
        Passed to MomentumMeasure.measure().  The run from the expansion date
        ends when the close drops this % from its running peak (also acts as
        the loss limit if the stock drops immediately).  Default 4.0%.
        Set to 0 to skip momentum computation.
    sell_strategy : str
        ``"weakness"`` (default) — uses the SellInWeakness / stop-loss trade
        already built by ``find_ranges`` (2 consecutive daily closes below MA).
        ``"stage3"`` — rebuilds the trade using
        ``build_range_expansion_trade_sell_on_stage3``: sell at the open of
        the first trading day the weekly stage transitions to Stage 3; stop
        loss still fires if it's hit first.
    All remaining kwargs are forwarded verbatim to ``find_ranges``.

    Returns
    -------
    list[RangeWithStage]
        One entry per range found, enriched with stage context and momentum.
        ``momentum`` is None for ranges with no up expansion.
    """
    from base_identification import (    # local import to avoid circular dep
        find_ranges,
        MomentumMeasure,
        build_range_expansion_trade_sell_on_stage3,
    )

    mm = MomentumMeasure(daily) if momentum_drawdown_pct > 0 else None

    ranges = find_ranges(
        daily,
        box_pct=box_pct,
        min_days=min_days,
        expansion_pct=expansion_pct,
        max_expansion_days=max_expansion_days,
        close_threshold_pct=close_threshold_pct,
        price_mode=price_mode,
        trade_ma_type=trade_ma_type,
        allow_rising_close_exception=allow_rising_close_exception,
        stop_type=stop_type,
        stop_buffer_pct=stop_buffer_pct,
        stop_constant_pct=stop_constant_pct,
        max_loss_pct=max_loss_pct,
        start_date=start_date,
        end_date=end_date,
    )

    def _etf_stage_at(seg: Optional[StageSegment], etf_list_attr: str,
                      date: pd.Timestamp) -> Optional[StageSegment]:
        """Find the ETF StageSegment that covers *date* from the pre-attached list."""
        if seg is None:
            return None
        etf_segs = getattr(seg, etf_list_attr, [])
        for s in etf_segs:
            if s.start_date <= date <= s.end_date:
                return s
        return None

    result = []
    for r in ranges:
        seg: Optional[StageSegment] = stage_result.state_at(r.end_date)

        # Point-in-time ETF stage — derived from overlapping lists already on seg
        ind_seg = _etf_stage_at(seg, "industry_segments", r.end_date)
        sec_seg = _etf_stage_at(seg, "sector_segments",   r.end_date)

        # Elapsed weeks = how long the ETF has been in that stage as of end_date
        def _weeks_elapsed(etf_seg, date):
            if etf_seg is None:
                return None
            return max(0, round((date - etf_seg.start_date).days / 7))

        stock_weeks = _weeks_elapsed(seg,     r.end_date)
        ind_weeks   = _weeks_elapsed(ind_seg, r.end_date)
        sec_weeks   = _weeks_elapsed(sec_seg, r.end_date)

        # Compute momentum from the expansion date (up expansions only)
        momentum = None
        if mm is not None and r.expansion is not None and r.expansion.direction == "up":
            momentum = mm.measure(r.expansion.date, drawdown_pct=momentum_drawdown_pct)

        # Resolve the trade object according to the chosen sell strategy.
        # sell_strategy="stage3": Stage 2 ranges exit on the S2→S3 transition;
        # all other stages (1, 3, 4) still use the SellInWeakness trade.
        if (sell_strategy == "stage3"
                and r.expansion is not None
                and r.expansion.direction == "up"
                and seg is not None and seg.state == 2):
            trade = build_range_expansion_trade_sell_on_stage3(
                expansion_date=r.expansion.date,
                daily=daily,
                stage_analysis=stage_result,
                stop_type=stop_type,
                stop_buffer_pct=stop_buffer_pct,
                stop_constant_pct=stop_constant_pct,
                max_loss_pct=max_loss_pct,
            )
        else:
            trade = r.trade   # SellInWeakness trade built by find_ranges

        result.append(RangeWithStage(
            range_start_date=r.start_date,
            range_end_date=r.end_date,
            range_high=r.high,
            range_low=r.low,
            range_height_pct=r.height_pct,
            range_length_days=r.length_days,
            price_mode=r.price_mode,
            expansion=r.expansion,
            trade=trade,
            priming_patterns=r.priming_patterns,
            above_50dma=r.above_50dma,
            above_21ema=r.above_21ema,
            slope_200dma=r.slope_200dma,
            slope_50dma=r.slope_50dma,
            slope_21ema=r.slope_21ema,
            stage_segment=seg,
            stock_stage_weeks_elapsed=stock_weeks,
            industry_etf=seg.industry_etf if seg is not None else None,
            industry_stage_segment=ind_seg,
            industry_stage_weeks_elapsed=ind_weeks,
            sector_etf=seg.sector_etf if seg is not None else None,
            sector_stage_segment=sec_seg,
            sector_stage_weeks_elapsed=sec_weeks,
            momentum=momentum,
        ))
    return result
