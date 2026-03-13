import sys
import os
import pandas as pd
import requests

# Ensure the root of the project is in the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.trading_strategies import backtester

def get_nasdaq_100_tickers():
    """Fetches the current Nasdaq-100 tickers from Wikipedia using requests to bypass 403."""
    url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
    headers = {
        'User-Agent': 'Mozilla/5.0'
    }
    response = requests.get(url, headers=headers)
    tables = pd.read_html(response.text)
    
    for df in tables:
        if 'Ticker' in df.columns:
            tickers = df['Ticker'].tolist()
            return [t.replace('.', '-') for t in tickers]
    return []

if __name__ == "__main__":
    print("Fetching Nasdaq-100 tickers...")
    try:
        ndx_tickers = get_nasdaq_100_tickers()
        # For testing speed, we can run all 100 since there are fewer than the S&P 500
        test_tickers = ndx_tickers 
        print(f"Running Moglen Breakout strategy test over {len(test_tickers)} Nasdaq-100 component tickers...")
        
        pf = backtester.run_backtest(
            tickers=test_tickers,
            start_date="2022-01-01",
            end_date="2025-03-01",
            initial_capital=100000.0
        )
        print("\n=== Backtest Complete ===")
        total_ret = pf.total_return()
        if isinstance(total_ret, pd.Series):
             print(f"Portfolio Total Return: {total_ret.mean() * 100:.2f}% (Average across {len(total_ret)} traded assets)")
        else:
            print(f"Portfolio Total Return: {total_ret * 100:.2f}%")
        
        print("\n--- Comprehensive Stats ---")
        print(pf.stats())
        
    except Exception as e:
        print(f"\nError during backtest: {e}")
