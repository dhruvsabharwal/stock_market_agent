import sys
import os
import pandas as pd
import requests

# Ensure the root of the project is in the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.trading_strategies import data_loader
from agents.trading_strategies import feature_engineering
from agents.trading_strategies import edges_and_scoring

def get_nasdaq_100_tickers():
    """Fetches the current Nasdaq-100 tickers from Wikipedia."""
    url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = requests.get(url, headers=headers)
    tables = pd.read_html(response.text)
    
    # NDX components usually in the table index 4 or 5
    for df in tables:
        if 'Ticker' in df.columns:
            tickers = df['Ticker'].tolist()
            return [t.replace('.', '-') for t in tickers]
    return []

def run_screener():
    print("Fetching Nasdaq-100 tickers...")
    tickers = get_nasdaq_100_tickers()
    if not tickers:
        print("Failed to fetch tickers.")
        return
        
    print(f"Running Moglen Setup Screener heavily parallelized for {len(tickers)} tickers...")
    
    import datetime
    end_date = datetime.datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.datetime.now() - datetime.timedelta(days=730)).strftime('%Y-%m-%d')
    
    all_tickers = list(set(tickers + ['SPY']))
    raw_data = data_loader.fetch_data(all_tickers, start_date=start_date, end_date=end_date)
    
    try:
        spy_data = data_loader.get_ticker_data(raw_data, 'SPY')
    except:
        spy_data = None
        
    passing_stocks = []
    
    for ticker in tickers:
        try:
            ticker_df = data_loader.get_ticker_data(raw_data, ticker)
        except Exception:
            continue
            
        if len(ticker_df) < 252:
            continue # not enough data
            
        # Add features and score
        ticker_df = feature_engineering.add_features(ticker_df, spy_data)
        ticker_df = edges_and_scoring.calculate_setup_score(ticker_df)
        
        # Get the most recent day's data
        last_day = ticker_df.iloc[-1]
        
        # Moglen Screener Rules:
        # 1. Price > $10
        # 2. Avg Volume (20d) > 500,000
        # 3. Setup Score (TIGERs) >= 2 (out of 4)
        # 4. Valid Green Line Base (40+ days consolidation)
        if (last_day['close'] > 10.0 and 
            last_day['volume_sma_20'] > 500000 and 
            last_day['setup_score'] >= 2 and 
            last_day['is_base']):
            
            passing_stocks.append({
                'Ticker': ticker,
                'Close': round(last_day['close'], 2),
                'TIGERs Score': last_day['setup_score'],
                'Avg Vol (M)': round(last_day['volume_sma_20'] / 1e6, 2),
                'Green Line Pivot': round(last_day['pivot_point'], 2),
                'Distance to Pivot %': round(((last_day['pivot_point'] - last_day['close']) / last_day['close']) * 100, 2)
            })
            
    if passing_stocks:
        print("\n=== Stocks Meeting Moglen Requirements Today ===")
        results_df = pd.DataFrame(passing_stocks).sort_values(by='TIGERs Score', ascending=False)
        print(results_df.to_string(index=False))
    else:
        print("\nNo stocks meet the strict Moglen setup requirements today.")

if __name__ == "__main__":
    run_screener()
