# mr_etf sensitivity sweep — 2026-04-21T03:13:17.697123+00:00

- **Period:** 2022-01-01 → 2024-12-31
- **Cells total:** 108
- **Cells with metrics:** 36
- **Cells skipped:** 72

## Top cells meeting `n_trades >= 100 AND sharpe >= 1.0 AND max_dd <= 0.20`

| universe | timeframe | adx_max | rsi_oversold | n_trades | sharpe | max_dd | profit_factor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| liquid_etfs_top20 | 1d | 30.0000 | 5.0000 | 107 | 1.7830 | 0.0511 | 1.8100 |
| liquid_etfs_top20 | 1d | 30.0000 | 10.0000 | 137 | 1.4820 | 0.0415 | 1.6240 |
| liquid_etfs_top20 | 1d | 20.0000 | 15.0000 | 114 | 1.3760 | 0.0428 | 1.3720 |
| liquid_etfs_top20 | 1d | 25.0000 | 10.0000 | 123 | 1.3350 | 0.0433 | 1.4380 |
| liquid_etfs_top20 | 1d | 20.0000 | 10.0000 | 107 | 1.3330 | 0.0399 | 1.4320 |

## Skipped cells (by reason)

| skip_reason | count |
| --- | --- |
| intraday-1h-not-wired | 36 |
| intraday-4h-not-wired | 36 |

## Full results

| universe | timeframe | adx_max | rsi_oversold | n_trades | sharpe | max_dd | profit_factor | skipped | skip_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| large_caps_50 | 1d | 15.0000 | 5.0000 | 63 | 0.9870 | 0.1160 | 1.4080 | False |  |
| large_caps_50 | 1d | 15.0000 | 10.0000 | 99 | 0.8430 | 0.1044 | 1.2130 | False |  |
| large_caps_50 | 1d | 15.0000 | 15.0000 | 106 | 0.8120 | 0.1092 | 1.1870 | False |  |
| large_caps_50 | 1d | 20.0000 | 5.0000 | 139 | 0.8650 | 0.1276 | 1.2080 | False |  |
| large_caps_50 | 1d | 20.0000 | 10.0000 | 189 | 1.1170 | 0.1088 | 1.2780 | False |  |
| large_caps_50 | 1d | 20.0000 | 15.0000 | 203 | 1.1420 | 0.1321 | 1.2820 | False |  |
| large_caps_50 | 1d | 25.0000 | 5.0000 | 188 | 0.5060 | 0.1324 | 1.0060 | False |  |
| large_caps_50 | 1d | 25.0000 | 10.0000 | 236 | 0.8670 | 0.1256 | 1.1480 | False |  |
| large_caps_50 | 1d | 25.0000 | 15.0000 | 256 | 0.8790 | 0.1335 | 1.1740 | False |  |
| large_caps_50 | 1d | 30.0000 | 5.0000 | 212 | 0.4070 | 0.0892 | 0.9830 | False |  |
| large_caps_50 | 1d | 30.0000 | 10.0000 | 256 | 0.7010 | 0.1044 | 1.0730 | False |  |
| large_caps_50 | 1d | 30.0000 | 15.0000 | 274 | 0.4470 | 0.1258 | 0.9590 | False |  |
| large_caps_50 | 1h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 1h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| large_caps_50 | 4h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| large_caps_50 | 4h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 1d | 15.0000 | 5.0000 | 39 | 1.4120 | 0.0613 | 1.6030 | False |  |
| liquid_etfs_top20 | 1d | 15.0000 | 10.0000 | 51 | 0.5250 | 0.0420 | 0.9330 | False |  |
| liquid_etfs_top20 | 1d | 15.0000 | 15.0000 | 56 | 0.4940 | 0.0423 | 0.8960 | False |  |
| liquid_etfs_top20 | 1d | 20.0000 | 5.0000 | 80 | 1.8560 | 0.0516 | 1.7370 | False |  |
| liquid_etfs_top20 | 1d | 20.0000 | 10.0000 | 107 | 1.3330 | 0.0399 | 1.4320 | False |  |
| liquid_etfs_top20 | 1d | 20.0000 | 15.0000 | 114 | 1.3760 | 0.0428 | 1.3720 | False |  |
| liquid_etfs_top20 | 1d | 25.0000 | 5.0000 | 97 | 1.7320 | 0.0543 | 1.7300 | False |  |
| liquid_etfs_top20 | 1d | 25.0000 | 10.0000 | 123 | 1.3350 | 0.0433 | 1.4380 | False |  |
| liquid_etfs_top20 | 1d | 25.0000 | 15.0000 | 131 | 1.2150 | 0.0451 | 1.2950 | False |  |
| liquid_etfs_top20 | 1d | 30.0000 | 5.0000 | 107 | 1.7830 | 0.0511 | 1.8100 | False |  |
| liquid_etfs_top20 | 1d | 30.0000 | 10.0000 | 137 | 1.4820 | 0.0415 | 1.6240 | False |  |
| liquid_etfs_top20 | 1d | 30.0000 | 15.0000 | 149 | 1.3140 | 0.0469 | 1.4170 | False |  |
| liquid_etfs_top20 | 1h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 1h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| liquid_etfs_top20 | 4h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| liquid_etfs_top20 | 4h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 1d | 15.0000 | 5.0000 | 4 | 1.5180 | 0.0166 | 3.5390 | False |  |
| spy_qqq | 1d | 15.0000 | 10.0000 | 4 | 0.8910 | 0.0063 | 3.2390 | False |  |
| spy_qqq | 1d | 15.0000 | 15.0000 | 4 | 0.3080 | 0.0066 | 0.8810 | False |  |
| spy_qqq | 1d | 20.0000 | 5.0000 | 4 | 1.5710 | 0.0166 | 3.5390 | False |  |
| spy_qqq | 1d | 20.0000 | 10.0000 | 6 | 1.4220 | 0.0083 | 6.1400 | False |  |
| spy_qqq | 1d | 20.0000 | 15.0000 | 7 | 1.1400 | 0.0092 | 2.5770 | False |  |
| spy_qqq | 1d | 25.0000 | 5.0000 | 6 | 1.7110 | 0.0227 | 6.0730 | False |  |
| spy_qqq | 1d | 25.0000 | 10.0000 | 8 | 1.6330 | 0.0083 | 8.5480 | False |  |
| spy_qqq | 1d | 25.0000 | 15.0000 | 9 | 1.0630 | 0.0090 | 1.8440 | False |  |
| spy_qqq | 1d | 30.0000 | 5.0000 | 6 | 1.7100 | 0.0179 | 6.0730 | False |  |
| spy_qqq | 1d | 30.0000 | 10.0000 | 10 | 1.4380 | 0.0102 | 4.0650 | False |  |
| spy_qqq | 1d | 30.0000 | 15.0000 | 11 | 0.9270 | 0.0090 | 1.6210 | False |  |
| spy_qqq | 1h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 1h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-1h-not-wired |
| spy_qqq | 4h | 15.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 15.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 15.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 20.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 20.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 20.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 25.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 25.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 25.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 30.0000 | 5.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 30.0000 | 10.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |
| spy_qqq | 4h | 30.0000 | 15.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | True | intraday-4h-not-wired |