import sys
import os
import asyncio
import pandas as pd

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
from agents.technical_analysis_agent.technical_analysis import TechnicalAnalysis
from agents.combined_agent.combined import analyze_multiple_stocks

async def test_fundamental():
    print("\nTesting Fundamental Analysis...")
    fa = FundamentalAnalysis()
    # Test async method
    result = await fa.analyze_single_stock("AAPL")
    if result and 'ROE' in result:
        print("✅ Fundamental Analysis (Async) Passed")
    else:
        print("❌ Fundamental Analysis (Async) Failed")
        print(result)

async def test_technical():
    print("\nTesting Technical Analysis...")
    ta = TechnicalAnalysis()
    # Test async method
    df, stock = await ta.get_stock_data("AAPL")
    if df is not None:
        result = await ta.complete_technical_analysis(df)
        if result and 'moving_averages' in result:
            print("✅ Technical Analysis (Async) Passed")
        else:
            print("❌ Technical Analysis (Async) Failed")
            print(result)
    else:
        print("❌ Technical Analysis Data Fetch Failed")

async def test_combined():
    print("\nTesting Combined Analysis...")
    tickers = ["AAPL", "MSFT"]
    results = await analyze_multiple_stocks(tickers, batch_size=2)
    if len(results) == 2 and 'AAPL' in results and 'MSFT' in results:
        print("✅ Combined Analysis Passed")
    else:
        print("❌ Combined Analysis Failed")
        print(results)

async def main():
    await test_fundamental()
    await test_technical()
    await test_combined()

if __name__ == "__main__":
    asyncio.run(main())
