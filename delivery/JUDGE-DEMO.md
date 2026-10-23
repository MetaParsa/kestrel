# اجرای دمو‌ی داوران — Judge demo record

این صفحه خروجی `uv run python -m judge.demo --windows 25` است: همه‌ی شواهد، از جمله **هر ۲۵ پنجره‌ی بک‌تست**، از داده‌ی ثبت‌شده در `data/sample/` و لاگ‌های `delivery/server-logs/` دوباره ساخته و با عدد ثبت‌شده در `reports/out/` مقایسه شد. بدون کلید API و بدون اینترنت. یک کلون تازه از GitHub روی ویندوز (venv تازه با `uv sync`) هم `pytest` (۲۵۵ تست در آن لحظه) و `judge.demo --quick` را بدون خطا و با ۱۵ از ۱۵ عدد یکسان اجرا کرد.

This is the output of `uv run python -m judge.demo --windows 25` on 2026-10-23 (all 25 backtest windows in parallel on a 28-core laptop). A fresh GitHub clone on Windows with a fresh venv also ran `pytest` and `judge.demo --quick` with every number matching.

| check | committed | reproduced | |
|---|---|---|---|
| Self-grading: Claude forecasts scored | 58 | 58 | ✅ |
| Self-grading: Claude direction hit rate | 0.466 | 0.466 | ✅ |
| Self-grading: naive baseline hit rate | 0.414 | 0.414 | ✅ |
| Self-grading: 'always flat' hit rate | 0.483 | 0.483 | ✅ |
| Self-grading: playbooks citing own scorecard | 30 | 30 | ✅ |
| Seatbelt: −10% crash, with seatbelt (%) | -0.769 | -0.769 | ✅ |
| Seatbelt: −10% crash, held through (%) | -1.968 | -1.968 | ✅ |
| Seatbelt: −30% glitch print, result (%) | -0.028 | -0.028 | ✅ |
| Seatbelt: panic sells on the glitch | 0 | 0 | ✅ |
| Control group: Claude, after selling to TMN (%) | 0.014 | 0.014 | ✅ |
| Control group: rule twin, after selling to TMN (%) | 0.014 | 0.014 | ✅ |
| Control group: Claude max drawdown (%) | 0.026 | 0.026 | ✅ |
| Control group: rule twin max drawdown (%) | 0.177 | 0.177 | ✅ |
| Control group: Claude fees (TMN) | 601 | 601 | ✅ |
| Control group: rule twin fees (TMN) | 4879 | 4879 | ✅ |
| Backtest window 2026-09-25: return (%) | -0.187 | -0.187 | ✅ |
| Backtest window 2026-09-25: max drawdown (%) | 0.273 | 0.273 | ✅ |
| Backtest window 2026-09-25: fills | 13 | 13 | ✅ |
| Backtest window 2026-09-25: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-09-26: return (%) | -0.026 | -0.026 | ✅ |
| Backtest window 2026-09-26: max drawdown (%) | 0.254 | 0.254 | ✅ |
| Backtest window 2026-09-26: fills | 11 | 11 | ✅ |
| Backtest window 2026-09-26: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-09-27: return (%) | 0.442 | 0.442 | ✅ |
| Backtest window 2026-09-27: max drawdown (%) | 0.341 | 0.341 | ✅ |
| Backtest window 2026-09-27: fills | 12 | 12 | ✅ |
| Backtest window 2026-09-27: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-09-28: return (%) | 0.278 | 0.278 | ✅ |
| Backtest window 2026-09-28: max drawdown (%) | 0.427 | 0.427 | ✅ |
| Backtest window 2026-09-28: fills | 14 | 14 | ✅ |
| Backtest window 2026-09-28: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-09-29: return (%) | 0.056 | 0.056 | ✅ |
| Backtest window 2026-09-29: max drawdown (%) | 0.513 | 0.513 | ✅ |
| Backtest window 2026-09-29: fills | 13 | 13 | ✅ |
| Backtest window 2026-09-29: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-09-30: return (%) | -0.09 | -0.09 | ✅ |
| Backtest window 2026-09-30: max drawdown (%) | 0.415 | 0.415 | ✅ |
| Backtest window 2026-09-30: fills | 12 | 12 | ✅ |
| Backtest window 2026-09-30: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-01: return (%) | 0.33 | 0.33 | ✅ |
| Backtest window 2026-10-01: max drawdown (%) | 0.299 | 0.299 | ✅ |
| Backtest window 2026-10-01: fills | 11 | 11 | ✅ |
| Backtest window 2026-10-01: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-02: return (%) | 0.169 | 0.169 | ✅ |
| Backtest window 2026-10-02: max drawdown (%) | 0.313 | 0.313 | ✅ |
| Backtest window 2026-10-02: fills | 13 | 13 | ✅ |
| Backtest window 2026-10-02: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-03: return (%) | 0.366 | 0.366 | ✅ |
| Backtest window 2026-10-03: max drawdown (%) | 0.282 | 0.282 | ✅ |
| Backtest window 2026-10-03: fills | 12 | 12 | ✅ |
| Backtest window 2026-10-03: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-04: return (%) | -0.203 | -0.203 | ✅ |
| Backtest window 2026-10-04: max drawdown (%) | 0.554 | 0.554 | ✅ |
| Backtest window 2026-10-04: fills | 9 | 9 | ✅ |
| Backtest window 2026-10-04: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-05: return (%) | -0.273 | -0.273 | ✅ |
| Backtest window 2026-10-05: max drawdown (%) | 0.466 | 0.466 | ✅ |
| Backtest window 2026-10-05: fills | 6 | 6 | ✅ |
| Backtest window 2026-10-05: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-06: return (%) | -0.341 | -0.341 | ✅ |
| Backtest window 2026-10-06: max drawdown (%) | 0.37 | 0.37 | ✅ |
| Backtest window 2026-10-06: fills | 8 | 8 | ✅ |
| Backtest window 2026-10-06: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-07: return (%) | -0.473 | -0.473 | ✅ |
| Backtest window 2026-10-07: max drawdown (%) | 0.526 | 0.526 | ✅ |
| Backtest window 2026-10-07: fills | 12 | 12 | ✅ |
| Backtest window 2026-10-07: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-08: return (%) | -0.248 | -0.248 | ✅ |
| Backtest window 2026-10-08: max drawdown (%) | 0.327 | 0.327 | ✅ |
| Backtest window 2026-10-08: fills | 8 | 8 | ✅ |
| Backtest window 2026-10-08: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-09: return (%) | -0.047 | -0.047 | ✅ |
| Backtest window 2026-10-09: max drawdown (%) | 0.144 | 0.144 | ✅ |
| Backtest window 2026-10-09: fills | 4 | 4 | ✅ |
| Backtest window 2026-10-09: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-10: return (%) | -0.23 | -0.23 | ✅ |
| Backtest window 2026-10-10: max drawdown (%) | 0.262 | 0.262 | ✅ |
| Backtest window 2026-10-10: fills | 5 | 5 | ✅ |
| Backtest window 2026-10-10: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-11: return (%) | -0.34 | -0.34 | ✅ |
| Backtest window 2026-10-11: max drawdown (%) | 0.379 | 0.379 | ✅ |
| Backtest window 2026-10-11: fills | 11 | 11 | ✅ |
| Backtest window 2026-10-11: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-12: return (%) | -0.433 | -0.433 | ✅ |
| Backtest window 2026-10-12: max drawdown (%) | 0.433 | 0.433 | ✅ |
| Backtest window 2026-10-12: fills | 13 | 13 | ✅ |
| Backtest window 2026-10-12: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-13: return (%) | -0.31 | -0.31 | ✅ |
| Backtest window 2026-10-13: max drawdown (%) | 0.437 | 0.437 | ✅ |
| Backtest window 2026-10-13: fills | 14 | 14 | ✅ |
| Backtest window 2026-10-13: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-14: return (%) | -0.405 | -0.405 | ✅ |
| Backtest window 2026-10-14: max drawdown (%) | 0.596 | 0.596 | ✅ |
| Backtest window 2026-10-14: fills | 11 | 11 | ✅ |
| Backtest window 2026-10-14: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-15: return (%) | -0.431 | -0.431 | ✅ |
| Backtest window 2026-10-15: max drawdown (%) | 0.485 | 0.485 | ✅ |
| Backtest window 2026-10-15: fills | 12 | 12 | ✅ |
| Backtest window 2026-10-15: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-16: return (%) | -0.494 | -0.494 | ✅ |
| Backtest window 2026-10-16: max drawdown (%) | 0.494 | 0.494 | ✅ |
| Backtest window 2026-10-16: fills | 8 | 8 | ✅ |
| Backtest window 2026-10-16: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-17: return (%) | -0.361 | -0.361 | ✅ |
| Backtest window 2026-10-17: max drawdown (%) | 0.369 | 0.369 | ✅ |
| Backtest window 2026-10-17: fills | 8 | 8 | ✅ |
| Backtest window 2026-10-17: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-18: return (%) | -0.378 | -0.378 | ✅ |
| Backtest window 2026-10-18: max drawdown (%) | 0.447 | 0.447 | ✅ |
| Backtest window 2026-10-18: fills | 10 | 10 | ✅ |
| Backtest window 2026-10-18: ended fully in TMN | True | True | ✅ |
| Backtest window 2026-10-19: return (%) | 0.25 | 0.25 | ✅ |
| Backtest window 2026-10-19: max drawdown (%) | 0.339 | 0.339 | ✅ |
| Backtest window 2026-10-19: fills | 12 | 12 | ✅ |
| Backtest window 2026-10-19: ended fully in TMN | True | True | ✅ |

**115/115 numbers reproduced exactly.** Full outputs: `judge_out/<step>/` (the reports, the Persian pages, and the engine's own decision/order/fill logs for each run).
