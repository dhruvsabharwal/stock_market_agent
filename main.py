"""
Main Financial Analysis Application
Demonstrates the complete framework for stock analysis and valuation
"""

import pandas as pd
import numpy as np
from src.data_collector import StockDataCollector
from src.valuation_framework import ValuationFramework
from src.analysis_visualization import FinancialAnalyzer, FinancialVisualizer
import warnings
warnings.filterwarnings('ignore')


def main():
    """Main function to demonstrate the financial analysis framework"""
    
    print("🚀 Financial Analysis Framework")
    print("=" * 50)
    print("Implementing Benjamin Graham, Damodaran, and Warren Buffett methods")
    print()
    
    # Initialize components
    collector = StockDataCollector()
    valuation = ValuationFramework()
    analyzer = FinancialAnalyzer()
    visualizer = FinancialVisualizer()
    
    # Set market conditions (you can adjust these)
    valuation.set_market_conditions(risk_free_rate=0.05, market_risk_premium=0.06)
    
    # Example 1: Analyze individual stock
    print("📊 Example 1: Individual Stock Analysis")
    print("-" * 40)
    
    # Get data for a well-known stock (Apple)
    ticker = "AAPL"
    print(f"Analyzing {ticker}...")
    
    stock_data = collector.get_all_data(ticker)
    
    if stock_data['basic_info']:
        print(f"Company: {stock_data['basic_info']['name']}")
        print(f"Sector: {stock_data['basic_info']['sector']}")
        print(f"Market Cap: ${stock_data['basic_info']['market_cap']:,.0f}")
        print(f"Current Price: ${stock_data['basic_info']['current_price']:.2f}")
        print(f"P/E Ratio: {stock_data['basic_info']['pe_ratio']:.2f}")
        print()
        
        # Calculate valuations
        print("🔍 Valuation Analysis:")
        print("-" * 20)
        
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
            print("📈 Financial Ratios:")
            print("-" * 20)
            print(f"Net Margin: {ratios['profitability']['net_margin']:.2%}")
            print(f"ROE: {ratios['profitability']['roe']:.2%}")
            print(f"ROA: {ratios['profitability']['roa']:.2%}")
            print(f"Current Ratio: {ratios['liquidity']['current_ratio']:.2f}")
            print(f"Debt/Equity: {ratios['solvency']['debt_to_equity']:.2f}")
            print()
        
        # Historical performance
        performance = analyzer.analyze_historical_performance(stock_data)
        if performance:
            print("📊 Historical Performance:")
            print("-" * 25)
            print(f"Annualized Return: {performance.get('annualized_return', 0):.2%}")
            print(f"Annualized Volatility: {performance.get('annualized_volatility', 0):.2%}")
            print(f"Sharpe Ratio: {performance.get('sharpe_ratio', 0):.2f}")
            print(f"Max Drawdown: {performance.get('max_drawdown', 0):.2%}")
            print()
        
        # Create visualizations
        print("📊 Creating visualizations...")
        try:
            # Plot stock price history
            visualizer.plot_stock_price_history(stock_data)
            
            # Plot financial ratios
            visualizer.plot_financial_ratios(stock_data)
            
            # Create interactive dashboard
            visualizer.create_interactive_dashboard(stock_data)
            
        except Exception as e:
            print(f"Visualization error: {str(e)}")
    
    # Example 2: Screen multiple stocks
    print("\n🔍 Example 2: Stock Screening")
    print("-" * 40)
    
    # Get S&P 500 tickers (limited for demonstration)
    print("Getting S&P 500 tickers...")
    sp500_tickers = collector.get_sp500_tickers()[:10]  # Limit to first 10 for demo
    
    if sp500_tickers:
        print(f"Analyzing {len(sp500_tickers)} stocks...")
        
        # Collect basic data for multiple stocks
        stocks_basic_data = collector.get_multiple_stocks_data(sp500_tickers)
        
        if not stocks_basic_data.empty:
            print(f"Successfully collected data for {len(stocks_basic_data)} stocks")
            print("\nTop stocks by market cap:")
            top_stocks = stocks_basic_data.nlargest(5, 'market_cap')
            print(top_stocks[['ticker', 'name', 'market_cap', 'pe_ratio', 'price_to_book']].to_string(index=False))
            
            # Screen stocks based on basic criteria
            print("\n🔍 Screening stocks...")
            screened_stocks = stocks_basic_data[
                (stocks_basic_data['pe_ratio'] > 0) & 
                (stocks_basic_data['pe_ratio'] < 20) &
                (stocks_basic_data['price_to_book'] > 0) &
                (stocks_basic_data['price_to_book'] < 2)
            ]
            
            if not screened_stocks.empty:
                print(f"\nFound {len(screened_stocks)} stocks meeting basic criteria:")
                print(screened_stocks[['ticker', 'name', 'pe_ratio', 'price_to_book']].to_string(index=False))
                
                # Create comparison visualization
                try:
                    # Prepare data for visualization
                    comparison_data = []
                    for ticker in screened_stocks['ticker'].head(5):  # Limit to top 5
                        stock_data = collector.get_all_data(ticker)
                        if stock_data['basic_info']:
                            comparison_data.append(stock_data)
                    
                    if comparison_data:
                        visualizer.plot_valuation_comparison(comparison_data)
                        
                except Exception as e:
                    print(f"Comparison visualization error: {str(e)}")
            else:
                print("No stocks met the screening criteria.")
    
    # Example 3: Export results
    print("\n💾 Example 3: Data Export")
    print("-" * 40)
    
    if stock_data['basic_info']:
        # Create comprehensive analysis DataFrame
        analysis_data = {
            'ticker': [ticker],
            'company_name': [stock_data['basic_info']['name']],
            'sector': [stock_data['basic_info']['sector']],
            'market_cap': [stock_data['basic_info']['market_cap']],
            'current_price': [stock_data['basic_info']['current_price']],
            'pe_ratio': [stock_data['basic_info']['pe_ratio']],
            'price_to_book': [stock_data['basic_info']['price_to_book']],
            'graham_score': [composite_results.get('graham_valuation', {}).get('graham_score', 0) if composite_results else 0],
            'buffett_score': [composite_results.get('buffett_metrics', {}).get('buffett_score', 0) if composite_results else 0],
            'composite_score': [composite_results.get('composite_score', 0) if composite_results else 0],
            'recommendation': [composite_results.get('recommendation', 'N/A') if composite_results else 'N/A']
        }
        
        analysis_df = pd.DataFrame(analysis_data)
        
        # Export to Excel
        excel_filename = f"{ticker}_analysis.xlsx"
        with pd.ExcelWriter(excel_filename, engine='openpyxl') as writer:
            analysis_df.to_excel(writer, sheet_name='Valuation_Summary', index=False)
            
            if stock_data['financials'].get('income_statement') is not None:
                stock_data['financials']['income_statement'].to_excel(writer, sheet_name='Income_Statement')
            
            if stock_data['financials'].get('balance_sheet') is not None:
                stock_data['financials']['balance_sheet'].to_excel(writer, sheet_name='Balance_Sheet')
            
            if stock_data['financials'].get('cash_flow') is not None:
                stock_data['financials']['cash_flow'].to_excel(writer, sheet_name='Cash_Flow')
        
        print(f"Analysis exported to {excel_filename}")
    
    print("\n✅ Financial Analysis Complete!")
    print("\n📚 Framework Features:")
    print("• Benjamin Graham: Intrinsic value calculation and margin of safety")
    print("• Damodaran: DCF valuation with WACC and terminal value")
    print("• Warren Buffett: Quality metrics (ROE, ROA, debt ratios)")
    print("• Composite scoring system for investment decisions")
    print("• Comprehensive financial ratio analysis")
    print("• Historical performance metrics")
    print("• Interactive visualizations and charts")
    print("• Stock screening capabilities")
    print("• Data export to Excel")


if __name__ == "__main__":
    main()

