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
