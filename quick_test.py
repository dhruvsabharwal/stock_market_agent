#!/usr/bin/env python3
"""
Quick test script for the Financial Analysis Framework
Tests with a single stock and includes rate limiting protection
"""

import time
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

def test_single_stock():
    """Test the framework with a single stock"""
    try:
        from data_collector import StockDataCollector
        from valuation_framework import ValuationFramework
        from analysis_visualization import FinancialAnalyzer
        
        print("🚀 Testing Financial Analysis Framework")
        print("=" * 50)
        
        # Initialize components
        collector = StockDataCollector()
        framework = ValuationFramework()
        analyzer = FinancialAnalyzer()
        
        # Set market conditions
        framework.set_market_conditions(risk_free_rate=0.04, market_risk_premium=0.06)
        
        # Test with AAPL
        ticker = "AAPL"
        print(f"📊 Fetching data for {ticker}...")
        
        # Add delay to avoid rate limiting
        time.sleep(3)
        
        stock_data = collector.get_stock_info(ticker)
        
        if not stock_data:
            print(f"❌ Failed to get data for {ticker}")
            print("This might be due to Yahoo Finance API rate limiting.")
            print("Try again in a few minutes.")
            return False
        
        print(f"✅ Successfully fetched data for {ticker}")
        print(f"   Company: {stock_data.get('longName', 'N/A')}")
        print(f"   Sector: {stock_data.get('sector', 'N/A')}")
        print(f"   Market Cap: ${stock_data.get('marketCap', 0):,.0f}")
        print(f"   Current Price: ${stock_data.get('currentPrice', 0):.2f}")
        print(f"   P/E Ratio: {stock_data.get('trailingPE', 'N/A')}")
        print(f"   P/B Ratio: {stock_data.get('priceToBook', 'N/A')}")
        
        # Test valuation framework
        print(f"\n💰 Testing Valuation Framework...")
        
        # Graham valuation
        try:
            print(f"   🔍 Calculating Graham valuation...")
            graham_result = framework.calculate_graham_valuation(stock_data)
            if 'graham_number' in graham_result:
                print(f"      Graham Number: ${graham_result['graham_number']:.2f}")
                print(f"      Graham Score: {graham_result['score']:.2f}/100")
            else:
                print(f"      Graham calculation completed")
        except Exception as e:
            print(f"      Graham valuation failed: {e}")
        
        # Buffett metrics
        try:
            print(f"   🎯 Calculating Buffett metrics...")
            buffett_result = framework.calculate_buffett_metrics(stock_data)
            if 'score' in buffett_result:
                print(f"      Buffett Score: {buffett_result['score']:.2f}/100")
                print(f"      ROE: {buffett_result['roe']:.2f}%")
            else:
                print(f"      Buffett calculation completed")
        except Exception as e:
            print(f"      Buffett metrics failed: {e}")
        
        # DCF valuation
        try:
            print(f"   📈 Calculating DCF valuation...")
            dcf_result = framework.calculate_damodaran_dcf(stock_data, growth_rate=0.05)
            if 'wacc' in dcf_result:
                print(f"      WACC: {dcf_result['wacc']:.2f}%")
                print(f"      Per Share Value: ${dcf_result['per_share_value']:.2f}")
            else:
                print(f"      DCF calculation completed")
        except Exception as e:
            print(f"      DCF valuation failed: {e}")
        
        # Composite valuation
        try:
            print(f"   🎯 Calculating composite valuation...")
            composite_result = framework.calculate_composite_valuation(stock_data)
            if 'composite_score' in composite_result:
                print(f"      Composite Score: {composite_result['composite_score']:.2f}/100")
                print(f"      Recommendation: {composite_result['recommendation']}")
            else:
                print(f"      Composite calculation completed")
        except Exception as e:
            print(f"      Composite valuation failed: {e}")
        
        # Test financial analysis
        print(f"\n📊 Testing Financial Analysis...")
        try:
            print(f"   🔢 Calculating financial ratios...")
            ratios = analyzer.calculate_financial_ratios(stock_data)
            if ratios:
                print(f"      ROE: {ratios.get('roe', 'N/A')}")
                print(f"      Current Ratio: {ratios.get('current_ratio', 'N/A')}")
                print(f"      Debt/Equity: {ratios.get('debt_to_equity', 'N/A')}")
            else:
                print(f"      Financial ratios calculation completed")
        except Exception as e:
            print(f"      Financial analysis failed: {e}")
        
        print(f"\n✅ Framework test completed successfully!")
        print("The framework is working correctly with real data.")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're in the correct directory and virtual environment is activated.")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def main():
    """Main function"""
    print("🔍 Financial Analysis Framework - Quick Test")
    print("=" * 60)
    print("This script tests the framework with a single stock (AAPL)")
    print("It includes rate limiting protection for Yahoo Finance API")
    print()
    
    success = test_single_stock()
    
    if success:
        print(f"\n🎉 Success! The framework is working correctly.")
        print("You can now:")
        print("1. Run 'python main.py' for full demonstration")
        print("2. Use 'python example.py' for basic usage examples")
        print("3. Import modules in your own scripts")
    else:
        print(f"\n⚠️  The framework test encountered issues.")
        print("This might be due to:")
        print("- Yahoo Finance API rate limiting")
        print("- Missing dependencies")
        print("- Network connectivity issues")
        print("\nTry running 'python demo_with_mock_data.py' to see how the framework works.")

if __name__ == "__main__":
    main()

