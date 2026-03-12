"""
Technical Analysis Agent
A comprehensive class for technical analysis of stocks using multiple indicators.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import asyncio
import sys
import warnings
import logging

# Nuclear silence for all warnings and loggers
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'

# Add parent directory to path to allow imports from agents
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from agents.utils import StockDataProvider, Config
except ImportError:
    # Fallback for when running from different contexts
    from utils import StockDataProvider, Config

class TechnicalAnalysis:
    """
    A comprehensive technical analysis class that provides various technical indicators
    and analysis capabilities for stock evaluation.
    """
    
    def __init__(self):
        """
        Initialize the TechnicalAnalysis class.
        """
        pass
    
    def get_yfinance_data(self, ticker):
        """
        Retrieve stock data from yfinance.
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            yfinance.Ticker: yfinance ticker object
        """
        return StockDataProvider.get_ticker(ticker)


    async def get_stock_data(self, ticker, period="1y"):
        """
        Fetch historical price data from yfinance.
        """
        try:
            stock = self.get_yfinance_data(ticker)
            # Use to_thread to prevent blocking the event loop during network I/O
            df = await asyncio.to_thread(StockDataProvider.get_history, ticker, period=period)
            
            if df is None or df.empty:
                return {"error": "EMPTY_DATA", "message": f"No data found for {ticker}"}, None
                
            return df, stock
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "Too Many Requests" in err_msg or "Rate Limit" in err_msg:
                return {"error": "RATE_LIMIT_HIT", "message": err_msg}, None
            return {"error": "FETCH_ERROR", "message": err_msg}, None
    
    def calculate_moving_averages(self, df):
        """
        Calculate 20, 50, and 200-day SMAs.
        
        Args:
            df (DataFrame): Price data with 'Close' column
        
        Returns:
            DataFrame: Original data with MA columns added
        """
        df = df.copy()
        
        # Simple Moving Averages
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        
        # Exponential Moving Averages (for reference)
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        
        return df
    
    def analyze_moving_averages(self, df):
        """
        Analyze moving average signals.
        
        Args:
            df (DataFrame): Price data with MA columns
        
        Returns:
            dict: MA analysis with signals and scores
        """
        if df is None or len(df) < 1:
            return None
        
        latest = df.iloc[-1]
        current_price = latest.get('Close', 0)
        
        # Check conditions
        above_50 = current_price > latest['SMA_50']
        above_200 = current_price > latest['SMA_200']
        golden_cross = latest['SMA_50'] > latest['SMA_200']
        
        # Distance from MAs (as percentage)
        dist_20 = ((current_price - latest['SMA_20']) / latest['SMA_20'] * 100) if pd.notna(latest['SMA_20']) else None
        dist_50 = ((current_price - latest['SMA_50']) / latest['SMA_50'] * 100) if pd.notna(latest['SMA_50']) else None
        dist_200 = ((current_price - latest['SMA_200']) / latest['SMA_200'] * 100) if pd.notna(latest['SMA_200']) else None
        
        # Score (0-3)
        score = sum([above_50, above_200, golden_cross])
        
        # Determine entry quality
        entry_signal = "AVOID"
        if score == 3:
            if -2 <= dist_20 <= 5:  # Price within 2% below to 5% above 20-day MA
                entry_signal = "BUY - Ideal pullback entry"
            elif dist_20 > 5:
                entry_signal = "WAIT - Extended above MA"
            else:
                entry_signal = "STRONG BUY - At MA support"
        elif score == 2:
            entry_signal = "WAIT - Partial trend"
        
        return {
            'current_price': current_price,
            'SMA_20': latest['SMA_20'],
            'SMA_50': latest['SMA_50'],
            'SMA_200': latest['SMA_200'],
            'above_50': above_50,
            'above_200': above_200,
            'golden_cross': golden_cross,
            'dist_from_20': dist_20,
            'dist_from_50': dist_50,
            'dist_from_200': dist_200,
            'score': score,
            'max_score': 3,
            'signal': entry_signal
        }
    
    def calculate_macd(self, df, fast=Config.MACD_FAST, slow=Config.MACD_SLOW, signal=Config.MACD_SIGNAL):
        """
        Calculate MACD indicator.
        
        Args:
            df (DataFrame): Price data
            fast (int): Fast EMA period
            slow (int): Slow EMA period
            signal (int): Signal line EMA period
        
        Returns:
            DataFrame: Data with MACD columns
        """
        df = df.copy()
        
        # Calculate EMAs
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
        
        # MACD line
        df['MACD'] = ema_fast - ema_slow
        
        # Signal line
        df['MACD_signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        
        # Histogram
        df['MACD_hist'] = df['MACD'] - df['MACD_signal']
        
        return df
    
    def analyze_macd(self, df):
        """
        Analyze MACD signals.
        
        Args:
            df (DataFrame): Price data with MACD columns
        
        Returns:
            dict: MACD analysis with signals and scores
        """
        if df is None or df.empty:
            return None
        
        if len(df) < 2:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Current values
        macd = latest.get('MACD')
        signal = latest.get('MACD_signal')
        hist = latest.get('MACD_hist')

        if macd is None or signal is None:
            return None
        
        # Check conditions
        bullish_crossover = macd > signal
        above_zero = macd > 0
        hist_positive = hist > 0
        hist_growing = hist > prev['MACD_hist']
        
        # Check for recent crossover (within last 10 days)
        recent_cross = False
        crossover_days_ago = None
        for i in range(1, min(11, len(df))):
            if df.iloc[-i]['MACD'] > df.iloc[-i]['MACD_signal'] and df.iloc[-i-1]['MACD'] <= df.iloc[-i-1]['MACD_signal']:
                recent_cross = True
                crossover_days_ago = i
                break
        
        # Score (0-3)
        score = sum([bullish_crossover, above_zero, hist_positive])
        
        # Determine signal
        if bullish_crossover and recent_cross and crossover_days_ago <= 5:
            signal_str = f"STRONG BUY - Fresh crossover ({crossover_days_ago} days ago)"
        elif bullish_crossover and hist_growing:
            signal_str = "BUY - Momentum building"
        elif bullish_crossover:
            signal_str = "HOLD - Bullish but watch for weakening"
        else:
            signal_str = "AVOID - Bearish momentum"
        
        return {
            'MACD': macd,
            'MACD_signal': signal,
            'MACD_hist': hist,
            'bullish_crossover': bullish_crossover,
            'above_zero': above_zero,
            'histogram_positive': hist_positive,
            'histogram_growing': hist_growing,
            'recent_crossover': recent_cross,
            'crossover_days_ago': crossover_days_ago,
            'score': score,
            'max_score': 3,
            'signal': signal_str
        }
    
    def calculate_rsi(self, df, period=Config.RSI_PERIOD):
        """
        Calculate RSI (Relative Strength Index) using Wilder's smoothing method.
        
        Args:
            df (DataFrame): Price data
            period (int): RSI period (default 14)
        
        Returns:
            DataFrame: Data with RSI column
        """
        df = df.copy()
        
        # Calculate price changes
        delta = df['Close'].diff()
        
        # Separate gains and losses
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        # Use Wilder's smoothing (equivalent to EMA with alpha = 1/period)
        # This is the standard RSI calculation used by TradingView, etc.
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        
        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        return df
    
    def analyze_rsi(self, df):
        """
        Analyze RSI signals.
        
        Args:
            df (DataFrame): Price data with RSI column
        
        Returns:
            dict: RSI analysis with signals and scores
        """
        if df is None or len(df) < 1:
            return None
        
        latest = df.iloc[-1]
        rsi = latest.get('RSI')
        
        if rsi is None or pd.isna(rsi):
            return None
        
        # Check conditions
        not_overbought = rsi < 70
        above_50 = rsi > 50
        in_sweet_spot = 40 < rsi < 70
        
        # Score (0-3)
        score = sum([not_overbought, above_50, in_sweet_spot])
        
        # Determine signal
        if 45 <= rsi <= 60:
            signal = "STRONG BUY - Ideal zone"
        elif 40 <= rsi <= 70:
            signal = "BUY - Acceptable zone"
        elif rsi > 70:
            signal = "WAIT - Overbought, pullback likely"
        elif rsi < 40:
            signal = "CAUTION - Oversold, wait for bounce confirmation"
        else:
            signal = "NEUTRAL"
        
        # Classification
        if rsi > 70:
            zone = "Overbought"
        elif rsi < 30:
            zone = "Oversold"
        elif rsi > 50:
            zone = "Bullish"
        else:
            zone = "Bearish"
        
        return {
            'RSI': rsi,
            'not_overbought': not_overbought,
            'above_50': above_50,
            'in_sweet_spot': in_sweet_spot,
            'zone': zone,
            'score': score,
            'max_score': 3,
            'signal': signal
        }
    
    def calculate_vwma(self, df, period=Config.VWMA_PERIOD):
        """
        Calculate Volume Weighted Moving Average.
        
        Args:
            df (DataFrame): Price data with Close and Volume
            period (int): VWMA period
        
        Returns:
            DataFrame: Data with VWMA column
        """
        df = df.copy()
        
        # VWMA = Sum(Price * Volume) / Sum(Volume)
        df['VWMA'] = (df['Close'] * df['Volume']).rolling(window=period).sum() / \
                      df['Volume'].rolling(window=period).sum()
        
        # Also calculate average volume
        df['Avg_Volume'] = df['Volume'].rolling(window=period).mean()
        
        return df
    
    def analyze_vwma(self, df):
        """
        Analyze VWMA and volume patterns.
        
        Args:
            df (DataFrame): Price data with VWMA column
        
        Returns:
            dict: VWMA analysis with signals and scores
        """
        if df is None or len(df) < 1:
            return None
        
        latest = df.iloc[-1]
        current_price = latest.get('Close', 0)
        vwma = latest.get('VWMA')

        if vwma is None or pd.isna(vwma):
            return None
        
        # Check conditions
        above_vwma = current_price > vwma
        vwma_rising = df['VWMA'].iloc[-5:].is_monotonic_increasing if len(df) >= 5 else False
        
        # Volume analysis (last 10 days)
        recent_data = df.tail(10).copy()
        recent_data['Price_Change'] = recent_data['Close'].pct_change()
        
        # Volume on up days vs down days
        up_days = recent_data[recent_data['Price_Change'] > 0]
        down_days = recent_data[recent_data['Price_Change'] < 0]
        
        avg_vol_up = up_days['Volume'].mean() if len(up_days) > 0 else 0
        avg_vol_down = down_days['Volume'].mean() if len(down_days) > 0 else 0
        
        volume_pattern_bullish = avg_vol_up > avg_vol_down
        
        # Current volume vs average
        current_vol = latest['Volume']
        avg_vol = latest['Avg_Volume']
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
        
        # Score (0-3)
        score = sum([above_vwma, vwma_rising, volume_pattern_bullish])
        
        # Determine signal
        if above_vwma and vwma_rising and volume_pattern_bullish:
            signal = "STRONG BUY - Institutional accumulation"
        elif above_vwma and volume_pattern_bullish:
            signal = "BUY - Good volume support"
        elif above_vwma:
            signal = "HOLD - Above VWMA but watch volume"
        else:
            signal = "AVOID - Below VWMA, weak support"
        
        return {
            'current_price': current_price,
            'VWMA': vwma,
            'above_vwma': above_vwma,
            'vwma_rising': vwma_rising,
            'volume_pattern_bullish': volume_pattern_bullish,
            'current_volume': current_vol,
            'avg_volume': avg_vol,
            'volume_ratio': vol_ratio,
            'avg_volume_up_days': avg_vol_up,
            'avg_volume_down_days': avg_vol_down,
            'score': score,
            'max_score': 3,
            'signal': signal
        }
    
    def calculate_support_resistance(self, df, lookback=60):
        """
        Identify support and resistance levels.
        
        Args:
            df (DataFrame): Price data
            lookback (int): Days to look back for levels
        
        Returns:
            DataFrame: Data with support/resistance columns
        """
        df = df.copy()
        
        # Rolling highs and lows
        df['Resistance_20'] = df['High'].rolling(window=20).max()
        df['Support_20'] = df['Low'].rolling(window=20).min()
        
        df['Resistance_60'] = df['High'].rolling(window=60).max()
        df['Support_60'] = df['Low'].rolling(window=60).min()
        
        return df
    
    def find_pivot_points(self, df, window=5):
        """
        Find pivot highs and lows (more sophisticated support/resistance).
        
        Args:
            df (DataFrame): Price data
            window (int): Window for pivot detection
        
        Returns:
            tuple: (resistance_levels, support_levels)
        """
        if df is None or len(df) < window * 2:
            return [], []
        
        resistance_levels = []
        support_levels = []
        
        # Find pivot highs (resistance)
        for i in range(window, len(df) - window):
            if df['High'].iloc[i] == df['High'].iloc[i-window:i+window+1].max():
                resistance_levels.append(df['High'].iloc[i])
        
        # Find pivot lows (support)
        for i in range(window, len(df) - window):
            if df['Low'].iloc[i] == df['Low'].iloc[i-window:i+window+1].min():
                support_levels.append(df['Low'].iloc[i])
        
        # Get unique levels (cluster similar levels within 1%)
        def cluster_levels(levels):
            if not levels:
                return []
            levels = sorted(levels)
            clustered = [levels[0]]
            for level in levels[1:]:
                if level / clustered[-1] > 1.01:  # More than 1% different
                    clustered.append(level)
            return clustered[-5:]  # Return last 5 most recent levels
        
        return cluster_levels(resistance_levels), cluster_levels(support_levels)
    
    def analyze_support_resistance(self, df):
        """
        Analyze current price relative to support/resistance.
        
        Args:
            df (DataFrame): Price data with support/resistance columns
        
        Returns:
            dict: Support/resistance analysis
        """
        if df is None or df.empty:
            return None
        
        latest = df.iloc[-1]
        current_price = latest['Close']
        
        # Get recent support/resistance
        resistance_levels, support_levels = self.find_pivot_points(df, window=5)
        
        # Find nearest levels
        resistance_above = [r for r in resistance_levels if r > current_price]
        support_below = [s for s in support_levels if s < current_price]
        
        nearest_resistance = min(resistance_above) if resistance_above else latest['Resistance_60']
        nearest_support = max(support_below) if support_below else latest['Support_60']
        
        # Calculate distances
        dist_to_resistance = ((nearest_resistance - current_price) / current_price * 100)
        dist_to_support = ((current_price - nearest_support) / current_price * 100)
        
        # Check if at support
        at_support = dist_to_support < 2  # Within 2% of support
        near_resistance = dist_to_resistance < 2  # Within 2% of resistance
        
        # Determine position quality
        if at_support and dist_to_resistance > 5:
            signal = "STRONG BUY - At support with room to resistance"
        elif at_support:
            signal = "BUY - At support but near resistance"
        elif near_resistance:
            signal = "WAIT - Near resistance, likely pullback"
        elif 3 < dist_to_support < 8 and dist_to_resistance > 5:
            signal = "BUY - Good risk/reward setup"
        else:
            signal = "NEUTRAL - No clear level nearby"
        
        # Score based on position
        score = 0
        if at_support:
            score += 2
        elif dist_to_support < 5:
            score += 1
        if dist_to_resistance > 5:
            score += 1
        
        return {
            'current_price': current_price,
            'nearest_resistance': nearest_resistance,
            'nearest_support': nearest_support,
            'dist_to_resistance_%': dist_to_resistance,
            'dist_to_support_%': dist_to_support,
            'at_support': at_support,
            'near_resistance': near_resistance,
            'resistance_levels': resistance_levels[-3:] if len(resistance_levels) >= 3 else resistance_levels,
            'support_levels': support_levels[-3:] if len(support_levels) >= 3 else support_levels,
            'score': score,
            'max_score': 3,
            'signal': signal
        }
    
    async def analyze_market_trend(self, period="6mo"):
        """
        Analyze overall market trend using S&P 500 (SPY).
        
        Args:
            period (str): Data period for analysis
        
        Returns:
            dict: Market analysis
        """
        spy_data,_ = await self.get_stock_data("SPY", period=period)

        if spy_data is None or spy_data.empty:
            return None
        
        spy_data = self.calculate_moving_averages(spy_data)
        latest = spy_data.iloc[-1]
        
        above_50 = latest['Close'] > latest['SMA_50']
        above_200 = latest['Close'] > latest['SMA_200']
        golden_cross = latest['SMA_50'] > latest['SMA_200']
        
        # Try to get VIX (volatility)
        try:
            vix = StockDataProvider.get_ticker("^VIX")
            vix_data = vix.history(period="5d")
            vix_level = vix_data['Close'].iloc[-1] if not vix_data.empty else None
            low_vix = vix_level < 20 if vix_level else None
        except:
            vix_level = None
            low_vix = None
        
        # Score
        checks = [above_50, above_200, golden_cross]
        if low_vix is not None:
            checks.append(low_vix)
        score = sum(checks)
        total = len(checks)
        
        # Determine market state
        if score >= total * 0.75:
            state = "BULL"
            recommendation = "Aggressive - Take 5-7 positions"
        elif score >= total * 0.5:
            state = "NEUTRAL"
            recommendation = "Selective - Take 3-5 positions"
        else:
            state = "BEAR"
            recommendation = "Defensive - Maximum 2 positions, mostly cash"
        
        return {
            'SPY_price': latest['Close'],
            'SPY_50MA': latest['SMA_50'],
            'SPY_200MA': latest['SMA_200'],
            'above_50': above_50,
            'above_200': above_200,
            'golden_cross': golden_cross,
            'VIX': vix_level,
            'low_vix': low_vix,
            'score': score,
            'total_checks': total,
            'market_state': state,
            'recommendation': recommendation
        }, spy_data
    
    def compare_relative_strength(self, ticker_df, benchmark_df, period="6mo"):
        """
        Compare stock performance vs benchmark.
        
        Args:
            ticker_df (DataFrame): Stock data
            benchmark_df (DataFrame): Benchmark data
            period (str): Comparison period
        
        Returns:
            dict: Relative strength analysis
        """
        stock_data = ticker_df
        bench_data = benchmark_df

        if stock_data is None or bench_data is None or stock_data.empty or bench_data.empty:
            return None
        
        # Calculate returns
        stock_return = ((stock_data['Close'].iloc[-1] / stock_data['Close'].iloc[0]) - 1) * 100
        bench_return = ((bench_data['Close'].iloc[-1] / bench_data['Close'].iloc[0]) - 1) * 100
        
        outperformance = stock_return - bench_return
        
        # Determine signal
        if outperformance > 10:
            signal = "STRONG - Significant outperformance"
        elif outperformance > 0:
            signal = "GOOD - Outperforming market"
        elif outperformance > -10:
            signal = "WEAK - Slight underperformance"
        else:
            signal = "AVOID - Significant underperformance"
        
        return {
            'stock_return_%': stock_return,
            'benchmark_return_%': bench_return,
            'outperformance_%': outperformance,
            'outperforming': outperformance > 0,
            'signal': signal
        }
    
    async def complete_technical_analysis(self, df, period="1y"):
        """
        Run complete technical analysis on a stock.
        
        Args:
            df (DataFrame): Stock data
            period (str): Data period for analysis
        
        Returns:
            dict: Complete technical analysis results
        """
        if df is None or df.empty:
            return None
        
        # Calculate all indicators
        df = self.calculate_moving_averages(df)
        df = self.calculate_macd(df)
        df = self.calculate_rsi(df)
        df = self.calculate_vwma(df)
        df = self.calculate_support_resistance(df)
        
        # Analyze each indicator
        ma_analysis = self.analyze_moving_averages(df)
        macd_analysis = self.analyze_macd(df)
        rsi_analysis = self.analyze_rsi(df)
        vwma_analysis = self.analyze_vwma(df)
        sr_analysis = self.analyze_support_resistance(df)
        
        # Market context
        market_analysis, spy_df = await self.analyze_market_trend()
        rel_strength = self.compare_relative_strength(df, spy_df)
        
        # Calculate total score safely
        # Use .get() and defaults in case any analysis returned None
        def get_score(analysis):
            if analysis is None: return 0
            return analysis.get('score', 0)

        total_score = (
            get_score(ma_analysis) +
            get_score(macd_analysis) +
            get_score(rsi_analysis) +
            get_score(vwma_analysis) +
            get_score(sr_analysis)
        )
        max_score = 15
        
        # Determine overall signal
        score_pct = (total_score / max_score) * 100
        
        if score_pct >= 67:
            overall_signal = "🟢 STRONG BUY"
            action = "Enter position"
        elif score_pct >= 50:
            overall_signal = "🟡 WAIT"
            action = "Need more confirmation"
        else:
            overall_signal = "🔴 AVOID"
            action = "Poor setup"
        
        # Compile results
        results = {
            'analysis_date': datetime.now().strftime('%Y-%m-%d'),
            'current_price': df.iloc[-1]['Close'] if not df.empty else 0,
            'moving_averages': ma_analysis or {'score': 0, 'signal': 'N/A'},
            'macd': macd_analysis or {'score': 0, 'signal': 'N/A'},
            'rsi': rsi_analysis or {'score': 0, 'signal': 'N/A'},
            'vwma': vwma_analysis or {'score': 0, 'signal': 'N/A'},
            'support_resistance': sr_analysis or {'score': 0, 'signal': 'N/A'},
            'market_context': market_analysis,
            'relative_strength': rel_strength,
            'total_score': total_score,
            'max_score': max_score,
            'score_percentage': score_pct,
            'overall_technical_signal': overall_signal,
            'action': action,
        }
        
        return results
    
    def print_technical_report(self, results):
        """
        Print formatted technical analysis report.
        
        Args:
            results (dict): Results from complete_technical_analysis()
        """
        if results is None:
            print("No results to display")
            return
        
        print(f"\n{'='*70}")
        print(f"TECHNICAL ANALYSIS REPORT")
        print(f"Date: {results['analysis_date']}")
        print(f"Current Price: ${results['current_price']:.2f}")
        print(f"{'='*70}\n")
        
        # Market Context
        if results['market_context']:
            mc = results['market_context']
            print("MARKET CONTEXT (S&P 500):")
            print(f"  State: {mc['market_state']}")
            print(f"  SPY: ${mc['SPY_price']:.2f}")
            print(f"  Above 50-day MA: {'✓' if mc['above_50'] else '✗'}")
            print(f"  Above 200-day MA: {'✓' if mc['above_200'] else '✗'}")
            print(f"  Golden Cross: {'✓' if mc['golden_cross'] else '✗'}")
            if mc['VIX']:
                print(f"  VIX: {mc['VIX']:.1f}")
            print(f"  Recommendation: {mc['recommendation']}\n")
        
        # Relative Strength
        if results['relative_strength']:
            rs = results['relative_strength']
            print("RELATIVE STRENGTH:")
            print(f"  Stock Return (6mo): {rs['stock_return_%']:.1f}%")
            print(f"  S&P 500 Return: {rs['benchmark_return_%']:.1f}%")
            print(f"  Outperformance: {rs['outperformance_%']:.1f}%")
            print(f"  Signal: {rs['signal']}\n")
        
        # Moving Averages
        ma = results['moving_averages']
        print("1. MOVING AVERAGES:")
        print(f"  20-day MA: ${ma['SMA_20']:.2f} ({ma['dist_from_20']:.1f}% from price)")
        print(f"  50-day MA: ${ma['SMA_50']:.2f} ({ma['dist_from_50']:.1f}% from price)")
        print(f"  200-day MA: ${ma['SMA_200']:.2f} ({ma['dist_from_200']:.1f}% from price)")
        print(f"  Above 50-day: {'✓' if ma['above_50'] else '✗'}")
        print(f"  Above 200-day: {'✓' if ma['above_200'] else '✗'}")
        print(f"  Golden Cross: {'✓' if ma['golden_cross'] else '✗'}")
        print(f"  Score: {ma['score']}/{ma['max_score']}")
        print(f"  Signal: {ma['signal']}\n")
        
        # MACD
        macd = results['macd']
        print("2. MACD:")
        print(f"  MACD: {macd['MACD']:.2f}")
        print(f"  Signal: {macd['MACD_signal']:.2f}")
        print(f"  Histogram: {macd['MACD_hist']:.2f}")
        print(f"  Bullish Crossover: {'✓' if macd['bullish_crossover'] else '✗'}")
        print(f"  Above Zero: {'✓' if macd['above_zero'] else '✗'}")
        if macd['recent_crossover']:
            print(f"  Recent Crossover: {macd['crossover_days_ago']} days ago")
        print(f"  Score: {macd['score']}/{macd['max_score']}")
        print(f"  Signal: {macd['signal']}\n")
        
        # RSI
        rsi = results['rsi']
        print("3. RSI:")
        print(f"  RSI: {rsi['RSI']:.1f}")
        print(f"  Zone: {rsi['zone']}")
        print(f"  Not Overbought: {'✓' if rsi['not_overbought'] else '✗'}")
        print(f"  Above 50: {'✓' if rsi['above_50'] else '✗'}")
        print(f"  Score: {rsi['score']}/{rsi['max_score']}")
        print(f"  Signal: {rsi['signal']}\n")
        
        # VWMA
        vwma = results['vwma']
        print("4. VWMA & VOLUME:")
        print(f"  VWMA: ${vwma['VWMA']:.2f}")
        print(f"  Above VWMA: {'✓' if vwma['above_vwma'] else '✗'}")
        print(f"  VWMA Rising: {'✓' if vwma['vwma_rising'] else '✗'}")
        print(f"  Volume Ratio: {vwma['volume_ratio']:.2f}x")
        print(f"  Score: {vwma['score']}/{vwma['max_score']}")
        print(f"  Signal: {vwma['signal']}\n")
        
        # Support/Resistance
        sr = results['support_resistance']
        print("5. SUPPORT & RESISTANCE:")
        print(f"  Nearest Resistance: ${sr['nearest_resistance']:.2f} ({sr['dist_to_resistance_%']:.1f}% away)")
        print(f"  Nearest Support: ${sr['nearest_support']:.2f} ({sr['dist_to_support_%']:.1f}% away)")
        print(f"  At Support: {'✓' if sr['at_support'] else '✗'}")
        print(f"  Near Resistance: {'✓' if sr['near_resistance'] else '✗'}")
        print(f"  Score: {sr['score']}/{sr['max_score']}")
        print(f"  Signal: {sr['signal']}\n")
        
        # Summary
        print(f"{'='*70}")
        print(f"OVERALL SIGNAL: {results['overall_technical_signal']}")
        print(f"TOTAL SCORE: {results['total_score']}/{results['max_score']} ({results['score_percentage']:.1f}%)")
        print(f"ACTION: {results['action']}")
        print(f"{'='*70}\n")
