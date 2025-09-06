# Financial Analysis Framework - Usage Guide

## 🚀 Overview

This framework implements three major investment valuation methodologies:
- **Benjamin Graham**: Value investing with intrinsic value calculation
- **Aswath Damodaran**: DCF valuation with WACC and terminal value
- **Warren Buffett**: Quality metrics and business analysis

## 📦 Installation

1. **Create virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## 🔧 Basic Usage

### 1. Individual Stock Analysis

```python
from src.data_collector import StockDataCollector
from src.valuation_framework import ValuationFramework
from src.analysis_visualization import FinancialAnalyzer

# Initialize components
collector = StockDataCollector()
framework = ValuationFramework()
analyzer = FinancialAnalyzer()

# Get stock data
stock_data = collector.get_stock_info("AAPL")

# Calculate valuations
graham_result = framework.calculate_graham_valuation(stock_data)
buffett_result = framework.calculate_buffett_metrics(stock_data)
dcf_result = framework.calculate_damodaran_dcf(stock_data)
composite_result = framework.calculate_composite_valuation(stock_data)

# Analyze financial ratios
ratios = analyzer.calculate_financial_ratios(stock_data)
```

### 2. Stock Screening

```python
# Get multiple stocks
tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
stocks_data = []

for ticker in tickers:
    data = collector.get_stock_info(ticker)
    if data:
        stocks_data.append(data)

# Screen stocks based on composite score
screened_stocks = framework.screen_stocks(stocks_data, min_score=0.6)
```

### 3. Data Export

```python
from src.analysis_visualization import FinancialVisualizer

visualizer = FinancialVisualizer()

# Create charts
visualizer.plot_stock_price_history(stock_data, "charts/aapl_history.png")
visualizer.plot_financial_ratios(stock_data, "charts/aapl_ratios.png")

# Export to Excel
# (Excel export functionality is included in the main.py example)
```

## 🚨 Handling Yahoo Finance Rate Limiting

The Yahoo Finance API has rate limits that can cause "429 Too Many Requests" errors. Here are solutions:

### Solution 1: Add Delays Between Requests

```python
import time

def get_multiple_stocks_with_delay(tickers, delay_seconds=2):
    results = []
    for ticker in tickers:
        data = collector.get_stock_info(ticker)
        if data:
            results.append(data)
        time.sleep(delay_seconds)  # Wait between requests
    return results
```

### Solution 2: Use Alternative Data Sources

Consider these alternatives when Yahoo Finance is unavailable:

1. **Alpha Vantage** (free tier available)
2. **IEX Cloud** (free tier available)
3. **Polygon.io** (paid service)
4. **Finnhub** (free tier available)

### Solution 3: Implement Retry Logic

```python
import time
from requests.exceptions import HTTPError

def get_stock_data_with_retry(ticker, max_retries=3, delay=5):
    for attempt in range(max_retries):
        try:
            data = collector.get_stock_info(ticker)
            return data
        except HTTPError as e:
            if e.response.status_code == 429:  # Rate limited
                print(f"Rate limited, waiting {delay} seconds...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise e
    return None
```

## 🎯 Customization Options

### 1. Adjust Valuation Parameters

```python
# Set market conditions
framework.set_market_conditions(
    risk_free_rate=0.03,      # 3% risk-free rate
    market_risk_premium=0.07  # 7% market risk premium
)

# Customize DCF growth rate
dcf_result = framework.calculate_damodaran_dcf(
    stock_data, 
    growth_rate=0.06  # 6% growth rate
)
```

### 2. Modify Scoring Weights

Edit `src/valuation_framework.py` to adjust the composite scoring:

```python
# In calculate_composite_valuation method
weights = {
    'graham': 0.4,      # 40% weight to Graham
    'buffett': 0.35,    # 35% weight to Buffett
    'dcf': 0.25         # 25% weight to DCF
}
```

### 3. Add Custom Criteria

```python
def calculate_custom_metrics(stock_data):
    """Add your own investment criteria"""
    custom_score = 0
    
    # Example: High dividend yield preference
    if stock_data.get('dividendYield', 0) > 0.03:
        custom_score += 20
    
    # Example: Low volatility preference
    if stock_data.get('beta', 1) < 1.0:
        custom_score += 15
    
    return custom_score
```

## 📊 Understanding the Output

### Graham Valuation
- **Graham Number**: Intrinsic value based on P/E and P/B ratios
- **Margin of Safety**: Percentage below intrinsic value
- **Score**: 0-100 based on value criteria

### Buffett Metrics
- **ROE**: Return on Equity (target > 15%)
- **ROA**: Return on Assets (target > 10%)
- **Score**: 0-100 based on quality criteria

### DCF Valuation
- **WACC**: Weighted Average Cost of Capital
- **Terminal Value**: Long-term value projection
- **Per Share Value**: Calculated intrinsic value per share

### Composite Score
- **Overall Score**: 0-100 weighted average
- **Recommendation**: Strong Buy, Buy, Hold, Sell, Strong Sell
- **Risk Level**: Low, Medium, High

## 🔍 Troubleshooting

### Common Issues

1. **Import Errors**
   - Ensure you're in the correct directory
   - Check that `src/__init__.py` exists
   - Verify virtual environment is activated

2. **Data Missing**
   - Some stocks may not have complete financial data
   - Check if ticker symbol is correct
   - Verify market hours for real-time data

3. **Calculation Errors**
   - Ensure all required financial fields are present
   - Check for division by zero in ratios
   - Verify data types (numbers vs strings)

### Debug Mode

Enable detailed logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# This will show detailed API calls and calculations
```

## 🚀 Advanced Features

### 1. Portfolio Analysis

```python
def analyze_portfolio(tickers, weights):
    """Analyze a portfolio of stocks"""
    portfolio_score = 0
    portfolio_data = []
    
    for ticker, weight in zip(tickers, weights):
        data = collector.get_stock_info(ticker)
        if data:
            composite = framework.calculate_composite_valuation(data)
            portfolio_score += composite['composite_score'] * weight
            portfolio_data.append({
                'ticker': ticker,
                'weight': weight,
                'score': composite['composite_score']
            })
    
    return portfolio_score, portfolio_data
```

### 2. Sector Analysis

```python
def analyze_sector(sector_name):
    """Analyze all stocks in a sector"""
    # Get sector stocks (implement based on your data source)
    sector_stocks = get_sector_stocks(sector_name)
    
    sector_analysis = []
    for stock in sector_stocks:
        analysis = framework.calculate_composite_valuation(stock)
        sector_analysis.append(analysis)
    
    return sector_analysis
```

### 3. Historical Analysis

```python
def track_stock_performance(ticker, period="1y"):
    """Track how valuation changes over time"""
    historical_data = collector.get_historical_data(ticker, period)
    
    # Analyze at different points in time
    # (Implementation depends on your specific needs)
    return historical_data
```

## 📈 Best Practices

1. **Rate Limiting**: Always implement delays between API calls
2. **Error Handling**: Use try-catch blocks for robust operation
3. **Data Validation**: Check data quality before calculations
4. **Regular Updates**: Refresh data regularly for accurate analysis
5. **Multiple Sources**: Consider using multiple data sources for validation

## 🔗 Additional Resources

- **Benjamin Graham**: "The Intelligent Investor"
- **Aswath Damodaran**: "Investment Valuation"
- **Warren Buffett**: Berkshire Hathaway annual letters
- **Yahoo Finance API**: [Documentation](https://finance.yahoo.com/apis/)
- **Financial Ratios**: [Investopedia Guide](https://www.investopedia.com/)

## 💡 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review the example files in the repository
3. Check Yahoo Finance API status
4. Consider using mock data for testing

---

**Happy Investing! 📈💰**

