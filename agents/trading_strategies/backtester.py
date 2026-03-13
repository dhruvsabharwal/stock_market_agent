import vectorbt as vbt
import pandas as pd
import numpy as np
from . import data_loader
from . import feature_engineering
from . import edges_and_scoring
from . import execution

def run_backtest(tickers, start_date, end_date, initial_capital=100000.0):
    """
    Runs the full vectorized Base Breakout strategy backtest using vectorbt.
    """
    # 1. Fetch Data
    print(f"Fetching data for {tickers}...")
    all_tickers = list(set(tickers + ['SPY']))
    raw_data = data_loader.fetch_data(all_tickers, start_date=start_date, end_date=end_date)
    
    try:
        spy_data = data_loader.get_ticker_data(raw_data, 'SPY')
    except ValueError:
        spy_data = None
        print("Warning: SPY data not found. Relative strength will be 0.")
    
    portfolio_signals = {}
    
    for ticker in tickers:
        print(f"Processing {ticker}...")
        try:
            ticker_df = data_loader.get_ticker_data(raw_data, ticker)
        except Exception as e:
            print(f"Skipping {ticker}: {e}")
            continue
            
        # 2. Feature Engineering
        ticker_df = feature_engineering.add_features(ticker_df, spy_data)
        
        # 3. Edges & Scoring
        ticker_df = edges_and_scoring.calculate_setup_score(ticker_df)
        
        # 4. Execution Logic (Generate Target Weights incorporating T+1, scaling out, trailing stops)
        weights = execution.generate_target_weights(ticker_df)
        ticker_df['weight'] = weights
        
        portfolio_signals[ticker] = ticker_df
    
    if not portfolio_signals:
        raise ValueError("No valid tickers processed.")
        
    close_prices = pd.DataFrame({t: portfolio_signals[t]['close'] for t in portfolio_signals}).astype('float64')
    open_prices = pd.DataFrame({t: portfolio_signals[t]['open'] for t in portfolio_signals}).astype('float64')
    high_prices = pd.DataFrame({t: portfolio_signals[t]['high'] for t in portfolio_signals}).astype('float64')
    low_prices = pd.DataFrame({t: portfolio_signals[t]['low'] for t in portfolio_signals}).astype('float64')
    
    sizes = pd.DataFrame({t: portfolio_signals[t]['weight'] for t in portfolio_signals})
    sizes = sizes.replace([np.inf, -np.inf], np.nan).fillna(0).astype('float64')
    
    print("Simulating Portfolio Execution...")
    # 5. Run vectorbt Portfolio Simulation
    pf = vbt.Portfolio.from_orders(
        close=close_prices,
        price=open_prices,         # Execute orders at the open price
        size=sizes,
        size_type='targetpercent',  # Sizes are explicit percentage of current portfolio value per day
        init_cash=initial_capital,
        cash_sharing=True,         # Pool the initial_cash across all assets perfectly
        group_by=True,             # Group all asset columns together into one single stats output
        freq='1D'
    )
    
    return pf
