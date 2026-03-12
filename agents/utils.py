import yfinance as yf
import numpy as np
import pandas as pd
import os
import logging
import warnings

# Nuclear silence for all warnings and loggers
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Configuration for financial analysis agents."""
    # Fundamental Analysis
    YEARS_HISTORY = 5
    MIN_YEARS_FOR_AVG = 3
    
    # Technical Analysis
    RSI_PERIOD = 14
    VWMA_PERIOD = 20
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    
    # Directories
    DATA_DIR = "yfin"
    COMBINED_DIR = "combined_csv"
    DETAILED_DIR = "detailed_csv"

    # LLM Configuration
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    LLM_MODEL = "gemini-2.5-flash"

class StockDataProvider:
    """Provider for stock data using yfinance."""
    
    @staticmethod
    def get_ticker(ticker: str) -> yf.Ticker:
        """Get yfinance Ticker object."""
        return yf.Ticker(ticker)
    
    @staticmethod
    def get_history(ticker: str, period: str = "1y") -> pd.DataFrame:
        """Get historical price data. Raises exception on failure or empty data."""
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            raise ValueError(f"YFinance returned empty data for {ticker}")
        return df

class MathUtils:
    """Common mathematical utility functions."""
    
    @staticmethod
    def calculate_cagr(start_value, end_value, periods):
        """Calculate Compound Annual Growth Rate (CAGR)."""
        if start_value <= 0 or end_value <= 0 or periods <= 0:
            return 0
        return ((end_value / start_value) ** (1 / periods) - 1) * 100
    
    @staticmethod
    def get_means(num_list, min_n=2):
        """Calculate mean with minimum number requirement."""
        num_list = [c for c in num_list if MathUtils.null_check(c)]
        mean_cal = np.mean(num_list)
        
        if MathUtils.null_check(mean_cal) and len(num_list) >= min_n:
            return mean_cal
        else:
            return -1
            
    @staticmethod
    def null_check(c):
        """Check if value is not null/NaN."""
        if c is not np.nan and c is not None and pd.notnull(c):
            return True
        else:
            return False

    @staticmethod
    def ensure_directories(dirs: list):
        """Ensure output directories exist."""
        for d in dirs:
            os.makedirs(d, exist_ok=True)
