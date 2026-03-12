"""
Enhanced Scoring Functions for Combined Analysis
Uses ALL fundamental and technical indicators specified by the user.
"""

def calculate_comprehensive_fundamental_score(fund_result):
    """
    Comprehensive fundamental scoring using ALL specified indicators.
    Max Score: 100
    
    Categories:
    1. Profitability & Efficiency (25 points)
    2. Growth (20 points)
    3. Valuation (20 points)
    4. Financial Health & Leverage (20 points)
    5. Cash Flow Quality (15 points)
    """
    score = 0
    
    # ===== 1. PROFITABILITY & EFFICIENCY (25 points) =====
    # ROCE (3yr avg) - 7 points
    roce_3yr = fund_result.get('ROCE (3yr avg)', 0)
    if roce_3yr > 25: score += 7
    elif roce_3yr > 20: score += 5
    elif roce_3yr > 15: score += 3
    
    # 3-5yr Average ROE - 7 points
    roe_avg = fund_result.get('3-5yr Average ROE (%)', 0)
    if roe_avg > 25: score += 7
    elif roe_avg > 20: score += 5
    elif roe_avg > 15: score += 3
    
    # 3-5yr Average ROA - 4 points
    roa_avg = fund_result.get('3-5yr Average ROA (%)', 0)
    if roa_avg > 15: score += 4
    elif roa_avg > 10: score += 2
    
    # Av NPM (over 3 years) - 4 points
    npm = fund_result.get('Av NPM (over 3 years)', 0)
    if npm > 15: score += 4
    elif npm > 10: score += 2
    
    # NFAT (3yr avg) - 3 points
    nfat = fund_result.get('NFAT (3yr avg)', 0)
    if nfat > 5: score += 3
    elif nfat > 3: score += 2
    
    # ===== 2. GROWTH (20 points) =====
    # Earnings Growth 5yr cagr - 10 points
    eps_growth = fund_result.get('Earnings Growth 5yr cagr', 0)
    if eps_growth > 20: score += 10
    elif eps_growth > 15: score += 7
    elif eps_growth > 10: score += 5
    elif eps_growth > 5: score += 3
    
    # Sales Growth 5yr cagr - 7 points
    sales_growth = fund_result.get('Sales Growth 5yr cagr', 0)
    if sales_growth > 20: score += 7
    elif sales_growth > 15: score += 5
    elif sales_growth > 10: score += 3
    
    # SSGR (Self Sustainable Growth Rate) - 3 points
    ssgr = fund_result.get('SSGR', 0)
    if ssgr > sales_growth: score += 3  # Can sustain growth internally
    elif ssgr > 0: score += 1
    
    # ===== 3. VALUATION (20 points) =====
    # P/E Ratio - 6 points
    pe = fund_result.get('p/e', float('inf'))
    if 0 < pe < 15: score += 6
    elif 0 < pe < 25: score += 4
    elif 0 < pe < 35: score += 2
    
    # PEG Ratio - 6 points
    peg = fund_result.get('PEG', float('inf'))
    if 0 < peg < 1: score += 6
    elif 0 < peg < 1.5: score += 4
    elif 0 < peg < 2: score += 2
    
    # EY (Earnings Yield) - 4 points
    ey = fund_result.get('EY', 0)
    if ey > 10: score += 4
    elif ey > 7: score += 3
    elif ey > 5: score += 1
    
    # P/S Ratio - 4 points
    ps = fund_result.get('p/s', float('inf'))
    if 0 < ps < 2: score += 4
    elif 0 < ps < 4: score += 2
    
    # ===== 4. FINANCIAL HEALTH & LEVERAGE (20 points) =====
    # d/e_market (Market-based D/E) - 6 points
    de_market = fund_result.get('d/e_market', 100)
    if de_market < 0.3: score += 6
    elif de_market < 0.5: score += 4
    elif de_market < 1.0: score += 2
    
    # Interest coverage - 6 points
    interest_cov = fund_result.get('Interest coverage', 0)
    if interest_cov > 10: score += 6
    elif interest_cov > 5: score += 4
    elif interest_cov > 3: score += 2
    
    # Tax % (reasonable tax rate) - 3 points
    tax_pct = fund_result.get('tax %', 0)
    if 15 < tax_pct < 30: score += 3  # Normal corporate tax range
    elif tax_pct > 0: score += 1
    
    # Av Retention ratio - 3 points
    retention = fund_result.get('Av Retention ratio (over 3 years)', 0)
    if retention > 70: score += 3
    elif retention > 50: score += 2
    
    # Av Dep%NFA - 2 points (lower is better, means assets are newer)
    dep_nfa = fund_result.get('Av Dep%NFA (over 3 years)', 100)
    if dep_nfa < 20: score += 2
    elif dep_nfa < 40: score += 1
    
    # ===== 5. CASH FLOW QUALITY (15 points) =====
    # cCFO/cPAT (Cumulative CFO vs PAT) - 6 points
    ccfo_cpat = fund_result.get('cCFO/cPAT', 0)
    if ccfo_cpat > 1.2: score += 6
    elif ccfo_cpat > 1.0: score += 4
    elif ccfo_cpat > 0.8: score += 2
    
    # FCF% - 5 points
    fcf_pct = fund_result.get('FCF%', -100)
    if fcf_pct > 80: score += 5
    elif fcf_pct > 50: score += 3
    elif fcf_pct > 0: score += 1
    
    # FCF/CFO - 4 points
    fcf_cfo = fund_result.get('FCF/CFO', 0)
    if fcf_cfo > 0.8: score += 4
    elif fcf_cfo > 0.5: score += 2
    
    return min(score, 100)


def calculate_comprehensive_technical_score(tech_result):
    """
    Comprehensive technical scoring using ALL specified indicators.
    Max Score: 100
    
    Uses the sub-scores from each indicator and normalizes them.
    """
    total_score = 0
    max_possible = 0
    
    # ===== 1. MOVING AVERAGES (Weight: 30%) =====
    ma = tech_result.get('moving_averages', {})
    ma_score = ma.get('score', 0)
    ma_max = ma.get('max_score', 3)
    
    # Normalize to 30 points
    if ma_max > 0:
        total_score += (ma_score / ma_max) * 30
    max_possible += 30
    
    # Bonus for distance metrics (proximity to support)
    dist_20 = ma.get('dist_from_20', 0)
    if -5 < dist_20 < 5:  # Within 5% of 20-day MA (good entry)
        total_score += 5
    max_possible += 5
    
    # ===== 2. MACD (Weight: 25%) =====
    macd = tech_result.get('macd', {})
    macd_score = macd.get('score', 0)
    macd_max = macd.get('max_score', 3)
    
    # Normalize to 20 points
    if macd_max > 0:
        total_score += (macd_score / macd_max) * 20
    max_possible += 20
    
    # Bonus for recent crossover
    if macd.get('recent_crossover') and macd.get('crossover_days_ago', 100) <= 5:
        total_score += 5
    max_possible += 5
    
    # ===== 3. RSI (Weight: 20%) =====
    rsi_data = tech_result.get('rsi', {})
    rsi_score = rsi_data.get('score', 0)
    rsi_max = rsi_data.get('max_score', 3)
    
    # Normalize to 20 points
    if rsi_max > 0:
        total_score += (rsi_score / rsi_max) * 20
    max_possible += 20
    
    # ===== 4. VWMA (Weight: 20%) =====
    vwma = tech_result.get('vwma', {})
    vwma_score = vwma.get('score', 0)
    vwma_max = vwma.get('max_score', 3)
    
    # Normalize to 15 points
    if vwma_max > 0:
        total_score += (vwma_score / vwma_max) * 15
    max_possible += 15
    
    # Bonus for volume pattern
    if vwma.get('volume_pattern_bullish'):
        total_score += 5
    max_possible += 5
    
    # Normalize to 100
    if max_possible > 0:
        final_score = (total_score / max_possible) * 100
    else:
        final_score = 0
    
    return min(final_score, 100)
