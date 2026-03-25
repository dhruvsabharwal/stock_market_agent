import asyncio
import sys
from typing import List
from agents.base_breakout_strategy.base_breakout import BaseBreakoutAnalyzer

async def analyze_ticker_async(ticker: str):
    """
    Async wrapper to run the synchronous BaseBreakoutAnalyzer in a separate thread.
    """
    print(f"\n>>> Starting Parallel Analysis for: {ticker}")
    analyzer = BaseBreakoutAnalyzer(ticker)
    
    # Run the heavy computation/networking in a thread to keep asyncio loop free
    success = await asyncio.to_thread(analyzer.run_analysis)
    
    if success:
        report = await asyncio.to_thread(analyzer.generate_report)
        return {"ticker": ticker, "success": True, "report": report, "score": analyzer.score}
    else:
        print(f"!!! Analysis failed for {ticker}")
        return {"ticker": ticker, "success": False, "report": None, "score": 0}

async def analyze_multiple_stocks(tickers: List[str]):
    """
    Runs analysis for a list of tickers in parallel.
    """
    tasks = [analyze_ticker_async(ticker) for ticker in tickers]
    results = await asyncio.gather(*tasks)
    
    print("\n" + "="*50)
    print("PARALLEL ANALYSIS SUMMARY")
    print("="*50)
    for res in results:
        status = "PASSED" if res['success'] else "FAILED"
        score = f"Score: {res['score']}/11" if res['success'] else "N/A"
        print(f"{res['ticker']:<10} | {status:<8} | {score}")
    print("="*50)
    
    return results

async def main():
    # If tickers provided via CLI arguments, use them; otherwise ask for input
    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1:]]
    else:
        user_input = input("Enter tickers separated by commas (e.g. AAPL, NVDA, TSLA): ")
        tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

    if not tickers:
        print("No tickers provided. Exiting.")
        return

    await analyze_multiple_stocks(tickers)

if __name__ == "__main__":
    asyncio.run(main())
