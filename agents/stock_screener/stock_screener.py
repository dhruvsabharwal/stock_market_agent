import pandas as pd
import requests
import numpy as np
import os
import concurrent.futures
from typing import List, Dict
import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class StockScreener:
    """
    Highly optimized Stock Screener module implementing the TraderLion Base Breakout 
    strategy and the TIGERS framework using a two-stage funnel approach.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("FMP_key")
        if not self.api_key:
            raise ValueError("FMP API Key not found. Please provide it or set FMP_key in .env")
            
        self.base_url = "https://financialmodelingprep.com/api/v3"
        
        # Configurable Thresholds (Easily adjustable class attributes)
        self.eps_growth_threshold = 0.25 # 25%
        self.revenue_growth_threshold = 0.25 # 25%
        self.min_price = 10.0
        self.min_avg_volume = 500000
        
    def _fetch_fmp_json(self, endpoint: str, params: Dict = None) -> List:
        """Helper to fetch JSON data from FMP API."""
        if params is None:
            params = {}
        params['apikey'] = self.api_key
        # Ensure endpoint doesn't start with a slash and base_url handles the versioning
        endpoint = endpoint.lstrip("/")
        url = f"https://financialmodelingprep.com/api/v3/{endpoint}"
        
        # If 'stable' is already in the endpoint, use it directly without v3
        if "stable/" in endpoint:
            url = f"https://financialmodelingprep.com/{endpoint}"

        try:
            response = requests.get(url, params=params)
            if response.status_code == 403:
                # Silent failure for 403 to avoid cluttering logs during mass screening
                return {"error": "forbidden", "message": "Restricted endpoint"}
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # print(f"Error fetching {url}: {e}")
            return []

    def _get_local_tickers(self) -> List[str]:
        """Load tickers from all_tickers.txt and filter for US stocks."""
        try:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            file_path = os.path.join(root_dir, "all_tickers.txt")
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    tickers = [line.strip() for line in f if line.strip() and not line.strip().endswith(".NS")]
                    return list(set(tickers))
        except Exception as e:
            print(f"Error loading local tickers: {e}")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"]

    def get_growth_shortlist(self) -> List[str]:
        """
        Stage 1: Fundamental Screening.
        """
        print("Stage 1: Fundamental Screening...")
        
        # Try FMP company-screener (stable)
        candidates = self._fetch_fmp_json("stable/company-screener", params={
            'priceMoreThan': self.min_price,
            'volumeMoreThan': self.min_avg_volume,
            'isEtf': 'false',
            'isActivelyTrading': 'true',
            'limit': 1000
        })
        
        if isinstance(candidates, dict) and (candidates.get("error") == "forbidden" or "Restricted" in str(candidates)):
            print("FMP Screener restricted. Falling back to local ticker list and manual growth verification...")
            tickers = self._get_local_tickers()
        elif not candidates:
            print("FMP Screener returned no results. Using local fallback.")
            tickers = self._get_local_tickers()
        else:
            tickers = [c['symbol'] for c in candidates if 'symbol' in c]
            
        print(f"Verifying growth for {len(tickers)} candidates...")
        
        shortlist = []
        growth_metrics_store = {}

        def check_growth(ticker):
            # Using stable endpoint
            growth_data = self._fetch_fmp_json(f"stable/financial-growth/{ticker}", params={'limit': 1, 'period': 'quarter'})
            if isinstance(growth_data, list) and len(growth_data) > 0:
                latest_growth = growth_data[0]
                eps_g = latest_growth.get('epsgrowth', 0)
                rev_g = latest_growth.get('revenueGrowth', 0)
                
                if eps_g >= self.eps_growth_threshold and rev_g >= self.revenue_growth_threshold:
                    return {
                        'symbol': ticker,
                        'eps_growth': eps_g,
                        'rev_growth': rev_g
                    }
            return None

        # Process in smaller batches to avoid hitting rate limits too hard
        batch_size = 100
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i+batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(check_growth, batch))
                for res in results:
                    if res:
                        shortlist.append(res['symbol'])
                        growth_metrics_store[res['symbol']] = {
                            'YoY_EPS_Growth_%': round(res['eps_growth'] * 100, 2),
                            'YoY_Revenue_Growth_%': round(res['rev_growth'] * 100, 2)
                        }
            if len(shortlist) > 200: # Found enough candidates to proceed
                break
                
        print(f"Growth screening complete. {len(shortlist)} tickers in shortlist.")
        self.growth_metrics_store = growth_metrics_store
        return shortlist

    def fetch_technical_data(self, tickers: List[str]) -> pd.DataFrame:
        """
        Stage 2: Bulk Technical Fetch.
        """
        print(f"Stage 2: Bulk Technical Fetch for {len(tickers)} tickers + SPY...")
        all_tickers = list(set(tickers + ['SPY']))
        
        chunk_size = 50
        ticker_chunks = [all_tickers[i:i + chunk_size] for i in range(0, len(all_tickers), chunk_size)]
        
        combined_data = []
        
        def fetch_chunk(chunk):
            ticker_str = ",".join(chunk)
            # Use stable/historical-price-full
            endpoint = f"stable/historical-price-full/{ticker_str}"
            data = self._fetch_fmp_json(endpoint, params={'timeseries': 100})
            
            if isinstance(data, dict):
                if 'historicalStockList' in data:
                    return data['historicalStockList']
                elif 'historical' in data:
                    return [data]
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            chunk_results = list(executor.map(fetch_chunk, ticker_chunks))
            
        for chunk in chunk_results:
            for stock_data in chunk:
                symbol = stock_data.get('symbol')
                historical = stock_data.get('historical', [])
                if historical:
                    df = pd.DataFrame(historical)
                    df['symbol'] = symbol
                    combined_data.append(df)
                    
        if not combined_data:
            return pd.DataFrame()
            
        full_df = pd.concat(combined_data)
        full_df['date'] = pd.to_datetime(full_df['date'])
        full_df = full_df.sort_values(['symbol', 'date']).reset_index(drop=True)
        return full_df

    def apply_technical_filters(self, technical_df: pd.DataFrame) -> pd.DataFrame:
        """
        Stage 3: Local Vectorized Technical Screening.
        """
        print("Stage 3: Local Vectorized Technical Screening...")
        
        # Prepare SPY data
        spy_df = technical_df[technical_df['symbol'] == 'SPY'].set_index('date')['close']
        
        final_candidates = []
        grouped = technical_df[technical_df['symbol'] != 'SPY'].groupby('symbol')
        
        for ticker, df in grouped:
            if len(df) < 50: continue
            
            df = df.set_index('date')
            
            # 1. True Relative Strength Slope (R)
            df['rs_ratio'] = df['close'] / spy_df
            def get_slope(y):
                if len(y) < 20: return 0
                x = np.arange(len(y))
                return np.polyfit(x, y, 1)[0]
            
            df['rs_slope_20d'] = df['rs_ratio'].rolling(window=20).apply(get_slope, raw=True)
            
            # 2. Tight Areas & Volume
            df['daily_spread'] = (df['high'] - df['low']) / df['close']
            df['volume_sma_50'] = df['volume'].rolling(window=50).mean()
            
            # 3. Moving Averages
            df['ema_10'] = df['close'].ewm(span=10).mean()
            df['ema_21'] = df['close'].ewm(span=21).mean()
            
            # 4. Signal Generation
            last_day = df.iloc[-1]
            max_high_20d = df['high'].rolling(window=20).max().iloc[-1]
            dist_to_high_20d = (max_high_20d - last_day['close']) / last_day['close']
            
            is_tight = (last_day['daily_spread'] < 0.03) and (last_day['volume'] < 0.5 * last_day['volume_sma_50'])
            is_near_high = dist_to_high_20d < 0.02
            
            setup_flag = None
            if is_tight and is_near_high:
                setup_flag = 'T1_Breakout'
            elif (abs((last_day['close'] - last_day['ema_10']) / last_day['ema_10']) < 0.01 or 
                  abs((last_day['close'] - last_day['ema_21']) / last_day['ema_21']) < 0.01) and \
                 (last_day['volume'] < last_day['volume_sma_50']):
                setup_flag = 'Pullback_Entry'
                
            if setup_flag:
                growth_info = self.growth_metrics_store.get(ticker, {})
                max_spread_5d = df['daily_spread'].rolling(window=5).max().iloc[-1]
                
                final_candidates.append({
                    'Ticker': ticker,
                    'YoY_EPS_Growth_%': growth_info.get('YoY_EPS_Growth_%', 0),
                    'YoY_Revenue_Growth_%': growth_info.get('YoY_Revenue_Growth_%', 0),
                    'True_RS_Slope_20D': round(last_day['rs_slope_20d'], 6),
                    'Max_Spread_5D_%': round(max_spread_5d * 100, 2),
                    'Volume_vs_50SMA_%': round((last_day['volume'] / last_day['volume_sma_50']) * 100, 2),
                    'Dist_to_21EMA_%': round(((last_day['close'] - last_day['ema_21']) / last_day['ema_21']) * 100, 2),
                    'Setup_Type_Flag': setup_flag
                })
                
        return pd.DataFrame(final_candidates)

    def run(self):
        """Execute the full screening funnel."""
        try:
            shortlist = self.get_growth_shortlist()
            if not shortlist:
                print("No growth candidates found.")
                return
                
            tech_data = self.fetch_technical_data(shortlist)
            if tech_data.empty:
                print("No technical data fetched.")
                return
                
            screened_df = self.apply_technical_filters(tech_data)
            
            if not screened_df.empty:
                output_file = "screened_candidates.csv"
                screened_df.to_csv(output_file, index=False)
                print(f"\nScreener completed. {len(screened_df)} candidates found.")
                print(f"Results saved to {output_file}")
                print(screened_df.to_string(index=False))
            else:
                print("\nNo candidates passed the technical filters today.")
        except Exception as e:
            print(f"Error during screening execution: {e}")

if __name__ == "__main__":
    screener = StockScreener()
    screener.run()
