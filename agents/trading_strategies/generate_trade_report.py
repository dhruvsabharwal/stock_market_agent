"""
generate_trade_report.py - Moglen Green Line Backtest Trade Report (2022-2025)

Runs the full Moglen strategy on the Nasdaq-100 universe and produces:
1. A per-trade CSV with every entry, exit, stop level, exit reason & P&L
2. A per-stock setup score on the signal day
3. Summary statistics grouped by exit reason, year, and stock

Usage: uv run python agents/trading_strategies/generate_trade_report.py
"""

import sys
import os
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from typing import List

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from agents.trading_strategies import data_loader, feature_engineering, edges_and_scoring

# ──────────────────────────────────────────────────────────────────────────────
# Nasdaq-100 ticker fetch
# ──────────────────────────────────────────────────────────────────────────────
def get_growth_universe() -> List[str]:
    """
    Combines Nasdaq-100 and S&P 500 to create a broad high-alpha growth universe.
    Uses requests with a User-Agent to avoid Wikipedia's 403 Forbidden error.
    """
    tickers = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # Nasdaq 100
    try:
        response = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers=headers)
        ndx = pd.read_html(response.text)[4]
        tickers.extend(ndx['Ticker'].tolist())
    except Exception as e:
        print(f"NDX Fetch Error: {e}")
    
    # S&P 500
    try:
        response = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
        sp500 = pd.read_html(response.text)[0]
        tickers.extend(sp500['Symbol'].tolist())
    except Exception as e:
        print(f"S&P 500 Fetch Error: {e}")

    # S&P MidCap 400
    try:
        response = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies', headers=headers)
        sp400 = pd.read_html(response.text)[0]
        tickers.extend(sp400['Symbol'].tolist())
    except Exception as e:
        print(f"S&P 400 Fetch Error: {e}")
        
    # Clean and unique
    tickers = list(set([str(t).replace('.', '-') for t in tickers]))
    return sorted([t for t in tickers if isinstance(t, str) and len(t) > 0])


# ─── Path-dependent execution with full trade logging ─────────────────────────
def simulate_with_logging(ticker: str, df: pd.DataFrame) -> list[dict]:
    """
    Runs the full Moglen execution loop on a single stock, returning a list of
    completed trade dicts with granular fields for the report.
    """
    pivot_shift = df['pivot_point'].shift(1)
    base_valid_shift = df['is_base'].shift(1)

    price_trigger = df['high'] > pivot_shift
    volume_trigger = df['volume'] > df['volume_sma_20']
    score_trigger  = df['setup_score'] >= 2
    market_trigger = df['market_uptrend'] == 1 if 'market_uptrend' in df.columns else True

    raw_setup = price_trigger & volume_trigger & score_trigger & base_valid_shift & market_trigger

    close_below_ema  = df['close'] < df['ema_21']
    two_days_below_ema = close_below_ema & close_below_ema.shift(1)

    raw_setup_arr          = raw_setup.fillna(False).values
    two_days_below_ema_arr = two_days_below_ema.fillna(False).values

    # Raw Indicator Arrays
    tightness_arr = df['raw_tightness'].values
    ignition_arr  = df['raw_ignition'].values
    rs_dist_arr   = df['raw_rs_distance'].values
    trend_arr     = df['edge_4_trend'].values
    market_arr    = (df['market_uptrend'] == 1).values if 'market_uptrend' in df.columns else np.ones(len(df))

    opens  = df['open'].values
    highs  = df['high'].values
    lows   = df['low'].values
    closes = df['close'].values
    scores = df['setup_score'].values
    dates  = df.index

    completed_trades = []

    in_position    = False
    entry_date     = None
    entry_price    = 0.0
    hard_stop      = 0.0
    position_pct   = 0.0
    signal_score   = 0
    green_line     = 0.0
    scaled_out      = False
    scale_out_date  = None
    scale_out_price = 0.0
    initial_position_size = 0.0

    # Numeric edge metrics captured at signal (Day T)
    edge_metrics = {}

    # Volume & RSI for verification
    volumes = df['volume'].values
    vol_sma = df['volume_sma_20'].values
    rsi_vals = df['rsi_14'].values
    atr_14_arr = df['atr_14'].values

    for i in range(1, len(df)):
        current_date = dates[i]
        
        # ── T+1 EXECUTION LOGIC: Gap Cap (T+1 Open) ─────────────────────────
        # signal from Day i-1
        is_explosive = (volumes[i-1] > vol_sma[i-1] * 1.50)
        
        # Liquidity Filter: Price > $10 AND 50-day Avg Volume > 500k
        price_too_low = opens[i] < 10.0
        avg_vol_50 = df['volume_sma_50'].iloc[i-1] if 'volume_sma_50' in df.columns else 1_000_000
        vol_too_low = avg_vol_50 < 500_000
        is_liquid = not (price_too_low or vol_too_low)
        
        if not in_position and raw_setup_arr[i - 1] and scores[i-1] >= 3 and is_explosive and is_liquid:
            # We have a valid signal. Check the T+1 Open for Gap Cap.
            pivot_price = df['pivot_point'].iloc[i-1]
            day_t1_open = opens[i]
            
            # Gap Cap Rule: Only buy if gap up is <= 2% from the pivot
            if day_t1_open <= pivot_price * 1.02:
                if current_date.strftime('%Y-%m-%d') >= START_DATE:
                    in_position     = True
                    entry_date      = current_date
                    entry_price     = day_t1_open
                    signal_score    = float(scores[i - 1])
                    
                    # ── STOP LOSS LOGIC ─────────────────────────────────────────
                    # Logical Stop: 1.5 * ATR
                    logical_stop = entry_price - (1.5 * atr_14_arr[i-1])
                    # WIDENED Hard Stop: Max 6% drop (0.94 multiplier)
                    max_stop_price = entry_price * 0.94 
                    # Use the higher/tighter stop
                    hard_stop = max(logical_stop, max_stop_price)
                    
                    # ── DYNAMIC POSITION SIZING ─────────────────────────────────
                    risk_amt_pct = (entry_price - hard_stop) / entry_price
                    if risk_amt_pct > 0:
                        raw_pos_pct = 0.01 / risk_amt_pct
                    else:
                        raw_pos_pct = 0.25 
                    
                    initial_position_size = min(raw_pos_pct, 0.25)
                    position_pct    = initial_position_size
                    
                    # Capture metrics for reporting
                    edge_metrics = {
                        'Tightness (ATR Ratio)': round(float(tightness_arr[i-1]), 3),
                        'Ignition (Advance %)':  f"{round(float(ignition_arr[i-1]) * 100, 1)}%",
                        'RS Dist from High':     round(float(rs_dist_arr[i-1]), 3),
                        'Market Regime':         "Trend Aligned" if market_arr[i-1] else "Blocked",
                        'Sales Growth %':        f"{round(getattr(df.iloc[i-1], 'sales_growth', 0)*100, 1)}%",
                        'EPS Growth %':          f"{round(getattr(df.iloc[i-1], 'eps_growth', 0)*100, 1)}%",
                        'Execution Type':        "Gap Cap (T+1 Open)"
                    }

                    scaled_out      = False
                    scale_out_date  = None
                    scale_out_price = 0.0
                    scale_out_logged = False

        elif in_position:
            current_low  = lows[i]
            current_high = highs[i]

            # ── EXIT: HARD STOP or BREAKEVEN STOP ───────────────────────────
            if current_low <= hard_stop:
                exit_price  = hard_stop
                exit_reason = 'Hard Stop (ATR/8%)' if not scaled_out else 'Breakeven Stop'
                
                if scaled_out:
                    # Log the 60% runner exit
                    trade_data = {
                        'Ticker':          ticker,
                        'Entry Date':      entry_date,
                        'Entry Price':     round(entry_price, 4),
                        'TIGERs Score':    signal_score,
                        'Stop Dist %':     round(risk_amt_pct, 4)
                    }
                    trade_data.update(edge_metrics)
                    trade_data.update({
                        'Position Size % (Static)': round(initial_position_size * 0.60 * 100, 2),
                        'Hard Stop Price': round(hard_stop, 4),
                        'Exit Date':       current_date,
                        'Exit Price':      round(exit_price, 4),
                        'Exit Reason':     exit_reason,
                        'PnL %':          round((exit_price - entry_price) / entry_price * 100, 2),
                        'Holding Days':    (current_date - entry_date).days,
                    })
                    completed_trades.append(trade_data)
                else:
                    # Log full 100% exit
                    trade_data = {
                        'Ticker':          ticker,
                        'Entry Date':      entry_date,
                        'Entry Price':     round(entry_price, 4),
                        'TIGERs Score':    signal_score,
                        'Stop Dist %':     round(risk_amt_pct, 4)
                    }
                    trade_data.update(edge_metrics)
                    trade_data.update({
                        'Position Size % (Static)': round(initial_position_size * 100, 2),
                        'Hard Stop Price': round(hard_stop, 4),
                        'Exit Date':       current_date,
                        'Exit Price':      round(exit_price, 4),
                        'Exit Reason':     exit_reason,
                        'PnL %':          round((exit_price - entry_price) / entry_price * 100, 2),
                        'Holding Days':    (current_date - entry_date).days,
                    })
                    completed_trades.append(trade_data)
                in_position  = False

            # ── SCALE OUT at +10% (40% Slice) ───────────────────────────────
            elif not scaled_out and current_high >= entry_price * 1.10:
                scaled_out      = True
                exit_price      = entry_price * 1.10
                
                # Log the 40% scale-out immediately
                trade_data = {
                    'Ticker':          ticker,
                    'Entry Date':      entry_date,
                    'Entry Price':     round(entry_price, 4),
                    'TIGERs Score':    signal_score,
                    'Stop Dist %':     round(risk_amt_pct, 4)
                }
                trade_data.update(edge_metrics)
                trade_data.update({
                    'Position Size % (Static)': round(initial_position_size * 0.40 * 100, 2),
                    'Hard Stop Price': round(hard_stop, 4),
                    'Exit Date':       current_date,
                    'Exit Price':      round(exit_price, 4),
                    'Exit Reason':     'Scale-Out (+10%)',
                    'PnL %':          10.0,
                    'Holding Days':    (current_date - entry_date).days,
                })
                completed_trades.append(trade_data)
                
                # Shift stop to breakeven for the remaining 60%
                hard_stop = entry_price

            # ── EXIT: TRAILING STOP (Close < 21 EMA on High Volume) ─────────
            elif df['close'].iloc[i] < df['ema_21'].iloc[i] and df['volume'].iloc[i] > df['volume_sma_20'].iloc[i]:
                exit_price  = opens[i] if i+1 < len(df) else closes[i]
                exit_reason = '21-EMA Trailing Stop (High Vol)'
                
                # Log whatever remains (100% or 60% runner)
                rem_size = 0.60 if scaled_out else 1.0
                trade_data = {
                    'Ticker':          ticker,
                    'Entry Date':      entry_date,
                    'Entry Price':     round(entry_price, 4),
                    'TIGERs Score':    signal_score,
                    'Stop Dist %':     round(risk_amt_pct, 4)
                }
                trade_data.update(edge_metrics)
                trade_data.update({
                    'Position Size % (Static)': round(initial_position_size * rem_size * 100, 2),
                    'Hard Stop Price': round(hard_stop, 4),
                    'Exit Date':       current_date,
                    'Exit Price':      round(exit_price, 4),
                    'Exit Reason':     exit_reason,
                    'PnL %':          round((exit_price - entry_price) / entry_price * 100, 2),
                    'Holding Days':    (current_date - entry_date).days,
                })
                completed_trades.append(trade_data)
                in_position  = False

    return completed_trades


def replay_portfolio_dollars(trades_list, initial_capital):
    """
    Replays all trades chronologically.
    Standard positioning: 1% account risk on every setup.
    """
    if not trades_list:
        return pd.DataFrame(), pd.DataFrame(), {}
        
    sorted_trades = sorted(trades_list, key=lambda x: x['Entry Date'])
    current_equity = initial_capital
    updated_trades = []
    
    # Track capital deployed for 2024 specifically
    capital_deployment_2024 = [] 
    equity_curve = [] # (Date, total_equity)
    
    # Initialize equity curve with start state
    equity_curve.append({'Date': sorted_trades[0]['Entry Date'], 'Equity': initial_capital})

    for row in sorted_trades:
        # 1% Account Risk vs Stop Distance
        stop_dist_pct = row.get('Stop Dist %', 0.06)
        # Position Size = 1% Risk / Stop %
        dynamic_pos_pct = min(0.01 / stop_dist_pct, 0.25)
        
        pnl_pct_scaled = row['PnL %'] / 100.0
        
        dollar_entry = current_equity * dynamic_pos_pct
        dollar_exit  = dollar_entry * (1.0 + pnl_pct_scaled)
        dollar_pnl   = dollar_exit - dollar_entry
        
        # Track 2024 Capital Deployment
        if row['Entry Date'].year == 2024:
            capital_deployment_2024.append(dynamic_pos_pct)
        
        current_equity += dollar_pnl
        
        row_dict = row.copy()
        row_dict['Actual $ Invested'] = round(dollar_entry, 2)
        row_dict['Actual $ Exit'] = round(dollar_exit, 2)
        row_dict['Dollar PnL'] = round(dollar_pnl, 2)
        row_dict['Portfolio Equity'] = round(current_equity, 2)
        row_dict['Position Size %'] = f"{round(dynamic_pos_pct*100, 1)}%"
        row_dict['R-Multiple'] = round(row['PnL %'] / (stop_dist_pct * 100), 2)
        
        updated_trades.append(row_dict)
        equity_curve.append({'Date': row['Exit Date'], 'Equity': current_equity})
        
    final_df = pd.DataFrame(updated_trades)
    
    # Calculate Max Drawdown
    equity_series = pd.Series([initial_capital] + [t['Equity'] for t in equity_curve])
    cum_max = equity_series.cummax()
    drawdown = (equity_series - cum_max) / cum_max
    max_drawdown = drawdown.min()
    
    # Average Cap Deployed 2024
    avg_cap_2024 = sum(capital_deployment_2024) / len(capital_deployment_2024) if capital_deployment_2024 else 0
    
    # Monthly Snaps from exit dates
    hist_df = pd.DataFrame(equity_curve)
    hist_df.set_index('Date', inplace=True)
    monthly_snaps = hist_df['Equity'].resample('M').last().ffill().reset_index()
    monthly_snaps.columns = ['Month', 'Portfolio_Value']
    
    # Summary Object for the final report
    summary_stats = {
        'Max Drawdown %': f"{round(max_drawdown * 100, 2)}%",
        'Avg Cap Deployed 2024': f"{round(avg_cap_2024 * 100, 2)}%",
        'Avg R-Multiple': round(final_df['R-Multiple'].mean(), 2),
    }
    
    return final_df, monthly_snaps, summary_stats


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    START_DATE     = '2022-01-01'
    END_DATE       = '2025-03-01'
    INITIAL_CAPITAL = 100_000.0
    OUTPUT_CSV     = os.path.join(os.path.dirname(__file__), 'moglen_trade_report.csv')
    OUTPUT_REPORT  = os.path.join(os.path.dirname(__file__), 'moglen_trade_report.md')

    print("Fetching Growth Universe (Nasdaq-100 + S&P 500)...")
    tickers = get_growth_universe()
    all_tickers = list(set(tickers + ['SPY']))

    print(f"Downloading {len(all_tickers)} tickers with 1-year buffer for indicators...")
    FETCH_START = '2021-01-01' 
    raw_data = data_loader.fetch_data(all_tickers, start_date=FETCH_START, end_date=END_DATE)

    try:
        spy_data = data_loader.get_ticker_data(raw_data, 'SPY')
    except ValueError:
        spy_data = None
        print("Warning: SPY data not found.")

    all_trades = []

    print("Fetching Fundamental Growth Metrics (Sales/EPS > 25%)...")
    growth_df = data_loader.fetch_growth_metrics(tickers)

    all_trades = []

    for ticker in tickers:
        try:
            df = data_loader.get_ticker_data(raw_data, ticker)
            
            # Inject growth metrics into the dataframe for the simulator
            if ticker in growth_df.index:
                df['sales_growth'] = growth_df.loc[ticker, 'sales_growth']
                df['eps_growth'] = growth_df.loc[ticker, 'eps_growth']
            else:
                df['sales_growth'] = 0.0
                df['eps_growth'] = 0.0
                
            df = feature_engineering.add_features(df, spy_data)
            df = edges_and_scoring.calculate_setup_score(df)
            trades = simulate_with_logging(ticker, df)
            all_trades.extend(trades)
            if trades:
                print(f"  {ticker}: {len(trades)} trades")
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    if not all_trades:
        print("No trades found in the date range.")
        sys.exit(0)

    # ── Replay for Dollars ──────────────────────────────────────────────────
    print("\nReplaying all trades chronologically for dollar figures...")
    trades_df, monthly_snaps, summary_stats = replay_portfolio_dollars(all_trades, INITIAL_CAPITAL)
    
    trades_df.index += 1
    trades_df.to_csv(OUTPUT_CSV, index_label='Trade #')
    # ── Monthly and Annual Aggregation ──────────────────────────────────────
    trades_df['Exit Month'] = trades_df['Exit Date'].dt.to_period('M')
    trades_df['Exit Year']  = trades_df['Exit Date'].dt.year

    monthly_grp = trades_df.groupby('Exit Month').agg(
        Trades_Closed        = ('PnL %',       'count'),
        Monthly_PnL_Dollar   = ('Dollar PnL',  'sum'),
        Win_Rate_Pct         = ('PnL %',        lambda x: round((x > 0).mean() * 100, 1)),
        Best_Trade_Pct       = ('PnL %',        'max'),
    ).reset_index()
    
    # Merge with replayed portfolio value for accuracy
    monthly_grp['Month_Str'] = monthly_grp['Exit Month'].astype(str)
    monthly_snaps['Month_Str'] = monthly_snaps['Month'].dt.to_period('M').astype(str)
    
    monthly_export = pd.merge(
        monthly_grp, 
        monthly_snaps[['Month_Str', 'Portfolio_Value']], 
        on='Month_Str', 
        how='left'
    )
    monthly_export['Month'] = monthly_export['Month_Str']
    monthly_export = monthly_export.drop(columns=['Exit Month', 'Month_Str'])
    
    monthly_export['Month_Return_Pct'] = (monthly_export['Monthly_PnL_Dollar'] / INITIAL_CAPITAL * 100).round(2)

    OUTPUT_PORTFOLIO_CSV = os.path.join(os.path.dirname(__file__), 'moglen_portfolio_monthly.csv')
    monthly_export.to_csv(OUTPUT_PORTFOLIO_CSV, index=False)
    print(f"Portfolio monthly CSV saved → {OUTPUT_PORTFOLIO_CSV}")

    # ── Annual Summary ──────────────────────────────────────────────────────
    annual_grp = trades_df.groupby('Exit Year').agg(
        Trades        = ('PnL %',      'count'),
        Annual_PnL_USD = ('Dollar PnL', 'sum'),
        Win_Rate_Pct  = ('PnL %',      lambda x: round((x > 0).mean() * 100, 1)),
        Best_Trade    = ('PnL %',      'max'),
        Worst_Trade   = ('PnL %',      'min'),
    ).reset_index()
    annual_summary = annual_grp.rename(columns={'Exit Year': 'Year'})
    annual_summary['Annual_Return_%'] = (annual_summary['Annual_PnL_USD'] / INITIAL_CAPITAL * 100).round(2)

    # Total across all trades
    total_pnl    = trades_df['Dollar PnL'].sum()
    final_equity = INITIAL_CAPITAL + total_pnl
    total_return = (final_equity / INITIAL_CAPITAL - 1) * 100
    total_trades       = len(trades_df)
    winners            = trades_df[trades_df['PnL %'] > 0]
    losers             = trades_df[trades_df['PnL %'] <= 0]
    win_rate           = len(winners) / total_trades * 100
    avg_win            = winners['PnL %'].mean()
    avg_loss           = losers['PnL %'].mean()
    best_trade         = trades_df.loc[trades_df['PnL %'].idxmax()]
    worst_trade        = trades_df.loc[trades_df['PnL %'].idxmin()]
    avg_hold           = trades_df['Holding Days'].mean()

    by_reason = trades_df.groupby('Exit Reason').agg(
        Count  =('PnL %', 'count'),
        Avg_PnL=('PnL %', 'mean'),
        Win_Rate=('PnL %', lambda x: (x > 0).mean() * 100)
    ).round(2)

    top10_trades = trades_df.nlargest(10, 'PnL %')[
        ['Ticker', 'Entry Date', 'Actual $ Invested', 'Exit Date', 'Actual $ Exit', 'PnL %', 'Holding Days', 'R-Multiple']
    ]
    worst10_trades = trades_df.nsmallest(10, 'PnL %')[
        ['Ticker', 'Entry Date', 'Actual $ Invested', 'Exit Date', 'Actual $ Exit', 'PnL %', 'Holding Days', 'R-Multiple']
    ]

    md = []
    md.append("# Moglen Green Line Backtest — Trade Report")
    md.append(f"> **Universe: Growth (NDX+SPY500)** | **Period:** {START_DATE} to {END_DATE}")
    md.append(f"> **Initial Capital:** ${INITIAL_CAPITAL:,.0f} | **Final Portfolio Value:** ${final_equity:,.2f} | **Total Return:** {total_return:.2f}%")
    md.append("")
    md.append("## Executive Summary")
    md.append(f"| Metric | Value |")
    md.append(f"|:--|:--|")
    md.append(f"| Total Trades | {total_trades} |")
    md.append(f"| Winners | {len(winners)} ({win_rate:.1f}%) |")
    md.append(f"| Losers | {len(losers)} |")
    md.append(f"| Avg Winning Trade | +{avg_win:.2f}% |")
    md.append(f"| Avg Losing Trade | {avg_loss:.2f}% |")
    md.append(f"| Best Trade | {best_trade['Ticker']} on {best_trade['Entry Date']} → +{best_trade['PnL %']:.2f}% |")
    md.append(f"| Worst Trade | {worst_trade['Ticker']} on {worst_trade['Entry Date']} → {worst_trade['PnL %']:.2f}% |")
    md.append(f"| Avg Holding Period | {avg_hold:.0f} days |")
    md.append("")
    md.append("## Post-Trade Analytics Summary")
    md.append(f"| Metric | Value |")
    md.append(f"|:--|:--|")
    md.append(f"| Max Drawdown % | {summary_stats['Max Drawdown %']} |")
    md.append(f"| Avg Capital Deployed 2024 | {summary_stats['Avg Cap Deployed 2024']} |")
    md.append(f"| Avg R-Multiple | {summary_stats['Avg R-Multiple']} |")
    md.append("")
    md.append("## Breakdown by Exit Reason")
    md.append(by_reason.to_markdown())
    md.append("")
    md.append("## Top 10 Best Trades")
    md.append(top10_trades.to_markdown(index=False))
    md.append("")
    md.append("## Top 10 Worst Trades")
    md.append(worst10_trades.to_markdown(index=False))
    md.append("")
    
    # ── Annual Portfolio Performance ─────────────────────────────────────────
    md.append("## Annual Portfolio Performance")
    md.append(annual_summary.to_markdown(index=False))
    md.append("")
    
    # ── Monthly Portfolio Snapshot table ────────────────────────────────────
    md.append("## Monthly Portfolio Snapshot")
    md.append(monthly_export.to_markdown(index=False))
    md.append("")
    
    md.append("## Sample Trades (TIGERs Audit)")
    cols_to_show = [
        'Ticker', 'Entry Date', 'Position Size %', 'R-Multiple',
        'Sales Growth %', 'EPS Growth %', 'Execution Type',
        'Exit Date', 'PnL %', 'Exit Reason'
    ]
    sample = trades_df.head(30)[cols_to_show]
    md.append(sample.to_markdown(index=False))
    md.append("")
    md.append(f"*Full trade log saved to `moglen_trade_report.csv` ({total_trades} total trades). Monthly portfolio snapshots in `moglen_portfolio_monthly.csv`.*")

    report_text = '\n'.join(md)
    with open(OUTPUT_REPORT, 'w') as f:
        f.write(report_text)
    print(f"Markdown report saved → {OUTPUT_REPORT}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total Trades:    {total_trades}")
    print(f"Win Rate:        {win_rate:.1f}%")
    print(f"Avg Win:         +{avg_win:.2f}%")
    print(f"Avg Loss:         {avg_loss:.2f}%")
    print(f"Best Trade:      {best_trade['Ticker']}  +{best_trade['PnL %']:.2f}%")
    print(f"Worst Trade:     {worst_trade['Ticker']} {worst_trade['PnL %']:.2f}%")
    print(f"Avg Hold:        {avg_hold:.0f} days")
    print("=" * 60)
