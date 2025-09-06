"""
Data Collector Module for Financial Analysis
Fetches stock data from Yahoo Finance API
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')


class StockDataCollector:
    """Collects comprehensive stock data from Yahoo Finance"""
    
    def __init__(self):
        self.cache = {}
    
    def get_stock_info(self, ticker: str) -> Dict:
        """
        Get basic stock information for a given ticker
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            Dict: Basic stock information
        """
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Extract key information
            stock_info = {
                'ticker': ticker,
                'name': info.get('longName', 'N/A'),
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'market_cap': info.get('marketCap', 0),
                'enterprise_value': info.get('enterpriseValue', 0),
                'pe_ratio': info.get('trailingPE', 0),
                'forward_pe': info.get('forwardPE', 0),
                'price_to_book': info.get('priceToBook', 0),
                'price_to_sales': info.get('priceToSalesTrailing12Months', 0),
                'dividend_yield': info.get('dividendYield', 0),
                'beta': info.get('beta', 0),
                '52_week_high': info.get('fiftyTwoWeekHigh', 0),
                '52_week_low': info.get('fiftyTwoWeekLow', 0),
                'current_price': info.get('currentPrice', 0),
                'volume': info.get('volume', 0),
                'avg_volume': info.get('averageVolume', 0)
            }
            
            return stock_info
            
        except Exception as e:
            print(f"Error fetching info for {ticker}: {str(e)}")
            return None
    
    def get_financial_statements(self, ticker: str, period: str = "annual") -> Dict:
        """
        Get financial statements (income statement, balance sheet, cash flow)
        
        Args:
            ticker (str): Stock ticker symbol
            period (str): 'annual' or 'quarterly'
            
        Returns:
            Dict: Financial statements data
        """
        try:
            stock = yf.Ticker(ticker)
            
            # Get financial statements
            income_stmt = stock.income_stmt
            balance_sheet = stock.balance_sheet
            cash_flow = stock.cashflow
            
            if period == "quarterly":
                income_stmt = stock.quarterly_income_stmt
                balance_sheet = stock.quarterly_balance_sheet
                cash_flow = stock.quarterly_cashflow
            
            financials = {
                'income_statement': income_stmt,
                'balance_sheet': balance_sheet,
                'cash_flow': cash_flow
            }
            
            return financials
            
        except Exception as e:
            print(f"Error fetching financials for {ticker}: {str(e)}")
            return None
    
    def get_historical_data(self, ticker: str, period: str = "5y") -> pd.DataFrame:
        """
        Get historical price data
        
        Args:
            ticker (str): Stock ticker symbol
            period (str): Time period ('1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max')
            
        Returns:
            pd.DataFrame: Historical price data
        """
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)
            return hist
        except Exception as e:
            print(f"Error fetching historical data for {ticker}: {str(e)}")
            return pd.DataFrame()
    
    def get_analyst_recommendations(self, ticker: str) -> pd.DataFrame:
        """
        Get analyst recommendations
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            pd.DataFrame: Analyst recommendations
        """
        try:
            stock = yf.Ticker(ticker)
            recommendations = stock.recommendations
            return recommendations
        except Exception as e:
            print(f"Error fetching recommendations for {ticker}: {str(e)}")
            return pd.DataFrame()
    
    def get_earnings_dates(self, ticker: str) -> pd.DataFrame:
        """
        Get earnings dates
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            pd.DataFrame: Earnings dates
        """
        try:
            stock = yf.Ticker(ticker)
            earnings_dates = stock.earnings_dates
            return earnings_dates
        except Exception as e:
            print(f"Error fetching earnings dates for {ticker}: {str(e)}")
            return pd.DataFrame()
    
    def get_all_data(self, ticker: str) -> Dict:
        """
        Get comprehensive data for a stock
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            Dict: All available data for the stock
        """
        data = {
            'basic_info': self.get_stock_info(ticker),
            'financials': self.get_financial_statements(ticker),
            'historical_data': self.get_historical_data(ticker),
            'recommendations': self.get_analyst_recommendations(ticker),
            'earnings_dates': self.get_earnings_dates(ticker)
        }
        
        return data
    
    def get_multiple_stocks_data(self, tickers: List[str]) -> pd.DataFrame:
        """
        Get basic information for multiple stocks
        
        Args:
            tickers (List[str]): List of stock ticker symbols
            
        Returns:
            pd.DataFrame: DataFrame with basic info for all stocks
        """
        all_data = []
        
        for ticker in tickers:
            info = self.get_stock_info(ticker)
            if info:
                all_data.append(info)
        
        return pd.DataFrame(all_data)
    
    def get_sp500_tickers(self) -> List[str]:
        """
        Get S&P 500 ticker symbols
        
        Returns:
            List[str]: List of S&P 500 tickers
        """
        try:
            # Using Wikipedia to get S&P 500 constituents
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            tables = pd.read_html(url)
            sp500_table = tables[0]
            tickers = sp500_table['Symbol'].tolist()
            return tickers
        except Exception as e:
            print(f"Error fetching S&P 500 tickers: {str(e)}")
            return []
    
    def get_nasdaq100_tickers(self) -> List[str]:
        """
        Get NASDAQ-100 ticker symbols
        
        Returns:
            List[str]: List of NASDAQ-100 tickers
        """
        try:
            # Using Wikipedia to get NASDAQ-100 constituents
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            tables = pd.read_html(url)
            nasdaq_table = tables[1]  # Usually the second table contains the constituents
            tickers = nasdaq_table['Ticker'].tolist()
            return tickers
        except Exception as e:
            print(f"Error fetching NASDAQ-100 tickers: {str(e)}")
            return []
