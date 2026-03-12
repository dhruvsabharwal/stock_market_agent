import pandas as pd
import numpy as np
import yfinance as yf
import vectorbt as vbt
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import os

# Set aesthetic style
plt.style.use('seaborn-v0_8-darkgrid')

def run_momentum_strategy():
    # 1. Configuration
    tickers = ['AAPL', 'MSFT', 'TSLA', 'NVDA', 'AMZN', 'RELIANCE.NS', 'HDFCBANK.NS', 'TCS.NS', 'INFY.NS', 'ICICIBANK.NS']
    start_date = (datetime.now() - timedelta(days=365*5)).strftime('%Y-%m-%d') # 5 years
    initial_cash = 100000
    allocation_per_trade = 0.10 # 10%
    
    print(f"Fetching data for universe: {', '.join(tickers)}")
    
    # 2. Fetch Data (Adjusted Close is default for yfinance download if not specified, but let's be explicit)
    data = yf.download(tickers, start=start_date, group_by='ticker', auto_adjust=True)
    
    # Extract Close and Volume efficiently
    close_price = pd.DataFrame({t: data[t]['Close'] for t in tickers})
    volume = pd.DataFrame({t: data[t]['Volume'] for t in tickers})
    high_price = pd.DataFrame({t: data[t]['High'] for t in tickers})
    
    # 2.5 Bridge Calendar Gaps (US vs India holidays)
    # Forward-fill NaNs to ensure rolling windows work
    close_price = close_price.ffill()
    volume = volume.fillna(0) # Volume should be 0 on holidays
    high_price = high_price.ffill()

    # 3. Calculate Indicators
    print("Calculating technical indicators...")
    # 52-week (252 days) rolling high
    rolling_high_52w = high_price.shift(1).rolling(window=252).max()
    
    # 200-day SMA
    sma_200 = close_price.rolling(window=200).mean()
    
    # 50-day SMA (for exit)
    sma_50 = close_price.rolling(window=50).mean()
    
    # 20-day Average Volume
    avg_vol_20 = volume.rolling(window=20).mean()
    
    # 4. Define Entry/Exit Signals
    # Entry: Close > 52w High AND Volume > 1.5x Avg Vol AND Close > 200 SMA
    # Restoring original 1.5x constraint as alignment is fixed
    entry_signals = (close_price > rolling_high_52w) & \
                    (volume > 1.5 * avg_vol_20) & \
                    (close_price > sma_200)
    
    # DEBUG: Check data alignment and NaNs
    print(f"Close Price shape: {close_price.shape}")
    print(f"SMA 200 Non-Nulls: {sma_200.notnull().sum().sum()}")
    print(f"52w High Non-Nulls: {rolling_high_52w.notnull().sum().sum()}")
    
    # Debug: see how many individual conditions are met
    print(f"Condition 1 (52w High Breakout) met: { (close_price > rolling_high_52w).sum().sum() } times")
    print(f"Condition 2 (Volume Spike 1.2x) met: { (volume > 1.2 * avg_vol_20).sum().sum() } times")
    print(f"Condition 3 (Above 200 SMA) met: { (close_price > sma_200).sum().sum() } times")
    print(f"Total entry signals triggered: {entry_signals.sum().sum()}")
    
    # Exit: Close < 50 SMA
    exit_signals = (close_price < sma_50)
    
    # 5. Run Backtest
    print("Running vectorized backtest...")
    portfolio = vbt.Portfolio.from_signals(
        close_price, 
        entries=entry_signals, 
        exits=exit_signals,
        init_cash=initial_cash,
        size=allocation_per_trade,
        size_type='percent', # 10% allocation
        direction='longonly',
        freq='1D',
        accumulate=False # Do not add to existing positions
    )
    
    # 6. Performance Metrics
    stats = portfolio.stats()
    
    # MFE/MAE Calculation (requires iteration over trades)
    print("Calculating advanced trade metrics (MFE/MAE)...")
    trades = portfolio.trades.records_readable
    
    # Note: vectorbt 'trades' object has MFE/MAE built-in if we use records
    mfe_avg = trades['Max Favorable Excursion'].mean() if 'Max Favorable Excursion' in trades.columns else 0
    mae_avg = trades['Max Adverse Excursion'].mean() if 'Max Adverse Excursion' in trades.columns else 0
    
    # 7. Reporting
    print("\n" + "="*40)
    print(" 52-WEEK HIGH MOMENTUM STRATEGY REPORT")
    print("="*40)
    print(f"Total Return: {stats['Total Return [%]']:.2f}%")
    print(f"Benchmark Return: {stats['Benchmark Return [%]']:.2f}%")
    print(f"Sharpe Ratio: {stats['Sharpe Ratio']:.2f}")
    print(f"Max Drawdown: {stats['Max Drawdown [%]']:.2f}%")
    print(f"Profit Factor: {stats['Profit Factor']:.2f}")
    print(f"Total Trades: {stats['Total Trades']}")
    print(f"Win Rate: {stats['Win Rate [%]']:.2f}%")
    print(f"Average MFE: {mfe_avg:.2f}%" if mfe_avg else "MFE: N/A")
    print(f"Average MAE: {mae_avg:.2f}%" if mae_avg else "MAE: N/A")
    print("="*40)
    
    # 8. Visualization
    print("Generating plots...")
    plot_path = "momentum_equity_curve.png"
    
    try:
        # Manual plot using matplotlib for better control over the aggregate equity curve
        plt.figure(figsize=(12, 7))
        
        # Portfolio value is the cumulative equity
        equity_curve = portfolio.value()
        equity_curve.plot(label='Strategy Equity', color='forestgreen', linewidth=2)
        
        # Calculate Benchmark (Equal-Weighted Buy & Hold of the same universe)
        # We assume initial cash is spread across all tickers equally at the start
        normalized_prices = close_price.divide(close_price.iloc[0])
        benchmark_curve = normalized_prices.mean(axis=1) * initial_cash
        benchmark_curve.plot(label='Equal-Weighted Benchmark', color='red', linestyle='--', alpha=0.7)
        
        plt.title('52-Week High Momentum Strategy vs Benchmark')
        plt.ylabel('Portfolio Value ($)')
        plt.xlabel('Date')
        plt.legend()
        plt.grid(True, which='both', linestyle='--', alpha=0.5)
        
        plt.tight_layout()
        plt.savefig(plot_path)
        print(f"Equity curve saved to {plot_path}")
        
    except Exception as e:
        print(f"Error during plotting: {e}")

if __name__ == "__main__":
    run_momentum_strategy()
