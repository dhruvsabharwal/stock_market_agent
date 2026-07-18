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


def plot_ticker(ticker, trades, *, log=True, emas=(10, 20), start=None, end=None,
                regime=True, volume=True, out=None):
    """Overlay EVERY trade for one ticker on its ACTUAL price chart. Entry =
    up-triangle, exit = down-triangle, green (win) / red (loss). Optional
    **weekly-trend regime shading** (green=uptrend, grey=basing, red=downtrend,
    recomputed per bar to match wk1035) + the **weekly 10-EMA** line, and a
    **volume** panel below (up/down-day colored + 20-day avg). Log price by
    default. `trades` = any ledger frame, filtered to `ticker` internally."""
    px = store.load(ticker)
    if start:
        px = px[px.index >= pd.Timestamp(start)]
    if end:
        px = px[px.index <= pd.Timestamp(end)]

    if volume:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(16, 9), sharex=True, layout="constrained",
                                      gridspec_kw=dict(height_ratios=[3.2, 1], hspace=.05))
    else:
        fig, ax = plt.subplots(figsize=(16, 8), layout="constrained"); axv = None

    if regime:                                    # shade contiguous weekly-state spans
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

    t = trades[trades["ticker"] == ticker].copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"]); t["exit_date"] = pd.to_datetime(t["exit_date"])
    if start:
        t = t[t.entry_date >= pd.Timestamp(start)]
    if end:
        t = t[t.entry_date <= pd.Timestamp(end)]
    for _, r in t.iterrows():
        col = "#2ca02c" if r["return_pct"] > 0 else "#d62728"
        ax.plot([r["entry_date"], r["exit_date"]], [r["entry_price"], r["exit_price"]],
                color=col, lw=1.0, alpha=.5, zorder=3)
        ax.scatter([r["entry_date"]], [r["entry_price"]], marker="^", color=col,
                   edgecolor="k", lw=.3, s=48, zorder=5)
        ax.scatter([r["exit_date"]], [r["exit_price"]], marker="v", color=col, s=22, alpha=.6, zorder=4)
    if log:
        ax.set_yscale("log")
    w = 100 * (t.return_pct > 0).mean() if len(t) else 0
    ax.set_title(f"{ticker} — {len(t)} entries | win {w:.0f}% mean {t.return_pct.mean():+.1f}%  "
                 f"▲entry ▼exit green=win red=loss"
                 + ("   | bg: green=wk-uptrend grey=basing red=downtrend" if regime else ""))
    ax.set_ylabel("price (log)" if log else "price"); ax.legend(fontsize=8, ncol=2, loc="upper left")
    ax.grid(alpha=.15, which="both")

    if volume:
        up = px["Close"].values >= px["Open"].values
        axv.bar(px.index[up], px["Volume"].values[up], color="#2ca02c", alpha=.5, width=2)
        axv.bar(px.index[~up], px["Volume"].values[~up], color="#d62728", alpha=.5, width=2)
        axv.plot(px.index, px["Volume"].rolling(20).mean(), color="#333", lw=0.8, label="20d avg vol")
        axv.set_ylabel("volume"); axv.legend(fontsize=8, loc="upper left"); axv.grid(alpha=.15)
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
