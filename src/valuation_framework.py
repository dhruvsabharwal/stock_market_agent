"""
Valuation Framework Module
Implements valuation methods from Benjamin Graham, Damodaran, and Warren Buffett
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')


class ValuationFramework:
    """Comprehensive valuation framework using multiple methodologies"""
    
    def __init__(self):
        self.risk_free_rate = 0.05  # Default 5% risk-free rate
        self.market_risk_premium = 0.06  # Default 6% market risk premium
        
    def set_market_conditions(self, risk_free_rate: float, market_risk_premium: float):
        """Set market conditions for valuation calculations"""
        self.risk_free_rate = risk_free_rate
        self.market_risk_premium = market_risk_premium
    
    def calculate_graham_number(self, stock_data: Dict) -> float:
        """
        Calculate Benjamin Graham's Intrinsic Value Formula
        Graham Number = sqrt(22.5 * EPS * Book Value per Share)
        
        Args:
            stock_data (Dict): Stock data containing financial metrics
            
        Returns:
            float: Graham Number (intrinsic value)
        """
        try:
            # Get financial statements
            financials = stock_data.get('financials', {})
            if not financials:
                return None
            
            income_stmt = financials.get('income_statement', pd.DataFrame())
            balance_sheet = financials.get('balance_sheet', pd.DataFrame())
            
            if income_stmt.empty or balance_sheet.empty:
                return None
            
            # Get latest annual data
            if len(income_stmt.columns) > 0:
                latest_year = income_stmt.columns[0]
                
                # Net Income (EPS * Shares Outstanding)
                net_income = income_stmt.loc['Net Income', latest_year] if 'Net Income' in income_stmt.index else 0
                
                # Book Value
                total_equity = balance_sheet.loc['Stockholders Equity', latest_year] if 'Stockholders Equity' in balance_sheet.index else 0
                
                # Get shares outstanding from basic info
                basic_info = stock_data.get('basic_info', {})
                market_cap = basic_info.get('market_cap', 0)
                current_price = basic_info.get('current_price', 0)
                
                if market_cap > 0 and current_price > 0:
                    shares_outstanding = market_cap / current_price
                    
                    if shares_outstanding > 0:
                        eps = net_income / shares_outstanding
                        book_value_per_share = total_equity / shares_outstanding
                        
                        # Graham Number calculation
                        graham_number = np.sqrt(22.5 * eps * book_value_per_share)
                        return graham_number
            
            return None
            
        except Exception as e:
            print(f"Error calculating Graham Number: {str(e)}")
            return None
    
    def calculate_graham_valuation(self, stock_data: Dict) -> Dict:
        """
        Comprehensive Graham valuation analysis
        
        Args:
            stock_data (Dict): Stock data
            
        Returns:
            Dict: Graham valuation metrics
        """
        try:
            basic_info = stock_data.get('basic_info', {})
            financials = stock_data.get('financials', {})
            
            if not basic_info or not financials:
                return {}
            
            # Get key metrics
            current_price = basic_info.get('current_price', 0)
            pe_ratio = basic_info.get('pe_ratio', 0)
            price_to_book = basic_info.get('price_to_book', 0)
            
            # Graham Number
            graham_number = self.calculate_graham_number(stock_data)
            
            # Graham's criteria
            graham_criteria = {
                'pe_ratio_under_15': pe_ratio < 15 if pe_ratio > 0 else False,
                'price_to_book_under_1_5': price_to_book < 1.5 if price_to_book > 0 else False,
                'debt_to_equity_under_0_5': True,  # Would need balance sheet data
                'current_ratio_above_2': True,  # Would need balance sheet data
                'positive_earnings': pe_ratio > 0 if pe_ratio != 0 else False
            }
            
            # Calculate margin of safety
            margin_of_safety = None
            if graham_number and current_price > 0:
                margin_of_safety = (graham_number - current_price) / graham_number
            
            graham_valuation = {
                'graham_number': graham_number,
                'current_price': current_price,
                'margin_of_safety': margin_of_safety,
                'graham_criteria': graham_criteria,
                'graham_score': sum(graham_criteria.values()) / len(graham_criteria)
            }
            
            return graham_valuation
            
        except Exception as e:
            print(f"Error in Graham valuation: {str(e)}")
            return {}
    
    def calculate_damodaran_dcf(self, stock_data: Dict, growth_rate: float = 0.05) -> Dict:
        """
        Calculate Damodaran's DCF valuation
        
        Args:
            stock_data (Dict): Stock data
            growth_rate (float): Terminal growth rate
            
        Returns:
            Dict: DCF valuation results
        """
        try:
            financials = stock_data.get('financials', {})
            basic_info = stock_data.get('basic_info', {})
            
            if not financials or not basic_info:
                return {}
            
            income_stmt = financials.get('income_statement', pd.DataFrame())
            balance_sheet = financials.get('balance_sheet', pd.DataFrame())
            cash_flow = financials.get('cash_flow', pd.DataFrame())
            
            if income_stmt.empty or balance_sheet.empty or cash_flow.empty:
                return {}
            
            # Get latest annual data
            if len(income_stmt.columns) > 0:
                latest_year = income_stmt.columns[0]
                
                # Key financial metrics
                net_income = income_stmt.loc['Net Income', latest_year] if 'Net Income' in income_stmt.index else 0
                total_assets = balance_sheet.loc['Total Assets', latest_year] if 'Total Assets' in balance_sheet.index else 0
                total_equity = balance_sheet.loc['Stockholders Equity', latest_year] if 'Stockholders Equity' in balance_sheet.index else 0
                operating_cash_flow = cash_flow.loc['Operating Cash Flow', latest_year] if 'Operating Cash Flow' in cash_flow.index else 0
                
                # Calculate ROE and ROA
                roe = net_income / total_equity if total_equity > 0 else 0
                roa = net_income / total_assets if total_assets > 0 else 0
                
                # Calculate cost of equity using CAPM
                beta = basic_info.get('beta', 1.0)
                cost_of_equity = self.risk_free_rate + beta * self.market_risk_premium
                
                # Calculate cost of debt (simplified)
                cost_of_debt = self.risk_free_rate + 0.02  # 2% spread
                
                # Calculate WACC (simplified)
                debt_to_equity = 0.3  # Assumption
                wacc = (cost_of_equity * (1 - debt_to_equity) + cost_of_debt * debt_to_equity * 0.7)
                
                # DCF calculation (simplified)
                fcf = operating_cash_flow * 0.8  # Convert to FCF
                
                # 5-year projection
                years = 5
                projected_fcf = []
                for i in range(years):
                    projected_fcf.append(fcf * (1 + growth_rate) ** (i + 1))
                
                # Terminal value
                terminal_value = projected_fcf[-1] * (1 + growth_rate) / (wacc - growth_rate)
                
                # Present value calculation
                present_values = []
                for i, fcf_year in enumerate(projected_fcf):
                    pv = fcf_year / ((1 + wacc) ** (i + 1))
                    present_values.append(pv)
                
                # Add terminal value
                terminal_pv = terminal_value / ((1 + wacc) ** years)
                present_values.append(terminal_pv)
                
                # Total enterprise value
                enterprise_value = sum(present_values)
                
                # Equity value (subtract net debt)
                net_debt = 0  # Would need actual debt data
                equity_value = enterprise_value - net_debt
                
                # Per share value
                shares_outstanding = basic_info.get('market_cap', 0) / basic_info.get('current_price', 1) if basic_info.get('current_price', 0) > 0 else 0
                per_share_value = equity_value / shares_outstanding if shares_outstanding > 0 else 0
                
                dcf_results = {
                    'enterprise_value': enterprise_value,
                    'equity_value': equity_value,
                    'per_share_value': per_share_value,
                    'wacc': wacc,
                    'roe': roe,
                    'roa': roa,
                    'cost_of_equity': cost_of_equity,
                    'projected_fcf': projected_fcf,
                    'terminal_value': terminal_value
                }
                
                return dcf_results
            
            return {}
            
        except Exception as e:
            print(f"Error in Damodaran DCF: {str(e)}")
            return {}
    
    def calculate_buffett_metrics(self, stock_data: Dict) -> Dict:
        """
        Calculate Warren Buffett's key investment metrics
        
        Args:
            stock_data (Dict): Stock data
            
        Returns:
            Dict: Buffett metrics
        """
        try:
            basic_info = stock_data.get('basic_info', {})
            financials = stock_data.get('financials', {})
            
            if not basic_info or not financials:
                return {}
            
            income_stmt = financials.get('income_statement', pd.DataFrame())
            balance_sheet = financials.get('balance_sheet', pd.DataFrame())
            
            if income_stmt.empty or balance_sheet.empty:
                return {}
            
            # Get latest annual data
            if len(income_stmt.columns) > 0:
                latest_year = income_stmt.columns[0]
                
                # Key Buffett metrics
                net_income = income_stmt.loc['Net Income', latest_year] if 'Net Income' in income_stmt.index else 0
                total_assets = balance_sheet.loc['Total Assets', latest_year] if 'Total Assets' in balance_sheet.index else 0
                total_equity = balance_sheet.loc['Stockholders Equity', latest_year] if 'Stockholders Equity' in balance_sheet.index else 0
                current_price = basic_info.get('current_price', 0)
                market_cap = basic_info.get('market_cap', 0)
                
                # Calculate Buffett metrics
                roe = net_income / total_equity if total_equity > 0 else 0
                roa = net_income / total_assets if total_assets > 0 else 0
                
                # Return on invested capital (simplified)
                roic = roe  # Simplified calculation
                
                # Debt to equity ratio
                total_debt = balance_sheet.loc['Total Debt', latest_year] if 'Total Debt' in balance_sheet.index else 0
                debt_to_equity = total_debt / total_equity if total_equity > 0 else 0
                
                # Current ratio
                current_assets = balance_sheet.loc['Current Assets', latest_year] if 'Current Assets' in balance_sheet.index else 0
                current_liabilities = balance_sheet.loc['Current Liabilities', latest_year] if 'Current Liabilities' in balance_sheet.index else 0
                current_ratio = current_assets / current_liabilities if current_liabilities > 0 else 0
                
                # Buffett's criteria
                buffett_criteria = {
                    'roe_above_15': roe > 0.15,
                    'roa_above_10': roa > 0.10,
                    'debt_to_equity_below_0_5': debt_to_equity < 0.5,
                    'current_ratio_above_1_5': current_ratio > 1.5,
                    'positive_earnings': net_income > 0,
                    'consistent_earnings': True  # Would need historical data
                }
                
                # Calculate Buffett score
                buffett_score = sum(buffett_criteria.values()) / len(buffett_criteria)
                
                buffett_metrics = {
                    'roe': roe,
                    'roa': roa,
                    'roic': roic,
                    'debt_to_equity': debt_to_equity,
                    'current_ratio': current_ratio,
                    'buffett_criteria': buffett_criteria,
                    'buffett_score': buffett_score
                }
                
                return buffett_metrics
            
            return {}
            
        except Exception as e:
            print(f"Error in Buffett metrics: {str(e)}")
            return {}
    
    def calculate_composite_valuation(self, stock_data: Dict) -> Dict:
        """
        Calculate composite valuation using all three methodologies
        
        Args:
            stock_data (Dict): Stock data
            
        Returns:
            Dict: Composite valuation results
        """
        try:
            # Get individual valuations
            graham_valuation = self.calculate_graham_valuation(stock_data)
            damodaran_dcf = self.calculate_damodaran_dcf(stock_data)
            buffett_metrics = self.calculate_buffett_metrics(stock_data)
            
            # Calculate composite score
            composite_score = 0
            total_weight = 0
            
            if graham_valuation:
                graham_weight = 0.4
                composite_score += graham_valuation.get('graham_score', 0) * graham_weight
                total_weight += graham_weight
            
            if damodaran_dcf:
                damodaran_weight = 0.35
                # Simple DCF score based on margin of safety
                current_price = stock_data.get('basic_info', {}).get('current_price', 0)
                dcf_value = damodaran_dcf.get('per_share_value', 0)
                if current_price > 0 and dcf_value > 0:
                    dcf_margin = (dcf_value - current_price) / dcf_value
                    dcf_score = min(1.0, max(0.0, (dcf_margin + 0.5)))  # Normalize to 0-1
                    composite_score += dcf_score * damodaran_weight
                    total_weight += damodaran_weight
            
            if buffett_metrics:
                buffett_weight = 0.25
                composite_score += buffett_metrics.get('buffett_score', 0) * buffett_weight
                total_weight += buffett_weight
            
            # Normalize composite score
            if total_weight > 0:
                composite_score = composite_score / total_weight
            
            # Determine valuation recommendation
            if composite_score >= 0.8:
                recommendation = "Strong Buy"
            elif composite_score >= 0.6:
                recommendation = "Buy"
            elif composite_score >= 0.4:
                recommendation = "Hold"
            elif composite_score >= 0.2:
                recommendation = "Sell"
            else:
                recommendation = "Strong Sell"
            
            composite_valuation = {
                'composite_score': composite_score,
                'recommendation': recommendation,
                'graham_valuation': graham_valuation,
                'damodaran_dcf': damodaran_dcf,
                'buffett_metrics': buffett_metrics
            }
            
            return composite_valuation
            
        except Exception as e:
            print(f"Error in composite valuation: {str(e)}")
            return {}
    
    def screen_stocks(self, stocks_data: List[Dict], min_score: float = 0.6) -> pd.DataFrame:
        """
        Screen stocks based on composite valuation score
        
        Args:
            stocks_data (List[Dict]): List of stock data dictionaries
            min_score (float): Minimum composite score threshold
            
        Returns:
            pd.DataFrame: Screened stocks with valuations
        """
        screened_stocks = []
        
        for stock_data in stocks_data:
            if stock_data.get('basic_info'):
                composite_valuation = self.calculate_composite_valuation(stock_data)
                
                if composite_valuation and composite_valuation.get('composite_score', 0) >= min_score:
                    stock_info = stock_data['basic_info'].copy()
                    stock_info.update({
                        'composite_score': composite_valuation.get('composite_score', 0),
                        'recommendation': composite_valuation.get('recommendation', 'N/A'),
                        'graham_score': composite_valuation.get('graham_valuation', {}).get('graham_score', 0),
                        'buffett_score': composite_valuation.get('buffett_metrics', {}).get('buffett_score', 0)
                    })
                    screened_stocks.append(stock_info)
        
        return pd.DataFrame(screened_stocks)
