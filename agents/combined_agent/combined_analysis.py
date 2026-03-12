"""
Combined Fundamental and Technical Analysis Integration
This script combines both fundamental and technical analysis for comprehensive stock evaluation.
"""

import sys
import os
import pandas as pd
from datetime import datetime
import warnings
import logging

# Nuclear silence for all warnings and loggers
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'
# Add the project root to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Add the agents directories to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
from agents.technical_analysis_agent.technical_analysis import TechnicalAnalysis
from agents.llm_agent import LLMAgent
from agents.utils import Config, MathUtils
from agents.scoring import calculate_comprehensive_fundamental_score, calculate_comprehensive_technical_score


class CombinedAnalysis:
    """
    A comprehensive class that combines fundamental and technical analysis
    for complete stock evaluation and portfolio screening.
    """
    
    def __init__(self):
        """
        Initialize the CombinedAnalysis class.
        """
        self.fund_analyzer = FundamentalAnalysis()
        self.tech_analyzer = TechnicalAnalysis()
        self.llm_agent = LLMAgent()
    
    async def analyze_stock_comprehensive(self, ticker, portfolio_value=100000, risk_percent=1.5):
        """
        Perform comprehensive analysis combining fundamental and technical analysis.
        
        Args:
            ticker (str): Stock ticker symbol
            portfolio_value (float): Total portfolio value for position sizing
            risk_percent (float): Risk per trade percentage
            
        Returns:
            dict: Comprehensive analysis results
        """
        print(f"\n{'='*80}")
        print(f"COMPREHENSIVE ANALYSIS: {ticker}")
        print(f"{'='*80}")
        
        # Fundamental Analysis
        print("\n1. FUNDAMENTAL ANALYSIS:")
        print("-" * 50)
        fund_result = await self.fund_analyzer.analyze_single_stock(ticker)
        
        if not isinstance(fund_result, dict) or not fund_result:
            print(f"❌ Fundamental analysis failed for {ticker}")
            return None
        
        # Technical Analysis
        print("\n2. TECHNICAL ANALYSIS:")
        print("-" * 50)
        df, stock = await self.tech_analyzer.get_stock_data(ticker)
        if df is None:
            print(f"❌ Technical analysis failed for {ticker} (No Data)")
            return None
            
        tech_result = await self.tech_analyzer.complete_technical_analysis(df)
        
        if not tech_result:
            print(f"❌ Technical analysis failed for {ticker}")
            return None
        
        # Calculate scores
        fund_score = calculate_comprehensive_fundamental_score(fund_result)
        tech_score = calculate_comprehensive_technical_score(tech_result)
        
        # Weighted Final Score (50% Fundamental, 50% Technical)
        combined_score = (fund_score * 0.5) + (tech_score * 0.5)
        
        # Determine overall recommendation
        if fund_score >= 70 and tech_score >= 70:
            recommendation = "🟢 STRONG BUY"
            action = "Enter position - Excellent fundamentals and technicals"
        elif fund_score >= 70 and tech_score >= 50:
            recommendation = "🟡 BUY WITH CAUTION"
            action = "Good fundamentals, wait for better technical setup"
        elif fund_score >= 50 and tech_score >= 70:
            recommendation = "🟡 TRADING BUY"
            action = "Good technicals, but fundamental concerns (Short term)"
        else:
            recommendation = "🔴 AVOID"
            action = "Poor fundamentals and/or technicals"
        
        # Position sizing (if buy signal)
        position_info = None
        if tech_score >= 60:
            try:
                entry_price = tech_result['current_price']
                stop_loss = tech_result['support_resistance']['nearest_support'] * 0.97
                
                position = self.tech_analyzer.calculate_position_size(
                    portfolio_value, risk_percent, entry_price, stop_loss
                )
                targets = self.tech_analyzer.calculate_targets(entry_price, stop_loss)
                
                position_info = {
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'position': position,
                    'targets': targets
                }
            except Exception as e:
                print(f"Warning: Could not calculate position sizing: {e}")
        
        # Compile results
        results = {
            'ticker': ticker,
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'fundamental_analysis': fund_result,
            'technical_analysis': tech_result,
            'fundamental_score': fund_score,
            'technical_score': tech_score,
            'combined_score': combined_score,
            'recommendation': recommendation,
            'action': action,
            'position_info': position_info,
            'llm_summary': "Pending..." # Placeholder
        }
        
        # Print summary
        self._print_analysis_summary(results)
        
        return results
    
    def _calculate_fundamental_score(self, fund_result):
        """
        Calculate a fundamental score based on key metrics.
        Max Score: 100
        """
        score = 0
        
        # 1. Profitability (30 points)
        roe = fund_result.get('ROE', 0)
        roce = fund_result.get('ROCE', 0)
        npm = fund_result.get('NPM', 0)
        
        if roe > 20: score += 10
        elif roe > 15: score += 5
        
        if roce > 20: score += 10
        elif roce > 15: score += 5
        
        if npm > 15: score += 10
        elif npm > 10: score += 5
        
        # 2. Growth (25 points)
        eps_growth = fund_result.get('Earnings Growth 5yr cagr', 0)
        sales_growth = fund_result.get('Sales Growth 5yr cagr', 0)
        
        if eps_growth > 20: score += 15
        elif eps_growth > 10: score += 10
        elif eps_growth > 5: score += 5
        
        if sales_growth > 15: score += 10
        elif sales_growth > 10: score += 5
        
        # 3. Valuation (20 points)
        pe = fund_result.get('p/e', float('inf'))
        peg = fund_result.get('PEG', float('inf'))
        
        if 0 < pe < 25: score += 10
        elif 0 < pe < 40: score += 5
        
        if 0 < peg < 1.5: score += 10
        elif 0 < peg < 2.0: score += 5
        
        # 4. Financial Health (25 points)
        de = fund_result.get('d/e', 100)
        interest_cov = fund_result.get('Interest coverage', 0)
        cfo = fund_result.get('CFO', 0)
        
        if de < 0.5: score += 10
        elif de < 1.0: score += 5
        
        if interest_cov > 5: score += 10
        elif interest_cov > 3: score += 5
        
        if cfo > 0: score += 5
        
        return min(score, 100)

    def _calculate_technical_score(self, tech_result):
        """
        Calculate a technical score based on indicators.
        Max Score: 100
        """
        score = 0
        
        # 1. Trend (Moving Averages) (30 points)
        ma = tech_result.get('moving_averages', {})
        if ma.get('above_200'): score += 15
        if ma.get('above_50'): score += 10
        if ma.get('golden_cross'): score += 5
        
        # 2. Momentum (MACD) (25 points)
        macd = tech_result.get('macd', {})
        if macd.get('bullish_crossover'): score += 10
        if macd.get('above_zero'): score += 10
        if macd.get('histogram_positive'): score += 5
        
        # 3. RSI (20 points)
        rsi_data = tech_result.get('rsi', {})
        rsi = rsi_data.get('RSI', 50)
        if 40 <= rsi <= 70: score += 20 # Sweet spot
        elif rsi > 70: score += 5 # Overbought but strong momentum
        elif rsi < 30: score += 5 # Oversold bounce candidate
        
        # 4. Volume (VWMA) (25 points)
        vwma = tech_result.get('vwma', {})
        if vwma.get('above_vwma'): score += 15
        if vwma.get('vwma_rising'): score += 10
        
        return min(score, 100)
    
    def _print_analysis_summary(self, results):
        """
        Print a summary of the analysis results.
        """
        print(f"\n{'='*80}")
        print(f"ANALYSIS SUMMARY: {results['ticker']}")
        print(f"{'='*80}")
        
        fund = results['fundamental_analysis']
        
        print(f"Company: {fund.get('longName', 'N/A')}")
        print(f"Current Price: ${fund.get('Current Price', 0):.2f}")
        
        print(f"\nSCORES:")
        print(f"  Fundamental: {results['fundamental_score']:.1f}/100")
        print(f"  Technical: {results['technical_score']:.1f}/100")
        print(f"  Combined: {results['combined_score']:.1f}/100")
        
        print(f"\nRECOMMENDATION: {results['recommendation']}")
        print(f"{'='*80}\n")
    
    async def analyze_portfolio(self, ticker_list, portfolio_value=100000, risk_percent=1.5, top_n_enrich=5):
        """
        Analyze a portfolio of stocks, sort by score, and enrich top N with LLM summary.
        """
        print(f"\n{'='*80}")
        print(f"PORTFOLIO ANALYSIS: {len(ticker_list)} STOCKS")
        print(f"{'='*80}")
        
        all_results = []
        
        # 1. Run Analysis
        for i, ticker in enumerate(ticker_list, 1):
            print(f"\n[{i}/{len(ticker_list)}] Analyzing {ticker}...")
            try:
                result = await self.analyze_stock_comprehensive(ticker, portfolio_value, risk_percent)
                if result:
                    all_results.append(result)
            except Exception as e:
                print(f"Error analyzing {ticker}: {e}")
                continue
        
        # 2. Sort by Combined Score
        all_results.sort(key=lambda x: x['combined_score'], reverse=True)
        
        # 3. Enrich Top N with LLM Summary
        print(f"\n{'='*80}")
        print(f"GENERATING LLM SUMMARIES FOR TOP {top_n_enrich} STOCKS")
        print(f"{'='*80}")
        
        for i in range(min(len(all_results), top_n_enrich)):
            result = all_results[i]
            ticker = result['ticker']
            print(f"Enriching {ticker}...")
            
            # Fetch News
            news = self.llm_agent.fetch_news(ticker)
            
            # Generate Summary
            summary = self.llm_agent.generate_summary(ticker, result, news)
            result['llm_summary'] = summary
            print(f"Summary generated for {ticker}")
        
        # 4. Save Results
        self._save_portfolio_results(all_results)
        
        return all_results
    
    def _save_portfolio_results(self, results):
        """
        Save portfolio results to CSV.
        """
        if not results:
            return
        
        summary_data = []
        for result in results:
            fund = result['fundamental_analysis']
            
            summary_data.append({
                'ticker': result['ticker'],
                'company_name': fund.get('longName', 'N/A'),
                'fundamental_score': result['fundamental_score'],
                'technical_score': result['technical_score'],
                'combined_score': result['combined_score'],
                'recommendation': result['recommendation'],
                'llm_summary': result.get('llm_summary', 'N/A'),
                'current_price': fund.get('Current Price', 0),
                'pe_ratio': fund.get('p/e', 0),
                'roe': fund.get('ROE', 0),
                'eps_growth_5yr': fund.get('Earnings Growth 5yr cagr', 0),
            })
        
        df = pd.DataFrame(summary_data)
        
        # Save to combined_stocks directory
        MathUtils.ensure_directories([Config.COMBINED_DIR])
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{Config.COMBINED_DIR}/portfolio_analysis_{timestamp}.csv'
        df.to_csv(filename, index=False)
        
        print(f"\nPortfolio analysis saved to: {filename}")

async def main():
    """
    Main function to demonstrate the combined analysis.
    """
    analyzer = CombinedAnalysis()
    
    # Example: Portfolio Analysis
    tickers = ["AAPL", "MSFT", "GOOGL"]
    await analyzer.analyze_portfolio(tickers, top_n_enrich=2)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
