"""
Basic Usage Example
Simple demonstration of the financial analysis framework
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_collector import StockDataCollector
from src.valuation_framework import ValuationFramework
from src.analysis_visualization import FinancialAnalyzer, FinancialVisualizer


def analyze_single_stock(ticker: str):
    """Analyze a single stock using all valuation methods"""
    
    print(f"🔍 Analyzing {ticker}...")
    print("=" * 50)
    
    # Initialize components
    collector = StockDataCollector()
    valuation = ValuationFramework()
    analyzer = FinancialAnalyzer()
    
    # Set market conditions
    valuation.set_market_conditions(risk_free_rate=0.05, market_risk_premium=0.06)
    
    # Get stock data
    stock_data = collector.get_all_data(ticker)
    
    if not stock_data['basic_info']:
        print(f"❌ Could not fetch data for {ticker}")
        return
    
    # Display basic information
    basic_info = stock_data['basic_info']
    print(f"Company: {basic_info['name']}")
    print(f"Sector: {basic_info['sector']}")
    print(f"Market Cap: ${basic_info['market_cap']:,.0f}")
    print(f"Current Price: ${basic_info['current_price']:.2f}")
    print(f"P/E Ratio: {basic_info['pe_ratio']:.2f}")
    print(f"Price to Book: {basic_info['price_to_book']:.2f}")
    print()
    
    # Calculate valuations
    print("📊 Valuation Analysis:")
    print("-" * 25)
    
    # Graham valuation
    graham_results = valuation.calculate_graham_valuation(stock_data)
    if graham_results:
        print(f"Graham Number: ${graham_results.get('graham_number', 0):.2f}")
        print(f"Margin of Safety: {graham_results.get('margin_of_safety', 0):.2%}")
        print(f"Graham Score: {graham_results.get('graham_score', 0):.2f}")
        print()
    
    # Buffett metrics
    buffett_results = valuation.calculate_buffett_metrics(stock_data)
    if buffett_results:
        print(f"ROE: {buffett_results.get('roe', 0):.2%}")
        print(f"ROA: {buffett_results.get('roa', 0):.2%}")
        print(f"Debt/Equity: {buffett_results.get('debt_to_equity', 0):.2f}")
        print(f"Current Ratio: {buffett_results.get('current_ratio', 0):.2f}")
        print(f"Buffett Score: {buffett_results.get('buffett_score', 0):.2f}")
        print()
    
    # Composite valuation
    composite_results = valuation.calculate_composite_valuation(stock_data)
    if composite_results:
        print(f"Composite Score: {composite_results.get('composite_score', 0):.2f}")
        print(f"Recommendation: {composite_results.get('recommendation', 'N/A')}")
        print()
    
    # Financial ratios
    ratios = analyzer.calculate_financial_ratios(stock_data)
    if ratios:
        print("📈 Key Financial Ratios:")
        print("-" * 25)
        print(f"Net Margin: {ratios['profitability']['net_margin']:.2%}")
        print(f"ROE: {ratios['profitability']['roe']:.2%}")
        print(f"ROA: {ratios['profitability']['roa']:.2%}")
        print(f"Current Ratio: {ratios['liquidity']['current_ratio']:.2f}")
        print(f"Debt/Equity: {ratios['solvency']['debt_to_equity']:.2f}")
        print()
    
    return stock_data, composite_results


def screen_stocks(tickers: list, min_pe: float = 20, max_pe: float = 50):
    """Screen stocks based on basic criteria"""
    
    print("🔍 Stock Screening Results:")
    print("=" * 50)
    
    collector = StockDataCollector()
    
    # Get basic data for multiple stocks
    stocks_data = collector.get_multiple_stocks_data(tickers)
    
    if stocks_data.empty:
        print("❌ No stock data available")
        return
    
    # Apply screening criteria
    screened_stocks = stocks_data[
        (stocks_data['pe_ratio'] > 0) & 
        (stocks_data['pe_ratio'] < max_pe) &
        (stocks_data['price_to_book'] > 0) &
        (stocks_data['price_to_book'] < 3)
    ]
    
    if not screened_stocks.empty:
        print(f"✅ Found {len(screened_stocks)} stocks meeting criteria:")
        print()
        
        # Display results
        for _, stock in screened_stocks.iterrows():
            print(f"📈 {stock['ticker']} - {stock['name']}")
            print(f"   P/E: {stock['pe_ratio']:.2f}")
            print(f"   P/B: {stock['price_to_book']:.2f}")
            print(f"   Market Cap: ${stock['market_cap']:,.0f}")
            print()
    else:
        print("❌ No stocks met the screening criteria")


def main():
    """Main function demonstrating the framework"""
    
    print("🚀 Financial Analysis Framework - Basic Usage")
    print("=" * 60)
    print()
    
    # Example 1: Analyze individual stocks
    print("📊 Example 1: Individual Stock Analysis")
    print("-" * 40)
    
    # Analyze a few well-known stocks
    stocks_to_analyze = ["AAPL", "MSFT", "GOOGL"]
    
    for ticker in stocks_to_analyze:
        try:
            stock_data, composite_results = analyze_single_stock(ticker)
            print("-" * 40)
        except Exception as e:
            print(f"❌ Error analyzing {ticker}: {str(e)}")
            print("-" * 40)
    
    # Example 2: Stock screening
    print("\n🔍 Example 2: Stock Screening")
    print("-" * 40)
    
    # Get some S&P 500 tickers for screening
    collector = StockDataCollector()
    sp500_tickers = collector.get_sp500_tickers()[:20]  # First 20 for demo
    
    if sp500_tickers:
        screen_stocks(sp500_tickers, min_pe=0, max_pe=25)
    
    print("\n✅ Basic usage demonstration complete!")
    print("\n💡 Next steps:")
    print("• Run 'python main.py' for the full demonstration")
    print("• Explore the src/ directory for more advanced features")
    print("• Check the README.md for detailed documentation")


if __name__ == "__main__":
    main()




