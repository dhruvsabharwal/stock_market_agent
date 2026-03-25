import os
import sys
import pandas as pd

# Add current directory to path so we can import AdvancedBaseBreakoutAnalyzer
# Since this script is now in agents/base_breakout_strategy/, we can import directly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from advanced_base_breakout import AdvancedBaseBreakoutAnalyzer
except ImportError:
    print("Error: Could not import AdvancedBaseBreakoutAnalyzer.")
    sys.exit(1)

def run_analysis(ticker_file):
    if not os.path.exists(ticker_file):
        # 1. Check in agents/stock_screener/tickers/
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        ticker_path_screener = os.path.join(base_dir, 'agents', 'stock_screener', 'tickers', ticker_file)
        
        # 2. Check in root (legacy / fallback)
        ticker_path_root = os.path.join(base_dir, ticker_file)
        
        if os.path.exists(ticker_path_screener):
            ticker_file = ticker_path_screener
        elif os.path.exists(ticker_path_root):
            ticker_file = ticker_path_root
        else:
            print(f"Error: {ticker_file} not found in {ticker_path_screener} or root.")
            return

    with open(ticker_file, 'r') as f:
        tickers = [line.strip() for line in f if line.strip()]
    
    if not tickers:
        print("No tickers found in the file.")
        return

    print(f"Loaded {len(tickers)} tickers from {ticker_file}.")
    
    analyzer = AdvancedBaseBreakoutAnalyzer(max_workers=5)
    
    print("Starting batch analysis (this may take a few minutes)...")
    csv_path = analyzer.analyze_batch_to_csv(tickers)
    
    print(f"\nAnalysis complete!")
    print(f"Results saved to: {csv_path}")
    
    df = pd.read_csv(csv_path)
    summary = df[['ticker', 'score', 'quality', 'current_price']]
    print("\nSummary (Top results):")
    print(summary.sort_values(by='score', ascending=False).head(20).to_string(index=False))

if __name__ == "__main__":
    ticker_file = sys.argv[1] if len(sys.argv) > 1 else "extracted_tickers.txt"
    run_analysis(ticker_file)
