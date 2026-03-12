import os
import pandas as pd
from bs4 import BeautifulSoup
import re

def extract_tickers():
    raw_tickers_dir = "/Users/dhruvsabharwal/Documents/personal/financial_analysis/raw_tickers"
    output_file = "/Users/dhruvsabharwal/Documents/personal/financial_analysis/all_tickers.txt"
    all_tickers = set()

    for filename in os.listdir(raw_tickers_dir):
        file_path = os.path.join(raw_tickers_dir, filename)
        
        if filename.startswith('.') or os.path.isdir(file_path):
            continue

        print(f"Processing {filename}...")

        try:
            is_indian = "india" in filename.lower() or "indian" in filename.lower() or filename == "EQUITY_L.csv"
            
            if filename.endswith('.csv'):
                df = pd.read_csv(file_path)
                col = find_ticker_column(df.columns)
                if col:
                    tickers = df[col].dropna().astype(str).tolist()
                    all_tickers.update(clean_tickers(tickers, is_indian))

            elif filename.endswith('.xlsx'):
                df = pd.read_excel(file_path)
                col = find_ticker_column(df.columns)
                if col:
                    tickers = df[col].dropna().astype(str).tolist()
                    all_tickers.update(clean_tickers(tickers, is_indian))

            elif filename.endswith('.txt'):
                with open(file_path, 'r') as f:
                    content = f.read()
                    parts = re.split(r'[,\n]', content)
                    for part in parts:
                        part = part.strip()
                        if not part: continue
                        if ':' in part:
                            ticker = part.split(':')[-1].upper()
                        else:
                            ticker = part.upper()
                        
                        if is_indian and not ticker.endswith('.NS'):
                            ticker += '.NS'
                        all_tickers.add(ticker)

            elif filename.endswith('.xml'):
                with open(file_path, 'r') as f:
                    soup = BeautifulSoup(f, 'lxml-xml' if filename.endswith('.xml') else 'html.parser')
                    for td in soup.find_all('td', align='center'):
                        text = td.get_text().strip()
                        if text and len(text) <= 6 and text.isupper() and text.isalpha():
                            ticker = text
                            if is_indian and not ticker.endswith('.NS'):
                                ticker += '.NS'
                            all_tickers.add(ticker)

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    # Final cleanup and sorting
    sorted_tickers = sorted(list(all_tickers))
    
    with open(output_file, 'w') as f:
        for ticker in sorted_tickers:
            f.write(f"{ticker}\n")

    print(f"Successfully extracted {len(sorted_tickers)} tickers to {output_file}")

def find_ticker_column(columns):
    target_names = ['symbol', 'SYMBOL', 'Ticker', 'TICKER', 'Symbol']
    for name in target_names:
        if name in columns:
            return name
    return None

def clean_tickers(ticker_list, is_indian=False):
    cleaned = set()
    for t in ticker_list:
        t = t.strip().upper()
        if t and ' ' not in t:
            if is_indian and not t.endswith('.NS'):
                t += '.NS'
            cleaned.add(t)
    return cleaned

if __name__ == "__main__":
    extract_tickers()
