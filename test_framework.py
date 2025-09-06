#!/usr/bin/env python3
"""
Simple test script for the Financial Analysis Framework
Demonstrates basic functionality with rate limiting protection
"""

import time
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from data_collector import StockDataCollector
from valuation_framework import ValuationFramework
from analysis_visualization import FinancialAnalyzer, FinancialVisualizer

def test_single_stock(ticker="AAPL"):
    """Test the framework with a single stock"""
    print(f"🔍 Testing framework with {ticker}")
    print("=" * 50)
    
    # Initialize components
    collector = StockDataCollector()
    framework = ValuationFramework()
    analyzer = FinancialAnalyzer()
    visualizer = FinancialVisualizer()
    
    try:
        # Get stock data with delay to avoid rate limiting
        print(f"📊 Fetching data for {ticker}...")
        time.sleep(2)  # Rate limiting protection
        
        stock_data = collector.get_stock_info(ticker)
        
        if not stock_data or 'error' in stock_data:
            print(f"❌ Failed to get data for {ticker}")
            return False
            
        print(f"✅ Successfully fetched data for {ticker}")
        print(f"   Company: {stock_data.get('longName', 'N/A')}")
        print(f"   Sector: {stock_data.get('sector', 'N/A')}")
        print(f"   Market Cap: ${stock_data.get('marketCap', 0):,.0f}")
        print(f"   P/E Ratio: {stock_data.get('trailingPE', 'N/A')}")
        
        # Test valuation framework
        print(f"\n💰 Testing Valuation Framework...")
        
        # Graham valuation
        try:
            graham_result = framework.calculate_graham_valuation(stock_data)
            print(f"   Graham Number: ${graham_result.get('graham_number', 'N/A'):.2f}")
            print(f"   Graham Score: {graham_result.get('score', 'N/A')}/100")
        except Exception as e:
            print(f"   Graham valuation failed: {e}")
        
        # Buffett metrics
        try:
            buffett_result = framework.calculate_buffett_metrics(stock_data)
            print(f"   Buffett Score: {buffett_result.get('score', 'N/A')}/100")
            print(f"   ROE: {buffett_result.get('roe', 'N/A'):.2f}%")
        except Exception as e:
            print(f"   Buffett metrics failed: {e}")
        
        # Composite valuation
        try:
            composite_result = framework.calculate_composite_valuation(stock_data)
            print(f"   Composite Score: {composite_result.get('composite_score', 'N/A'):.2f}/100")
            print(f"   Recommendation: {composite_result.get('recommendation', 'N/A')}")
        except Exception as e:
            print(f"   Composite valuation failed: {e}")
        
        # Test financial analysis
        print(f"\n📈 Testing Financial Analysis...")
        try:
            ratios = analyzer.calculate_financial_ratios(stock_data)
            print(f"   Current Ratio: {ratios.get('current_ratio', 'N/A'):.2f}")
            print(f"   Debt/Equity: {ratios.get('debt_to_equity', 'N/A'):.2f}")
        except Exception as e:
            print(f"   Financial ratios failed: {e}")
        
        print(f"\n✅ Framework test completed successfully for {ticker}")
        return True
        
    except Exception as e:
        print(f"❌ Error testing framework: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Financial Analysis Framework Test")
    print("=" * 50)
    
    # Test with a single stock
    success = test_single_stock("AAPL")
    
    if success:
        print("\n🎉 Framework is working correctly!")
        print("You can now use the full framework for comprehensive analysis.")
    else:
        print("\n⚠️  Framework test encountered issues.")
        print("This might be due to Yahoo Finance rate limiting or API changes.")
    
    print("\n💡 Next steps:")
    print("1. Run 'python main.py' for full demonstration")
    print("2. Use 'python example.py' for basic usage examples")
    print("3. Import modules in your own scripts for custom analysis")

if __name__ == "__main__":
    main()

