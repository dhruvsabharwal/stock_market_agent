"""
Fundamental Analysis Agent
A comprehensive class for fundamental analysis of stocks using yfinance.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf
import time
import os
import asyncio


class FundamentalAnalysis:
    """
    A comprehensive fundamental analysis class that provides various financial metrics
    and analysis capabilities for stock evaluation using yfinance.
    """
    
    def __init__(self):
        """
        Initialize the FundamentalAnalysis class.
        """
        pass
        
    def calculate_cagr(self, start_value, end_value, periods):
        """
        Calculate Compound Annual Growth Rate (CAGR).
        
        Args:
            start_value (float): Starting value
            end_value (float): Ending value
            periods (int): Number of periods
            
        Returns:
            float: CAGR percentage
        """
        if start_value <= 0 or end_value <= 0 or periods <= 0:
            return np.nan
        cagr = ((end_value / start_value) ** (1 / periods) - 1) * 100
        return cagr
    
    def get_means(self, num_list, min_n=2):
        """
        Calculate mean with minimum number requirement.
        
        Args:
            num_list (list): List of numbers
            min_n (int): Minimum number of entries required
            
        Returns:
            float: Mean value or -1 if insufficient data
        """
        num_list = [c for c in num_list if self.null_check(c)]
        mean_cal = np.mean(num_list)
        
        if self.null_check(mean_cal) and len(num_list) >= min_n:
            return mean_cal
        else:
            return -1
    
    def null_check(self, c):
        """
        Check if value is not null/NaN.
        
        Args:
            c: Value to check
            
        Returns:
            bool: True if value is valid
        """
        if c != np.nan and c is not None and pd.notnull(c):
            return True
        else:
            return False
    
    def get_yfinance_data(self, ticker):
        """
        Retrieve stock data from yfinance.
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            yfinance.Ticker: yfinance ticker object
        """
        return yf.Ticker(ticker)
    
    async def compute_yfinance_metrics(self, stock, years=5):
        """
        Compute fundamental metrics from yfinance data.
        
        Args:
            stock: yfinance.Ticker object
            years (int): Number of years of data to analyze
            
        Returns:
            dict: Computed metrics
        """
                #stock = yf.Ticker(ticker)
        # Fetch data
        income = stock.financials.transpose()  # Transpose to rows=years, columns=items; recent first
        balance = stock.balance_sheet.transpose()
        cashflow = stock.cashflow.transpose()
        info = stock.info  # Dict for quote/profile/metrics
        
        # Earnings history for EPS/Revenue (annual)
        #earnings = stock.earnings  # pd.DataFrame with Revenue, Earnings
        
        if income.empty or balance.empty or cashflow.empty:
            return "Error fetching data for this ticker"
        
        # Reset index to make date a column; sort oldest first for calculations

        

        income = income.reset_index().rename(columns={'index': 'Date'})
        balance = balance.reset_index().rename(columns={'index': 'Date'})
        cashflow = cashflow.reset_index().rename(columns={'index': 'Date'})
        
        income['Date'] = pd.to_datetime(income['Date'])
        balance['Date'] = pd.to_datetime(balance['Date'])
        cashflow['Date'] = pd.to_datetime(cashflow['Date'])

        income = income.sort_values('Date')
        balance = balance.sort_values('Date')
        cashflow = cashflow.sort_values('Date')
        # Current year index (latest)
        latest = -1
        
        # Available years
        num_years = len(income)
        
        # Compute metrics with defaults for missing data
        metrics = {}
        metrics['ticker'] = stock.ticker
        metrics['longName'] =stock.info['longName']
        # Return on Equity (ROE) - Latest and 3-year average
        net_income = income.get('Net Income', pd.Series([0]*num_years))
        equity = balance.get('Stockholders Equity', pd.Series([0]*num_years)).shift(1)
        #metrics['ROE'] = (net_income.iloc[latest] / equity.iloc[latest] * 100) if equity.iloc[latest] != 0 else 0
        #roe_3yr = [(net_income.iloc[i] / equity.iloc[i] * 100) for i in range(-min(3, num_years), 0) if equity.iloc[i] != 0]
        #metrics['ROE (3yr avg)'] = np.mean(roe_3yr) if roe_3yr else 0
        
        # Return on Capital Employed (ROCE) - Latest and 3-year average
        ebit = income.get('EBIT', pd.Series([0]*num_years))
        capital_employed = balance.get('Total Assets', pd.Series([0]*num_years)) - balance.get('Current Liabilities', pd.Series([0]*num_years))
        metrics['ROCE'] = (ebit.iloc[latest] / capital_employed.iloc[latest] * 100) if capital_employed.iloc[latest] != 0 else 0
        roce_3yr = [(ebit.iloc[i] / capital_employed.iloc[i] * 100) for i in range(-min(3, num_years), 0) if capital_employed.iloc[i] != 0]
        metrics['ROCE (3yr avg)'] = self.get_means(roce_3yr)#np.mean(roce_3yr) if roce_3yr else 0
        
        # Net Fixed Assets (NFA) for each year
        net_fixed_assets = balance.get('Net PPE', pd.Series([0]*num_years))
        
        # Net Fixed Asset Turnover (NFAT) - Calculate for each year, then 3-year average
        revenue = income.get('Total Revenue', pd.Series([0]*num_years))
        nfat_values = []
        try:
            for i in range(1, num_years):
                avg_nfa_year = (net_fixed_assets.iloc[i] + net_fixed_assets.iloc[i-1]) / 2
                nfat_year = revenue.iloc[i] / avg_nfa_year if avg_nfa_year != 0 else 0
                nfat_values.append(nfat_year)
        except:
            for i in range(1, num_years-1):
                avg_nfa_year = (net_fixed_assets.iloc[i] + net_fixed_assets.iloc[i-1]) / 2
                nfat_year = revenue.iloc[i] / avg_nfa_year if avg_nfa_year != 0 else 0
                nfat_values.append(nfat_year)
        if num_years == 1:
            avg_nfa_year = net_fixed_assets.iloc[latest]
            nfat_year = revenue.iloc[latest] / avg_nfa_year if avg_nfa_year != 0 else 0
            nfat_values.append(nfat_year)
        metrics['NFAT'] = nfat_values[-1] if nfat_values else 0  # Latest year's NFAT
        nfat_3yr = nfat_values[-min(3, len(nfat_values)):]

        #print(f"nfat_3yr : {nfat_3yr}")
        metrics['NFAT (3yr avg)'] = self.get_means(nfat_3yr)#np.mean(nfat_3yr) if nfat_3yr else metrics['NFAT']  # Fallback to latest if <3
        
        # Net Profit Margin (NPM) - 3-year average
        npm_3yr = [(net_income.iloc[i] / revenue.iloc[i] * 100) for i in range(-min(3, num_years), 0) if revenue.iloc[i] != 0]
        metrics['NPM'] = self.get_means(npm_3yr)#np.mean(npm_3yr) if npm_3yr else 0
        
        # Dividend Payout Ratio (DPR) - 3-year average
        dividends_paid = cashflow.get('Cash Dividends Paid', pd.Series([0]*num_years)).abs()
        dpr_3yr = [(dividends_paid.iloc[i] / net_income.iloc[i] * 100) for i in range(-min(3, num_years), 0) if net_income.iloc[i] != 0]
        metrics['DPR'] = self.get_means(dpr_3yr)#np.mean(dpr_3yr) if dpr_3yr else 0
        
        # Retention Ratio (1 - DPR)
        metrics['Retention Ratio'] = (1 - (metrics['DPR'] / 100)) * 100
        
        # Depreciation Rate (Dep as % of NFA) - 3-year average
        dep = cashflow.get('Depreciation And Amortization', pd.Series([0]*num_years))
        dep_3yr = []
        for i in range(-min(3, num_years), 0):
            avg_nfa_year = (net_fixed_assets.iloc[i] + net_fixed_assets.shift(1).iloc[i]) / 2 if num_years > 1 and i > -num_years + 1 else net_fixed_assets.iloc[i]
            dep_year = min((abs(dep.iloc[i]) / avg_nfa_year * 100) if avg_nfa_year != 0 else 0, 100)  # Use abs for dep, cap at 100%
            #print(avg_nfa_year)
            #if dep_year > 100:
            #    print(f"Warning: Dep % for year {i} exceeded 100%; capped to 100%. Check data for {ticker}.")
            dep_3yr.append(dep_year)
        metrics['Dep'] = self.get_means(dep_3yr)#np.mean(dep_3yr) if dep_3yr else 0
        
        # Self Sustainable Growth Rate (SSGR) = NFAT * NPM * (1 - DPR) - Dep
        npm_decimal = metrics['NPM'] / 100
        dpr_decimal = metrics['DPR'] / 100
        dep_decimal = metrics['Dep'] / 100
        metrics['SSGR'] = (metrics['NFAT (3yr avg)'] * npm_decimal * (1 - dpr_decimal) - dep_decimal) * 100  # As %
        
        # Avg NPM (over 3 years) - Already handled above
        metrics['Av NPM (over 3 years)'] = metrics['NPM']
        
        # Avg NFAT (over 3 years) - Already handled above
        metrics['Av NFA/T (over 3 years)'] = metrics['NFAT (3yr avg)']
        
        # Avg Dep % NFA (over 3 years) - Already handled above
        metrics['Av Dep%NFA (over 3 years)'] = metrics['Dep']
        
        # Avg Retention Ratio (over 3 years)
        ret_3yr = [(1 - (dividends_paid.iloc[i] / net_income.iloc[i])) * 100 for i in range(-min(3, num_years), 0) if net_income.iloc[i] != 0]
        metrics['Av Retention ratio (over 3 years)'] = self.get_means(ret_3yr) #np.mean(ret_3yr) if ret_3yr else 0

        # Debt to Equity (d/e)
        total_debt = balance.get('Total Debt', pd.Series([0]*num_years)).iloc[latest]
        metrics['d/e'] = (total_debt / equity.iloc[latest]) if equity.iloc[latest] != 0 else 0
        
        # Interest Coverage
        interest_exp = abs(income.get('Interest Expense', pd.Series([0]*num_years)).iloc[latest])
        metrics['Interest coverage'] = (income.get('Operating Income', pd.Series([0]*num_years)).iloc[latest] / interest_exp) if interest_exp != 0 else float('inf')
        
        # Tax %
        tax_exp = income.get('Income Tax Expense', pd.Series([0]*num_years)).iloc[latest] if 'Income Tax Expense' in income.columns else income.get('Tax Provision', pd.Series([0]*num_years)).iloc[latest] if 'Tax Provision' in income.columns else 0
        pretax = income.get('Pretax Income', pd.Series([0]*num_years)).iloc[latest]
        metrics['tax %'] = (tax_exp / pretax * 100) if pretax != 0 else 0
        
        # Cumulative PAT (cPAT) - Sum over available years (up to 5)
        metrics['cPAT'] = net_income[-min(5, num_years):].sum()
        
        # CFO (latest)
        metrics['CFO'] = cashflow.get('Operating Cash Flow', pd.Series([0]*num_years)).iloc[latest]
        
        # Cumulative CFO (cCFO)
        metrics['cCFO'] = cashflow.get('Operating Cash Flow', pd.Series([0]*num_years))[-min(5, num_years):].sum()
        
        # Cumulative CFO / cPAT
        metrics['cCFO/cPAT'] = (metrics['cCFO'] / metrics['cPAT']) if metrics['cPAT'] != 0 else 0
        
        # ROA (p/a)
        total_assets_prior = balance.get('Total Assets', pd.Series([0]*num_years)).shift(1).iloc[latest]
        metrics['p/a'] = (net_income.iloc[latest] / total_assets_prior * 100) if total_assets_prior != 0 else 0
        
        # Price to Earnings (p/e)
        pe = info.get('trailingPE', float('inf'))
        metrics['p/e'] = pe
        
        # Earnings Yield (EY)
        metrics['EY'] = (net_income.iloc[latest] / (info.get('sharesOutstanding', 1) * info.get('regularMarketPrice', 0)) * 100) if info.get('regularMarketPrice', 0) != 0 else 0
        
        # Earnings Growth 5yr CAGR
        eps_values = income.get('Basic EPS', pd.Series([0]*num_years))[-min(5, num_years):]
        if pd.isnull(eps_values.iloc[0]):
            eps_values = income.get('Basic EPS', pd.Series([0]*num_years))[-min(4, num_years):]
        periods = len(eps_values) - 1

        #print(f"eps : {eps_values}")
        metrics['Earnings Growth 5yr cagr'] = self.calculate_cagr(eps_values.iloc[0], eps_values.iloc[-1], periods) if periods > 0 else 0
        

        # Optional: Sales Growth 5yr CAGR
        revenue_values = income.get('Total Revenue', pd.Series([0]*num_years))[-min(5, num_years):]
        if pd.isnull(revenue_values.iloc[0]):
            revenue_values = income.get('Total Revenue', pd.Series([0]*num_years))[-min(4, num_years):]
        
        periods_revenue = len(revenue_values) - 1
        #print('2')
        sales_cagr = self.calculate_cagr(revenue_values.iloc[0], revenue_values.iloc[-1], periods_revenue) if periods_revenue > 0 else 0
        metrics['Sales Growth 5yr cagr'] = sales_cagr
        # PEG
        metrics['PEG'] = (pe / metrics['Earnings Growth 5yr cagr']) if metrics['Earnings Growth 5yr cagr'] != 0 else float('inf')
        
        # No. shares (cr) - in crores
        shares_out = info.get('sharesOutstanding', 0)
        metrics['no. shares (cr)'] = shares_out / 1e7

        metrics['Current Price'] = info.get('regularMarketPrice', 0)

        metrics['market cap'] = metrics['Current Price'] * shares_out
        metrics['d/e_market'] = total_debt/metrics['market cap']
        
        # Price to Sales (p/s)
        metrics['p/s'] = info.get('priceToSalesTrailing12Months', 0)
        
        # NFA + CWIP
        cwip = balance.get('Construction In Progress', pd.Series([0]*num_years)).iloc[latest]
        metrics['NFA + CWIP'] = net_fixed_assets.iloc[latest] + cwip
        
        # Capex = (NFA + CWIP end) - (NFA + CWIP start) + Dep
        if num_years >= 2:
            nfa_cwip_end = net_fixed_assets.iloc[latest] + cwip
            nfa_cwip_start = net_fixed_assets.iloc[latest-1] + balance.get('Construction In Progress', pd.Series([0]*num_years)).iloc[latest-1]
            metrics['Capex'] = nfa_cwip_end - nfa_cwip_start + dep.iloc[latest]
        else:
            metrics['Capex'] = abs(cashflow.get('Capital Expenditure', pd.Series([0]*num_years)).iloc[latest])  # Fallback
        
        metrics['Capex_from_cashflow_statement'] = abs(cashflow.get('Capital Expenditure', pd.Series([0]*num_years)).iloc[latest])

        # Free Cash Flow (FCF) = CFO - Capex
        metrics['FCF'] = metrics['CFO'] - metrics['Capex']
        
        # FCF%
        metrics['FCF%_from_balance_sheet'] = (metrics['FCF'] / net_income.iloc[latest] * 100) if net_income.iloc[latest] != 0 else 0

        # Free Cash Flow (FCF) = CFO - Capex
        metrics['FCF_capex_from_cashflow'] = metrics['CFO'] - metrics['Capex_from_cashflow_statement']
        
        # FCF%
        metrics['FCF%'] = (metrics['FCF_capex_from_cashflow'] / net_income.iloc[latest] * 100) if net_income.iloc[latest] != 0 else 0
    

        # Dividend Yield (DV)
        per_share_div = (dividends_paid.iloc[latest] / shares_out) if shares_out != 0 else 0
        metrics['DV'] = (per_share_div / info.get('regularMarketPrice', 0) * 100) if info.get('regularMarketPrice', 0) != 0 else 0
        
        # Mcap (cr)
        metrics['Mcap (cr)'] = info.get('marketCap', 0) / 1e7
        
        # d/e decreasing trend 5 yrs
        try:
            de_ratios = [balance.get('Total Debt', pd.Series([0]*num_years)).iloc[i] / balance.get('Stockholders Equity', pd.Series([1]*num_years)).iloc[i] for i in range(-min(5, num_years), 0)]
        except:
            de_ratios = [balance.get('Total Debt', pd.Series([0]*num_years)).iloc[i] / balance.get('Stockholders Equity', pd.Series([1]*num_years)).iloc[i] for i in range(-min(3, num_years), 0)]
        metrics['d/e decreasing trend 5 yrs'] = all(de_ratios[j] > de_ratios[j+1] for j in range(len(de_ratios)-1)) if len(de_ratios) > 1 else False
        
        # Financial Analysis Criteria
        sales_values = income.get('Total Revenue', pd.Series([0]*num_years))[-min(6, num_years):]
        sales_periods = len(sales_values) - 1
        #sales_cagr = calculate_cagr(sales_values.iloc[0], sales_values.iloc[-1], sales_periods) if sales_periods > 0 else 0
        metrics['Sales cagr >15%'] = sales_cagr > 15
        metrics['npm >8%'] = metrics['NPM'] > 8
        metrics['Tax payout >25%'] = metrics['tax %'] > 25
        metrics['Interest coverage >3'] = metrics['Interest coverage'] > 3
        metrics['d/e <0.5'] = metrics['d/e'] < 0.5
        metrics['CFO >0'] = metrics['CFO'] > 0
        
        #metrics['net cash flow positive'] = cashflow.get('Net Change in Cash', pd.Series([0]*num_years)).iloc[latest] > 0
        metrics['cCFO > PAT'] = metrics['cCFO/cPAT'] > 1
        
        # Valuation Analysis
        metrics['p/e <10'] = pe < 10
        metrics['peg <1'] = metrics['PEG'] < 1
        metrics['EY >7%'] = metrics['EY'] > 7
        metrics['p/b <3'] = info.get('priceToBook', float('inf')) < 3
        metrics['DV >3%'] = metrics['DV'] > 3
        
        # Margin of Safety
        metrics['EY >7'] = metrics['EY'] > 7  # Duplicate
        metrics['sgr > Sales growth (very linear)'] = metrics['SSGR'] > sales_cagr
        metrics['FCF/CFO'] = (metrics['FCF'] / metrics['CFO']) if metrics['CFO'] != 0 else 0
        
        # Current Price
        metrics['Current Price'] = info.get('regularMarketPrice', 0)
        # Raw Financial Data
        # Raw Financial Data (corrected and enhanced columns with column existence check)
        metrics['Market Cap'] = info.get('marketCap', 0)  # In original currency
        metrics['Net Income'] = net_income.iloc[latest] if 'Net Income' in income.columns else 0
        metrics['Total Revenue'] = revenue.iloc[latest] if 'Total Revenue' in income.columns else 0
        metrics['Total Assets'] = balance['Total Assets'].iloc[latest] if 'Total Assets' in balance.columns else 0  # Fallback to sum if needed
        if metrics['Total Assets'] == 0 and 'Total Non Current Assets' in balance.columns and 'Current Assets' in balance.columns:
            metrics['Total Assets'] = (balance['Total Non Current Assets'].iloc[latest] + 
                                    balance['Current Assets'].iloc[latest])
        metrics['Total Liabilities'] = balance['Total Liabilities Net Minority Interest'].iloc[latest] if 'Total Liabilities Net Minority Interest' in balance.columns else 0  # Use provided field
        if metrics['Total Liabilities'] == 0 and 'Total Non Current Liabilities Net Minority Interest' in balance.columns and 'Current Liabilities' in balance.columns:
            metrics['Total Liabilities'] = (balance['Total Non Current Liabilities Net Minority Interest'].iloc[latest] + 
                                        balance['Current Liabilities'].iloc[latest])
        metrics['Total Stockholders Equity'] = equity.iloc[latest] if 'Stockholders Equity' in balance.columns else 0
        
        metrics['Total Debt'] = (balance['Total Debt'].iloc[latest] if 'Total Debt' in balance.columns else balance['Long Term Debt'].iloc[latest] + balance['Current Debt'].iloc[latest] if 'Long Term Debt' in balance.columns and 'Current Debt' in balance.columns else 
                            balance['Total Non Current Liabilities Net Minority Interest'].iloc[latest] if 'Total Non Current Liabilities Net Minority Interest' in balance.columns else 0)
        metrics['Cash and Cash Equivalents'] = balance['Cash'].iloc[latest] if 'Cash' in balance.columns else balance['Other Current Assets'].iloc[latest] if 'Other Current Assets' in balance.columns else 0  # Fallback to Other Current Assets
        metrics['Current Assets'] = balance['Current Assets'].iloc[latest] if 'Current Assets' in balance.columns else 0
        if metrics['Current Assets'] == 0 and all(col in balance.columns for col in ['Other Current Assets', 'Hedging Assets Current', 'Assets Held For Sale Current', 'Prepaid Assets']):
            metrics['Current Assets'] = (balance['Other Current Assets'].iloc[latest] + 
                                        balance['Hedging Assets Current'].iloc[latest] + 
                                        balance['Assets Held For Sale Current'].iloc[latest] + 
                                        balance['Prepaid Assets'].iloc[latest])
        metrics['Current Liabilities'] = balance['Current Liabilities'].iloc[latest] if 'Current Liabilities' in balance.columns else 0
        metrics['Working Capital'] = balance['Working Capital'].iloc[latest] if 'Working Capital' in balance.columns else 0
        if metrics['Working Capital'] == 0 and all(col in balance.columns for col in ['Current Assets', 'Current Liabilities']):
            metrics['Working Capital'] = (metrics['Current Assets'] - metrics['Current Liabilities']) if metrics['Current Assets'] != 0 and metrics['Current Liabilities'] != 0 else 0
        metrics['Operating Cash Flow'] = metrics['CFO']  # Already calculated, stored raw
        metrics['Capital Expenditure'] = abs(cashflow['Capital Expenditure'].iloc[latest]) if 'Capital Expenditure' in cashflow.columns else 0
        metrics['Dividends Paid'] = dividends_paid.iloc[latest] if 'Cash Dividends Paid' in cashflow.columns else 0
        metrics['Depreciation & Amortization'] = dep.iloc[latest] if 'Depreciation And Amortization' in cashflow.columns else 0
        metrics['Interest Expense'] = interest_exp if 'Interest Expense' in income.columns else 0
        metrics['Income Tax Expense'] = income['Income Tax Expense'].iloc[latest] if 'Income Tax Expense' in income.columns else income['Tax Provision'].iloc[latest] if 'Tax Provision' in income.columns else 0
        metrics['Shares Outstanding'] = shares_out
        metrics['Current Price'] = info.get('regularMarketPrice', 0)  # Already calculated, stored raw
        metrics['Net Fixed Assets'] = net_fixed_assets.iloc[latest] if 'Net PPE' in balance.columns else 0
        metrics['Construction in Progress'] = cwip if 'Construction In Progress' in balance.columns else 0
        
        #print(metrics['Total Debt'])
        #print(metrics['Cash and Cash Equivalents'])
        metrics['Net Debt'] = metrics['Total Debt'] - metrics['Cash and Cash Equivalents'] if metrics['Total Debt'] != 0 and metrics['Cash and Cash Equivalents'] != 0 else 0
        
        # ROA and ROE Calculations (Average over 3-5 years)
        net_income_series = income.get('Net Income', pd.Series([0]*num_years))[-min(5, num_years):]
        total_assets_series = balance.get('Total Assets', pd.Series([0]*num_years))[-min(5, num_years):]
        equity_series = balance.get('Stockholders Equity', pd.Series([0]*num_years))[-min(5, num_years):]

        # Calculate yearly ROA and ROE
        years = min(4, num_years)
        roa_values = []
        roe_values = []
        for i in range(years):
            net_income = net_income_series.iloc[i] if not pd.isna(net_income_series.iloc[i]) else 0
            # Average Total Assets for ROA (current + previous) / 2
            if i > 0:
                avg_assets = (total_assets_series.iloc[i] + total_assets_series.iloc[i-1]) / 2
            else:
                avg_assets = total_assets_series.iloc[i]  # Fallback to current if no previous
            if avg_assets != 0:
                roa_values.append((net_income / avg_assets) * 100)
            
            # Average Equity for ROE (current + previous) / 2
            if i > 0:
                avg_equity = (equity_series.iloc[i] + equity_series.iloc[i-1]) / 2
            else:
                avg_equity = equity_series.iloc[i]  # Fallback to current if no previous
            if avg_equity != 0:
                roe_values.append((net_income / avg_equity) * 100)

        roa_values = [x for x in roa_values if pd.notnull(x)]
        roe_values = [x for x in roe_values if pd.notnull(x)]
        # Average ROA and ROE
        metrics['3-5yr Average ROA (%)'] = self.get_means(roa_values)#np.mean(roa_values) if roa_values else 0
        metrics['3-5yr Average ROE (%)'] = self.get_means(roe_values)#np.mean(roe_values) if roe_values else 0
        
        metrics['ROE'] = roe_values[latest]
        metrics['ROA'] = roa_values[latest]

        major_holders_dict = dict(stock.major_holders)['Value']
        price_targets = stock.analyst_price_targets
        metrics = {**metrics, **major_holders_dict, **price_targets}

        # Additions based on Damodaran's concepts

        # Normalized Earnings (3-year average as proxy for normalization)
        metrics['Normalized EBIT'] = self.get_means(ebit[-min(3, len(ebit)):]) #np.mean(ebit[-min(3, num_years):]) if num_years > 0 else 0
        metrics['Normalized Net Income'] = self.get_means(net_income_series[-min(3, len(net_income_series)):])# np.mean(net_income[-min(3, num_years):]) if num_years > 0 else 0

        # R&D Capitalization (if R&D data available)
        if 'Research And Development' in income.columns:
            rd = income['Research And Development']
            life = 5  # Assumed amortizable life (e.g., 5 years for tech)
            research_asset = 0
            amortization = 0
            current_idx = num_years - 1
            for age in range(0, life):
                idx = current_idx - age
                if idx >= 0:
                    rd_value = rd.iloc[idx] if not pd.isna(rd.iloc[idx]) else 0
                    unamort_fraction = (life - age) / life
                    research_asset += rd_value * unamort_fraction
                    amortization += rd_value / life
            metrics['Research Asset'] = research_asset
            metrics['R&D Amortization'] = amortization
            adjusted_ebit = ebit.iloc[latest] + rd.iloc[latest] - amortization if not pd.isna(rd.iloc[latest]) else ebit.iloc[latest]
            metrics['Adjusted EBIT (R&D)'] = adjusted_ebit
            adjusted_book_equity = equity.iloc[latest] + research_asset
            metrics['Adjusted Book Equity (R&D)'] = adjusted_book_equity
            adjusted_de = total_debt / adjusted_book_equity if adjusted_book_equity != 0 else 0
            metrics['Adjusted D/E (R&D)'] = adjusted_de
            tax_rate = metrics['tax %'] / 100 if 'tax %' in metrics and metrics['tax %'] != 0 else 0.25  # Fallback to 25% if missing
            nopat_adjusted = adjusted_ebit * (1 - tax_rate)
            invested_capital_adjusted = adjusted_book_equity + total_debt - metrics.get('Cash and Cash Equivalents', 0)
            metrics['Adjusted Invested Capital (R&D)'] = invested_capital_adjusted
            metrics['Adjusted ROC (R&D)'] = (nopat_adjusted / invested_capital_adjusted * 100) if invested_capital_adjusted != 0 else 0
            adjusted_roe = (net_income_series.iloc[latest] / adjusted_book_equity * 100) if adjusted_book_equity != 0 else 0
            metrics['Adjusted ROE (R&D)'] = adjusted_roe

        metrics_df = pd.DataFrame.from_dict(metrics, orient='index', columns=['Value'])
        #metrics_df.to_csv(f'yfin/{metrics['ticker']}.csv')
        return metrics
    
    def analyze_single_stock(self, ticker):
        """
        Analyze a single stock using yfinance.
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            dict: Analysis results
        """
        print(f'Analyzing {ticker}...')
        
        try:
            stock = self.get_yfinance_data(ticker)
            metrics = self.compute_yfinance_metrics(stock)
            print(f'Completed analysis for {ticker} using yfinance')
            return metrics
        except Exception as e:
            print(f'Error analyzing {ticker}: {e}')
            return None
    
    def analyze_multiple_stocks(self, ticker_list, delay=1):
        """
        Analyze multiple stocks in batch.
        
        Args:
            ticker_list (list): List of stock tickers
            delay (int): Delay between requests in seconds
            
        Returns:
            pd.DataFrame: Combined results for all stocks
        """
        print(f'Starting batch analysis of {len(ticker_list)} stocks...')
        
        for ticker in ticker_list:
            try:
                self.analyze_single_stock(ticker)
                time.sleep(delay)
            except Exception as e:
                print(f'Error analyzing {ticker}: {e}')
                continue
        
        # Combine results
        list_dfs = []
        for ticker in ticker_list:
            try:
                p1 = pd.read_csv(f'yfin/{ticker}.csv')
                columns_order = p1['Unnamed: 0'].values
                p1 = p1.fillna(-1)
                piv_df = p1.pivot_table(index=None, columns='Unnamed: 0', values='Value', aggfunc='first').reset_index(drop=True)[columns_order]
                list_dfs.append(piv_df)
            except:
                print(f"Could not process results for {ticker}")
        
        if list_dfs:
            combined_df = pd.concat(list_dfs, ignore_index=True)
            return combined_df
        else:
            return pd.DataFrame()
    
    def analyze_from_file(self, file_path, ticker_column, extra_columns=None, output_file=None):
        """
        Analyze stocks from a file (Excel or CSV).
        
        Args:
            file_path (str): Path to input file
            ticker_column (str): Name of column containing tickers
            extra_columns (list): Additional columns to include in output
            output_file (str): Output file name (without extension)
            
        Returns:
            pd.DataFrame: Combined results
        """
        # Read input file
        if file_path.endswith('.xlsx'):
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)
        
        ticker_list = df[ticker_column].tolist()
        
        # Analyze stocks
        results_df = self.analyze_multiple_stocks(ticker_list)
        
        # Merge with original data
        if extra_columns:
            original_data = df[[ticker_column] + extra_columns]
            final_df = original_data.merge(results_df, left_on=ticker_column, right_on='ticker', how='left')
        else:
            final_df = results_df
        
        # Save results
        if output_file:
            os.makedirs('combined_stocks', exist_ok=True)
            final_df.to_csv(f'combined_stocks/{output_file}.csv', index=False)
            print(f'Results saved to combined_stocks/{output_file}.csv')
        
        return final_df


# Example usage
if __name__ == "__main__":
    # Initialize analyzer
    analyzer = FundamentalAnalysis()
    
    # Analyze single stock
    result = analyzer.analyze_single_stock("AAPL")
    print(f"Analysis complete for AAPL")
    
    # Analyze multiple stocks
    tickers = ["AAPL", "MSFT", "GOOGL"]
    results = analyzer.analyze_multiple_stocks(tickers)
    print(f"Batch analysis complete for {len(tickers)} stocks")
    
    # Analyze from file
    # results = analyzer.analyze_from_file('stocks.xlsx', 'ticker', ['name', 'sector'], 'analysis_results')
