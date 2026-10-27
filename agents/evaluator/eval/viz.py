"""Trade visualizer — see what a trade did and where the exit sold it.

Everything is normalized to **% from entry** (entry = day 0 = 0%), so trades of
any price/era are directly comparable and overlayable. For each trade it shows
the forward price path, the **peak** (MFE = the best it offered), the **exit**
(where the sell actually fired, from whatever dataset the row came from), and the
swing-low **trailing stop** that produced it.

    from agents.evaluator.eval import viz
    import pandas as pd
    d = pd.read_parquet('agents/evaluator/runs/setups_all_wk1035_swing8.parquet')

    viz.plot_trade(d.iloc[100], out='trade.png')                 # ONE trade
    viz.plot_trades(d[d.peak_return_pct > 30], n=100, out='monsters.png')  # overlay

`plot_trade` takes a single ledger row (Series with ticker/entry_date/entry_price/
exit_date/return_pct/peak_return_pct/days_held/days_to_peak). `plot_trades` takes a
DataFrame of such rows. The swing-stop overlay mirrors ``SwingLowTrailExit``; pass
``swing_window`` to match the file's exit (default 8).
"""
from __future__ import annotations

import io

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import store


def _show(fig):
    """Render a figure inline in a notebook WITHOUT depending on the matplotlib
    backend (renders to PNG bytes and displays via IPython) — robust to
    `%matplotlib inline` not being active. Falls back to plt.show() elsewhere."""
    try:
        from IPython.display import Image, display
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        display(Image(data=buf.getvalue()))
    except Exception:
        plt.show()


def _bars_from_entry(ticker, entry_date, max_days):
    """Return (days, close%, high%, low%, ema10%, ema20%) from the entry bar,
    normalized to the entry OPEN (=0%)."""
    px = store.load(ticker)
    idx = {d.date() if hasattr(d, "date") else d: i for i, d in enumerate(px.index)}
    ed = entry_date.date() if hasattr(entry_date, "date") else entry_date
    i = idx.get(ed)
    if i is None:
        return None
    e = px["Open"].values[i]                # entry fill = open of entry bar
    j = min(len(px), i + max_days + 1)
    sl = slice(i, j)
    e10 = px["Close"].ewm(span=10, adjust=False).mean().values[sl]
    e20 = px["Close"].ewm(span=20, adjust=False).mean().values[sl]
    pct = lambda a: (a / e - 1) * 100
    return dict(days=np.arange(j - i), close=pct(px["Close"].values[sl]),
                high=pct(px["High"].values[sl]), low=pct(px["Low"].values[sl]),
                ema10=pct(e10), ema20=pct(e20),
                low_abs=px["Low"].values[sl], entry_px=e, i0=i, px=px)


def _swing_stop_pct(low_abs, entry_px, w=8, buf=0.01, max_loss=0.04):
    """Trailing-stop trajectory (% from entry) mirroring SwingLowTrailExit."""
    n = len(low_abs)
    stop = entry_px * (1 - max_loss)
    out = []
    for k in range(n):
        out.append((stop / entry_px - 1) * 100)
        j = k - w
        if j - w >= 0 and low_abs[j] == low_abs[j - w:j + w + 1].min():
            cand = low_abs[j] * (1 - buf)
            if cand > stop:
                stop = cand
    return np.array(out)


def plot_trade(trade, *, swing_window=8, max_days=None, out=None, ax=None):
    """Plot ONE trade normalized to % from entry: path, peak, exit, trailing stop."""
    dh = int(trade["days_held"])
    md = max_days or dh + 10
    b = _bars_from_entry(trade["ticker"], pd.Timestamp(trade["entry_date"]), md)
    if b is None:
        print("entry bar not found"); return
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(11, 6))
    ax.axhline(0, color="#888", lw=0.8)
    ax.axhline(-4, color="#d55", lw=0.6, ls=":", label="-4% initial stop")
    ax.plot(b["days"], b["close"], color="#1f77b4", lw=1.6, label="close")
    ax.plot(b["days"], b["ema10"], color="#ff9900", lw=0.8, alpha=.6, label="10-EMA")
    ax.plot(b["days"], b["ema20"], color="#9467bd", lw=0.8, alpha=.6, label="20-EMA")
    stop = _swing_stop_pct(b["low_abs"], b["entry_px"], w=swing_window)
    ax.plot(b["days"][:dh + 1], stop[:dh + 1], color="#2ca02c", lw=1.0, ls="--",
            label=f"swing{swing_window} stop")
    d2p, pk = int(trade["days_to_peak"]), trade["peak_return_pct"]
    ax.scatter([d2p], [pk], color="#2ca02c", marker="*", s=200, zorder=5,
               label=f"peak +{pk:.0f}%")
    ax.scatter([dh], [trade["return_pct"]], color="#d62728", marker="X", s=120, zorder=5,
               label=f"EXIT {trade['return_pct']:+.0f}% (d{dh})")
    ax.scatter([0], [0], color="k", marker="o", s=40, zorder=5)
    ax.set_title(f"{trade['ticker']}  entry {pd.Timestamp(trade['entry_date']).date()}  "
                 f"| peak +{pk:.0f}%  exit {trade['return_pct']:+.0f}%  "
                 f"gaveback {pk-trade['return_pct']:.0f}%")
    ax.set_xlabel("trading days from entry"); ax.set_ylabel("% from entry")
    ax.legend(fontsize=8, loc="best"); ax.grid(alpha=.2)
    if own:
        fig.tight_layout()
        if out:
            fig.savefig(out, dpi=110); plt.close(fig); print(f"saved {out}")
        else:
            _show(fig)


_REGIME_COLORS = {"uptrend": "#2ca02c", "basing": "#bbbbbb", "downtrend": "#d62728"}


def _weekly_regime(px, *, ext_ref_ema=10, slope_window=3, pivot_window=5):
    """Per-DAY weekly_trend_state (recomputed via WeeklyKellContext, matching the
    wk1035 config) + the weekly ref-EMA forward-filled onto the daily index."""
    from ..engine.records import Bar
    from ..strategy.weekly_kell import WeeklyKellContext
    wk = WeeklyKellContext(ema_periods=(10, 20), ext_ref_ema=ext_ref_ema,
                           trend_slope_window=slope_window, trend_pivot_window=pivot_window)
    states = []
    for ts, row in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        bar = Bar(date=d, open=row["Open"], high=row["High"], low=row["Low"],
                  close=row["Close"], volume=row["Volume"])
        wk.update(bar)
        states.append(wk.features(bar)["weekly_trend_state"])
    wema = px["Close"].resample("W").last().ewm(span=ext_ref_ema, adjust=False).mean() \
        .reindex(px.index, method="ffill")
    return pd.Series(states, index=px.index), wema


def _stage2(px, *, w=10, near=0.15, lookback=756):
    """Per-bar Stage-2 flag (the rejected-ceiling rule). Tradeable unless price is
    under a governing high that has already REJECTED a rally — a swing high that
    got near that high (within `near`) and was then FOLLOWED BY a daily downtrend
    (a real reversal, via the Kell daily `trend_state`), not just a pause. Making
    a new high, or pulling back from a high that has not yet failed, = tradeable."""
    from ..engine.records import Bar
    from ..strategy.kell import KellCycle
    hi = px["High"].values; c = px["Close"].values; n = len(px)
    kc = KellCycle(); dn = []
    for ts, row in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        bar = Bar(date=d, open=row["Open"], high=row["High"], low=row["Low"],
                  close=row["Close"], volume=row["Volume"])
        kc.update(bar)
        dn.append(1 if kc.features(bar)["trend_state"] == "downtrend" else 0)
    dn_cum = np.concatenate([[0], np.cumsum(dn)])
    piv = [(j + w, j, hi[j]) for j in range(w, n - w) if hi[j] == hi[j - w:j + w + 1].max()]
    scb = np.array([p[0] for p in piv]); spb = np.array([p[1] for p in piv]); spr = np.array([p[2] for p in piv])
    st2 = np.zeros(n, bool)
    for i in range(n):
        lo = max(0, i - lookback); H = hi[lo:i + 1].max(); h = lo + int(np.argmax(hi[lo:i + 1]))
        if c[i] >= H * 0.995:
            st2[i] = True; continue
        if len(spb):
            m = (scb < i) & (spb > h) & (spr >= H * (1 - near)) & (spr < H * 0.999)
            st2[i] = sum(1 for j in np.where(m)[0] if dn_cum[i] - dn_cum[spb[j]] > 0) == 0
        else:
            st2[i] = True
    return pd.Series(st2, index=px.index)


def _stuck_below_resistance(px, *, pivot_window=10, min_rejections=2, band_pct=12.0,
                            clear_buffer_pct=3.0, recency_window_days=None,
                            max_dist_pct=None):
    """Per-bar Stage-1 'stuck below a meaningful resistance' flag (dead-zone).
    See ``strategy/resistance.py`` — swing-high driven (no trend state): a zone
    needs >= ``min_rejections`` retest swing highs (within ``band_pct``) to be
    significant; stuck = one sits overhead and price hasn't closed above it (by
    more than ``clear_buffer_pct``)."""
    from ..engine.records import Bar
    from ..strategy.resistance import MeaningfulResistance
    mr = MeaningfulResistance(pivot_window=pivot_window, band_pct=band_pct,
                              min_rejections=min_rejections, clear_buffer_pct=clear_buffer_pct,
                              recency_window_days=recency_window_days, max_dist_pct=max_dist_pct)
    out = []
    for ts, row in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        bar = Bar(date=d, open=row["Open"], high=row["High"], low=row["Low"],
                  close=row["Close"], volume=row["Volume"])
        mr.update(bar)
        out.append(bool(mr.features(bar)["stuck_below_resistance"]))
    return pd.Series(out, index=px.index)


def _resistance_zones(px, *, pivot_window=10, band_pct=12.0, min_rejections=2,
                      clear_buffer_pct=3.0, recency_window_days=None, max_dist_pct=None):
    """Run the detector over ``px`` and return each MEANINGFUL zone (>= min_rejections
    retests) as (price, born_date, end_date) — end = the bar it was broken, or the
    last bar if never cleared. For drawing a level line from where it was set until
    it's broken."""
    from ..engine.records import Bar
    from ..strategy.resistance import MeaningfulResistance
    mr = MeaningfulResistance(pivot_window=pivot_window, band_pct=band_pct,
                              min_rejections=min_rejections, clear_buffer_pct=clear_buffer_pct,
                              recency_window_days=recency_window_days, max_dist_pct=max_dist_pct)
    for ts, row in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        mr.update(Bar(date=d, open=row["Open"], high=row["High"], low=row["Low"],
                      close=row["Close"], volume=row["Volume"]))
    n = len(px)
    zones = []
    for lv in mr._levels:
        if len(lv.reject_idxs) < min_rejections:      # only meaningful (significant) zones
            continue
        end = lv.cleared_idx if lv.cleared_idx is not None else n - 1
        zones.append((lv.price, px.index[lv.born_idx], px.index[end], len(lv.reject_idxs)))
    return zones


def _exh_stages(px):
    """Kell exhaustion-EXTENSION markers, placed at the PEAK of each extension (the
    1st/2nd/3rd 'stage' of the advance). `exh_since_downtrend` names the stage;
    `exhaustion_high_*` rises to the episode's top, so for each (leg, stage) we keep
    the HIGHEST exhaustion high seen — i.e. the peak, not the first +10% bar. A
    "leg" is a run between daily-downtrend resets. Returns [(peak_date, peak_price,
    stage#)]."""
    from ..engine.records import Bar
    from ..strategy.kell import KellCycle
    kc = KellCycle(); prev = 0; leg = 0; peaks = {}
    for ts, row in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        bar = Bar(date=d, open=row["Open"], high=row["High"], low=row["Low"],
                  close=row["Close"], volume=row["Volume"])
        kc.update(bar); f = kc.features(bar)
        cnt = f["exh_since_downtrend"]
        if cnt is None:
            continue
        if cnt < prev:                      # reset (a daily downtrend) → new leg
            leg += 1
        prev = cnt
        ehp, ehd = f["exhaustion_high_price"], f["exhaustion_high_date"]
        if cnt >= 1 and ehp and ehd:
            key = (leg, cnt)
            if key not in peaks or ehp > peaks[key][1]:   # keep the episode's top
                peaks[key] = (pd.Timestamp(ehd), ehp)
    return [(dt, pr, stage) for (lg, stage), (dt, pr) in peaks.items()]


def plot_ticker(ticker, trades, *, log=True, emas=(10, 20), start=None, end=None,
                regime=True, stage2=True, stages=True, volume=True, out=None,
                resistance_lines=True, exclude_stuck=False, label_trades=False,
                pivot_window=10, band_pct=12.0, min_rejections=2, clear_buffer_pct=3.0,
                recency_window_days=None, max_dist_pct=None):
    """Overlay EVERY trade for one ticker on its ACTUAL price chart. Entry =
    up-triangle, exit = down-triangle, green (win) / red (loss), + a **volume**
    panel below. With ``stage2=True`` (default), the background is shaded by the
    **Stage-2 gate × weekly trend** intersection:
      • **green**        = Stage-2 AND weekly uptrend  (strongest — trade)
      • **light green**  = Stage-2 AND weekly base      (pullback within the gate)
      • **light red**    = Stage-2 AND weekly downtrend  (allowed, but weekly rolling over)
      • **light yellow** = weekly uptrend but NOT Stage-2 (under a ceiling)
      • white            = everything else (skip)
    Set ``stage2=False`` for the plain weekly-regime shading instead. `trades` =
    any ledger frame, filtered to `ticker` internally."""
    px_full = store.load(ticker)
    stuckf = None
    if stage2 or exclude_stuck:                   # per-bar stuck flag (shading + filter)
        # s2f = _stage2(px_full)                  # [temporarily disabled — old Stage-2 gate]
        stuckf = _stuck_below_resistance(
            px_full, pivot_window=pivot_window, band_pct=band_pct,
            min_rejections=min_rejections, clear_buffer_pct=clear_buffer_pct,
            recency_window_days=recency_window_days, max_dist_pct=max_dist_pct)
    if stage2:                                    # compute on FULL history, then window
        res_zones = _resistance_zones(
            px_full, pivot_window=pivot_window, band_pct=band_pct,
            min_rejections=min_rejections, clear_buffer_pct=clear_buffer_pct,
            recency_window_days=recency_window_days, max_dist_pct=max_dist_pct) if resistance_lines else []
        stf, wemaf = _weekly_regime(px_full)
    exh_marks = _exh_stages(px_full) if stages else []
    px = px_full
    if start:
        px = px[px.index >= pd.Timestamp(start)]
    if end:
        px = px[px.index <= pd.Timestamp(end)]

    if volume:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(16, 9.5), sharex=True, layout="constrained",
                                      gridspec_kw=dict(height_ratios=[3.2, 1], hspace=.13))
    else:
        fig, ax = plt.subplots(figsize=(16, 8), layout="constrained"); axv = None

    ylo, yhi = px["Low"].min() * 0.85, px["High"].max() * 1.15
    if stage2:                                    # weekly-trend background × stuck-below-resistance
        stuck = stuckf.reindex(px.index).fillna(False).values.astype(bool)
        st = stf.reindex(px.index); wema = wemaf.reindex(px.index)
        up = (st == "uptrend").values; base = (st == "basing").values
        # RED (stuck below meaningful resistance) takes precedence over the weekly
        # trend colors: green = weekly uptrend, yellow = weekly base (both only
        # where NOT stuck); weekly downtrend = unshaded (white).
        ax.fill_between(px.index, ylo, yhi, where=up & ~stuck,   color="#2ca02c", alpha=.18, lw=0, zorder=0)  # green  = wk uptrend
        ax.fill_between(px.index, ylo, yhi, where=base & ~stuck, color="#e6c200", alpha=.16, lw=0, zorder=0)  # yellow = wk base
        ax.fill_between(px.index, ylo, yhi, where=stuck,         color="#d62728", alpha=.20, lw=0, zorder=0)  # red    = stuck below resistance
        ax.plot(px.index, wema, color="#1f77b4", lw=1.2, alpha=.7, label="weekly 10-EMA")
        # --- old Stage-2 gate shading (temporarily disabled; restore from git) ---
        # s2 = s2f.reindex(px.index).fillna(False).values.astype(bool); dn = (st == "downtrend").values
        # ax.fill_between(px.index, ylo, yhi, where=s2 & up, color="#2ca02c", alpha=.20, lw=0, zorder=0)  # green
    elif regime:                                  # shade contiguous weekly-state spans
        st, wema = _weekly_regime(px)
        blocks = (st != st.shift()).cumsum()
        for _, seg in px.groupby(blocks):
            s = st.loc[seg.index[0]]
            if s in _REGIME_COLORS:
                ax.axvspan(seg.index[0], seg.index[-1], color=_REGIME_COLORS[s], alpha=.09, zorder=0)
        ax.plot(px.index, wema, color="#1f77b4", lw=1.3, alpha=.8, label="weekly 10-EMA")

    ax.plot(px.index, px["Close"], color="#222", lw=0.9, label="close", zorder=2)
    for p in emas:
        ax.plot(px.index, px["Close"].ewm(span=p, adjust=False).mean(),
                lw=0.6, alpha=.4, label=f"{p}-EMA", zorder=1)
    # meaningful-resistance level lines: horizontal from where the level was set
    # (its swing high) until the bar it's broken (cleared), clipped to the window.
    if resistance_lines and res_zones:
        x0v, x1v = px.index[0], px.index[-1]
        drew = False
        for zprice, zborn, zend, zrej in res_zones:
            a, b = max(zborn, x0v), min(zend, x1v)
            if a > b:                       # lifespan falls outside the plotted window
                continue
            ax.hlines(zprice, a, b, color="#d62728", lw=1.0, ls="--", alpha=.55, zorder=3,
                      label=("meaningful resistance" if not drew else None))
            drew = True

    t = trades[trades["ticker"] == ticker].copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"]); t["exit_date"] = pd.to_datetime(t["exit_date"])
    if start:
        t = t[t.entry_date >= pd.Timestamp(start)]
    if end:
        t = t[t.entry_date <= pd.Timestamp(end)]
    t_all = t
    dropped = t.iloc[:0]
    if exclude_stuck and len(t) and stuckf is not None:
        # a trade is "in the dead zone" if its SIGNAL bar (bar before entry) is stuck
        _pos = {d.date(): i for i, d in enumerate(px_full.index)}
        _sv = stuckf.values
        def _sig_stuck(ed):
            i = _pos.get(ed.date())
            return bool(_sv[i - 1]) if (i is not None and i > 0) else False
        _m = t.entry_date.map(_sig_stuck)
        dropped, t = t[_m], t[~_m]
    t = t.sort_values("entry_date").reset_index(drop=True)
    for i, (_, r) in enumerate(t.iterrows(), 1):
        col = "#2ca02c" if r["return_pct"] > 0 else "#d62728"
        ax.plot([r["entry_date"], r["exit_date"]], [r["entry_price"], r["exit_price"]],
                color=col, lw=1.0, alpha=.5, zorder=3)
        ax.scatter([r["entry_date"]], [r["entry_price"]], marker="^", color=col,
                   edgecolor="k", lw=.3, s=48, zorder=5)
        ax.scatter([r["exit_date"]], [r["exit_price"]], marker="v", color=col, s=22, alpha=.6, zorder=4)
        if label_trades:                      # month/day at the entry (year is clear from the x-axis)
            ax.annotate(pd.Timestamp(r["entry_date"]).strftime("%m/%d"),
                        (r["entry_date"], r["entry_price"]), fontsize=5.5, color="#333",
                        ha="center", va="bottom", xytext=(0, 4), textcoords="offset points",
                        zorder=6, alpha=.9, rotation=45)
    if label_trades and len(t):
        print(f"  {ticker} trades:")
        for i, (_, r) in enumerate(t.iterrows(), 1):
            dh = f" {int(r['days_held']):3}d" if "days_held" in t.columns else ""
            pk = f" peak={r['peak_return_pct']:+.0f}%" if "peak_return_pct" in t.columns else ""
            print(f"   {i:3} {pd.Timestamp(r['entry_date']).date()} -> {pd.Timestamp(r['exit_date']).date()}"
                  f"{dh}  ret={r['return_pct']:+.0f}%{pk}")
    # exhaustion-stage markers (Kell 1st/2nd/3rd extension of the current up-leg)
    if stages:
        for dt, pr, sc in exh_marks:
            if pr is None or dt < px.index[0] or dt > px.index[-1]:
                continue
            ax.annotate(str(sc), (dt, pr), fontsize=7.5, fontweight="bold", color="#7a1fa2",
                        ha="center", va="bottom", zorder=6,
                        bbox=dict(boxstyle="circle,pad=0.15", fc="#f3e5f5", ec="#7a1fa2", lw=.6))
    if log:
        import matplotlib.ticker as mtick
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(mtick.LogLocator(base=10, subs=(1, 2, 5)))
        ax.yaxis.set_minor_locator(mtick.LogLocator(base=10, subs=(3, 4, 6, 7, 8, 9)))
        ax.yaxis.set_major_formatter(mtick.ScalarFormatter())
        ax.yaxis.set_minor_formatter(mtick.NullFormatter())
    ax.tick_params(axis="y", which="both", labelleft=True, labelright=True, left=True, right=True)
    def _st(s):                                   # compact stats block for a trade set
        if not len(s):
            return "n=0"
        win8 = f" win8={100*(s.return_pct>8).mean():.0f}%"   # realized return > 8%
        pk8 = f" pk8={100*(s.peak_return_pct>8).mean():.0f}%" if "peak_return_pct" in s.columns else ""
        pk30 = f" pk30={100*(s.peak_return_pct>30).mean():.0f}%" if "peak_return_pct" in s.columns else ""
        return f"n={len(s)} win={100*(s.return_pct>0).mean():.0f}%{win8}{pk8}{pk30} mean={s.return_pct.mean():+.1f}%"
    if exclude_stuck:
        print(f"{ticker}: ALL [{_st(t_all)}] | KEEP [{_st(t)}] | DROP-deadzone [{_st(dropped)}]")
        head = f"{ticker} — KEEP {_st(t)}   (excl dead zone: dropped {len(dropped)})"
    else:
        print(f"{ticker}: {_st(t)}")
        head = f"{ticker} — {_st(t)}"
    bg = ("   | bg: green=wk-uptrend  yellow=wk-base  red=stuck-below-resistance (red wins)"
          if stage2 else "   | bg: green=wk-uptrend grey=base red=downtrend" if regime else "")
    ax.set_title(head + "   ▲entry ▼exit" + bg, fontsize=10)
    ax.set_ylabel("price (log)" if log else "price"); ax.legend(fontsize=8, ncol=2, loc="upper left")
    ax.grid(alpha=.15, which="both")

    if volume:
        up = px["Close"].values >= px["Open"].values
        axv.bar(px.index[up], px["Volume"].values[up], color="#2ca02c", alpha=.5, width=2)
        axv.bar(px.index[~up], px["Volume"].values[~up], color="#d62728", alpha=.5, width=2)
        axv.plot(px.index, px["Volume"].rolling(20).mean(), color="#333", lw=0.8, label="20d avg vol")
        axv.set_ylabel("volume"); axv.legend(fontsize=8, loc="upper left"); axv.grid(alpha=.15)
        ax.tick_params(axis="x", which="both", labelbottom=True, labelsize=8)   # dates below the price panel too
    (axv or ax).set_xlabel("date")
    if out:
        fig.savefig(out, dpi=110); plt.close(fig); print(f"saved {out}  ({len(t)} trades on {ticker})")
    else:
        _show(fig)


def plot_trades(trades, *, n=100, max_days=80, out=None, color_by="outcome"):
    """Overlay up to `n` trades, each normalized to % from entry (entry=day0=0%).
    Lines run entry→exit; a dot marks each exit. Colored by outcome (green=win,
    red=loss) or 'peak' bucket. Shows the median path + summary."""
    t = trades.head(n) if len(trades) > n else trades
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.axhline(0, color="#888", lw=0.8); ax.axhline(-4, color="#d55", lw=0.6, ls=":")
    paths = []
    for _, r in t.iterrows():
        dh = int(r["days_held"])
        b = _bars_from_entry(r["ticker"], pd.Timestamp(r["entry_date"]), min(dh, max_days))
        if b is None:
            continue
        k = min(dh, max_days)
        col = ("#2ca02c" if r["return_pct"] > 0 else "#d62728") if color_by == "outcome" \
            else plt.cm.viridis(min(r["peak_return_pct"], 100) / 100)
        ax.plot(b["days"][:k + 1], b["close"][:k + 1], color=col, lw=0.6, alpha=.35)
        ax.scatter([k], [r["return_pct"]], color=col, s=8, alpha=.6, zorder=4)
        paths.append(pd.Series(b["close"][:k + 1], index=b["days"][:k + 1]))
    if paths:
        med = pd.concat(paths, axis=1).median(axis=1)
        ax.plot(med.index, med.values, color="k", lw=2.2, label="median path")
    w = 100 * (t.return_pct > 0).mean()
    ax.set_title(f"{len(t)} trades (normalized)  | win {w:.0f}%  mean {t.return_pct.mean():+.1f}%  "
                 f"mean peak {t.peak_return_pct.mean():+.1f}%  green=win red=loss")
    ax.set_xlabel("trading days from entry"); ax.set_ylabel("% from entry")
    ax.set_ylim(-15, np.nanpercentile(t.peak_return_pct, 95) + 10)
    ax.legend(fontsize=9); ax.grid(alpha=.2)
    fig.tight_layout()
    if out:
        fig.savefig(out, dpi=110); plt.close(fig); print(f"saved {out}")
    else:
        _show(fig)


def _candles(ax, px, *, body_w=0.6):
    """Hollow candlesticks (white=up, black=down, black outline — legible over
    any background shading) on ``ax`` for the OHLC frame ``px``."""
    import matplotlib.dates as mdates
    xs = mdates.date2num(px.index.to_pydatetime())
    for x, (_, row) in zip(xs, px.iterrows()):
        up = row["Close"] >= row["Open"]
        ax.vlines(x, row["Low"], row["High"], color="#1f2320", lw=1, zorder=2)
        y0, h = min(row["Open"], row["Close"]), abs(row["Close"] - row["Open"])
        ax.add_patch(plt.Rectangle((x - body_w / 2, y0), body_w, max(h, 0.05),
                                   facecolor=("white" if up else "#1f2320"),
                                   edgecolor="#1f2320", lw=0.8, zorder=2))
    ax.xaxis_date()


def plot_vcp_legs(ticker, *, start=None, end=None, pivot_window=5, lookback=120,
                  max_legs=4, min_contracting_run=2, show_tight_short=True,
                  tight_threshold=80.0, min_tight_days=3, show_vars=True,
                  vars_months=6, out=None):
    """VCP footprint, drawn on the actual candlestick chart. SCANS EVERY DAY in
    [start, end] and draws EVERY DISTINCT qualifying episode found (not just
    whichever one happens to be live on the last day) — a wide range like a full
    year will typically show several separate basing episodes, each its own red
    line + dots, each H->L leg labeled with its % depth. Only the legs that
    actually COUNT toward ``vcp_contracting_run`` for that episode are drawn — an
    earlier leg that broke the trailing streak is dropped entirely, not dimmed.

    A dangling swing high with no confirmed low yet is NEVER drawn — it isn't
    part of any completed leg the calculation actually uses, so showing it would
    misrepresent the structure (e.g. implying a base is 'still coiling' up to a
    point that in reality is well after the base already resolved/broke out).

        from agents.evaluator.eval import viz
        viz.plot_vcp_legs('NVDA', start='2024-01-01', end='2026-01-01')  # every episode in 2 years
        viz.plot_vcp_legs('NVDA')                    # full cached history, latest snapshot

    ``start``/``end`` bound the CHART's display window AND the episode scan (an
    episode anchored before ``start`` is simply not scanned into view — widen
    ``start`` to see it). The underlying pivot/leg computation always uses the
    ticker's FULL history up to ``end`` for correct warm-up, regardless of ``start``.
    The green 'live range' band, if shown, reflects the state as of ``end`` only.
    Title reports the VCP state as of ``end`` (default = last cached bar).

    ``show_tight_short`` (default True) additionally shades the background green
    wherever the SHORT-term ATR-based tightness score (``vcp_tight_swing_atr`` —
    5-day swing spread ÷ 14-day ATR%, ranked against its own trailing 50-day
    history; see ``strategy.tightness_score.VCPTightnessScore``) is >=
    ``tight_threshold`` (default 80) — i.e. only the genuinely-tight days, not
    the full red-to-green gradient, to keep this combined chart readable. Since
    it's a fresh daily percentile (5-day window), a single day can flash above
    threshold and drop right back — a real but fleeting reading, not sustained
    compression. ``min_tight_days`` (default 3) drops any run shorter than that,
    so only genuinely sustained tight stretches get shaded.

    ``show_vars`` (default True) adds a thin VARS sub-panel BELOW the main chart
    (its own axis, not another price-panel overlay, to keep the candlesticks
    readable) — vol-adjusted RS vs SPY (solid) and vs the resolved group
    benchmark (dashed), at ``vars_months`` horizon (default 6). The light green
    band marks the validated 'sweet spot' (0 to +15 — moderately, not extremely,
    ahead — the band that showed the real win-rate edge; see the R/VARS work)."""
    from ..strategy.vcp import VCPFootprint
    from ..strategy.volatility import Volatility
    from ..strategy.tightness_score import VCPTightnessScore
    from ..strategy.momentum import TrailingReturns
    from ..strategy.vars import VolAdjRelativeStrength
    from ..engine.records import Bar
    import matplotlib.dates as mdates

    px_full = store.load(ticker)
    end_ts = pd.Timestamp(end) if end else px_full.index[-1]
    df = px_full[px_full.index <= end_ts]
    if df.empty:
        raise ValueError(f"no cached bars for {ticker!r} on/before {end_ts.date()}")
    start_ts = pd.Timestamp(start) if start else end_ts - pd.Timedelta(days=180)
    dates = df.index

    vcp = VCPFootprint(pivot_window=pivot_window, lookback=lookback, max_legs=max_legs,
                       min_contracting_run=min_contracting_run)
    vol = Volatility()                       # short-term defaults: adr_window=20, atr_window=14
    ts = VCPTightnessScore()                 # short-term defaults: tight_window=5, baseline_window=50
    ret = TrailingReturns(months=(vars_months,))
    vars_p = VolAdjRelativeStrength(months=(vars_months,))
    tight_scores = {}                        # date -> vcp_tight_swing_atr, for the shading pass below
    vars_scores = {}                         # date -> (vars_spy, vars_group), for the VARS sub-panel
    vars_bench = None
    # SCAN every day through `end` (not just a single end-of-range snapshot) so
    # EVERY distinct qualifying episode inside [start, end] gets collected, not
    # just whichever one happens to be live on the final day.
    episodes, cur_start_key, final_feat = [], None, None
    for date, r in df.iterrows():
        bar = Bar.from_row(date, r)
        vcp.update(bar)
        vol.update(bar); ts.update(bar); ret.update(bar)
        feat = vcp.features(bar)
        vf = vol.features(bar) if (show_tight_short or show_vars) else {}
        if show_tight_short:
            tf = ts.features(bar, adr_pct=vf.get("adr_pct"), atr_pct=vf.get("atr_pct"))
            tight_scores[date] = tf.get("vcp_tight_swing_atr")
        if show_vars:
            rf = ret.features(bar)
            vsf = vars_p.features(bar, rf, vf.get("atr_pct"), ticker)
            vars_scores[date] = (vsf.get(f"vars_spy_{vars_months}m"), vsf.get(f"vars_{vars_months}m"))
            vars_bench = vsf.get("rs_benchmark", vars_bench)
        if date >= start_ts:
            final_feat = feat
            run = feat.get("vcp_contracting_run") or 0
            if run >= min_contracting_run:
                counted = vcp._last_legs[-run:]        # [(h_idx, h, l_idx, l), ...]
                start_key = counted[0][:2]              # (h_idx, h) of this run's first leg
                if start_key == cur_start_key and episodes:
                    episodes[-1] = counted              # same episode growing -> keep the fuller version
                else:
                    episodes.append(counted)            # a genuinely new episode
                    cur_start_key = start_key
            else:
                cur_start_key = None                    # streak broken; next qualifying run is a NEW episode

    w = px_full[(px_full.index >= start_ts) & (px_full.index <= end_ts)]

    if show_vars:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(15, 8.5), sharex=True, layout="constrained",
                                      gridspec_kw=dict(height_ratios=[3, 1], hspace=.08))
    else:
        fig, ax = plt.subplots(figsize=(15, 7), layout="constrained"); axv = None

    if show_tight_short:
        # green shading wherever the SHORT-term ATR tightness score clears the
        # threshold AND has stayed there for >= min_tight_days consecutive days
        # (a single-day flash is real but not sustained compression — dropped).
        # Edges extend to the midpoint between trading days so weekends/holidays
        # don't leave white gaps in the shading.
        xs = mdates.date2num(w.index.to_pydatetime())
        edges = ([xs[0] - (xs[1] - xs[0]) / 2] + [(xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)]
                 + [xs[-1] + (xs[-1] - xs[-2]) / 2])
        qualifies = [(tight_scores.get(d) is not None and tight_scores[d] >= tight_threshold)
                    for d in w.index]
        any_tight = False
        i = 0
        n = len(qualifies)
        while i < n:
            if qualifies[i]:
                j = i
                while j < n and qualifies[j]:
                    j += 1
                if (j - i) >= min_tight_days:           # run length in trading days
                    ax.axvspan(edges[i], edges[j], color="#2e9e4f", alpha=0.30, lw=0, zorder=0)
                    any_tight = True
                i = j
            else:
                i += 1
        if any_tight:
            from matplotlib.patches import Patch
            tight_leg = ax.legend(handles=[Patch(color="#2e9e4f", alpha=0.30,
                        label=f"short-term ATR tightness >= {tight_threshold:.0f}, "
                              f"{min_tight_days}+ consecutive days")],
                        loc="upper left", fontsize=9)
            ax.add_artist(tight_leg)   # preserved separately; the VCP-episode legend below replaces the "current" one

    _candles(ax, w)

    for legs in episodes:
        seq = []          # alternating (date, price, kind) walk for this one episode
        for h_idx, h, l_idx, l in legs:
            seq.append((dates[h_idx - 1], h, "H"))
            seq.append((dates[l_idx - 1], l, "L"))
        seq_x = [mdates.date2num(d) for d, p, k in seq]
        seq_y = [p for d, p, k in seq]
        ax.plot(seq_x, seq_y, color="#a8321c", lw=2, marker="o", markersize=6, zorder=4)
        for d, p, k in seq:
            ax.annotate(f"{d.strftime('%b %d')}\n${p:.0f}", (mdates.date2num(d), p),
                       textcoords="offset points", xytext=(0, 10 if k == "H" else -22),
                       fontsize=8, ha="center", color="#a8321c")
        for (h_idx, h, l_idx, l) in legs:               # % depth at each leg's midpoint
            hd, ld = dates[h_idx - 1], dates[l_idx - 1]
            mid_x = mdates.date2num(hd) + (mdates.date2num(ld) - mdates.date2num(hd)) / 2
            mid_y = (h + l) / 2
            depth = (h - l) / h * 100.0
            ax.annotate(f"-{depth:.1f}%", (mid_x, mid_y), fontsize=9, fontweight="bold",
                       ha="center", va="center", color="#a8321c",
                       bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#a8321c", alpha=.85))
    if episodes:
        ax.plot([], [], color="#a8321c", lw=2, marker="o", markersize=6,
               label=f"VCP leg sequences ({len(episodes)} episode(s), counted legs only)")

    feat = final_feat or {}
    if feat.get("vcp_in_range") and feat.get("vcp_last_leg_high"):
        lo, hi = feat["vcp_last_leg_low"], feat["vcp_last_leg_high"]
        ax.axhspan(lo, hi, color="#2e9e4f", alpha=0.08, zorder=0,
                  label=f"live range as of {end_ts.date()} ({lo:.0f}-{hi:.0f})")

    ax.set_xlim(mdates.date2num(start_ts), mdates.date2num(end_ts))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d %Y'))
    ax.set_title(f"{ticker} VCP legs, {start_ts.date()} to {end_ts.date()}  —  "
                f"{len(episodes)} episode(s)  |  as of {end_ts.date()}: "
                f"contracting={feat.get('vcp_contracting')}  "
                f"in_range={feat.get('vcp_in_range')}")
    ax.set_ylabel("price ($)"); ax.grid(alpha=.2)
    if episodes or feat.get("vcp_in_range"):
        ax.legend(loc="lower left", fontsize=9)

    if axv is not None:
        vd = w.index
        spy_y = [vars_scores.get(d, (None, None))[0] for d in vd]
        grp_y = [vars_scores.get(d, (None, None))[1] for d in vd]
        axv.axhspan(0, 15, color="#2e9e4f", alpha=0.08, zorder=0)
        axv.axhline(0, color="#888", lw=1, ls=":")
        axv.plot(vd, spy_y, color="#3b6ea5", lw=1.4, label=f"VARS vs SPY ({vars_months}m)")
        bench_label = f"VARS vs {vars_bench} ({vars_months}m)" if vars_bench else f"VARS vs group ({vars_months}m)"
        axv.plot(vd, grp_y, color="#a8321c", lw=1.4, ls="--", label=bench_label)
        axv.set_ylabel("VARS"); axv.grid(alpha=.2)
        axv.legend(loc="upper left", fontsize=8)
        ax.set_xlabel("")
    if out:
        fig.savefig(out, dpi=110); plt.close(fig); print(f"saved {out}")
    else:
        _show(fig)
