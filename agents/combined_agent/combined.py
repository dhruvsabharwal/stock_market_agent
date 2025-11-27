import asyncio
from typing import List, Dict
import os, sys
# Add the parent directory (agents folder) to the path
current_dir = os.path.dirname(os.path.abspath('__file__'))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
from technical_analysis_agent.technical_analysis import TechnicalAnalysis


async def analyze_stock(ticker: str, f_an: FundamentalAnalysis, t_an: TechnicalAnalysis) -> Dict:
    """Analyze a single stock - both fundamental and technical"""
    try:
        # Get stock data
        df, stock = await t_an.get_stock_data(ticker)
        
        # Run both analyses in parallel
        fun, tech = await asyncio.gather(
            f_an.compute_yfinance_metrics(stock),
            t_an.complete_technical_analysis(df),
            return_exceptions=True
        )
        
        # Handle exceptions
        if isinstance(fun, Exception):
            print(f"Error in fundamental analysis for {ticker}: {fun}")
            fun = {}
        if isinstance(tech, Exception):
            print(f"Error in technical analysis for {ticker}: {tech}")
            tech = {}
        
        # Combine results
        complete_ = {**fun, **tech, 'ticker': ticker}
        return complete_
        
    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        return {'ticker': ticker, 'error': str(e)}


async def analyze_multiple_stocks(tickers: List[str], batch_size: int = 10) -> Dict[str, Dict]:
    """
    Analyze multiple stocks in parallel with batching to avoid overwhelming APIs
    
    Args:
        tickers: List of stock tickers
        batch_size: Number of stocks to process concurrently (default 10)
    
    Returns:
        Dictionary mapping ticker to analysis results
    """
    f_an = FundamentalAnalysis()
    t_an = TechnicalAnalysis()
    
    results = {}
    
    # Process in batches to avoid rate limits
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        print(f"Processing batch {i//batch_size + 1}: {batch}")
        
        # Run batch in parallel
        batch_results = await asyncio.gather(
            *[analyze_stock(ticker, f_an, t_an) for ticker in batch],
            return_exceptions=True
        )
        
        # Store results
        for ticker, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                results[ticker] = {'ticker': ticker, 'error': str(result)}
            else:
                results[ticker] = result
        
        print(f"Completed batch {i//batch_size + 1}")
    
    return results

