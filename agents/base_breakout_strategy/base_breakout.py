import yfinance as yf
import pandas as pd
import numpy as np
import sys
from datetime import datetime

class BaseBreakoutAnalyzer:
    def __init__(self, ticker):
        self.ticker = ticker.upper()
        self.stock_weekly = None
        self.stock_daily = None
        self.spy_weekly = None
        self.results = {}
        self.score = 0

    def get_data(self):
        print(f"Fetching data for {self.ticker}...")
        # Round-trip 1: weekly price data for stock + SPY together
        symbols = list(set([self.ticker, "SPY"]))
        raw = yf.download(symbols, period="3y", interval="1wk", group_by="ticker", progress=False)
        
        if isinstance(raw.columns, pd.MultiIndex):
            self.stock_weekly = raw[self.ticker].dropna() if self.ticker in raw.columns.levels[0] else pd.DataFrame()
            self.spy_weekly = raw["SPY"].dropna() if "SPY" in raw.columns.levels[0] else pd.DataFrame()
        else:
            # Single ticker or returned as simple index
            self.stock_weekly = raw.dropna()
            self.spy_weekly = self.stock_weekly.copy() if self.ticker == "SPY" else pd.DataFrame()

        # If SPY is empty but stock is not, try to fetch SPY separately as a fallback
        if self.spy_weekly.empty and not self.stock_weekly.empty and self.ticker != "SPY":
            self.spy_weekly = yf.download("SPY", period="3y", interval="1wk", progress=False).dropna()

        # Round-trip 2: Ticker object — reuse for daily history AND fundamentals
        self.t = yf.Ticker(self.ticker)
        self.stock_daily = self.t.history(period="1y", interval="1d").dropna()
        self.info = self.t.info
        self.financials = self.t.quarterly_financials

        # Ensure tz-naive for all to avoid comparison errors
        for df in [self.stock_weekly, self.spy_weekly, self.stock_daily]:
            if not df.empty and hasattr(df.index, 'tz') and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            
        return not self.stock_weekly.empty and not self.stock_daily.empty

    def analyze_fundamentals(self):
        print(f"Analyzing fundamentals for {self.ticker}...")
        info = self.info
        financials = self.financials
        
        res = {}
        f_score = 0

        try:
            # EPS Growth (YoY)
            eps_row = financials.index[financials.index.str.contains('Diluted EPS', case=False)].tolist()
            if not eps_row:
                eps_row = financials.index[financials.index.str.contains('Basic EPS', case=False)].tolist()
                
            if eps_row:
                eps_data = financials.loc[eps_row[0]]
                if len(eps_data) >= 5:
                    yoy_eps_growth = (eps_data.iloc[0] / eps_data.iloc[4] - 1) * 100
                    res['eps_growth_yoy'] = round(yoy_eps_growth, 1)
                    if yoy_eps_growth >= 25: f_score += 1
                else:
                    res['eps_growth_yoy'] = "N/A"
            else:
                res['eps_growth_yoy'] = "N/A"

            # Revenue Growth (YoY)
            rev_row = financials.index[financials.index.str.contains('Total Revenue', case=False)].tolist()
            if rev_row:
                rev_data = financials.loc[rev_row[0]]
                if len(rev_data) >= 5:
                    yoy_rev_growth = (rev_data.iloc[0] / rev_data.iloc[4] - 1) * 100
                    res['rev_growth_yoy'] = round(yoy_rev_growth, 1)
                    if yoy_rev_growth >= 20: f_score += 1
                else:
                    res['rev_growth_yoy'] = "N/A"
            else:
                res['rev_growth_yoy'] = "N/A"

            # Net Margin
            res['net_margin'] = round(info.get('profitMargins', 0) * 100, 1) if info.get('profitMargins') else 0
            industry = info.get('industry', '').lower()
            margin_threshold = 15 if ('software' in industry or 'technology' in industry) else 8
            res['margin_pass'] = res['net_margin'] >= margin_threshold

        except Exception as e:
            print(f"Error in fundamentals: {e}")
            res.update({'eps_growth_yoy': "N/A", 'rev_growth_yoy': "N/A", 'net_margin': "N/A", 'margin_pass': False})

        self.results['fundamentals'] = res
        return f_score

    def calculate_rs_line(self):
        stock_close = self.stock_weekly['Close']
        spy_close = self.spy_weekly['Close']
        common_dates = stock_close.index.intersection(spy_close.index)
        stock_close, spy_close = stock_close.loc[common_dates], spy_close.loc[common_dates]

        rs_line = stock_close / spy_close

        def get_rs_change(weeks):
            return round((rs_line.iloc[-1] / rs_line.iloc[-weeks] - 1) * 100, 1) if len(rs_line) >= weeks else "N/A"

        res = {
            'rs_4w': get_rs_change(4), 'rs_13w': get_rs_change(13),
            'rs_26w': get_rs_change(26), 'rs_52w': get_rs_change(52),
        }
        
        if len(rs_line) >= 52:
            rs_52w_high = rs_line.iloc[-52:].max()
            res['rs_at_52w_high'] = rs_line.iloc[-1] >= (rs_52w_high * 0.98) # within 2%
            # Leading RS check will be done in run_analysis when pivot is known
            res['rs_52w_high_val'] = rs_52w_high
        else:
            res.update({'rs_at_52w_high': False, 'leading_rs': False})

        self.results['rs'] = res
        return 1 if res['rs_13w'] != "N/A" and res['rs_13w'] > 0 else 0

    def stage_2_check(self):
        df_w = self.stock_weekly.copy()
        df_w['SMA30w'] = df_w['Close'].rolling(30).mean()
        
        curr_price = df_w['Close'].iloc[-1]
        curr_sma30 = df_w['SMA30w'].iloc[-1]
        
        if len(df_w) < 34: return False
        
        sma30_4w_prior = df_w['SMA30w'].iloc[-5]
        stage2_pass = curr_price > curr_sma30 and curr_sma30 > sma30_4w_prior
        
        # 52w high position check
        high_52w = self.stock_daily['High'].rolling(window=252, min_periods=50).max().iloc[-1]
        pct_from_high = (curr_price / high_52w - 1) * 100
        
        low_52w = self.stock_daily['Low'].rolling(window=252, min_periods=50).min().iloc[-1]
        prior_uptrend = (curr_price / low_52w - 1) * 100 >= 30

        self.results['stage2'] = {
            'confirmed': stage2_pass, 'sma30': round(curr_sma30, 2),
            'price': round(curr_price, 2), 'prior_uptrend': prior_uptrend,
            'pct_from_high': round(pct_from_high, 1),
            'high_52w': high_52w, 'low_52w': low_52w
        }
        return 1 if stage2_pass else 0

    def base_analysis(self):
        weekly_close = self.stock_weekly['Close']
        lookback = min(54, len(weekly_close))
        base_high = weekly_close.iloc[-lookback:].max()
        base_high_idx = weekly_close.iloc[-lookback:].idxmax()
        
        post_peak = weekly_close.loc[base_high_idx:]
        if post_peak.empty: return 0, None
        
        base_low = post_peak.min()
        base_depth = round((base_low / base_high - 1) * 100, 1)
        base_len = len(post_peak)
        
        pattern = "Unclear"
        if -33 <= base_depth <= -15 and base_len >= 7: pattern = "Cup with Handle"
        elif -15 < base_depth <= -5 and base_len >= 5: pattern = "Flat Base"
        elif -35 <= base_depth <= -10 and base_len >= 5: pattern = "VCP"

        swings = [0, 0, 0]
        n = len(post_peak)
        if n >= 6:
            for i, seg in enumerate([post_peak.iloc[:n//3], post_peak.iloc[n//3:2*n//3], post_peak.iloc[2*n//3:]]):
                swings[i] = round((seg.max() - seg.min()) / seg.max() * 100, 1)
            vcp_c = swings[2] < swings[1] < swings[0]
        else: vcp_c = False

        self.results['base'] = {
            'pattern': pattern, 'length': base_len, 'depth': base_depth,
            'high': base_high, 'high_date': base_high_idx,
            'low': base_low, 'low_date': post_peak.idxmin(),
            'pass_depth': base_depth >= -50,
            'swings': swings, 'vcp_contracting': vcp_c, 'high_idx': base_high_idx
        }
        
        score_add = (1 if pattern != "Unclear" and base_depth >= -50 else 0) + (1 if vcp_c else 0)
        return score_add, base_high_idx

    def volume_analysis(self, base_start_idx):
        df_w = self.stock_weekly.loc[base_start_idx:]
        returns = df_w['Close'].pct_change()
        # Accumulation check restricted to base period
        up_weeks = df_w[returns > 0]
        down_weeks = df_w[returns < 0]
        
        avg_up = up_weeks['Volume'].mean() if not up_weeks.empty else 0
        avg_down = down_weeks['Volume'].mean() if not down_weeks.empty else 0
        acc_ratio = round(avg_up / avg_down, 2) if avg_down > 0 else 0
        
        # Right side vol dry up
        n = len(df_w)
        left_avg = df_w['Volume'].iloc[:n//2].mean() if n > 1 else 0
        right_avg = df_w['Volume'].iloc[n//2:].mean() if n > 1 else 0
        vol_dry = round(((right_avg / left_avg) - 1) * 100, 1) if left_avg > 0 else 0
        
        self.results['volume'] = {
            'avg_up': avg_up, 'avg_down': avg_down, 'acc_ratio': acc_ratio, 'vol_dry': vol_dry
        }
        # Strong: ratio > 1.3, Pass: ratio > 1.0
        return (1 if acc_ratio > 1.0 else 0) + (1 if vol_dry <= -20 else 0)

    def tight_area_detection(self):
        df_d = self.stock_daily.copy()
        df_d['range_pct'] = (df_d['High'] - df_d['Low']) / df_d['Close'] * 100
        df_d['vol_vs_avg'] = df_d['Volume'] / df_d['Volume'].rolling(50).mean()
        df_d['is_tight'] = (df_d['range_pct'] <= 1.5) & (df_d['vol_vs_avg'] <= 0.5)
        
        recent = df_d.tail(60)
        tight_count, current_run = 0, 0
        for val in recent['is_tight']:
            if val: current_run += 1
            else:
                if current_run >= 2: tight_count += 1
                current_run = 0
                
        last_tight = df_d[df_d['is_tight']].index[-1] if df_d['is_tight'].any() else None
        
        # Macro contraction: last 30 vs first 30 of recent 60
        first_half = recent['range_pct'].iloc[:30].mean()
        last_half = recent['range_pct'].iloc[30:].mean()
        macro_v = round(((last_half / first_half) - 1) * 100, 1) if first_half > 0 else 0
        
        self.results['tightness'] = {
            'count': tight_count, 'last_date': last_tight, 
            'first_half_range': round(first_half, 2), 'last_half_range': round(last_half, 2),
            'macro_v': macro_v, 'daily_df': df_d
        }
        return 1 if tight_count > 0 else 0

    def priming_patterns(self, pivot_price):
        df_d = self.results['tightness']['daily_df']
        inside, reversal, tight_setup = [], [], []
        
        for i in range(len(df_d)-10, len(df_d)):
            today, yest = df_d.iloc[i], df_d.iloc[i-1]
            
            # Inside Bar
            if today['High'] < yest['High'] and today['Low'] > yest['Low'] and today['vol_vs_avg'] < 0.75:
                inside.append({'date': today.name.strftime('%Y-%m-%d'), 'vol_ratio': round(today['vol_vs_avg'], 2)})
            
            # Upside Reversal
            day_range = today['High'] - today['Low']
            if day_range > 0:
                drop_pct = (today['Low'] / today['Open'] - 1) * 100
                close_pos = (today['Close'] - today['Low']) / day_range * 100
                if drop_pct <= -1.0 and close_pos >= 70:
                    reversal.append(today.name.strftime('%Y-%m-%d'))
            
            # Tight Setup Day
            at_pivot = abs(today['Close'] / pivot_price - 1) * 100 <= 2.0
            if at_pivot and today['range_pct'] <= 1.0 and today['vol_vs_avg'] <= 0.4:
                tight_setup.append({
                    'date': today.name.strftime('%Y-%m-%d'), 
                    'dist': round((today['Close'] / pivot_price - 1) * 100, 1),
                    'vol_ratio': round(today['vol_vs_avg'], 2)
                })
                
        self.results['priming'] = {'inside': inside, 'reversal': reversal, 'tight_setup': tight_setup}
        return 1 if inside or reversal or tight_setup else 0

    def find_support_levels(self, base_high_idx):
        df_d = self.stock_daily.loc[base_high_idx:]
        lows = df_d['Low']
        support_levels = []
        for low in lows:
            # Level test within 1.5%
            nearby = lows[(lows >= low * 0.985) & (lows <= low * 1.015)]
            if len(nearby) >= 2:
                support_levels.append(round(low, 2))
        
        support_levels = sorted(list(set(support_levels)))
        curr_price = self.stock_daily['Close'].iloc[-1]
        primary_support = max([s for s in support_levels if s < curr_price], default=None)
        
        self.results['support'] = {
            'all_levels': support_levels,
            'primary': primary_support
        }

    def stop_loss_status(self):
        df_d = self.results['tightness']['daily_df']
        df_d['EMA10'] = df_d['Close'].ewm(span=10, adjust=False).mean()
        df_d['EMA21'] = df_d['Close'].ewm(span=21, adjust=False).mean()
        df_d['SMA50'] = df_d['Close'].rolling(50).mean()
        
        curr = df_d.iloc[-1]
        closes = df_d['Close'].tail(2)
        ema10s = df_d['EMA10'].tail(2)
        
        consecutive_10 = (closes.iloc[-1] < ema10s.iloc[-1]) and (closes.iloc[-2] < ema10s.iloc[-2])
        below_21 = curr['Close'] < curr['EMA21']
        
        ps = self.results['support']['primary']
        below_support = ps is not None and curr['Close'] < ps * 0.99
        below_50 = curr['Close'] < curr['SMA50']
        
        rec = "HOLD"
        reason = "Trend is healthy"
        
        if below_support or below_50:
            rec = "EXIT"
            reason = "Thesis broken - below support or SMA50"
        elif below_21:
            rec = "REDUCE 50-100%"
            reason = "Trend weakening - EMA21 broken"
        elif consecutive_10:
            rec = "WATCH/REDUCE 25-33%"
            reason = "Warning - 2 consecutive closes below EMA10"
            if curr['vol_vs_avg'] > 1.0:
                rec = "REDUCE 50%"
                reason += " on high volume"

        # Hard stop check (calculated relative to pivot entry if broken out)
        entry = self.results['pivot']['entry']
        hard_stop_triggered = (curr['Close'] / entry - 1) * 100 < -7.5
        if hard_stop_triggered:
            rec = "EXIT (HARD STOP)"
            reason = "7.5% maximum loss reached"

        self.results['stop_status'] = {
            'ema10_dist': round((curr['Close'] / curr['EMA10'] - 1) * 100, 1),
            'ema10_state': "2 CLOSES BELOW" if consecutive_10 else "1 CLOSE BELOW" if curr['Close'] < curr['EMA10'] else "ABOVE",
            'ema21_dist': round((curr['Close'] / curr['EMA21'] - 1) * 100, 1),
            'ema21_state': "BELOW" if below_21 else "ABOVE",
            'sma50_dist': round((curr['Close'] / curr['SMA50'] - 1) * 100, 1),
            'sma50_state': "BELOW" if below_50 else "ABOVE",
            'support_dist': round((curr['Close'] / ps - 1) * 100, 1) if ps else "N/A",
            'support_state': "BELOW" if below_support else "ABOVE",
            'recommendation': rec,
            'reason': reason
        }
        # Score point 11: clean stop structure
        risk_pct = self.results['pivot'].get('risk_pct', 100)
        return 1 if ps and risk_pct <= 4.0 else 0

    def run_analysis(self):
        if not self.get_data(): return False
        
        self.score += self.analyze_fundamentals()
        self.score += self.calculate_rs_line()
        self.score += self.stage_2_check()
        
        score_base, base_start_idx = self.base_analysis()
        self.score += score_base
        
        if base_start_idx:
            self.score += self.volume_analysis(base_start_idx)
            self.find_support_levels(base_start_idx)
        else:
            self.results['support'] = {'all_levels': [], 'primary': None}
        
        self.score += self.tight_area_detection()
        
        # Pivot identification
        pivot = round(self.stock_weekly.tail(10)['High'].max(), 2)
        self.score += self.priming_patterns(pivot)
        
        curr_price = self.stock_daily['Close'].iloc[-1]
        broken_out = curr_price > pivot
        
        # Entry & Risk
        last_tight_date = self.results['tightness']['last_date']
        early_trigger, early_stop, risk_pct = None, None, 0
        if last_tight_date:
            df_d = self.results['tightness']['daily_df']
            early_trigger = df_d.loc[last_tight_date, 'High']
            early_stop = df_d.loc[last_tight_date, 'Low']
            risk_pct = round((early_trigger / early_stop - 1) * 100, 1)
            
        self.results['pivot'] = {
            'price': pivot, 'dist': round((curr_price / pivot - 1) * 100, 1),
            'entry': round(pivot * 1.005, 2), 'broken_out': broken_out,
            'early_trigger': round(early_trigger, 2) if early_trigger else None,
            'initial_stop': round(early_stop, 2) if early_stop else None,
            'risk_pct': risk_pct
        }
        
        # RS leading signal check
        rs_res = self.results['rs']
        if not broken_out and rs_res.get('rs_at_52w_high'):
            rs_res['leading_rs'] = True
        else:
            rs_res['leading_rs'] = False
            
        self.score += self.stop_loss_status()
        
        # Post breakout
        if broken_out:
            df_d = self.results['tightness']['daily_df']
            breakout_day = df_d[df_d['Close'] > pivot].index[0]
            vol_surge = df_d.loc[breakout_day, 'Volume'] / df_d.loc[:breakout_day, 'Volume'].rolling(50).mean().iloc[-1]
            
            last_5w = self.stock_weekly.tail(5)
            climax = (last_5w['Close'].iloc[-1] / last_5w['Close'].iloc[0] - 1) * 100 > 25
            vol_s = self.stock_weekly['Volume'].iloc[-1] / self.stock_weekly['Volume'].rolling(10).mean().iloc[-1] > 2.0
            close_pos = (last_5w['Close'].iloc[-1] - last_5w['Low'].iloc[-1]) / (last_5w['High'].iloc[-1] - last_5w['Low'].iloc[-1]) * 100
            
            self.results['breakout_quality'] = {
                'date': breakout_day.strftime('%Y-%m-%d'),
                'vol_surge': round(vol_surge, 2),
                'climax_warning': climax and vol_s and close_pos < 30
            }
        return True

    def generate_report(self):
        r = self.results
        #print(r)
        lines = []
        lines.append(f"=====================================")
        lines.append(f"BASE BREAKOUT ANALYSIS: {self.ticker}")
        lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
        lines.append(f"Current Price: ${r['stage2']['price']}")
        lines.append(f"=====================================")
        
        f = r['fundamentals']
        lines.append(f"\n[1] FUNDAMENTALS SNAPSHOT")
        lines.append(f"  EPS Growth (YoY):       {f['eps_growth_yoy']}%")
        lines.append(f"  Revenue Growth (YoY):   {f['rev_growth_yoy']}%")
        lines.append(f"  Net Margin:             {f['net_margin']}%")
        
        rs = r['rs']
        lines.append(f"\n[2] TRUE RELATIVE STRENGTH vs SPY")
        lines.append(f"  RS Line (13w):          {rs['rs_13w']}%")
        lines.append(f"  RS Line (52w):          {rs['rs_52w']}%")
        lines.append(f"  RS Line at 52w high?    {'YES' if rs['rs_at_52w_high'] else 'NO'}")
        lines.append(f"  Leading RS signal?      {'YES' if rs['leading_rs'] else 'NO'}")
        
        s2 = r['stage2']
        lines.append(f"\n[3] STAGE 2 CHECK")
        lines.append(f"  Above 30w SMA?          {'YES' if s2['confirmed'] else 'NO'} (SMA: ${s2['sma30']})")
        lines.append(f"  Stage 2 confirmed?      {'YES' if s2.get('prepared', s2['confirmed']) else 'NO'}")
        
        b = r['base']
        lines.append(f"\n[4] BASE ANALYSIS")
        lines.append(f"  Base pattern type:      {b['pattern']}")
        lines.append(f"  Base length:            {b['length']} weeks")
        lines.append(f"  Base depth:             {b['depth']}%")
        lines.append(f"  Already broken out?     {'YES' if r['pivot']['broken_out'] else 'NO'}")
        lines.append(f"  VCP contraction:        Swing1={b['swings'][0]}%, Swing2={b['swings'][1]}%, Swing3={b['swings'][2]}%")
        
        v = r['volume']
        lines.append(f"\n[5] VOLUME ANALYSIS")
        lines.append(f"  Accumulation ratio:     {v['acc_ratio']}")
        lines.append(f"  Right-side vol vs left: {v['vol_dry']}% change")
        
        t = r['tightness']
        lines.append(f"\n[6] TIGHTNESS (DAILY)")
        lines.append(f"  Tight areas (last 30d): {t['count']}")
        lines.append(f"  Volatility contraction: {t['macro_v']}%")
        
        p = r['priming']
        lines.append(f"\n[7] PRIMING PATTERNS (last 10 days)")
        lines.append(f"  Inside bars found:      {', '.join([ib['date'] for ib in p['inside']]) if p['inside'] else 'NONE'}")
        lines.append(f"  Upside reversals found: {', '.join(p['reversal']) if p['reversal'] else 'NONE'}")
        lines.append(f"  Tight setup day:        {p['tight_setup'][0]['date'] if p['tight_setup'] else 'NONE'}")
        
        pv = r['pivot']
        lines.append(f"\n[8] PIVOT & ENTRY")
        lines.append(f"  Pivot price:            ${pv['price']}")
        lines.append(f"  Breakout entry:         ${pv['entry']}")
        lines.append(f"  Risk %:                 {pv['risk_pct']}%")
        
        stop = r['stop_status']
        lines.append(f"\n[9] STOP LOSS STATUS")
        lines.append(f"  Primary support level:  ${r['support']['primary']}")
        lines.append(f"  EMA10 status:           {stop['ema10_dist']}% away / {stop['ema10_state']}")
        lines.append(f"  EMA21 status:           {stop['ema21_dist']}% away / {stop['ema21_state']}")
        lines.append(f"  Current recommendation: {stop['recommendation']}")
        lines.append(f"  Reason:                 {stop['reason']}")
        
        if r['pivot']['broken_out'] and 'breakout_quality' in r:
            bq = r['breakout_quality']
            lines.append(f"\n[10] POST-BREAKOUT STATUS")
            lines.append(f"  Breakout date:          {bq['date']}")
            lines.append(f"  Volume surge:           {bq['vol_surge']}x")
            lines.append(f"  Climax warning:         {'YES' if bq['climax_warning'] else 'NO'}")

        lines.append(f"\n=====================================")
        lines.append(f"OVERALL SETUP SCORE: {self.score}/11")
        quality = "ACTIONABLE" if self.score >= 10 else "DEVELOPING" if self.score >= 7 else "NOT READY" if self.score >= 5 else "AVOID"
        lines.append(f"SETUP QUALITY:       {quality}")
        lines.append(f"=====================================")

        report = "\n".join(lines)
        self.results['report'] = report
        print(report)
        return self.results

def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else input("Ticker: ").upper()
    if ticker:
        analyzer = BaseBreakoutAnalyzer(ticker)
        if analyzer.run_analysis():
            analyzer.generate_report()

if __name__ == "__main__":
    main()
