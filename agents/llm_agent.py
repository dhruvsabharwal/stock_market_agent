"""
LLM Agent
Agent for fetching news and generating market summaries using Google Gemini.
"""

import os
import sys
import yfinance as yf
import google.generativeai as genai
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from agents.utils import Config
except ImportError:
    from utils import Config

class LLMAgent:
    """
    Agent responsible for fetching news and generating qualitative analysis using LLMs.
    """
    
    def __init__(self):
        """Initialize the LLM Agent."""
        self.api_key = Config.GEMINI_API_KEY
        self.model = None
        if self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel(Config.LLM_MODEL)
            except Exception as e:
                print(f"Warning: Could not initialize Gemini client: {e}")
    
    def fetch_news(self, ticker, limit=5):
        """
        Fetch recent news for a ticker using yfinance.
        
        Args:
            ticker (str): Stock ticker
            limit (int): Number of news items to return
            
        Returns:
            list: List of news dictionaries (title, publisher, link, time)
        """
        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            
            formatted_news = []
            for item in news[:limit]:
                published = datetime.fromtimestamp(item.get('providerPublishTime', 0))
                formatted_news.append({
                    'title': item.get('title'),
                    'publisher': item.get('publisher'),
                    'link': item.get('link'),
                    'published': published.strftime('%Y-%m-%d %H:%M')
                })
            return formatted_news
        except Exception as e:
            print(f"Error fetching news for {ticker}: {e}")
            return []

    def generate_summary(self, ticker, analysis_data, news_items):
        """
        Generate a market summary using the LLM.
        
        Args:
            ticker (str): Stock ticker
            analysis_data (dict): Combined analysis results (scores, metrics)
            news_items (list): List of news items
            
        Returns:
            str: LLM generated summary
        """
        if not self.model:
            return "LLM Summary Unavailable (No API Key or Client Error)"
            
        # Construct the prompt
        prompt = self._construct_prompt(ticker, analysis_data, news_items)
        
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"Error generating LLM summary for {ticker}: {e}")
            return f"Error generating summary: {str(e)}"

    def _construct_prompt(self, ticker, data, news):
        """Construct the prompt for the LLM."""
        
        # Extract key metrics safely
        fund = data.get('fundamental_analysis', {})
        tech = data.get('technical_analysis', {})
        
        fund_score = data.get('fundamental_score', 'N/A')
        tech_score = data.get('technical_score', 'N/A')
        final_score = data.get('combined_score', 'N/A')
        recommendation = data.get('recommendation', 'N/A')
        
        # Format news
        news_str = ""
        if news:
            for i, item in enumerate(news, 1):
                news_str += f"{i}. {item['title']} ({item['publisher']} - {item['published']})\n"
        else:
            news_str = "No recent news available."
            
        prompt = f"""
        You are a senior financial analyst. Provide a concise, professional summary of {ticker} based on the provided technical and fundamental indicators and recent news. Focus on the 'Why' behind the score.

        **Scores:**
        - Fundamental Score: {fund_score}/100
        - Technical Score: {tech_score}/100
        - Final Score: {final_score}/100
        - System Recommendation: {recommendation}
        
        **Key Fundamental Metrics:**
        - P/E Ratio: {fund.get('p/e', 'N/A')}
        - ROE: {fund.get('ROE', 'N/A')}%
        - Revenue Growth (5yr): {fund.get('Sales Growth 5yr cagr', 'N/A')}%
        - Debt/Equity: {fund.get('d/e', 'N/A')}
        
        **Key Technical Indicators:**
        - RSI: {tech.get('rsi', {}).get('RSI', 'N/A')} ({tech.get('rsi', {}).get('signal', 'N/A')})
        - MACD: {tech.get('macd', {}).get('signal', 'N/A')}
        - Price vs 200 SMA: {'Above' if tech.get('moving_averages', {}).get('above_200') else 'Below'}
        
        **Recent News:**
        {news_str}
        
        **Task:**
        Write a concise 3-4 sentence summary explaining the score. 
        1. Highlight the strongest positive factor.
        2. Highlight the biggest risk or negative factor.
        3. Mention any relevant news context if it explains recent price action.
        4. Conclude with a brief outlook (Bullish/Bearish/Neutral).
        """
        return prompt

if __name__ == "__main__":
    # Test the agent
    agent = LLMAgent()
    print("Fetching news for AAPL...")
    news = agent.fetch_news("AAPL", limit=3)
    for n in news:
        print(f"- {n['title']}")
