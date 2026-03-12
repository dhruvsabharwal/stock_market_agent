import asyncio
import os
import sys
import json
from typing import List, Dict, Any
from mcp.server.fastmcp import FastMCP

# Add the project root to the path so we can import agents
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

try:
    from agents.combined_agent.combined import analyze_stock, analyze_multiple_stocks
    from agents.fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
    from agents.technical_analysis_agent.technical_analysis import TechnicalAnalysis
except ImportError as e:
    print(f"Error importing agents: {e}")
    sys.exit(1)

# Initialize FastMCP server
mcp = FastMCP("StockMarketAgent")

@mcp.tool()
async def analyze_stock_full(ticker: str) -> str:
    """
    Perform a comprehensive (fundamental + technical) analysis of a single stock.
    Returns a JSON string with scores and detailed metrics.
    
    Args:
        ticker: The stock ticker symbol (e.g., 'AAPL', 'MSFT').
    """
    f_an = FundamentalAnalysis()
    t_an = TechnicalAnalysis()
    
    result = await analyze_stock(ticker, f_an, t_an)
    
    # Clean up results for JSON serialization if needed
    # (The combined analysis result is usually a dict)
    return json.dumps(result, indent=2, default=str)

@mcp.tool()
async def analyze_multiple_stocks_batch(tickers: List[str], batch_size: int = 5) -> str:
    """
    Perform comprehensive analysis for a list of stock tickers in batches.
    
    Args:
        tickers: List of stock ticker symbols.
        batch_size: Number of stocks to process concurrently (default: 5).
    """
    results = await analyze_multiple_stocks(tickers, batch_size=batch_size)
    return json.dumps(results, indent=2, default=str)

@mcp.tool()
async def get_fundamental_analysis(ticker: str) -> str:
    """
    Retrieve only the fundamental analysis and metrics for a stock.
    
    Args:
        ticker: The stock ticker symbol.
    """
    f_an = FundamentalAnalysis()
    result = await f_an.analyze_single_stock(ticker)
    return json.dumps(result, indent=2, default=str)

@mcp.tool()
async def get_technical_analysis(ticker: str) -> str:
    """
    Retrieve only the technical analysis, indicators, and signals for a stock.
    
    Args:
        ticker: The stock ticker symbol.
    """
    t_an = TechnicalAnalysis()
    df, stock = await t_an.get_stock_data(ticker)
    
    if df is None:
        return f"Error: Could not fetch technical data for {ticker}"
        
    result = await t_an.complete_technical_analysis(df)
    return json.dumps(result, indent=2, default=str)

if __name__ == "__main__":
    # Run the server using stdio transport
    mcp.run(transport='stdio')
