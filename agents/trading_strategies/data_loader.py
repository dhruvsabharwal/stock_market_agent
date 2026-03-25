import yfinance as yf
import pandas as pd
from typing import List, Union

import concurrent.futures
import time

def _fetch_single_ticker(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        # yfinance 0.2.65 with pandas 2.3+ bug: `auto_adjust=True` causes MultiIndex `isna` errors 
        # for a single ticker if we don't handle it carefully. We'll use the Ticker object directly
        # to pull the history which natively avoids the batch download MultiIndex bugs.
        tkr = yf.Ticker(ticker)
        df = tkr.history(start=start_date, end=end_date, auto_adjust=True)
        
        if not df.empty:
            # We add the ticker as the top level to match batch download format
            df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
            return df
    except Exception as e:
        print(f"Failed to fetch {ticker}: {e}")
    return pd.DataFrame()

def fetch_data(tickers: Union[str, List[str]], start_date: str, end_date: str = None) -> pd.DataFrame:
    """
    Fetches OHLCV data for the given tickers concurrently using ThreadPoolExecutor.
    Uses yfinance with auto_adjust=True to adjust OHLC prices for splits and dividends.
    """
    if isinstance(tickers, str):
        tickers = tickers.split()
        
    print(f"Fetching data for {len(tickers)} tickers concurrently...")
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all tasks
        future_to_ticker = {
            executor.submit(_fetch_single_ticker, ticker, start_date, end_date): ticker 
            for ticker in tickers
        }
        
        # Process as they complete
        for future in concurrent.futures.as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                df = future.result()
                if not df.empty:
                    results.append(df)
            except Exception as e:
                print(f"Exception for {ticker}: {e}")
                
    if not results:
        return pd.DataFrame()
        
    # Concatenate all individual multi-index dataframes into one large dataframe
    combined_df = pd.concat(results, axis=1)
    
    # Sort index to ensure chronological order
    combined_df = combined_df.sort_index()
    return combined_df

def get_ticker_data(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Extracts data for a specific ticker from a MultiIndex yfinance DataFrame,
    or handles a single ticker DataFrame. Normalizes column names to lowercase.
    """
    if isinstance(df.columns, pd.MultiIndex):
        try:
            ticker_df = df.xs(ticker, axis=1, level=1).copy()
        except KeyError:
            try:
                ticker_df = df.xs(ticker, axis=1, level=0).copy()
            except KeyError:
                raise ValueError(f"Ticker {ticker} not found in columns.")
    else:
        ticker_df = df.copy()

    # Standardize column names
    cols_map = {
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    }
    ticker_df = ticker_df.rename(columns=lambda x: cols_map.get(x, x))
    
    # Forward fill to prevent lookahead bias on missing days, then drop initial NaNs
    ticker_df = ticker_df.ffill()
    ticker_df = ticker_df.dropna()
    
    return ticker_df
def fetch_growth_metrics(tickers: List[str]) -> pd.DataFrame:
    """
    Fetches quarterly financials for each ticker and calculates YoY Revenue and Net Income growth.
    Returns a DataFrame indexed by Ticker with 'sales_growth' and 'eps_growth' columns.
    """
    growth_data = []
    
    # We'll use a single-threaded approach for now as yfinance rate limits 
    # more aggressively on financials than history.
    for ticker in tickers:
        try:
            tkr = yf.Ticker(ticker)
            fin = tkr.quarterly_financials
            
            sales_growth = 0.0
            eps_growth = 0.0
            
            if fin is not None and not fin.empty:
                # Sales Growth (YoY)
                if 'Total Revenue' in fin.index:
                    rev = fin.loc['Total Revenue']
                    if len(rev) >= 5:
                        sales_growth = (rev.iloc[0] / rev.iloc[4]) - 1
                
                # Net Income Growth (YoY) - serving as proxy for EPS growth
                if 'Net Income Common Stockholders' in fin.index:
                    ni = fin.loc['Net Income Common Stockholders']
                    if len(ni) >= 5:
                        eps_growth = (ni.iloc[0] / ni.iloc[4]) - 1
            
            growth_data.append({
                'Ticker': ticker,
                'sales_growth': sales_growth,
                'eps_growth': eps_growth
            })
        except Exception as e:
            print(f"Growth fetch error for {ticker}: {e}")
            growth_data.append({'Ticker': ticker, 'sales_growth': 0.0, 'eps_growth': 0.0})
            
    return pd.DataFrame(growth_data).set_index('Ticker')
