"""
Analysis and Visualization Module
Provides tools for analyzing financial data and creating visualizations
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

# Set style for matplotlib
plt.style.use('seaborn-v0_8')
sns.set_palette("husl")


class FinancialAnalyzer:
    """Analyzes financial data and provides insights"""
    
    def __init__(self):
        pass
    
    def calculate_financial_ratios(self, stock_data: Dict) -> Dict:
        """
        Calculate comprehensive financial ratios
        
        Args:
            stock_data (Dict): Stock data
            
        Returns:
            Dict: Financial ratios
        """
        try:
            basic_info = stock_data.get('basic_info', {})
            financials = stock_data.get('financials', {})
            
            if not basic_info or not financials:
                return {}
            
            income_stmt = financials.get('income_statement', pd.DataFrame())
            balance_sheet = financials.get('balance_sheet', pd.DataFrame())
            cash_flow = financials.get('cash_flow', pd.DataFrame())
            
            if income_stmt.empty or balance_sheet.empty:
                return {}
            
            # Get latest annual data
            if len(income_stmt.columns) > 0:
                latest_year = income_stmt.columns[0]
                
                # Extract key financial data
                net_income = income_stmt.loc['Net Income', latest_year] if 'Net Income' in income_stmt.index else 0
                total_revenue = income_stmt.loc['Total Revenue', latest_year] if 'Total Revenue' in income_stmt.index else 0
                total_assets = balance_sheet.loc['Total Assets', latest_year] if 'Total Assets' in balance_sheet.index else 0
                total_equity = balance_sheet.loc['Stockholders Equity', latest_year] if 'Stockholders Equity' in balance_sheet.index else 0
                total_debt = balance_sheet.loc['Total Debt', latest_year] if 'Total Debt' in balance_sheet.index else 0
                current_assets = balance_sheet.loc['Current Assets', latest_year] if 'Current Assets' in balance_sheet.index else 0
                current_liabilities = balance_sheet.loc['Current Liabilities', latest_year] if 'Current Liabilities' in balance_sheet.index else 0
                
                # Calculate ratios
                ratios = {
                    'profitability': {
                        'net_margin': net_income / total_revenue if total_revenue > 0 else 0,
                        'roe': net_income / total_equity if total_equity > 0 else 0,
                        'roa': net_income / total_assets if total_assets > 0 else 0
                    },
                    'liquidity': {
                        'current_ratio': current_assets / current_liabilities if current_liabilities > 0 else 0,
                        'quick_ratio': (current_assets - 0) / current_liabilities if current_liabilities > 0 else 0  # Simplified
                    },
                    'solvency': {
                        'debt_to_equity': total_debt / total_equity if total_equity > 0 else 0,
                        'debt_to_assets': total_debt / total_assets if total_assets > 0 else 0
                    },
                    'efficiency': {
                        'asset_turnover': total_revenue / total_assets if total_assets > 0 else 0,
                        'equity_turnover': total_revenue / total_equity if total_equity > 0 else 0
                    }
                }
                
                return ratios
            
            return {}
            
        except Exception as e:
            print(f"Error calculating financial ratios: {str(e)}")
            return {}
    
    def analyze_historical_performance(self, stock_data: Dict) -> Dict:
        """
        Analyze historical stock performance
        
        Args:
            stock_data (Dict): Stock data
            
        Returns:
            Dict: Historical performance metrics
        """
        try:
            historical_data = stock_data.get('historical_data', pd.DataFrame())
            
            if historical_data.empty:
                return {}
            
            # Calculate performance metrics
            if len(historical_data) > 1:
                # Returns
                daily_returns = historical_data['Close'].pct_change().dropna()
                annualized_return = daily_returns.mean() * 252
                annualized_volatility = daily_returns.std() * np.sqrt(252)
                
                # Risk metrics
                sharpe_ratio = annualized_return / annualized_volatility if annualized_volatility > 0 else 0
                max_drawdown = self._calculate_max_drawdown(historical_data['Close'])
                
                # Price metrics
                current_price = historical_data['Close'].iloc[-1]
                price_52w_high = historical_data['High'].rolling(window=252).max().iloc[-1]
                price_52w_low = historical_data['Low'].rolling(window=252).min().iloc[-1]
                
                performance_metrics = {
                    'annualized_return': annualized_return,
                    'annualized_volatility': annualized_volatility,
                    'sharpe_ratio': sharpe_ratio,
                    'max_drawdown': max_drawdown,
                    'current_price': current_price,
                    '52w_high': price_52w_high,
                    '52w_low': price_52w_low,
                    'price_range_52w': (current_price - price_52w_low) / (price_52w_high - price_52w_low) if price_52w_high != price_52w_low else 0
                }
                
                return performance_metrics
            
            return {}
            
        except Exception as e:
            print(f"Error analyzing historical performance: {str(e)}")
            return {}
    
    def _calculate_max_drawdown(self, prices: pd.Series) -> float:
        """Calculate maximum drawdown"""
        peak = prices.expanding().max()
        drawdown = (prices - peak) / peak
        return drawdown.min()
    
    def compare_stocks(self, stocks_data: List[Dict]) -> pd.DataFrame:
        """
        Compare multiple stocks across key metrics
        
        Args:
            stocks_data (List[Dict]): List of stock data dictionaries
            
        Returns:
            pd.DataFrame: Comparison DataFrame
        """
        comparison_data = []
        
        for stock_data in stocks_data:
            if stock_data.get('basic_info'):
                basic_info = stock_data['basic_info']
                ratios = self.calculate_financial_ratios(stock_data)
                performance = self.analyze_historical_performance(stock_data)
                
                comparison_row = {
                    'ticker': basic_info.get('ticker', 'N/A'),
                    'name': basic_info.get('name', 'N/A'),
                    'sector': basic_info.get('sector', 'N/A'),
                    'market_cap': basic_info.get('market_cap', 0),
                    'pe_ratio': basic_info.get('pe_ratio', 0),
                    'price_to_book': basic_info.get('price_to_book', 0),
                    'roe': ratios.get('profitability', {}).get('roe', 0),
                    'roa': ratios.get('profitability', {}).get('roa', 0),
                    'debt_to_equity': ratios.get('solvency', {}).get('debt_to_equity', 0),
                    'current_ratio': ratios.get('liquidity', {}).get('current_ratio', 0),
                    'annualized_return': performance.get('annualized_return', 0),
                    'sharpe_ratio': performance.get('sharpe_ratio', 0),
                    'max_drawdown': performance.get('max_drawdown', 0)
                }
                
                comparison_data.append(comparison_row)
        
        return pd.DataFrame(comparison_data)


class FinancialVisualizer:
    """Creates financial data visualizations"""
    
    def __init__(self):
        pass
    
    def plot_stock_price_history(self, stock_data: Dict, save_path: Optional[str] = None):
        """
        Plot stock price history
        
        Args:
            stock_data (Dict): Stock data
            save_path (Optional[str]): Path to save the plot
        """
        try:
            historical_data = stock_data.get('historical_data', pd.DataFrame())
            basic_info = stock_data.get('basic_info', {})
            
            if historical_data.empty:
                print("No historical data available")
                return
            
            ticker = basic_info.get('ticker', 'Unknown')
            
            # Create plot
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
            
            # Price plot
            ax1.plot(historical_data.index, historical_data['Close'], label='Close Price', linewidth=2)
            ax1.plot(historical_data.index, historical_data['High'], label='High', alpha=0.7)
            ax1.plot(historical_data.index, historical_data['Low'], label='Low', alpha=0.7)
            ax1.fill_between(historical_data.index, historical_data['Low'], historical_data['High'], alpha=0.1)
            ax1.set_title(f'{ticker} Stock Price History', fontsize=16, fontweight='bold')
            ax1.set_ylabel('Price ($)', fontsize=12)
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # Volume plot
            ax2.bar(historical_data.index, historical_data['Volume'], alpha=0.7, color='blue')
            ax2.set_title(f'{ticker} Trading Volume', fontsize=14, fontweight='bold')
            ax2.set_ylabel('Volume', fontsize=12)
            ax2.set_xlabel('Date', fontsize=12)
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            
            if save_path:
                plt.savefig(save_path, dpi=300, bbox_inches='tight')
            
            plt.show()
            
        except Exception as e:
            print(f"Error plotting stock price history: {str(e)}")
    
    def plot_financial_ratios(self, stock_data: Dict, save_path: Optional[str] = None):
        """
        Plot financial ratios
        
        Args:
            stock_data (Dict): Stock data
            save_path (Optional[str]): Path to save the plot
        """
        try:
            ratios = FinancialAnalyzer().calculate_financial_ratios(stock_data)
            basic_info = stock_data.get('basic_info', {})
            
            if not ratios:
                print("No financial ratios available")
                return
            
            ticker = basic_info.get('ticker', 'Unknown')
            
            # Create subplots for different ratio categories
            fig, axes = plt.subplots(2, 2, figsize=(15, 12))
            fig.suptitle(f'{ticker} Financial Ratios Analysis', fontsize=16, fontweight='bold')
            
            # Profitability ratios
            profitability_data = list(ratios['profitability'].values())
            profitability_labels = list(ratios['profitability'].keys())
            axes[0, 0].bar(profitability_labels, profitability_data, color=['green', 'blue', 'orange'])
            axes[0, 0].set_title('Profitability Ratios', fontweight='bold')
            axes[0, 0].set_ylabel('Ratio Value')
            axes[0, 0].tick_params(axis='x', rotation=45)
            
            # Liquidity ratios
            liquidity_data = list(ratios['liquidity'].values())
            liquidity_labels = list(ratios['liquidity'].keys())
            axes[0, 1].bar(liquidity_labels, liquidity_data, color=['cyan', 'purple'])
            axes[0, 1].set_title('Liquidity Ratios', fontweight='bold')
            axes[0, 1].set_ylabel('Ratio Value')
            axes[0, 1].tick_params(axis='x', rotation=45)
            
            # Solvency ratios
            solvency_data = list(ratios['solvency'].values())
            solvency_labels = list(ratios['solvency'].keys())
            axes[1, 0].bar(solvency_labels, solvency_data, color=['red', 'brown'])
            axes[1, 0].set_title('Solvency Ratios', fontweight='bold')
            axes[1, 0].set_ylabel('Ratio Value')
            axes[1, 0].tick_params(axis='x', rotation=45)
            
            # Efficiency ratios
            efficiency_data = list(ratios['efficiency'].values())
            efficiency_labels = list(ratios['efficiency'].keys())
            axes[1, 1].bar(efficiency_labels, efficiency_data, color=['pink', 'yellow'])
            axes[1, 1].set_title('Efficiency Ratios', fontweight='bold')
            axes[1, 1].set_ylabel('Ratio Value')
            axes[1, 1].tick_params(axis='x', rotation=45)
            
            plt.tight_layout()
            
            if save_path:
                plt.savefig(save_path, dpi=300, bbox_inches='tight')
            
            plt.show()
            
        except Exception as e:
            print(f"Error plotting financial ratios: {str(e)}")
    
    def plot_valuation_comparison(self, stocks_data: List[Dict], save_path: Optional[str] = None):
        """
        Plot valuation comparison across multiple stocks
        
        Args:
            stocks_data (List[Dict]): List of stock data dictionaries
            save_path (Optional[str]): Path to save the plot
        """
        try:
            if not stocks_data:
                print("No stock data available")
                return
            
            # Extract key metrics for comparison
            comparison_data = []
            for stock_data in stocks_data:
                if stock_data.get('basic_info'):
                    basic_info = stock_data['basic_info']
                    comparison_data.append({
                        'ticker': basic_info.get('ticker', 'N/A'),
                        'pe_ratio': basic_info.get('pe_ratio', 0),
                        'price_to_book': basic_info.get('price_to_book', 0),
                        'market_cap': basic_info.get('market_cap', 0)
                    })
            
            if not comparison_data:
                print("No comparison data available")
                return
            
            df = pd.DataFrame(comparison_data)
            
            # Create comparison plots
            fig, axes = plt.subplots(2, 2, figsize=(15, 12))
            fig.suptitle('Stock Valuation Comparison', fontsize=16, fontweight='bold')
            
            # P/E Ratio comparison
            axes[0, 0].bar(df['ticker'], df['pe_ratio'], color='skyblue')
            axes[0, 0].set_title('P/E Ratio Comparison', fontweight='bold')
            axes[0, 0].set_ylabel('P/E Ratio')
            axes[0, 0].tick_params(axis='x', rotation=45)
            
            # Price to Book comparison
            axes[0, 1].bar(df['ticker'], df['price_to_book'], color='lightgreen')
            axes[0, 1].set_title('Price to Book Comparison', fontweight='bold')
            axes[0, 1].set_ylabel('P/B Ratio')
            axes[0, 1].tick_params(axis='x', rotation=45)
            
            # Market Cap comparison (log scale)
            axes[1, 0].bar(df['ticker'], np.log10(df['market_cap']), color='orange')
            axes[1, 0].set_title('Market Cap Comparison (Log Scale)', fontweight='bold')
            axes[1, 0].set_ylabel('Log10(Market Cap)')
            axes[1, 0].tick_params(axis='x', rotation=45)
            
            # Scatter plot: P/E vs P/B
            axes[1, 1].scatter(df['pe_ratio'], df['price_to_book'], s=100, alpha=0.7)
            for i, ticker in enumerate(df['ticker']):
                axes[1, 1].annotate(ticker, (df['pe_ratio'].iloc[i], df['price_to_book'].iloc[i]))
            axes[1, 1].set_xlabel('P/E Ratio')
            axes[1, 1].set_ylabel('Price to Book Ratio')
            axes[1, 1].set_title('P/E vs P/B Scatter Plot', fontweight='bold')
            axes[1, 1].grid(True, alpha=0.3)
            
            plt.tight_layout()
            
            if save_path:
                plt.savefig(save_path, dpi=300, bbox_inches='tight')
            
            plt.show()
            
        except Exception as e:
            print(f"Error plotting valuation comparison: {str(e)}")
    
    def create_interactive_dashboard(self, stock_data: Dict):
        """
        Create an interactive Plotly dashboard
        
        Args:
            stock_data (Dict): Stock data
        """
        try:
            historical_data = stock_data.get('historical_data', pd.DataFrame())
            basic_info = stock_data.get('basic_info', {})
            
            if historical_data.empty:
                print("No historical data available")
                return
            
            ticker = basic_info.get('ticker', 'Unknown')
            
            # Create interactive price chart
            fig = make_subplots(
                rows=3, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.05,
                subplot_titles=(f'{ticker} Stock Price', 'Volume', 'Returns'),
                row_width=[0.5, 0.25, 0.25]
            )
            
            # Price chart
            fig.add_trace(
                go.Scatter(
                    x=historical_data.index,
                    y=historical_data['Close'],
                    mode='lines',
                    name='Close Price',
                    line=dict(color='blue', width=2)
                ),
                row=1, col=1
            )
            
            # Volume chart
            fig.add_trace(
                go.Bar(
                    x=historical_data.index,
                    y=historical_data['Volume'],
                    name='Volume',
                    marker_color='lightblue'
                ),
                row=2, col=1
            )
            
            # Returns chart
            returns = historical_data['Close'].pct_change().dropna()
            fig.add_trace(
                go.Scatter(
                    x=returns.index,
                    y=returns,
                    mode='lines',
                    name='Daily Returns',
                    line=dict(color='green', width=1)
                ),
                row=3, col=1
            )
            
            # Update layout
            fig.update_layout(
                title=f'{ticker} Financial Dashboard',
                height=800,
                showlegend=True,
                xaxis_rangeslider_visible=False
            )
            
            fig.show()
            
        except Exception as e:
            print(f"Error creating interactive dashboard: {str(e)}")
