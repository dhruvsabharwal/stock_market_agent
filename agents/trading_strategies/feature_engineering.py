import pandas as pd
import numpy as np

def calculate_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()

def calculate_ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()

def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """
    Calculate Average True Range (ATR).
    """
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window, min_periods=window).mean()
    return atr

def calculate_relative_strength(stock_close: pd.Series, spy_close: pd.Series) -> pd.Series:
    """
    Calculate the Relative Strength (RS) line vs SPY.
    RS = Stock Price / SPY Price
    """
    return stock_close / spy_close

def calculate_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """
    Standard Relative Strength Index (RSI).
    """
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def add_features(df: pd.DataFrame, spy_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Adds all technical indicators required for the High-Alpha Growth strategy.
    """
    df = df.copy()
    
    # Price Moving Averages
    df['sma_50'] = calculate_sma(df['close'], 50)
    df['sma_150'] = calculate_sma(df['close'], 150)
    df['sma_200'] = calculate_sma(df['close'], 200)
    df['ema_21'] = calculate_ema(df['close'], 21)
    
    # RSI for momentum "Power Zone" filtering
    df['rsi_14'] = calculate_rsi(df['close'], 14)
    
    # Blue Sky Filters: 1-year (252-day) high and rolling low
    df['rolling_high_252d'] = df['close'].rolling(window=252, min_periods=126).max()
    df['rolling_low_252d'] = df['close'].rolling(window=252, min_periods=126).min()
    
    # ATR
    df['atr_14'] = calculate_atr(df, 14)
    df['atr_63'] = calculate_atr(df, 63)
    
    # Volume Metrics
    df['volume_sma_20'] = calculate_sma(df['volume'], 20)
    df['volume_sma_50'] = calculate_sma(df['volume'], 50) # Added for liquidity filtering
    # Peak volume in last year for context
    df['volume_high_1y'] = df['volume'].rolling(window=252, min_periods=126).max()
    
    # True RS: Stock Price vs Benchmark (SPY)
    if spy_df is not None and not spy_df.empty:
        # Align indexes to ensure matching days
        common_idx = df.index.intersection(spy_df.index)
        spy_close = spy_df.loc[common_idx, 'close']
        stock_close = df.loc[common_idx, 'close']
        
        # RS Line = Stock Price / SPY Price
        df.loc[common_idx, 'rs_line'] = stock_close / spy_close
        
        # 1-year high of RS line (52-week high)
        df['rs_high_1y'] = df['rs_line'].rolling(window=252, min_periods=126).max()
        
        # Simplified Market Regime: Closing Price > 21 EMA AND > 50 SMA
        df['spy_ema_21'] = calculate_ema(spy_close, 21)
        df['spy_sma_50'] = calculate_sma(spy_close, 50)
        
        # Rule: Both conditions must be met for a "Green Light"
        ma_alignment = (spy_close > df['spy_ema_21']) & (spy_close > df['spy_sma_50'])
        
        # Final Market Regime flag
        df['market_uptrend'] = ma_alignment.astype(int)
    else:
        df['rs_line'] = 1.0
        df['rs_high_1y'] = 1.0
        df['market_uptrend'] = 1
    
    return df
