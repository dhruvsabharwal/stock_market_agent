import requests
from bs4 import BeautifulSoup
import time
import sys
import os

def extract_tickers(base_url):
    """
    Extracts all tickers from a Finviz screener URL, handling pagination.
    """
    # Ensure URL is the Overview view (v=111) for consistent parsing
    if 'v=111' not in base_url:
        if '?' in base_url:
            base_url += '&v=111'
        else:
            base_url += '?v=111'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    all_tickers = []
    page_row = 1
    
    print(f"Starting extraction from: {base_url}")
    
    while True:
        # Construct the URL for the current page
        url = f"{base_url}&r={page_row}" if page_row > 1 else base_url
        print(f"Fetching page starting at row {page_row}...")
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching URL: {e}")
            break
            
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Tickers are in links with class 'tab-link' and href starting with 'quote.ashx?t='
        ticker_links = soup.select('a.tab-link[href^="quote.ashx?t="]')
        
        if not ticker_links:
            print("No more tickers found.")
            break
            
        page_tickers = [link.text.strip() for link in ticker_links]
        
        # Avoid duplicates and check if we've already seen these tickers (pagination end)
        new_tickers = [t for t in page_tickers if t not in all_tickers]
        if not new_tickers:
            print("Reached the end of results or duplicate page.")
            break
            
        all_tickers.extend(new_tickers)
        print(f"Found {len(page_tickers)} tickers on this page. Total so far: {len(all_tickers)}")
        
        # Check for total count to avoid unnecessary requests
        total_text_elem = soup.find('td', string=lambda x: x and 'Total' in x)
        if total_text_elem:
            try:
                total_parts = total_text_elem.text.split('/')
                if len(total_parts) > 1:
                    total_count = int(total_parts[1].split()[0])
                    if len(all_tickers) >= total_count:
                        print(f"Reached total count of {total_count} stocks.")
                        break
            except (ValueError, IndexError):
                pass

        # Increment for next page (20 items per page)
        page_row += 20
        
        # Brief sleep to avoid hit rate limiting
        time.sleep(1)

    return all_tickers

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run extract_finviz_tickers.py <finviz_url> [identifier]")
        print("Example: uv run extract_finviz_tickers.py 'https://finviz.com/screener.ashx?v=111&...' 'my_screener'")
        sys.exit(1)

    url = sys.argv[1]
    identifier = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    tickers = extract_tickers(url)
    
    print("\n" + "="*20)
    print(f"Extracted {len(tickers)} Tickers:")
    print(", ".join(tickers))
    print("="*20)
    
    # Save to file in a subfolder within stock_screener
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "tickers")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    output_filename = f"extracted_tickers_{identifier}.txt" if identifier != "default" else "extracted_tickers.txt"
    output_path = os.path.join(output_dir, output_filename)
    
    with open(output_path, "w") as f:
        f.write("\n".join(tickers))
    print(f"Tickers saved to '{output_path}'")
