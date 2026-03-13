import sys
import os
import pandas as pd

# Ensure the root of the project is in the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.trading_strategies import backtester

if __name__ == "__main__":
    test_tickers = ['AAPL', 'TSLA'] # SPY is fetched automatically by the backtester as the benchmark
    print(f"Running Base Breakout strategy test over tickers: {test_tickers}")
    try:
        pf = backtester.run_backtest(
            tickers=test_tickers,
            start_date="2020-01-01",
            end_date="2023-12-31",
            initial_capital=100000.0
        )
        print("\n=== Backtest Complete ===")
        # pf.total_return() returns a Series when multiple assets are traded, handle safely
        total_ret = pf.total_return()
        if isinstance(total_ret, pd.Series):
            print("Total Return per Asset:")
            print(total_ret * 100)
        else:
            print(f"Total Return: {total_ret * 100:.2f}%")
        
        print("\n--- Comprehensive Stats ---")
        print(pf.stats())
        
        print("\n--- Recent Trade Execution Log ---")
        print(pf.orders.records_readable.tail(15))
        
    except ImportError as e:
        print(f"\nImportError: {e}. Please ensure 'vectorbt' is installed: pip install vectorbt")
    except Exception as e:
        print(f"\nError during backtest: {e}")
