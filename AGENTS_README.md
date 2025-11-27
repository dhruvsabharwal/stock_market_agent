# Financial Analysis Agents

This directory contains two organized agent classes for comprehensive stock analysis:

## 📊 Fundamental Analysis Agent
**Location:** `agents/fundamanetal_analysis_agent/fundamental_analysis.py`

### Features
- **yfinance Integration**: Uses yfinance for reliable financial data
- **Comprehensive Metrics**: Calculates 50+ financial metrics including:
  - Profitability ratios (ROE, ROCE, NPM)
  - Growth metrics (CAGR, SSGR)
  - Valuation ratios (P/E, P/S, P/B)
  - Cash flow analysis (FCF, CFO)
  - Debt analysis (D/E, Interest Coverage)
- **Batch Processing**: Analyze multiple stocks at once
- **File Integration**: Read stock lists from Excel/CSV files

### Key Methods
```python
from fundamental_analysis import FundamentalAnalysis

# Initialize (yfinance only)
analyzer = FundamentalAnalysis()

# Single stock analysis
result = analyzer.analyze_single_stock("AAPL")

# Batch analysis
results = analyzer.analyze_multiple_stocks(["AAPL", "MSFT", "GOOGL"])

# File-based analysis
results = analyzer.analyze_from_file('stocks.xlsx', 'ticker', ['name', 'sector'])
```

## 📈 Technical Analysis Agent
**Location:** `agents/technical_analysis_agent/technical_analysis.py`

### Features
- **5 Core Indicators**:
  1. **Moving Averages** (20, 50, 200-day SMAs)
  2. **MACD** (Moving Average Convergence Divergence)
  3. **RSI** (Relative Strength Index)
  4. **VWMA** (Volume Weighted Moving Average)
  5. **Support & Resistance** (Pivot points analysis)
- **Market Context**: S&P 500 trend analysis, VIX monitoring
- **Relative Strength**: Compare stock vs benchmark performance
- **Position Sizing**: Risk-based position calculation
- **Comprehensive Scoring**: 15-point scoring system with clear signals

### Key Methods
```python
from technical_analysis import TechnicalAnalysis

# Initialize
analyzer = TechnicalAnalysis()

# Complete analysis
results = analyzer.complete_technical_analysis("AAPL")

# Individual indicators
df = analyzer.get_stock_data("AAPL")
df = analyzer.calculate_moving_averages(df)
ma_analysis = analyzer.analyze_moving_averages(df)

# Position sizing
position = analyzer.calculate_position_size(100000, 1.5, 150.0, 140.0)
```

## 🚀 Quick Start

### 1. Single Stock Analysis
```python
from fundamental_analysis import FundamentalAnalysis
from technical_analysis import TechnicalAnalysis

# Initialize both agents
fund_analyzer = FundamentalAnalysis()
tech_analyzer = TechnicalAnalysis()

# Analyze a stock
ticker = "AAPL"

# Fundamental analysis
fund_result = fund_analyzer.analyze_single_stock(ticker)
print(f"P/E Ratio: {fund_result['p/e']}")
print(f"ROCE: {fund_result['ROCE']:.2f}%")

# Technical analysis
tech_result = tech_analyzer.complete_technical_analysis(ticker)
print(f"Technical Score: {tech_result['score_percentage']:.0f}%")
print(f"Signal: {tech_result['overall_signal']}")
```

### 2. Batch Analysis
```python
# Analyze multiple stocks
tickers = ["AAPL", "MSFT", "GOOGL", "NVDA", "META"]

# Fundamental batch analysis
fund_results = fund_analyzer.analyze_multiple_stocks(tickers)

# Technical analysis for each
for ticker in tickers:
    tech_result = tech_analyzer.complete_technical_analysis(ticker)
    print(f"{ticker}: {tech_result['score_percentage']:.0f}% - {tech_result['overall_signal']}")
```

### 3. File-Based Analysis
```python
# Create a CSV with stock data
import pandas as pd
stocks_df = pd.DataFrame({
    'ticker': ['AAPL', 'MSFT', 'GOOGL'],
    'name': ['Apple Inc.', 'Microsoft Corp.', 'Alphabet Inc.'],
    'sector': ['Technology', 'Technology', 'Technology']
})
stocks_df.to_csv('my_stocks.csv', index=False)

# Analyze from file
results = fund_analyzer.analyze_from_file(
    'my_stocks.csv', 
    'ticker', 
    ['name', 'sector'], 
    'my_analysis_results'
)
```

## 📁 File Structure
```
agents/
├── fundamanetal_analysis_agent/
│   ├── fundamental_analysis.py    # Main class
│   └── finprep.ipynb             # Original notebook
├── technical_analysis_agent/
│   ├── technical_analysis.py     # Main class
│   └── technical_analysis.ipynb  # Original notebook
├── test_agents.py                # Test script
├── example_usage.py              # Usage examples
└── AGENTS_README.md              # This file
```

## 🔧 Dependencies
- `pandas` - Data manipulation
- `numpy` - Numerical calculations
- `yfinance` - Stock data from Yahoo Finance
- `datetime` - Date handling

## 📊 Output Files
- **Fundamental Analysis**: Saves individual CSV files in `yfin/` directory
- **Batch Analysis**: Saves combined results in `combined_stocks/` directory
- **Technical Analysis**: Returns structured dictionaries with all metrics

## 🎯 Use Cases

### For Individual Investors
- Analyze single stocks before buying
- Screen multiple stocks for opportunities
- Get position sizing recommendations

### For Portfolio Managers
- Batch analyze large stock lists
- Combine fundamental and technical signals
- Risk management with position sizing

### For Researchers
- Access to 50+ financial metrics
- Customizable technical indicators
- Historical data analysis

## 🔍 Example Output

### Fundamental Analysis
```
Company: Apple Inc.
Current Price: $150.25
Market Cap: $2,400,000,000,000
P/E Ratio: 25.5
ROCE: 18.5%
NPM: 22.1%
```

### Technical Analysis
```
Overall Score: 12/15 (80%)
Signal: 🟢 STRONG BUY
Action: Enter position

Individual Scores:
- Moving Averages: 3/3
- MACD: 2/3  
- RSI: 3/3
- VWMA: 2/3
- Support/Resistance: 2/3
```

## 🚨 Important Notes

1. **Data Source**: Uses yfinance for all financial data - no API keys required.

2. **Rate Limits**: Be mindful of API rate limits when doing batch analysis. The classes include built-in delays.

3. **Data Quality**: Always verify data quality, especially for international stocks or less liquid securities.

4. **Risk Management**: Use position sizing recommendations as guidelines, not absolute rules.

5. **Market Conditions**: Consider overall market conditions when making investment decisions.

## 📞 Support

For questions or issues:
1. Check the example files (`example_usage.py`, `test_agents.py`)
2. Review the class docstrings for detailed method documentation
3. Ensure all dependencies are installed correctly

---

**Happy Analyzing! 📈💰**
