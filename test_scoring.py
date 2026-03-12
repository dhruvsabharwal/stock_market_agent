import asyncio
import pandas as pd
import sys
sys.path.append('.')

from agents.combined_agent.combined import analyze_multiple_stocks

async def test_scoring():
    """Test the scoring integration"""
    tickers = ["AAPL", "MSFT", "GOOGL"]
    
    print("Running analysis with scoring...")
    results = await analyze_multiple_stocks(tickers, batch_size=10)
    
    # Convert to DataFrame
    df = pd.json_normalize(results.values())
    
    # Define columns to include
    fundamental_cols = ["ticker","longName","ROCE (3yr avg)","NFAT (3yr avg)","SSGR",
    "Av NPM (over 3 years)","Av NFA/T (over 3 years)",
    "Av Dep%NFA (over 3 years)","Av Retention ratio (over 3 years)",
    "Interest coverage","tax %","cCFO/cPAT","p/a",
    "p/e","EY","Earnings Growth 5yr cagr","Sales Growth 5yr cagr",
    "PEG","p/s","d/e","d/e_market","FCF%","FCF/CFO",
    "3-5yr Average ROA (%)","3-5yr Average ROE (%)"]
    
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
    
    # Score columns
    score_cols = [
        'profitability_score', 'growth_score', 'valuation_score', 
        'financial_health_score', 'cashflow_quality_score', 'fundamental_total_score',
        'ma_score', 'ma_distance_bonus', 'macd_score', 'macd_crossover_bonus',
        'rsi_score', 'vwma_score', 'vwma_volume_bonus', 'technical_total_score',
        'combined_score'
    ]
    
    # Select columns that exist
    available_cols = [col for col in fundamental_cols + technical_cols + score_cols if col in df.columns]
    
    # Save with scores
    df_output = df[available_cols]
    df_output.to_csv('combined_stocks/test_with_scores.csv', index=False)
    
    print("\n" + "="*80)
    print("SCORING SUMMARY")
    print("="*80)
    for _, row in df_output.iterrows():
        print(f"\n{row['ticker']} - {row.get('longName', 'N/A')}")
        print(f"  Fundamental Breakdown:")
        print(f"    - Profitability: {row.get('profitability_score', 0):.1f}/25")
        print(f"    - Growth: {row.get('growth_score', 0):.1f}/20")
        print(f"    - Valuation: {row.get('valuation_score', 0):.1f}/20")
        print(f"    - Financial Health: {row.get('financial_health_score', 0):.1f}/20")
        print(f"    - Cash Flow Quality: {row.get('cashflow_quality_score', 0):.1f}/15")
        print(f"    - TOTAL: {row.get('fundamental_total_score', 0):.1f}/100")
        print(f"  Technical Breakdown:")
        print(f"    - MA Score: {row.get('ma_score', 0):.1f}/30 (+{row.get('ma_distance_bonus', 0):.1f} bonus)")
        print(f"    - MACD Score: {row.get('macd_score', 0):.1f}/20 (+{row.get('macd_crossover_bonus', 0):.1f} bonus)")
        print(f"    - RSI Score: {row.get('rsi_score', 0):.1f}/20")
        print(f"    - VWMA Score: {row.get('vwma_score', 0):.1f}/15 (+{row.get('vwma_volume_bonus', 0):.1f} bonus)")
        print(f"    - TOTAL: {row.get('technical_total_score', 0):.1f}/100")
        print(f"  COMBINED SCORE: {row.get('combined_score', 0):.1f}/100")
    
    print(f"\n✅ CSV saved to: combined_stocks/test_with_scores.csv")
    print(f"   Columns: {len(available_cols)} (including {len([c for c in score_cols if c in available_cols])} score columns)")

if __name__ == "__main__":
    asyncio.run(test_scoring())
