import pandas as pd
import numpy as np

def generate_target_weights(df: pd.DataFrame) -> pd.Series:
    """
    Computes a path-dependent sequence of target portfolio weights for a single asset.
    Implements Moglen's precise tactics:
    - T+1 Execution: Buys on the OPEN of the day AFTER the setup signal.
    - 3.5% Initial Hard Stop.
    - Scale-out: Sells 1/3rd of the position when price reaches +10% gain.
    - Breakeven Stop: Moves the hard stop to the entry price after scaling out.
    - Trailing Stop: Exits remaining position if price closes below 21 EMA for 2 consecutive days.
    """
    weights = pd.Series(0.0, index=df.index, dtype=float)
    
    # Pre-compute vectorized conditions
    pivot_shift = df['pivot_point'].shift(1)
    base_valid_shift = df['is_base'].shift(1)
    
    price_trigger = df['high'] > pivot_shift
    volume_trigger = df['volume'] > df['volume_sma_20']
    score_trigger = df['setup_score'] >= 2
    market_trigger = df['market_uptrend'] == 1 if 'market_uptrend' in df.columns else True
    
    # raw_setup is True on the day the breakout OCCURS (Day T)
    raw_setup = price_trigger & volume_trigger & score_trigger & base_valid_shift & market_trigger
    
    # Trailing Stop condition: 2 consecutive closes below 21 EMA
    close_below_ema = df['close'] < df['ema_21']
    two_days_below_ema = close_below_ema & close_below_ema.shift(1)
    
    in_position = False
    entry_price = 0.0
    hard_stop = 0.0
    shares_held_pct = 0.0
    scaled_out = False
    
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    setup_scores = df['setup_score'].values
    
    raw_setup_arr = raw_setup.fillna(False).values
    two_days_below_ema_arr = two_days_below_ema.fillna(False).values
    
    for i in range(1, len(df)):
        # Check if yesterday generated a setup signal. If so, execute today (T+1)
        if not in_position and raw_setup_arr[i-1]:
            in_position = True
            entry_price = opens[i] # Execute at T+1 open price
            
            # Position sizing based on User's New Rules:
            # Score 2 = 5%, Score 3 = 10%, Score 4 = 15% (Max Cap)
            setup_score = setup_scores[i-1]
            if setup_score == 2:
                shares_held_pct = 0.05
            elif setup_score == 3:
                shares_held_pct = 0.10
            elif setup_score >= 4:
                shares_held_pct = 0.15
            else:
                shares_held_pct = 0.0 
                
            hard_stop_pct = 0.030 # 3% Hard Stop
            hard_stop = entry_price * (1.0 - hard_stop_pct)
            scaled_out = False
            # These variables were introduced in the user's snippet, assuming they are meant to be initialized here.
            # They are not used later in the provided function, but are kept for faithfulness to the instruction.
            scale_out_date = None
            scale_out_price = 0.0 
            
        if in_position:
            current_low = lows[i]
            current_high = highs[i]
            
            # 1. Check Hard Stop (Did we slice through initial stop or breakeven stop?)
            if current_low <= hard_stop:
                in_position = False
                shares_held_pct = 0.0
                
            # 2. Check Scale Out (+10% gain)
            elif not scaled_out and current_high >= entry_price * 1.10:
                scaled_out = True
                shares_held_pct = shares_held_pct * (2.0 / 3.0) # Sell 1/3rd of position
                hard_stop = entry_price # Move stop to breakeven
                
            # 3. Check Trailing Stop (Yesterday was the 2nd consecutive close below 21 EMA)
            # We exit today.
            elif two_days_below_ema_arr[i-1]:
                in_position = False
                shares_held_pct = 0.0
                
        weights.iloc[i] = shares_held_pct
        
    return weights

