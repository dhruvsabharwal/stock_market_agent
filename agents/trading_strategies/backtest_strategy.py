import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import argparse
import os

class Backtester:
    def __init__(self, ticker, start_date, end_date, initial_capital=10000):
        self.ticker = ticker
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.data = None
        self.trades = []
        self.equity_curve = []

    def fetch_data(self):
        print(f"Fetching data for {self.ticker}...")
        # Fetch extra data to calculate moving averages
        fetch_start = (datetime.strptime(self.start_date, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
        self.data = yf.download(self.ticker, start=fetch_start, end=self.end_date)
        if self.data.empty:
            raise ValueError(f"No data found for {self.ticker}")
        
        # Trim MultiIndex if present (yfinance sometimes returns MultiIndex columns)
        if isinstance(self.data.columns, pd.MultiIndex):
            self.data.columns = self.data.columns.get_level_values(0)
            
        return self.data

    def calculate_indicators(self):
        print("Calculating indicators...")
        df = self.data.copy()
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['Avg_Vol_20'] = df['Volume'].rolling(window=20).mean()
        df['High_5'] = df['High'].shift(1).rolling(window=5).max()
        
        # Filter to the actual backtest period
        self.data = df[df.index >= self.start_date].copy()
        print(f"Data ready from {self.data.index[0].date()} to {self.data.index[-1].date()}")

    def run_backtest(self):
        print("Running backtest...")
        df = self.data
        capital = self.initial_capital
        position = 0
        entry_price = 0
        entry_date = None
        days_in_trade = 0
        
        equity = capital
        self.equity_curve = []

        for i in range(len(df)):
            current_date = df.index[i]
            row = df.iloc[i]
            
            # Update equity
            if position > 0:
                current_equity = position * row['Close']
            else:
                current_equity = capital
            self.equity_curve.append({'Date': current_date, 'Equity': current_equity})

            # Check Exit Conditions
            if position > 0:
                days_in_trade += 1
                exit_signal = False
                exit_reason = ""

                # Exit Rule 1: After 5 trading days
                if days_in_trade >= 5:
                    exit_signal = True
                    exit_reason = "Time-based Exit (5 days)"
                
                # Exit Rule 2: Close below 20 SMA
                elif row['Close'] < row['SMA_20']:
                    exit_signal = True
                    exit_reason = "Stop Loss (Close < SMA_20)"

                if exit_signal:
                    capital = position * row['Close']
                    exit_price = row['Close']
                    profit = (exit_price - entry_price) / entry_price * 100
                    self.trades.append({
                        'Ticker': self.ticker,
                        'Entry Date': entry_date,
                        'Exit Date': current_date,
                        'Entry Price': entry_price,
                        'Exit Price': exit_price,
                        'Profit %': profit,
                        'Reason': exit_reason
                    })
                    position = 0
                    days_in_trade = 0
                    print(f"EXIT: {current_date.date()} at {exit_price:.2f} | Profit: {profit:.2f}% | Reason: {exit_reason}")
                continue

            # Check Entry Conditions
            if position == 0:
                # 1. Above 200 SMA
                cond1 = row['Close'] > row['SMA_200']
                # 2. Above 20 SMA
                cond2 = row['Close'] > row['SMA_20']
                # 3. Volume Spike (> 1.5x Avg)
                cond3 = row['Volume'] > 1.5 * row['Avg_Vol_20']
                # 4. New 5-day High
                cond4 = row['Close'] > row['High_5']

                if cond1 and cond2 and cond3 and cond4:
                    entry_price = row['Close']
                    position = capital / entry_price
                    entry_date = current_date
                    days_in_trade = 0
                    print(f"ENTRY: {current_date.date()} at {entry_price:.2f}")

        print("Backtest completed.")
        return self.trades

    def show_results(self):
        if not self.trades:
            print("No trades were made.")
            return

        trades_df = pd.DataFrame(self.trades)
        total_return = (self.equity_curve[-1]['Equity'] - self.initial_capital) / self.initial_capital * 100
        win_rate = (trades_df['Profit %'] > 0).mean() * 100
        avg_profit = trades_df['Profit %'].mean()

        print("\n" + "="*30)
        print(f"RESULTS FOR {self.ticker}")
        print("="*30)
        print(f"Total Return: {total_return:.2f}%")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Profit per Trade: {avg_profit:.2f}%")
        print(f"Total Trades: {len(trades_df)}")
        print("="*30)
        
        # Simple plot
        equity_df = pd.DataFrame(self.equity_curve).set_index('Date')
        plt.figure(figsize=(10, 6))
        plt.plot(equity_df['Equity'], label='Strategy Equity')
        plt.title(f'Equity Curve - {self.ticker}')
        plt.xlabel('Date')
        plt.ylabel('Equity ($)')
        plt.legend()
        plt.grid(True)
        # Instead of plt.show() which might block, we save it
        plot_path = f"{self.ticker}_backtest.png"
        plt.savefig(plot_path)
        print(f"Equity curve saved to {plot_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Backtest Volume Breakout Swing Strategy')
    parser.add_argument('--ticker', type=str, default='AAPL', help='Stock ticker symbol')
    parser.add_argument('--start', type=str, default='2023-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='End date (YYYY-MM-DD)')
    
    args = parser.parse_args()

    bt = Backtester(args.ticker, args.start, args.end)
    bt.fetch_data()
    bt.calculate_indicators()
    bt.run_backtest()
    bt.show_results()
