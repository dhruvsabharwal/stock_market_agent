weekly_trend_state trend_state dstage  tr_n  tr_sell_win  tr_mom_win  tr_reward8  ho_n  ho_sell_win  ho_mom_win  ho_reward8
           uptrend     uptrend      0   829         29.1        43.1        27.3   420         31.0        42.6        28.6
           uptrend      basing      0   971         27.4        41.8        26.9   560         28.6        38.9        26.4
            basing     uptrend      0  2393         27.2        43.8        28.5  1627         26.6        42.4        27.5
            basing      basing      0  9243         29.3        41.7        28.2  6539         25.5        39.8        25.6
            basing     uptrend      1  4745         20.8        40.1        27.9  4305         20.7        39.3        27.1
           uptrend     uptrend      1  3651         20.4        39.6        26.1  2567         20.5        39.0        26.6
            basing      basing      1  4847         23.5        38.6        27.5  4227         20.2        37.7        26.2
           uptrend      basing      1  2098         24.9        42.6        29.3  1513         19.9        39.3        26.0
            basing      basing      2  1361         21.2        37.5        27.0  1273         19.1        36.1        25.4
           uptrend     uptrend      2  1779         18.5        38.9        27.3  1257         18.6        35.3        25.5
            basing      basing     3+   584         20.7        36.0        24.7   444         18.5        35.1        25.0
           uptrend      basing      2  1047         23.2        43.4        29.0   805         18.0        37.1        26.2
           uptrend      basing     3+   618         17.6        37.2        25.7   487         17.9        40.0        29.0
            basing     uptrend     3+   447         15.4        32.7        22.6   359         17.8        38.2        27.0
            basing     uptrend      2  1235         19.1        38.1        27.5  1059         17.0        37.1        25.7
           uptrend     uptrend     3+  1016         18.6        39.5        27.6   678         13.6        34.4        22.9



volume filter > 500k should always be there sanity

weekly_trend_state trend_state dstage  tr_n  tr_sell_win  tr_mom_win  tr_reward8  ho_n  ho_sell_win  ho_mom_win  ho_reward8
           uptrend     uptrend      0   640         28.9        42.8        28.0   378         31.5        43.4        28.6
           uptrend      basing      0   749         28.0        43.5        27.4   512         29.5        39.6        27.1
            basing     uptrend      0  1759         27.1        43.4        27.6  1463         27.1        43.1        27.7
            basing      basing      0  6058         31.7        45.5        31.0  5428         26.6        41.4        26.7
            basing     uptrend      1  3043         21.3        40.4        27.6  3628         21.7        40.2        27.5
            basing      basing      1  2801         24.3        41.1        28.8  3300         21.2        38.7        27.1
           uptrend      basing      1  1523         25.3        44.5        30.1  1345         21.0        40.3        27.0
           uptrend     uptrend      1  2644         20.0        39.2        25.4  2281         20.6        38.5        25.7
            basing      basing      2   676         21.2        40.5        28.7   905         20.3        38.5        27.1
           uptrend     uptrend      2  1267         18.2        39.2        27.1  1132         18.9        35.4        25.4
            basing      basing     3+   260         20.4        39.6        27.7   288         18.8        35.4        26.7
           uptrend      basing     3+   413         17.7        37.0        25.4   428         18.2        39.7        29.0
           uptrend      basing      2   766         24.8        45.4        30.8   722         18.0        37.0        26.0
            basing     uptrend     3+   248         16.5        31.9        21.8   255         17.6        39.2        27.5
            basing     uptrend      2   729         20.0        39.2        27.3   836         17.3        37.2        25.4
           uptrend     uptrend     3+   727         17.7        38.9        26.4   617         14.3        35.7        23.7



market in uptrend

weekly_trend_state trend_state dstage  tr_n  tr_sell_win  tr_mom_win  tr_reward8  ho_n  ho_sell_win  ho_mom_win  ho_reward8
           uptrend     uptrend      0   409         28.9        43.5        27.9   280         31.1        42.9        27.5
           uptrend      basing      0   434         27.4        43.1        25.8   353         26.6        38.2        25.8
            basing     uptrend      0   919         28.2        44.1        27.2   981         25.0        41.7        26.3
            basing      basing      0  2824         32.2        45.2        31.8  3489         24.8        39.6        25.4
           uptrend      basing      1   822         25.3        43.2        30.7   974         21.1        38.8        27.4
            basing      basing      2   351         20.8        39.9        27.6   640         20.6        39.8        28.6
           uptrend     uptrend      1  1575         19.0        38.5        24.6  1774         20.6        38.2        25.4
            basing     uptrend      1  1684         22.2        40.6        27.8  2552         20.1        38.8        26.5
            basing      basing      1  1383         25.4        39.9        28.9  2256         20.0        37.3        26.1
           uptrend     uptrend      2   731         16.4        36.9        24.6   894         20.0        36.4        26.2
           uptrend      basing     3+   227         17.6        32.2        23.8   322         18.0        38.2        28.0
           uptrend      basing      2   411         21.9        43.6        28.5   522         16.7        34.9        25.9
            basing     uptrend      2   409         21.3        39.1        28.6   612         16.3        35.9        24.5
           uptrend     uptrend     3+   412         14.8        36.2        22.6   498         13.7        34.1        23.1


Clean, important result: no market-regime filter improves the win rate. All variants, on the vol base:

(none) vol base                        | TRAIN n= 24303 sell=24.9% mom=42.1% || HOLD n= 23518 sell=22.5% mom=39.6%
SPY 1m & 3m >= 0  (your proxy)         | TRAIN n= 12880 sell=24.6% mom=41.3% || HOLD n= 16541 sell=21.3% mom=38.4%
SPY 6m >= 0  (slower)                  | TRAIN n= 14967 sell=25.2% mom=42.1% || HOLD n= 19241 sell=22.5% mom=39.6%
NOT severe: SPY 3m > -8%               | TRAIN n= 19107 sell=24.5% mom=41.6% || HOLD n= 22843 sell=22.3% mom=39.4%
NOT severe: SPY 3m > -12%              | TRAIN n= 19833 sell=24.4% mom=41.6% || HOLD n= 23205 sell=22.3% mom=39.5%
QQQ above 50sma (regime col)           | TRAIN n= 12674 sell=23.5% mom=40.4% || HOLD n= 19685 sell=22.0% mom=39.0%
QQQ above 20ema                        | TRAIN n= 13001 sell=23.7% mom=40.4% || HOLD n= 18628 sell=22.2% mom=39.2%
QQQ above 20ema AND 50sma              | TRAIN n= 11781 sell=23.4% mom=40.0% || HOLD n= 17920 sell=22.1% mom=39.2%



BASE (vol>500K, investable): TRAIN sell=24.9% mom=42.1%  || HOLD sell=22.5% mom=39.6%

FILTER (indep, on vol base)          ho_n tr_sell ho_sell tr_mom ho_mom  ho_sell lift
coiled_up                            6675    30.8    26.5   44.6   41.2          +4.0
rs_vs_spy_6m in -10..30 (sweet)     11462    26.8    25.7   42.2   41.0          +3.2
eps_yoy_growth > 0                   9171    26.0    25.7   41.2   41.2          +3.2
overhead_highest_pct_6m < 10         7194    28.7    24.8   44.7   41.2          +2.3
ret_3m in 0..40 (moderate)          15602    26.8    24.2   42.9   40.6          +1.7
breakout_vol_ratio > 1.5            10547    26.5    23.7   43.1   39.2          +1.2
breakout_vol_ratio > 1              17336    25.6    23.4   42.4   39.6          +0.9
strong_close                        10234    24.7    23.3   42.5   41.2          +0.8
close_ext_200sma_pct > 0            19018    25.0    23.1   42.1   39.9          +0.6
rs_vs_spy_6m > 0                    15928    24.2    22.8   41.7   39.8          +0.3
above_10ema                         23499    24.9    22.5   42.1   39.6          +0.0
above_20ema                         23496    24.8    22.5   42.1   39.6          +0.0
expansion_closing_range > 0.5       23508    24.9    22.5   42.2   39.6          +0.0
blue_sky_6m (no overhead)            8851    23.2    22.4   41.1   38.9          -0.1

The five that work — coiled_up, RS-sweet-band, eps>0, low-overhead, moderate-ret_3m — map to the five independent winner themes from 

BASE (vol>500K investable): TRAIN sell=24.9% || HOLD sell=22.5% mom=39.6%

--- range_length_days ---
            bucket   tr_n tr_sell tr_mom |   ho_n ho_sell ho_mom
                 3   5841    19.8   38.7 |   6529    19.1   37.6
               4-5   7650    23.3   41.5 |   7509    20.9   39.5
               6-8   5733    26.2   43.3 |   5168    23.5   40.7
              9-13   3314    29.4   44.6 |   2853    27.8   41.5
               14+   1765    35.6   47.4 |   1459    31.3   42.2

--- range_height_hl_pct ---
            bucket   tr_n tr_sell tr_mom |   ho_n ho_sell ho_mom
    (-0.001, 5.26]   5629    27.4   43.0 |   3975    26.7   41.6
      (5.26, 6.02]   5042    26.9   42.3 |   4538    25.3   40.8
      (6.02, 6.86]   4888    27.0   44.3 |   4660    23.7   40.4
      (6.86, 8.15]   4685    22.9   42.3 |   4885    21.4   38.8
     (8.15, 46.85]   4059    18.4   37.9 |   5460    17.0   37.3

--- range_last3_height_hl_pct ---
            bucket   tr_n tr_sell tr_mom |   ho_n ho_sell ho_mom
    (-0.001, 3.64]   5654    31.0   45.2 |   3953    28.5   41.2
       (3.64, 4.7]   5210    27.0   43.8 |   4364    24.9   40.6
       (4.7, 5.71]   4998    23.9   41.1 |   4577    23.5   40.1
      (5.71, 7.12]   4538    22.3   41.8 |   4988    21.0   39.7
     (7.12, 45.87]   3903    17.2   37.0 |   5636    16.9   37.3

--- tight_range_pct_2pct ---
            bucket   tr_n tr_sell tr_mom |   ho_n ho_sell ho_mom
             0-25%  10197    19.5   39.2 |  13257    18.9   38.1

--- tight_range_pct_last3_2pct ---
            bucket   tr_n tr_sell tr_mom |   ho_n ho_sell ho_mom
             0-33%  12449    20.9   40.1 |  15371    19.8   38.7



Holdout sell_win — the joint grid
length ↓ / tightness →	
        tight <4%    med 4-6%    wide >6%
3           26.3        23.0        17.0
4-5         27.0        21.8        18.1
6-8         25.7        22.9        21.9
9-13        28.5        27.7        25.4
14+         31.8        31.7        23.3*


[filter: avg_dollar_vol_20d>500000 and range_length_days>=4 and range_length_days<=6 and range_last3_height_hl_pct<6]  holdout base: sell_win=22.8% mom_win=40.1% reward8=26.2%  (n=5758)
weekly_trend_state trend_state dstage  tr_n  tr_sell_win  tr_mom_win  tr_reward8  ho_n  ho_sell_win  ho_mom_win  ho_reward8
           uptrend     uptrend      0   186         32.3        44.6        29.6    99         29.3        47.5        28.3
           uptrend      basing     3+   103         17.5        41.7        30.1    77         28.6        54.5        41.6
            basing     uptrend      0   669         26.8        43.2        27.7   504         25.4        42.9        26.2
            basing      basing      0  1643         31.2        45.3        31.0  1400         24.6        41.4        25.6
           uptrend      basing      0   165         20.6        39.4        24.8    91         24.2        31.9        24.2
            basing      basing      1   779         25.0        43.5        31.3   715         23.5        41.8        28.7
           uptrend      basing      2   218         26.1        45.9        31.2   160         22.5        38.8        28.1
           uptrend     uptrend      1   880         21.1        38.6        25.2   634         22.4        37.9        25.6
           uptrend      basing      1   426         28.6        45.8        31.9   287         22.3        42.9        27.5
            basing     uptrend      1   928         22.6        40.3        28.0   978         22.0        39.7        26.2
            basing     uptrend      2   199         21.1        39.2        27.6   155         18.1        40.6        29.0
           uptrend     uptrend      2   403         18.9        41.4        28.3   258         16.7        32.6        22.1
            basing      basing      2   144         22.2        41.0        28.5   160         16.2        30.6        18.8
           uptrend     uptrend     3+   191         23.0        41.4        28.8   138         13.8        31.9        18.8