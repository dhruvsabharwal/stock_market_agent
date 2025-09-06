#!/usr/bin/env python3
"""
Explore StockDataCollector - Yahoo Finance API Data Explorer

This script lets you explore what data is available from the Yahoo Finance API 
using our StockDataCollector class. Run it to see exactly what information 
you can get back and identify additional columns you might want to add.

Usage:
    python explore_data_collector.py
"""

import sys
import os
import time
import json
from datetime import datetime

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

def print_header(title):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"🔍 {title}")
    print(f"{'='*60}")

def print_section(title):
    """Print a formatted section header"""
    print(f"\n📋 {title}")
    print("-" * 40)

def explore_basic_stock_info(collector, ticker="AAPL"):
    """Explore basic stock information"""
    print_header("BASIC STOCK INFORMATION")
    
    print(f"📊 Fetching basic info for {ticker}...")
    time.sleep(2)  # Rate limiting protection
    
    try:
        stock_info = collector.get_stock_info(ticker)
        
        if stock_info:
            print(f"✅ Successfully fetched data for {ticker}!")
            print(f"📋 Total fields available: {len(stock_info)}")
            
            # Display all available fields
            print_section("All Available Fields")
            for key, value in stock_info.items():
                if isinstance(value, (int, float)):
                    if value > 1000000:
                        print(f"{key:30} = ${value:,.0f}")
                    else:
                        print(f"{key:30} = {value:,.4f}")
                else:
                    print(f"{key:30} = {value}")
            
            # Show data types
            print_section("Data Types")
            for key, value in stock_info.items():
                print(f"{key:30} = {type(value).__name__}")
                
            return stock_info
        else:
            print(f"❌ No data returned for {ticker}")
            return None
            
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")
        return None

def explore_financial_statements(collector, ticker="AAPL"):
    """Explore financial statements"""
    print_header("FINANCIAL STATEMENTS")
    
    print(f"📊 Fetching financial statements for {ticker}...")
    time.sleep(3)  # Rate limiting protection
    
    try:
        financials = collector.get_financial_statements(ticker, period="annual")
        
        if financials:
            print(f"✅ Successfully fetched financial statements!")
            
            # Explore income statement
            if 'income_statement' in financials:
                income_stmt = financials['income_statement']
                print_section("Income Statement (Annual)")
                print(f"📅 Years available: {list(income_stmt.columns)}")
                print(f"📋 Line items: {list(income_stmt.index)}")
                
                # Show first few rows
                print("\n📊 Sample Income Statement Data:")
                print(income_stmt.head(10))
            
            # Explore balance sheet
            if 'balance_sheet' in financials:
                balance_sheet = financials['balance_sheet']
                print_section("Balance Sheet (Annual)")
                print(f"📅 Years available: {list(balance_sheet.columns)}")
                print(f"📋 Line items: {list(balance_sheet.index)}")
                
                # Show first few rows
                print("\n📊 Sample Balance Sheet Data:")
                print(balance_sheet.head(10))
            
            # Explore cash flow
            if 'cash_flow' in financials:
                cash_flow = financials['cash_flow']
                print_section("Cash Flow Statement (Annual)")
                print(f"📅 Years available: {list(cash_flow.columns)}")
                print(f"📋 Line items: {list(cash_flow.index)}")
                
                # Show first few rows
                print("\n📊 Sample Cash Flow Data:")
                print(cash_flow.head(10))
                
        else:
            print(f"❌ No financial statements returned for {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching financial statements: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_historical_data(collector, ticker="AAPL"):
    """Explore historical price data"""
    print_header("HISTORICAL PRICE DATA")
    
    print(f"📈 Fetching historical data for {ticker}...")
    time.sleep(3)  # Rate limiting protection
    
    try:
        historical_data = collector.get_historical_data(ticker, period="1y")
        
        if historical_data is not None and not historical_data.empty:
            print(f"✅ Successfully fetched historical data!")
            print(f"📅 Date range: {historical_data.index[0].strftime('%Y-%m-%d')} to {historical_data.index[-1].strftime('%Y-%m-%d')}")
            print(f"📊 Total trading days: {len(historical_data)}")
            
            # Show available columns
            print_section("Available Columns")
            for col in historical_data.columns:
                print(f"• {col}")
            
            # Show data types
            print_section("Data Types")
            print(historical_data.dtypes)
            
            # Show first and last few rows
            print("\n📊 First 5 Trading Days:")
            print(historical_data.head())
            
            print("\n📊 Last 5 Trading Days:")
            print(historical_data.tail())
            
            # Basic statistics
            print_section("Price Statistics")
            print(f"Highest Close: ${historical_data['Close'].max():.2f}")
            print(f"Lowest Close: ${historical_data['Close'].min():.2f}")
            print(f"Current Close: ${historical_data['Close'].iloc[-1]:.2f}")
            print(f"Average Volume: {historical_data['Volume'].mean():,.0f}")
            
        else:
            print(f"❌ No historical data returned for {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching historical data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_analyst_data(collector, ticker="AAPL"):
    """Explore analyst recommendations"""
    print_header("ANALYST RECOMMENDATIONS")
    
    print(f"🎯 Fetching analyst recommendations for {ticker}...")
    time.sleep(3)  # Rate limiting protection
    
    try:
        analyst_data = collector.get_analyst_recommendations(ticker)
        
        if analyst_data is not None and not analyst_data.empty:
            print(f"✅ Successfully fetched analyst data!")
            print(f"📊 Total recommendations: {len(analyst_data)}")
            
            # Show available columns
            print_section("Available Columns")
            for col in analyst_data.columns:
                print(f"• {col}")
            
            # Show data types
            print_section("Data Types")
            print(analyst_data.dtypes)
            
            # Show first few recommendations
            print("\n📊 Sample Analyst Recommendations:")
            print(analyst_data.head(10))
            
        else:
            print(f"❌ No analyst data returned for {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching analyst data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_earnings_data(collector, ticker="AAPL"):
    """Explore earnings dates"""
    print_header("EARNINGS DATES")
    
    print(f"📅 Fetching earnings dates for {ticker}...")
    time.sleep(3)  # Rate limiting protection
    
    try:
        earnings_data = collector.get_earnings_dates(ticker)
        
        if earnings_data is not None and not earnings_data.empty:
            print(f"✅ Successfully fetched earnings data!")
            print(f"📊 Total earnings dates: {len(earnings_data)}")
            
            # Show available columns
            print_section("Available Columns")
            for col in earnings_data.columns:
                print(f"• {col}")
            
            # Show data types
            print_section("Data Types")
            print(earnings_data.dtypes)
            
            # Show earnings dates
            print("\n📊 Earnings Dates:")
            print(earnings_data.head(10))
            
        else:
            print(f"❌ No earnings data returned for {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching earnings data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_all_data(collector, ticker="AAPL"):
    """Explore all available data"""
    print_header("ALL AVAILABLE DATA")
    
    print(f"🔍 Fetching ALL data for {ticker}...")
    time.sleep(5)  # Rate limiting protection
    
    try:
        all_data = collector.get_all_data(ticker)
        
        if all_data:
            print(f"✅ Successfully fetched ALL data!")
            print(f"📊 Data categories available: {list(all_data.keys())}")
            
            # Explore each category
            for category, data in all_data.items():
                print_section(f"{category.upper().replace('_', ' ')}")
                
                if isinstance(data, dict):
                    print(f"   Fields: {len(data)}")
                    if len(data) <= 10:
                        for key, value in data.items():
                            if isinstance(value, (int, float)) and value > 1000000:
                                print(f"     {key}: ${value:,.0f}")
                            else:
                                print(f"     {key}: {value}")
                    else:
                        print(f"     First 5 fields: {list(data.keys())[:5]}")
                        print(f"     ... and {len(data)-5} more")
                elif hasattr(data, 'shape'):  # DataFrame
                    print(f"   DataFrame: {data.shape[0]} rows × {data.shape[1]} columns")
                    print(f"   Columns: {list(data.columns)}")
                else:
                    print(f"   Type: {type(data).__name__}")
                    print(f"   Value: {data}")
            
        else:
            print(f"❌ No data returned for {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching all data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_multiple_stocks(collector):
    """Explore multiple stocks data"""
    print_header("MULTIPLE STOCKS DATA")
    
    tickers = ["AAPL", "MSFT", "GOOGL"]
    print(f"🧪 Testing multiple stocks: {tickers}")
    
    time.sleep(5)  # Rate limiting protection
    
    try:
        multiple_data = collector.get_multiple_stocks_data(tickers)
        
        if multiple_data is not None and not multiple_data.empty:
            print(f"✅ Successfully fetched data for multiple stocks!")
            print(f"📊 Shape: {multiple_data.shape}")
            
            # Show available columns
            print_section("Available Columns")
            for col in multiple_data.columns:
                print(f"• {col}")
            
            # Show the data
            print("\n📊 Multiple Stocks Data:")
            print(multiple_data)
            
        else:
            print(f"❌ No data returned for multiple stocks")
            
    except Exception as e:
        print(f"❌ Error fetching multiple stocks data: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def explore_available_tickers(collector):
    """Explore available tickers"""
    print_header("AVAILABLE TICKERS")
    
    print("🔍 Fetching available tickers...")
    time.sleep(3)  # Rate limiting protection
    
    try:
        # Try S&P 500 tickers
        print("📊 Fetching S&P 500 tickers...")
        sp500_tickers = collector.get_sp500_tickers()
        
        if sp500_tickers:
            print(f"✅ Successfully fetched S&P 500 tickers!")
            print(f"📊 Total tickers: {len(sp500_tickers)}")
            print(f"🔍 Sample tickers: {sp500_tickers[:10]}")
            
            # Test with a few random tickers
            test_tickers = sp500_tickers[:3]
            print(f"\n🧪 Testing with sample tickers: {test_tickers}")
            
            for ticker in test_tickers:
                try:
                    basic_info = collector.get_stock_info(ticker)
                    if basic_info:
                        print(f"   ✅ {ticker}: {basic_info.get('longName', 'N/A')}")
                    else:
                        print(f"   ❌ {ticker}: No data")
                    time.sleep(1)  # Small delay between requests
                except Exception as e:
                    print(f"   ❌ {ticker}: Error - {e}")
        else:
            print(f"❌ No S&P 500 tickers returned")
            
    except Exception as e:
        print(f"❌ Error fetching tickers: {e}")
        print("\n💡 This might be due to Yahoo Finance API rate limiting.")
        print("   Try waiting a few minutes and running again.")

def create_enhanced_data_function():
    """Create and demonstrate enhanced data collection function"""
    print_header("ENHANCED DATA COLLECTION FUNCTION")
    
    enhanced_function_code = '''
def get_enhanced_stock_data(collector, ticker):
    """Enhanced stock data collection with additional metrics"""
    try:
        # Get basic data
        basic_data = collector.get_stock_info(ticker)
        
        if not basic_data:
            return None
        
        # Calculate additional metrics
        enhanced_data = basic_data.copy()
        
        # Market cap categories
        market_cap = basic_data.get('marketCap', 0)
        if market_cap > 200000000000:  # $200B+
            enhanced_data['market_cap_category'] = 'Mega Cap'
        elif market_cap > 10000000000:  # $10B+
            enhanced_data['market_cap_category'] = 'Large Cap'
        elif market_cap > 2000000000:   # $2B+
            enhanced_data['market_cap_category'] = 'Mid Cap'
        elif market_cap > 300000000:    # $300M+
            enhanced_data['market_cap_category'] = 'Small Cap'
        else:
            enhanced_data['market_cap_category'] = 'Micro Cap'
        
        # P/E ratio categories
        pe_ratio = basic_data.get('trailingPE', 0)
        if pe_ratio > 0:
            if pe_ratio < 15:
                enhanced_data['pe_category'] = 'Value'
            elif pe_ratio < 25:
                enhanced_data['pe_category'] = 'Growth'
            else:
                enhanced_data['pe_category'] = 'High Growth'
        else:
            enhanced_data['pe_category'] = 'N/A'
        
        # Dividend yield categories
        dividend_yield = basic_data.get('dividendYield', 0)
        if dividend_yield > 0:
            if dividend_yield > 0.05:  # 5%+
                enhanced_data['dividend_category'] = 'High Yield'
            elif dividend_yield > 0.02:  # 2%+
                enhanced_data['dividend_category'] = 'Dividend Stock'
            else:
                enhanced_data['dividend_category'] = 'Low Yield'
        else:
            enhanced_data['dividend_category'] = 'No Dividend'
        
        # Beta risk categories
        beta = basic_data.get('beta', 1)
        if beta < 0.8:
            enhanced_data['risk_category'] = 'Low Risk'
        elif beta < 1.2:
            enhanced_data['risk_category'] = 'Medium Risk'
        else:
            enhanced_data['risk_category'] = 'High Risk'
        
        return enhanced_data
        
    except Exception as e:
        print(f"Error enhancing data for {ticker}: {e}")
        return None
'''
    
    print("💡 Here's an enhanced data collection function you can use:")
    print(enhanced_function_code)
    
    print("\n🔍 This function adds these categories:")
    print("   • Market Cap Category (Mega/Large/Mid/Small/Micro)")
    print("   • P/E Category (Value/Growth/High Growth)")
    print("   • Dividend Category (High Yield/Dividend Stock/Low Yield/No Dividend)")
    print("   • Risk Category (Low/Medium/High based on Beta)")
    print("\n💡 You can customize this function to add any metrics you want!")

def main():
    """Main exploration function"""
    print_header("STOCKDATA COLLECTOR EXPLORER")
    print("This script explores what data is available from Yahoo Finance API")
    print("Run each section to see exactly what information you can get back")
    print("and identify additional columns you might want to add to your analysis.")
    
    try:
        # Import our data collector
        from data_collector import StockDataCollector
        
        # Initialize the data collector
        collector = StockDataCollector()
        print("\n✅ StockDataCollector initialized successfully!")
        
        # Test ticker
        ticker = "AAPL"
        
        # Explore different types of data
        explore_basic_stock_info(collector, ticker)
        explore_financial_statements(collector, ticker)
        explore_historical_data(collector, ticker)
        explore_analyst_data(collector, ticker)
        explore_earnings_data(collector, ticker)
        explore_all_data(collector, ticker)
        explore_multiple_stocks(collector)
        explore_available_tickers(collector)
        
        # Show customization options
        create_enhanced_data_function()
        
        print_header("EXPLORATION COMPLETE")
        print("🎉 You've now explored all the data available from StockDataCollector!")
        print("\n💡 Next steps:")
        print("1. Review the data structure and available fields")
        print("2. Identify additional columns you want to add")
        print("3. Customize the data collection for your needs")
        print("4. Integrate enhanced data into your analysis framework")
        print("\n🚀 Happy exploring!")
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're in the correct directory and virtual environment is activated.")
        print("Run: source venv/bin/activate")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()

