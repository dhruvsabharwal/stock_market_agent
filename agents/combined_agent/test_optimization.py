"""
Quick test script for optimized analyze_multiple_stocks function
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.combined_agent.combined import analyze_multiple_stocks

async def test_optimization():
    """Test the optimized function with a small batch"""
    
    # Test with 3 stocks
    test_tickers = ['AAPL', 'MSFT', 'GOOGL']
    
    print("=" * 60)
    print("TEST 1: Fresh start with 3 stocks")
    print("=" * 60)
    
    results = await analyze_multiple_stocks(
        tickers=test_tickers,
        batch_size=2,
        delay_between_batches=0.5,
        fresh_start=True
    )
    
    print(f"\nAnalyzed {len(results)} stocks")
    print(f"Success: {sum(1 for r in results.values() if not r.get('error'))}")
    print(f"Errors: {sum(1 for r in results.values() if r.get('error'))}")
    
    print("\n" + "=" * 60)
    print("TEST 2: Resume mode (should skip already analyzed)")
    print("=" * 60)
    
    # Run again with resume (should skip all)
    results2 = await analyze_multiple_stocks(
        tickers=test_tickers,
        batch_size=2,
        delay_between_batches=0.5,
        fresh_start=False
    )
    
    print(f"\nSecond run analyzed: {len(results2)} stocks (should be 0)")

if __name__ == "__main__":
    asyncio.run(test_optimization())
