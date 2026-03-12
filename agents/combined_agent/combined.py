import os, sys
import asyncio
import warnings
import contextlib
import logging
from typing import List, Dict

# Nuclear silence for all warnings and loggers
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'

@contextlib.contextmanager
def silence_all():
    """Context manager to suppress all stdout and stderr."""
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

# Add the parent directory (agents folder) to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from agents.utils import Config, MathUtils
    from agents.fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
    from agents.technical_analysis_agent.technical_analysis import TechnicalAnalysis
except ImportError:
    # Fallback
    from utils import Config, MathUtils
    from fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
    from technical_analysis_agent.technical_analysis import TechnicalAnalysis


def safe_float(v, default=0):
    """Convert value to float safely, handling None, NaN, and strings."""
    try:
        if v is None: return default
        # Handle cases where v might be a pandas Series or something else
        if hasattr(v, 'iloc'): v = v.iloc[0]
        import math
        f = float(v)
        if math.isnan(f): return default
        return f
    except (ValueError, TypeError):
        return default


def calculate_fundamental_score_with_breakdown(fund_result):
    """
    Comprehensive fundamental scoring with intermediate scores.
    Returns: (total_score, breakdown_dict)
    """
    breakdown = {}
    
    # ===== 1. PROFITABILITY & EFFICIENCY (25 points) =====
    prof_score = 0
    roce_3yr = safe_float(fund_result.get('ROCE (3yr avg)', 0))
    if roce_3yr > 25: prof_score += 7
    elif roce_3yr > 20: prof_score += 5
    elif roce_3yr > 15: prof_score += 3
    
    roe_avg = safe_float(fund_result.get('3-5yr Average ROE (%)', 0))
    if roe_avg > 25: prof_score += 7
    elif roe_avg > 20: prof_score += 5
    elif roe_avg > 15: prof_score += 3
    
    roa_avg = safe_float(fund_result.get('3-5yr Average ROA (%)', 0))
    if roa_avg > 15: prof_score += 4
    elif roa_avg > 10: prof_score += 2
    
    npm = safe_float(fund_result.get('Av NPM (over 3 years)', 0))
    if npm > 15: prof_score += 4
    elif npm > 10: prof_score += 2
    
    nfat = safe_float(fund_result.get('NFAT (3yr avg)', 0))
    if nfat > 5: prof_score += 3
    elif nfat > 3: prof_score += 2
    
    breakdown['profitability_score'] = prof_score
    
    # ===== 2. GROWTH (20 points) =====
    growth_score = 0
    eps_growth = safe_float(fund_result.get('Earnings Growth 5yr cagr', 0))
    if eps_growth > 20: growth_score += 10
    elif eps_growth > 15: growth_score += 7
    elif eps_growth > 10: growth_score += 5
    elif eps_growth > 5: growth_score += 3
    
    sales_growth = safe_float(fund_result.get('Sales Growth 5yr cagr', 0))
    if sales_growth > 20: growth_score += 7
    elif sales_growth > 15: growth_score += 5
    elif sales_growth > 10: growth_score += 3
    
    ssgr = safe_float(fund_result.get('SSGR', 0))
    if ssgr > sales_growth: growth_score += 3
    elif ssgr > 0: growth_score += 1
    
    breakdown['growth_score'] = growth_score
    
    # ===== 3. VALUATION (20 points) =====
    valuation_score = 0
    pe = safe_float(fund_result.get('p/e', float('inf')), default=float('inf'))
    if 0 < pe < 15: valuation_score += 6
    elif 0 < pe < 25: valuation_score += 4
    elif 0 < pe < 35: valuation_score += 2
    
    peg = safe_float(fund_result.get('PEG', float('inf')), default=float('inf'))
    if 0 < peg < 1: valuation_score += 6
    elif 0 < peg < 1.5: valuation_score += 4
    elif 0 < peg < 2: valuation_score += 2
    
    ey = safe_float(fund_result.get('EY', 0))
    if ey > 10: valuation_score += 4
    elif ey > 7: valuation_score += 3
    elif ey > 5: valuation_score += 1
    
    ps = safe_float(fund_result.get('p/s', float('inf')), default=float('inf'))
    if 0 < ps < 2: valuation_score += 4
    elif 0 < ps < 4: valuation_score += 2
    
    breakdown['valuation_score'] = valuation_score
    
    # ===== 4. FINANCIAL HEALTH (20 points) =====
    health_score = 0
    de_market = safe_float(fund_result.get('d/e_market', 100), default=100)
    if de_market < 0.3: health_score += 6
    elif de_market < 0.5: health_score += 4
    elif de_market < 1.0: health_score += 2
    
    interest_cov = safe_float(fund_result.get('Interest coverage', 0))
    if interest_cov > 10: health_score += 6
    elif interest_cov > 5: health_score += 4
    elif interest_cov > 3: health_score += 2
    
    tax_pct = safe_float(fund_result.get('tax %', 0))
    if 15 < tax_pct < 30: health_score += 3
    elif tax_pct > 0: health_score += 1
    
    retention = safe_float(fund_result.get('Av Retention ratio (over 3 years)', 0))
    if retention > 70: health_score += 3
    elif retention > 50: health_score += 2
    
    dep_nfa = safe_float(fund_result.get('Av Dep%NFA (over 3 years)', 100), default=100)
    if dep_nfa < 20: health_score += 2
    elif dep_nfa < 40: health_score += 1
    
    breakdown['financial_health_score'] = health_score
    
    # ===== 5. CASH FLOW QUALITY (15 points) =====
    cashflow_score = 0
    ccfo_cpat = safe_float(fund_result.get('cCFO/cPAT', 0))
    if ccfo_cpat > 1.2: cashflow_score += 6
    elif ccfo_cpat > 1.0: cashflow_score += 4
    elif ccfo_cpat > 0.8: cashflow_score += 2
    
    fcf_pct = safe_float(fund_result.get('FCF%', -100), default=-100)
    if fcf_pct > 80: cashflow_score += 5
    elif fcf_pct > 50: cashflow_score += 3
    elif fcf_pct > 0: cashflow_score += 1
    
    fcf_cfo = safe_float(fund_result.get('FCF/CFO', 0))
    if fcf_cfo > 0.8: cashflow_score += 4
    elif fcf_cfo > 0.5: cashflow_score += 2
    
    breakdown['cashflow_quality_score'] = cashflow_score
    
    # Total
    total_score = min(prof_score + growth_score + valuation_score + health_score + cashflow_score, 100)
    breakdown['fundamental_total_score'] = total_score
    
    
    # Add percentage versions for easy interpretation
    breakdown['profitability_pct'] = round((prof_score / 25) * 100, 1)
    breakdown['growth_pct'] = round((growth_score / 20) * 100, 1)
    breakdown['valuation_pct'] = round((valuation_score / 20) * 100, 1)
    breakdown['financial_health_pct'] = round((health_score / 20) * 100, 1)
    breakdown['cashflow_quality_pct'] = round((cashflow_score / 15) * 100, 1)
    breakdown['fundamental_total_pct'] = round(total_score, 1)
    
    return total_score, breakdown


def calculate_technical_score_with_breakdown(tech_result):
    """
    Comprehensive technical scoring with intermediate scores.
    Returns: (total_score, breakdown_dict)
    """
    breakdown = {}
    total_score = 0
    max_possible = 0
    
    # ===== 1. MOVING AVERAGES =====
    ma = tech_result.get('moving_averages', {})
    ma_score = safe_float(ma.get('score', 0))
    ma_max = safe_float(ma.get('max_score', 3), default=3)
    
    ma_normalized = (ma_score / ma_max) * 30 if ma_max > 0 else 0
    total_score += ma_normalized
    max_possible += 30
    
    # Distance bonus
    dist_20 = safe_float(ma.get('dist_from_20', 0))
    dist_bonus = 5 if -5 < dist_20 < 5 else 0
    total_score += dist_bonus
    max_possible += 5
    
    breakdown['ma_score'] = ma_normalized
    breakdown['ma_distance_bonus'] = dist_bonus
    
    # ===== 2. MACD =====
    macd = tech_result.get('macd', {})
    macd_score = safe_float(macd.get('score', 0))
    macd_max = safe_float(macd.get('max_score', 3), default=3)
    
    macd_normalized = (macd_score / macd_max) * 20 if macd_max > 0 else 0
    total_score += macd_normalized
    max_possible += 20
    
    # Crossover bonus
    crossover_bonus = 0
    if macd.get('recent_crossover') and safe_float(macd.get('crossover_days_ago', 100)) <= 5:
        crossover_bonus = 5
    total_score += crossover_bonus
    max_possible += 5
    
    breakdown['macd_score'] = macd_normalized
    breakdown['macd_crossover_bonus'] = crossover_bonus
    
    # ===== 3. RSI =====
    rsi_data = tech_result.get('rsi', {})
    rsi_score = safe_float(rsi_data.get('score', 0))
    rsi_max = safe_float(rsi_data.get('max_score', 3), default=3)
    
    rsi_normalized = (rsi_score / rsi_max) * 20 if rsi_max > 0 else 0
    total_score += rsi_normalized
    max_possible += 20
    
    breakdown['rsi_score'] = rsi_normalized
    
    # ===== 4. VWMA =====
    vwma = tech_result.get('vwma', {})
    vwma_score = safe_float(vwma.get('score', 0))
    vwma_max = safe_float(vwma.get('max_score', 3), default=3)
    
    vwma_normalized = (vwma_score / vwma_max) * 15 if vwma_max > 0 else 0
    total_score += vwma_normalized
    max_possible += 15
    
    # Volume pattern bonus
    volume_bonus = 5 if vwma.get('volume_pattern_bullish') else 0
    total_score += volume_bonus
    max_possible += 5
    
    breakdown['vwma_score'] = vwma_normalized
    breakdown['vwma_volume_bonus'] = volume_bonus
    
    # Normalize to 100
    final_score = (total_score / max_possible) * 100 if max_possible > 0 else 0
    breakdown['technical_total_score'] = min(final_score, 100)
    
    # Add percentage versions for easy interpretation
    breakdown['ma_pct'] = round((ma_normalized / 30) * 100, 1)
    breakdown['macd_pct'] = round((macd_normalized / 20) * 100, 1)
    breakdown['rsi_pct'] = round((rsi_normalized / 20) * 100, 1)
    breakdown['vwma_pct'] = round((vwma_normalized / 15) * 100, 1)
    breakdown['technical_total_pct'] = round(final_score, 1)
    
    
    return min(final_score, 100), breakdown


async def analyze_stock(ticker: str, f_an: FundamentalAnalysis, t_an: TechnicalAnalysis) -> Dict:
    """Analyze a single stock - both fundamental and technical with scoring"""
    try:
        # Get stock data
        fetch_res, stock = await t_an.get_stock_data(ticker)
        
        # Check if get_stock_data returned an error dict instead of a DataFrame
        if isinstance(fetch_res, dict) and 'error' in fetch_res:
            if fetch_res.get('error') == 'RATE_LIMIT_HIT':
                return {'ticker': ticker, 'error': 'RATE_LIMIT_HIT', 'message': fetch_res.get('message')}
            return {'ticker': ticker, 'error': fetch_res.get('message', 'Could not fetch data')}

        df = fetch_res
        if df is None or stock is None:
             return {'ticker': ticker, 'error': "Unspecified data fetch error"}

        # Run both analyses in parallel
        # Note: These calls now internalize asyncio.to_thread for true parallelism
        res_fun, res_tech = await asyncio.gather(
            f_an.compute_yfinance_metrics(stock),
            t_an.complete_technical_analysis(df),
            return_exceptions=True
        )
        
        # Handle exceptions and non-dict results
        fun = res_fun if isinstance(res_fun, dict) else {}
        tech = res_tech if isinstance(res_tech, dict) else {}

        # Check for rate limit error in fundamental analysis
        if fun.get('error') == 'RATE_LIMIT_HIT':
            return {'ticker': ticker, 'error': 'RATE_LIMIT_HIT', 'message': fun.get('message')}

        if isinstance(res_fun, Exception):
            fun['error_fundamental'] = str(res_fun)
        if isinstance(res_tech, Exception):
            tech['error_technical'] = str(res_tech)
        
        # Calculate scores with breakdowns
        fund_score, fund_breakdown = calculate_fundamental_score_with_breakdown(fun)
        tech_score, tech_breakdown = calculate_technical_score_with_breakdown(tech)
        
        # Combined score (70% Fundamental, 30% Technical)
        combined_score = (fund_score * 0.7) + (tech_score * 0.3)
        
        # Combine all results
        complete_ = {
            **fun, 
            **tech, 
            'ticker': ticker,
            **fund_breakdown,
            **tech_breakdown,
            'combined_score': combined_score
        }
        return complete_
        
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "Too Many Requests" in err_msg or "Rate Limit" in err_msg:
             return {'ticker': ticker, 'error': 'RATE_LIMIT_HIT', 'message': err_msg}
        return {'ticker': ticker, 'error': err_msg}

fundamental_cols = ["ticker","longName","ROCE (3yr avg)","NFAT (3yr avg)","SSGR",
"Av NPM (over 3 years)","Av NFA/T (over 3 years)",
"Av Dep%NFA (over 3 years)","Av Retention ratio (over 3 years)",
"Interest coverage","tax %","cCFO/cPAT","p/a",
"p/e","EY","Earnings Growth 5yr cagr","Sales Growth 5yr cagr",
"PEG","p/s","d/e","d/e_market","FCF%","FCF/CFO",
"3-5yr Average ROA (%)","3-5yr Average ROE (%)"
]

technical_cols = ['moving_averages.current_price', 'moving_averages.SMA_20', 'moving_averages.SMA_50', 'moving_averages.SMA_200',
 'moving_averages.above_50', 'moving_averages.above_200', 'moving_averages.golden_cross', 'moving_averages.dist_from_20',
 'moving_averages.dist_from_50', 'moving_averages.dist_from_200', 'moving_averages.score', 'moving_averages.max_score',
 'moving_averages.signal', 'macd.MACD', 'macd.MACD_signal', 'macd.MACD_hist', 'macd.bullish_crossover',
 'macd.above_zero', 'macd.histogram_positive', 'macd.histogram_growing', 'macd.recent_crossover',
 'macd.crossover_days_ago', 'macd.score', 'macd.max_score', 'macd.signal', 'rsi.RSI', 'rsi.not_overbought',
 'rsi.above_50', 'rsi.in_sweet_spot', 'rsi.zone', 'rsi.score', 'rsi.max_score', 'rsi.signal',
 'vwma.current_price', 'vwma.VWMA', 'vwma.above_vwma', 'vwma.vwma_rising',
 'vwma.volume_pattern_bullish', 'vwma.current_volume', 'vwma.avg_volume', 'vwma.volume_ratio',
 'vwma.avg_volume_up_days', 'vwma.avg_volume_down_days', 'vwma.score', 'vwma.max_score', 'vwma.signal']


score_cols = ['profitability_pct','growth_pct','valuation_pct','financial_health_pct',
'cashflow_quality_pct','ma_pct','ma_distance_bonus',
'macd_pct','macd_crossover_bonus','rsi_pct','vwma_pct','vwma_volume_bonus',
'fundamental_total_score','technical_total_score','combined_score']



async def analyze_multiple_stocks(
    tickers: List[str], 
    batch_size: int = 50,
    delay_between_batches: float = 1.0,
    filename: str = "combined_analysis_detailed.csv"
) -> Dict[str, Dict]:
    """
    Analyze multiple stocks in parallel with batching.
    Saves a detailed CSV and a filtered summary CSV.
    """
    import pandas as pd
    from pathlib import Path
    from tqdm import tqdm
    
    f_an = FundamentalAnalysis()
    t_an = TechnicalAnalysis()
    
    # Ensure output directories exist
    MathUtils.ensure_directories([Config.DATA_DIR, Config.COMBINED_DIR, Config.DETAILED_DIR])
    
    results = {}
    output_path = Path(Config.DETAILED_DIR) / filename
    summary_path = Path(Config.COMBINED_DIR) / filename
    
    # Process in batches with progress bar
    batch_idx = 0
    pbar = tqdm(total=len(tickers), desc="Processing", unit="stock")
    
    current_delay = delay_between_batches
    base_wait_time = 300 # 5 minutes starting point
    
    while batch_idx < len(tickers):
        batch = tickers[batch_idx:batch_idx + batch_size]
        
        retries = 0
        max_retries = 3
        success = False
        
        while retries < max_retries and not success:
            # Run batch in parallel
            batch_results = await asyncio.gather(
                *[analyze_stock(ticker, f_an, t_an) for ticker in batch],
                return_exceptions=True
            )
            
            # Check for rate limits in any of the results
            rate_limit_detected = False
            for result in batch_results:
                if isinstance(result, dict) and result.get('error') == 'RATE_LIMIT_HIT':
                    rate_limit_detected = True
                    break
            
            if rate_limit_detected:
                retries += 1
                # Permanently slow down future batches
                current_delay += 1.5 
                
                if retries < max_retries:
                    # Exponential backoff: 300s, 600s, 1200s
                    wait_seconds = base_wait_time * (2 ** (retries - 1))
                    from datetime import datetime, timedelta
                    resume_time = (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%H:%M:%S')
                    
                    print(f"\n⚠️ Rate limit hit! Wait time: {wait_seconds}s. Delay penalty: +1.5s (Total: {current_delay}s).")
                    print(f"🕒 Analysis will resume at {resume_time}. Hang tight...")
                    
                    await asyncio.sleep(wait_seconds)
                else:
                    print(f"\n❌ Max retries reached for batch starting at {batch_idx}. Moving to next batch.")
                    success = True 
            else:
                success = True
            
            # Store results
            for ticker, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    results[ticker] = {'ticker': ticker, 'error': str(result)}
                else:
                    results[ticker] = result
        
        pbar.update(len(batch))
        batch_idx += batch_size
        
        # Delay between batches (except for last batch)
        if batch_idx < len(tickers):
            await asyncio.sleep(current_delay)
            
    pbar.close()
            
    # Consolidated Save
    if results:
        try:
            analysis_data = [res for res in results.values() if isinstance(res, dict)]
            if not analysis_data:
                return results

            # Flatten and round
            final_df = pd.json_normalize(analysis_data)
            final_df = final_df.round(3)
            
            # Save detailed version
            final_df.to_csv(output_path, index=False)

            # Filter and save summary version
            all_target_cols = fundamental_cols + technical_cols + score_cols
            available_cols = [c for c in all_target_cols if c in final_df.columns]
            
            if available_cols:
                final_df[available_cols].to_csv(summary_path, index=False)
            else:
                # If no specific columns found, save whatever we have or log it
                final_df.to_csv(summary_path, index=False)
                
        except Exception as e:
            # Report error in console since we are in a notebook
            print(f"Error saving results: {str(e)}")
    
    return results
