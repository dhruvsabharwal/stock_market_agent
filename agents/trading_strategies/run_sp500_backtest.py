import sys
import os
import pandas as pd
import requests

# Ensure the root of the project is in the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.trading_strategies import backtester

def get_sp500_tickers():
    """Fetches the current S&P 500 tickers from Wikipedia using requests to bypass 403."""
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = requests.get(url, headers=headers)
    table = pd.read_html(response.text)
    df = table[0]
    tickers = df['Symbol'].tolist()
    # Clean up tickers (e.g., BRK.B -> BRK-B for yfinance)
    tickers = [t.replace('.', '-') for t in tickers]
    return tickers

if __name__ == "__main__":
    print("Fetching S&P 500 tickers...")
    try:
        sp500_tickers = get_sp500_tickers()
        # For testing, limit to 50
        test_tickers = sp500_tickers[:50] 
        print(f"Running Base Breakout strategy test over {len(test_tickers)} S&P 500 component tickers...")
        
        pf = backtester.run_backtest(
            tickers=test_tickers,
            start_date="2020-01-01",
            end_date="2023-12-31",
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
