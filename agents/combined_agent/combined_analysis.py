"""
Combined Fundamental and Technical Analysis Integration
This script combines both fundamental and technical analysis for comprehensive stock evaluation.
"""

import sys
import os
import pandas as pd
from datetime import datetime

# Add the agents directories to the path
sys.path.append('agents/fundamanetal_analysis_agent')
sys.path.append('agents/technical_analysis_agent')

from fundamental_analysis_agent.fundamental_analysis import FundamentalAnalysis
from technical_analysis_agent.technical_analysis import TechnicalAnalysis


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
    
    def analyze_stock_comprehensive(self, ticker, portfolio_value=100000, risk_percent=1.5):
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
        fund_result = self.fund_analyzer.analyze_single_stock(ticker)
        
        if not fund_result:
            print(f"❌ Fundamental analysis failed for {ticker}")
            return None
        
        # Technical Analysis
        print("\n2. TECHNICAL ANALYSIS:")
        print("-" * 50)
        tech_result = self.tech_analyzer.complete_technical_analysis(ticker)
        
        if not tech_result:
            print(f"❌ Technical analysis failed for {ticker}")
            return None
        
        # Calculate combined score
        fund_score = self._calculate_fundamental_score(fund_result)
        tech_score = tech_result['score_percentage']
        combined_score = (fund_score + tech_score) / 2
        
        # Determine overall recommendation
        if fund_score >= 70 and tech_score >= 67:
            recommendation = "🟢 STRONG BUY"
            action = "Enter position - Excellent fundamentals and technicals"
        elif fund_score >= 70 and tech_score >= 50:
            recommendation = "🟡 BUY WITH CAUTION"
            action = "Good fundamentals, wait for better technical setup"
        elif fund_score >= 50 and tech_score >= 67:
            recommendation = "🟡 WAIT"
            action = "Good technicals, but fundamental concerns"
        else:
            recommendation = "🔴 AVOID"
            action = "Poor fundamentals and/or technicals"
        
        # Position sizing (if buy signal)
        position_info = None
        if tech_score >= 67:
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
            'position_info': position_info
        }
        
        # Print summary
        self._print_analysis_summary(results)
        
        return results
    
    def _calculate_fundamental_score(self, fund_result):
        """
        Calculate a fundamental score based on key metrics.
        
        Args:
            fund_result (dict): Fundamental analysis results
            
        Returns:
            float: Fundamental score (0-100)
        """
        score = 0
        max_score = 0
        
        # Profitability metrics (30 points)
        max_score += 30
        if fund_result.get('ROE', 0) > 15:
            score += 10
        elif fund_result.get('ROE', 0) > 10:
            score += 5
        
        if fund_result.get('ROCE', 0) > 15:
            score += 10
        elif fund_result.get('ROCE', 0) > 10:
            score += 5
        
        if fund_result.get('NPM', 0) > 10:
            score += 10
        elif fund_result.get('NPM', 0) > 5:
            score += 5
        
        # Growth metrics (20 points)
        max_score += 20
        if fund_result.get('Earnings Growth 5yr cagr', 0) > 15:
            score += 10
        elif fund_result.get('Earnings Growth 5yr cagr', 0) > 5:
            score += 5
        
        if fund_result.get('Sales Growth 5yr cagr', 0) > 15:
            score += 10
        elif fund_result.get('Sales Growth 5yr cagr', 0) > 5:
            score += 5
        
        # Financial health (25 points)
        max_score += 25
        if fund_result.get('d/e_market', 0) < 0.5:
            score += 10
        elif fund_result.get('d/e_market', 0) < 1.0:
            score += 5
        
        if fund_result.get('Interest coverage', 0) > 3:
            score += 10
        elif fund_result.get('Interest coverage', 0) > 2:
            score += 5
        
        if fund_result.get('CFO', 0) > 0:
            score += 5
        
        # Valuation (15 points)
        max_score += 15
        pe = fund_result.get('p/e', float('inf'))
        if pe < 15 and pe > 0:
            score += 10
        elif pe < 25 and pe > 0:
            score += 5
        
        if fund_result.get('EY', 0) > 7:
            score += 5
        
        # Cash flow quality (10 points)
        max_score += 10
        if fund_result.get('cCFO/cPAT', 0) > 1:
            score += 10
        elif fund_result.get('cCFO/cPAT', 0) > 0.8:
            score += 5
        
        return (score / max_score) * 100 if max_score > 0 else 0
    
    def _print_analysis_summary(self, results):
        """
        Print a summary of the analysis results.
        
        Args:
            results (dict): Analysis results
        """
        print(f"\n{'='*80}")
        print(f"ANALYSIS SUMMARY: {results['ticker']}")
        print(f"{'='*80}")
        
        # Basic info
        fund = results['fundamental_analysis']
        tech = results['technical_analysis']
        
        print(f"Company: {fund.get('longName', 'N/A')}")
        print(f"Current Price: ${fund.get('Current Price', 0):.2f}")
        print(f"Market Cap: ${fund.get('Market Cap', 0):,.0f}")
        
        # Scores
        print(f"\nSCORES:")
        print(f"  Fundamental: {results['fundamental_score']:.1f}/100")
        print(f"  Technical: {results['technical_score']:.1f}/100")
        print(f"  Combined: {results['combined_score']:.1f}/100")
        
        # Key metrics
        print(f"\nKEY METRICS:")
        print(f"  P/E Ratio: {fund.get('p/e', 'N/A')}")
        print(f"  ROE: {fund.get('ROE', 0):.2f}%")
        print(f"  ROCE: {fund.get('ROCE', 0):.2f}%")
        print(f"  NPM: {fund.get('NPM', 0):.2f}%")
        print(f"  D/E Ratio: {fund.get('d/e', 0):.2f}")
        print(f"  Interest Coverage: {fund.get('Interest coverage', 0):.2f}")
        
        # Recommendation
        print(f"\nRECOMMENDATION: {results['recommendation']}")
        print(f"ACTION: {results['action']}")
        
        # Position sizing
        if results['position_info']:
            pos = results['position_info']
            print(f"\nPOSITION SIZING:")
            print(f"  Entry Price: ${pos['entry_price']:.2f}")
            print(f"  Stop Loss: ${pos['stop_loss']:.2f}")
            print(f"  Shares: {pos['position']['shares']}")
            print(f"  Position Value: ${pos['position']['position_value']:,.2f}")
            print(f"  Risk Amount: ${pos['position']['risk_amount']:,.2f}")
        
        print(f"{'='*80}\n")
    
    def analyze_portfolio(self, ticker_list, portfolio_value=100000, risk_percent=1.5):
        """
        Analyze a portfolio of stocks.
        
        Args:
            ticker_list (list): List of stock tickers
            portfolio_value (float): Total portfolio value
            risk_percent (float): Risk per trade percentage
            
        Returns:
            list: List of analysis results for all stocks
        """
        print(f"\n{'='*80}")
        print(f"PORTFOLIO ANALYSIS: {len(ticker_list)} STOCKS")
        print(f"{'='*80}")
        
        all_results = []
        
        for i, ticker in enumerate(ticker_list, 1):
            print(f"\n[{i}/{len(ticker_list)}] Analyzing {ticker}...")
            try:
                result = self.analyze_stock_comprehensive(ticker, portfolio_value, risk_percent)
                if result:
                    all_results.append(result)
            except Exception as e:
                print(f"Error analyzing {ticker}: {e}")
                continue
        
        # Sort by combined score
        all_results.sort(key=lambda x: x['combined_score'], reverse=True)
        
        # Print portfolio summary
        self._print_portfolio_summary(all_results)
        
        # Save to CSV
        self._save_portfolio_results(all_results)
        
        return all_results
    
    def _print_portfolio_summary(self, results):
        """
        Print portfolio summary.
        
        Args:
            results (list): List of analysis results
        """
        print(f"\n{'='*80}")
        print(f"PORTFOLIO SUMMARY")
        print(f"{'='*80}")
        
        print(f"{'Ticker':<8} {'Fund':<8} {'Tech':<8} {'Combined':<10} {'Recommendation':<20}")
        print(f"{'-'*80}")
        
        for result in results:
            ticker = result['ticker']
            fund_score = result['fundamental_score']
            tech_score = result['technical_score']
            combined_score = result['combined_score']
            recommendation = result['recommendation']
            
            print(f"{ticker:<8} {fund_score:.1f}{'':<4} {tech_score:.1f}{'':<4} {combined_score:.1f}{'':<6} {recommendation:<20}")
        
        # Count recommendations
        strong_buy = len([r for r in results if 'STRONG BUY' in r['recommendation']])
        buy_caution = len([r for r in results if 'BUY WITH CAUTION' in r['recommendation']])
        wait = len([r for r in results if 'WAIT' in r['recommendation']])
        avoid = len([r for r in results if 'AVOID' in r['recommendation']])
        
        print(f"\nRECOMMENDATION BREAKDOWN:")
        print(f"  🟢 Strong Buy: {strong_buy}")
        print(f"  🟡 Buy with Caution: {buy_caution}")
        print(f"  🟡 Wait: {wait}")
        print(f"  🔴 Avoid: {avoid}")
    
    def _save_portfolio_results(self, results):
        """
        Save portfolio results to CSV.
        
        Args:
            results (list): List of analysis results
        """
        if not results:
            return
        
        # Create summary dataframe
        summary_data = []
        for result in results:
            fund = result['fundamental_analysis']
            tech = result['technical_analysis']
            
            summary_data.append({
                'ticker': result['ticker'],
                'company_name': fund.get('longName', 'N/A'),
                'current_price': fund.get('Current Price', 0),
                'market_cap': fund.get('Market Cap', 0),
                'pe_ratio': fund.get('p/e', 0),
                'roe': fund.get('ROE', 0),
                'roce': fund.get('ROCE', 0),
                'npm': fund.get('NPM', 0),
                'de_ratio': fund.get('d/e', 0),
                'interest_coverage': fund.get('Interest coverage', 0),
                'earnings_growth_5yr': fund.get('Earnings Growth 5yr cagr', 0),
                'sales_growth_5yr': fund.get('Sales Growth 5yr cagr', 0),
                'fundamental_score': result['fundamental_score'],
                'technical_score': result['technical_score'],
                'combined_score': result['combined_score'],
                'recommendation': result['recommendation'],
                'action': result['action']
            })
        
        df = pd.DataFrame(summary_data)
        
        # Save to combined_stocks directory
        os.makedirs('combined_stocks', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'combined_stocks/portfolio_analysis_{timestamp}.csv'
        df.to_csv(filename, index=False)
        
        print(f"\nPortfolio analysis saved to: {filename}")


def main():
    """
    Main function to demonstrate the combined analysis.
    """
    # Initialize combined analyzer
    analyzer = CombinedAnalysis()
    
    # Example 1: Single stock analysis
    print("EXAMPLE 1: Single Stock Analysis")
    result = analyzer.analyze_stock_comprehensive("AAPL")
    
    # Example 2: Portfolio analysis
    print("\n" + "="*80)
    print("EXAMPLE 2: Portfolio Analysis")
    tickers = ["AAPL", "MSFT", "GOOGL", "NVDA", "META", "TSLA", "AMZN", "NFLX"]
    portfolio_results = analyzer.analyze_portfolio(tickers)
    
    print(f"\nAnalysis complete! Check the 'combined_stocks' folder for detailed results.")


if __name__ == "__main__":
    main()
