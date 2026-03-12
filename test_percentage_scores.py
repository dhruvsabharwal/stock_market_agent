import asyncio
import pandas as pd
import sys
sys.path.append('.')

from agents.combined_agent.combined import analyze_multiple_stocks

async def test_percentage_scores():
    """Test the percentage scoring"""
    tickers = ["AAPL", "MSFT"]
    
    print("Running analysis with percentage scores...")
    results = await analyze_multiple_stocks(tickers, batch_size=10)
    
    # Convert to DataFrame
    df = pd.json_normalize(results.values())
    
    # Score columns (raw and percentage)
    score_cols = [
        # Fundamental
        'profitability_score', 'profitability_pct',
        'growth_score', 'growth_pct',
        'valuation_score', 'valuation_pct',
        'financial_health_score', 'financial_health_pct',
        'cashflow_quality_score', 'cashflow_quality_pct',
        'fundamental_total_score', 'fundamental_total_pct',
        # Technical
        'ma_score', 'ma_pct', 'ma_distance_bonus',
        'macd_score', 'macd_pct', 'macd_crossover_bonus',
        'rsi_score', 'rsi_pct',
        'vwma_score', 'vwma_pct', 'vwma_volume_bonus',
        'technical_total_score', 'technical_total_pct',
        # Combined
        'combined_score'
    ]
    
    # Select available columns
    available_cols = ['ticker', 'longName'] + [col for col in score_cols if col in df.columns]
    
    # Display
    print("\n" + "="*100)
    print("PERCENTAGE SCORING SUMMARY")
    print("="*100)
    
    for _, row in df.iterrows():
        print(f"\n{row['ticker']} - {row.get('longName', 'N/A')}")
        print(f"\n  📊 FUNDAMENTAL BREAKDOWN:")
        print(f"     Profitability:    {row.get('profitability_score', 0):.1f}/25  ({row.get('profitability_pct', 0):.1f}%)")
        print(f"     Growth:           {row.get('growth_score', 0):.1f}/20  ({row.get('growth_pct', 0):.1f}%)")
        print(f"     Valuation:        {row.get('valuation_score', 0):.1f}/20  ({row.get('valuation_pct', 0):.1f}%)")
        print(f"     Financial Health: {row.get('financial_health_score', 0):.1f}/20  ({row.get('financial_health_pct', 0):.1f}%)")
        print(f"     Cash Flow:        {row.get('cashflow_quality_score', 0):.1f}/15  ({row.get('cashflow_quality_pct', 0):.1f}%)")
        print(f"     ─────────────────────────────────")
        print(f"     TOTAL:            {row.get('fundamental_total_score', 0):.1f}/100 ({row.get('fundamental_total_pct', 0):.1f}%)")
        
        print(f"\n  📈 TECHNICAL BREAKDOWN:")
        print(f"     Moving Averages:  {row.get('ma_score', 0):.1f}/30  ({row.get('ma_pct', 0):.1f}%) +{row.get('ma_distance_bonus', 0):.0f} bonus")
        print(f"     MACD:             {row.get('macd_score', 0):.1f}/20  ({row.get('macd_pct', 0):.1f}%) +{row.get('macd_crossover_bonus', 0):.0f} bonus")
        print(f"     RSI:              {row.get('rsi_score', 0):.1f}/20  ({row.get('rsi_pct', 0):.1f}%)")
        print(f"     VWMA:             {row.get('vwma_score', 0):.1f}/15  ({row.get('vwma_pct', 0):.1f}%) +{row.get('vwma_volume_bonus', 0):.0f} bonus")
        print(f"     ─────────────────────────────────")
        print(f"     TOTAL:            {row.get('technical_total_score', 0):.1f}/100 ({row.get('technical_total_pct', 0):.1f}%)")
        
        print(f"\n  🎯 COMBINED SCORE: {row.get('combined_score', 0):.1f}/100")
        print(f"     (70% Fundamental + 30% Technical)")
    
    # Save
    df[available_cols].to_csv('combined_stocks/scores_with_percentages.csv', index=False)
    print(f"\n✅ CSV saved: combined_stocks/scores_with_percentages.csv")
    print(f"   Total columns: {len(available_cols)}")

if __name__ == "__main__":
    asyncio.run(test_percentage_scores())
