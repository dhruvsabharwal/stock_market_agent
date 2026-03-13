import pandas as pd
import numpy as np

def identify_green_line(df: pd.DataFrame, min_consolidation_days: int = 63) -> pd.DataFrame:
    """
    Identifies a "Blue Sky" Green Line: A 1-Year (252-day) high that has held for
    at least 3 months (63 trading days) without being breached.
    """
    # Use strict 1-year high (252 days)
    rolling_peak = df['close'].rolling(window=252, min_periods=126).max()
    peak_shift = rolling_peak.shift(min_consolidation_days)
    
    # Is the peak stable? (No higher highs in the last 63 days)
    is_valid_consolidation = (rolling_peak == peak_shift) & (rolling_peak > 0)
    
    return pd.DataFrame({
        'is_green_line_valid': is_valid_consolidation.fillna(False), 
        'green_line_pivot': rolling_peak
    }, index=df.index)

def calculate_setup_score(df: pd.DataFrame) -> pd.DataFrame:
    out_df = df.copy()
    
    gl_info = identify_green_line(out_df)
    out_df['is_base'] = gl_info['is_green_line_valid']       
    out_df['pivot_point'] = gl_info['green_line_pivot']
    
    # Raw metrics for reporting
    out_df['raw_tightness'] = (out_df['atr_14'] / out_df['atr_63'])
    out_df['raw_ignition'] = (out_df['close'] - out_df['rolling_low_252d']) / out_df['rolling_low_252d'].replace(0, np.nan)
    out_df['raw_rs_distance'] = (out_df['rs_high_1y'] - out_df['rs_line']) / out_df['rs_high_1y'].replace(0, np.nan)
    
    # Edge Flags (TIGERs)
    out_df['edge_1_tight'] = (out_df['raw_tightness'] < 0.90).astype(int)
    out_df['edge_2_ignite'] = (out_df['raw_ignition'] >= 0.25).astype(int)
    out_df['edge_3_rs'] = (out_df['raw_rs_distance'] <= 0.03).astype(int)
    
    # Edge 4: EMA/SMA Trend Alignment
    out_df['edge_4_trend'] = ((out_df['close'] > out_df['ema_21']) & 
                              (out_df['ema_21'] > out_df['sma_50']) & 
                              (out_df['sma_50'] > out_df['sma_200'])).astype(int)
    
    # Edge 5: Fundamental Growth Weight (+1 point if Sales Growth > 25%)
    # Note: sales_growth column is injected in generate_trade_report.py
    out_df['edge_5_funda'] = (out_df.get('sales_growth', 0) > 0.25).astype(int)
    
    out_df['setup_score'] = (
        out_df['edge_1_tight'] + 
        out_df['edge_2_ignite'] + 
        out_df['edge_3_rs'] + 
        out_df['edge_4_trend'] + 
        out_df['edge_5_funda']
    )
    
    return out_df
