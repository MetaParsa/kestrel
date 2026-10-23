# هوش مصنوعی‌ای که به خودش نمره می‌دهد

Claude در هر برنامه، جهت و نوسان ۴ ساعت بعدِ هر بازار را پیش‌بینی می‌کند. چهار ساعت بعد، کد پیش‌بینی را با بازار واقعی مقایسه می‌کند (جهت با باند خنثیِ ±۰٫۲٪؛ نوسان در برابر نوسان ۳۰ روزه)، کنارش دو مبنای ساده را هم نمره می‌دهد، و کارنامه را در چرخه‌ی بعد به خود Claude برمی‌گرداند. همه‌چیز بدون نگاه به آینده: فقط کندل‌هایی که بسته شده بودند.

**۵۳ برنامه، ۷۲ پیش‌بینی ارزیابی‌شده.**

| | Claude | مبنای ساده («مثل ۴ ساعت قبل») | «همیشه خنثی» |
|---|---|---|---|
| همه | ۴۶٪ | ۳۳٪ | ۴۲٪ |
| USDTTMN (۳۶) | ۵۳٪ | ۳۹٪ | ۴۷٪ |
| BTCTMN (۳۶) | ۳۹٪ | ۲۸٪ | ۳۶٪ |

پیش‌بینی نوسان: ۶۱٪ درست.

**صادقانه:** نمونه کوچک است و بیشتر ساعت‌ها بازار خنثی بوده؛ ارزش این بخش در خود حلقه‌ی بازخورد است، نه در یک عدد.

## جاهایی که Claude از کارنامه‌ی خودش استفاده کرد

- **۱۰-۲۲ ۱۷:۳۸** (`pb-20261022-1738-06fc9c`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN up/normal): Forecast scorecard: direction hit 1/6, mostly missed up moves by calling flat, so leaning toward the recent drift this time
- **۱۰-۲۲ ۱۸:۱۹** (`pb-20261022-1819-ad0564`، devaluation_drift، پیش‌بینی: BTCTMN flat/normal, USDTTMN up/normal): Forecast scorecard: I under-called USDT 'up' twice today (direction hit 0.25), so I'm calling the persistent up-grind this time
- **۱۰-۲۲ ۲۰:۰۲** (`pb-20261022-2002-76393b`، devaluation_drift، پیش‌بینی: BTCTMN flat/normal, USDTTMN up/low): Scorecard: twice I called USDTTMN flat and it went up, while my vol calls ran 'normal' and came in 'low'. I'm adjusting to up/low
- **۱۰-۲۲ ۲۰:۴۳** (`pb-20261022-2043-58f537`، devaluation_drift، پیش‌بینی: BTCTMN down/normal, USDTTMN flat/low): Recent scorecard: I said flat and USDTTMN rose, so I may be underestimating the drift. Its realized vol has been low in every recent window
- **۱۰-۲۲ ۲۱:۲۵** (`pb-20261022-2125-9a5b41`، devaluation_drift، پیش‌بینی: BTCTMN flat/normal, USDTTMN up/low): Scorecard: direction hits 0.357 vs baseline 0.286; my recent 'flat' calls on USDTTMN missed while the price rose, so I am following the persistent trend
- **۱۰-۲۲ ۲۲:۰۷** (`pb-20261022-2207-c8461c`، devaluation_drift، پیش‌بینی: BTCTMN down/normal, USDTTMN flat/normal): Forecast scorecard: direction hit rate 0.357 vs baseline 0.286; the 'flat' calls have been reasonable
- **۱۰-۲۳ ۰۰:۰۲** (`pb-20261023-0002-51e4f7`، devaluation_drift، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/low): The forecast scorecard shows a 0.45 direction hit rate vs a 0.30 baseline. The last USDT 'up' call missed (actual flat), so this time the call is flat
- **۱۰-۲۳ ۰۱:۱۹** (`pb-20261023-0119-bd435e`، devaluation_drift، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): The last two USDTTMN forecasts said 'up' and both came out flat, so I am calling flat this time
- **۱۰-۲۳ ۰۲:۰۰** (`pb-20261023-0200-9311fb`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Scorecard: direction hit rate 41.7%. Recent misses came from calling USDTTMN up when it stayed flat, so this time USDTTMN is called flat
- **۱۰-۲۳ ۰۲:۴۱** (`pb-20261023-0241-e152d0`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/normal): Scorecard: past vol calls of 'low' on USDTTMN turned out 'normal', so I am now saying 'normal'
- **۱۰-۲۳ ۰۳:۲۲** (`pb-20261023-0322-f30a16`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 39% vs 25% baseline; recent 'down' calls on BTCTMN missed (actual flat), so forecast flat
- **۱۰-۲۳ ۰۴:۰۴** (`pb-20261023-0404-3aac28`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): The forecast scorecard shows direction hits of 39% against a 25% baseline, and recent actuals were mostly flat, so I lean toward calling flat
- **۱۰-۲۳ ۰۴:۴۶** (`pb-20261023-0446-b2614e`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/low): The scorecard shows past directional calls were often wrong (39%) and the actual result was mostly flat and normal vol, so this forecast stays conservative
- **۱۰-۲۳ ۰۵:۲۸** (`pb-20261023-0528-2a2e26`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: 43% direction hit rate vs 27% for the baseline. Recent flat calls were right and my low-vol calls were usually too cautious or right
- **۱۰-۲۳ ۰۶:۱۰** (`pb-20261023-0610-8f4727`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Scorecard: direction hit rate 0.433 vs baseline 0.267; vol hit rate 0.467; the last flat calls were right
- **۱۰-۲۳ ۰۶:۵۲** (`pb-20261023-0652-fc5062`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 0.438 vs baseline 0.281; recent quiet-hour calls of flat/low were mostly correct
- **۱۰-۲۳ ۰۷:۳۳** (`pb-20261023-0733-1c7748`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 0.47 vs 0.29 for the baseline, so flat/low calls have been working on quiet hours
- **۱۰-۲۳ ۰۸:۱۵** (`pb-20261023-0815-05a60f`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 47% against a 31% baseline. Our flat calls on BTCTMN missed two small up moves, but there is no strong signal to call a direction now
- **۱۰-۲۳ ۰۸:۵۶** (`pb-20261023-0856-ce77fe`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 0.474 vs 0.316 for the baseline; recent BTCTMN calls of 'flat' missed small up moves, but the volatility calls of 'low' were right
- **۱۰-۲۳ ۰۹:۳۷** (`pb-20261023-0937-f1942e`، range، پیش‌بینی: BTCTMN up/low, USDTTMN flat/normal): Scorecard: our last three 'flat' calls for BTCTMN were wrong and the actual move was 'up' each time. Overall direction hit rate is 0.475 vs a 0.325 baseline
- **۱۰-۲۳ ۱۰:۱۸** (`pb-20261023-1018-a70b7b`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/normal): Scorecard: my last three BTCTMN 'flat' calls came out 'up', but the last 4 h return is only +0.14%; my USDTTMN vol calls of 'low' came out 'normal', so I changed that call to normal
- **۱۰-۲۳ ۱۰:۵۹** (`pb-20261023-1059-05e60e`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction hit rate 0.477 vs baseline 0.386. My recent BTCTMN 'flat' calls missed small up moves, but the current 4h return is close to zero
- **۱۰-۲۳ ۱۱:۴۰** (`pb-20261023-1140-c544bd`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Scorecard: direction hit rate 0.478 vs a 0.391 baseline. My recent BTC 'flat' calls missed small up moves, so confidence in direction stays low
- **۱۰-۲۳ ۱۲:۲۲** (`pb-20261023-1222-857816`، range، پیش‌بینی: BTCTMN up/normal, USDTTMN up/normal): Scorecard: direction hit rate 0.50 vs baseline 0.396; recent BTCTMN 'flat' calls missed up moves
- **۱۰-۲۳ ۱۲:۲۳** (`pb-20261023-1223-4b1434`، range، پیش‌بینی: BTCTMN up/normal, USDTTMN up/normal): Scorecard: direction hit rate 0.50 vs baseline 0.396. Recent BTCTMN 'flat' calls missed upward moves, so this time I am calling up.
- **۱۰-۲۳ ۱۳:۰۴** (`pb-20261023-1304-745794`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Forecast scorecard: direction 50% vs 40% for the baseline. My recent flat/low calls on USDTTMN were right, so I keep them
- **۱۰-۲۳ ۱۳:۴۶** (`pb-20261023-1346-06dc7d`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN up/normal): scorecard: BTCTMN 'flat' calls missed twice when it drifted down, so I lean against adding
- **۱۰-۲۳ ۱۴:۲۷** (`pb-20261023-1427-f556ea`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN up/low): Scorecard: direction hit rate 48% vs 41% for the baseline. My recent BTCTMN 'flat' calls missed twice when it fell and once when it rose, so this BTC call is low-confidence
- **۱۰-۲۳ ۱۵:۰۹** (`pb-20261023-1509-e5776b`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Scorecard: direction hit 48% vs baseline 41%; my recent misses were USDTTMN rising when I said flat, so I'm only moderately confident in flat · vs twin: it calls devaluation_drift at 15% from the 24h USDTTMN return of +0.85%. I agree the toman is drifting, but with under an hour of buying left, heavy BTCTMN sell pressure and thin Friday trading, adding about 11% of equity now would mainly add fees and drawdown risk, so I stay at 4%
- **۱۰-۲۳ ۱۵:۵۱** (`pb-20261023-1551-4d8db9`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN up/low): Scorecard: in recent playbooks I called USDTTMN flat and it went up 3 times in a row (direction hit rate 0.466, baseline 0.414). I am correcting that bias and calling USDTTMN up this time.
- **۱۰-۲۳ ۱۶:۳۳** (`pb-20261023-1633-74d045`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/normal): Scorecard: I called vol 'low' several times and it came out 'normal', so this time I call normal. My recent 'flat' calls also missed on up moves, but USDTTMN has now turned down over the last 4 h (-0.22%).
- **۱۰-۲۳ ۱۷:۳۵** (`pb-20261023-1735-96e80c`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN flat/low): Scorecard: direction 47% vs baseline 36%, vol 58%; my 'flat/low' BTC calls were right on vol but missed small up moves
- **۱۰-۲۳ ۱۷:۵۱** (`pb-20261023-1751-1959d3`، event_shock، پیش‌بینی: BTCTMN down/normal, USDTTMN flat/normal): Scorecard: direction hit 47% vs baseline 36%; my vol calls of 'low' missed when actual vol was normal, so I say normal
- **۱۰-۲۳ ۱۸:۰۱** (`pb-20261023-1801-b27bde`، event_shock، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/low): Scorecard: direction hit rate 47% vs 36% for the baseline. Volume during the shock was far above normal, so I forecast normal vol for BTCTMN rather than low.
- **۱۰-۲۳ ۱۸:۱۲** (`pb-20261023-1812-4c5ee2`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/low): Scorecard: direction 45.6% vs baseline 35.3%, and my last flat calls missed small downs, so I keep conviction low · vs twin: it says range 5% with down/low forecasts. I agree on range. I keep the existing 6% instead of trimming 1%, because a change that small would be skipped and would cost fees. I call BTC flat rather than down because the price is recovering after the shock
- **۱۰-۲۳ ۱۸:۲۴** (`pb-20261023-1824-31e828`، range، پیش‌بینی: BTCTMN flat/low, USDTTMN down/low): Scorecard: direction hit 45.6% vs baseline 35.3%. My last 'flat' calls missed slow drifts down, so this time I call the USDTTMN slide 'down' instead of 'flat'.
- **۱۰-۲۳ ۱۸:۳۵** (`pb-20261023-1835-83dff4`، range، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Forecast scorecard: direction hit rate 0.456 vs baseline 0.353. My last flat call missed a down move, so this time I am calling the toman's strengthening as down.
- **۱۰-۲۳ ۱۸:۴۷** (`pb-20261023-1847-dfd591`، range، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Scorecard: my 13:04 call of 'flat' was wrong (the market went down). The toman's strength has persisted all afternoon, so this time I forecast down instead of flat · vs twin: it says range 5% with a down/low forecast, but it actually holds no BTC. I agree on the regime and on direction, and I differ on volatility: the last hour's move is well above the 30-day hourly norm. I keep our existing ~6% position instead of selling, because realising the loss now almost guarantees a negative finish, while the stop already limits how much more we can lose
- **۱۰-۲۳ ۱۸:۵۵** (`pb-20261023-1855-ffe675`، range، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Scorecard: direction hit rate 0.457 vs baseline 0.343. My recent USDTTMN 'up' calls were wrong, so I follow the tape and call it down.
- **۱۰-۲۳ ۱۹:۰۶** (`pb-20261023-1906-1839e7`، range، پیش‌بینی: BTCTMN down/low, USDTTMN down/normal): Forecast scorecard: direction hit rate 45.7%, above the baseline's 34.3%, but my recent USDTTMN 'up' calls missed. So I am more cautious this time
- **۱۰-۲۳ ۱۹:۱۹** (`pb-20261023-1919-d75fdb`، range، پیش‌بینی: BTCTMN flat/normal, USDTTMN flat/normal): Forecast scorecard: direction hit rate 45.7% vs baseline 34.3%, but my last USDTTMN 'up' calls missed, so I now call flat instead of up
- **۱۰-۲۳ ۱۹:۲۹** (`pb-20261023-1929-433e12`، risk_off، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Scorecard: my direction hit rate is 45.8%, against a 33.3% baseline. My recent USDTTMN 'up' calls missed while the toman kept strengthening, so I am not betting on a reversal now.
- **۱۰-۲۳ ۱۹:۴۱** (`pb-20261023-1941-07c02c`، risk_off، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Scorecard: my recent 'up/flat' USDTTMN calls missed while it kept falling (direction hit rate 0.458), so I should not call a reversal too early.
- **۱۰-۲۳ ۱۹:۵۱** (`pb-20261023-1951-878e5c`، risk_off، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): My USDTTMN 'up' forecasts this afternoon were wrong 3 times running (direction hit rate 0.458), so I won't call a rebound without evidence
- **۱۰-۲۳ ۲۰:۰۲** (`pb-20261023-2002-3d3307`، risk_off، پیش‌بینی: BTCTMN down/normal, USDTTMN down/high): Scorecard: my recent USDTTMN 'up' calls missed in a falling tape. Direction hit rate 0.458, against 0.333 for the baseline. I now follow the momentum instead of calling a bounce
- **۱۰-۲۳ ۲۰:۱۳** (`pb-20261023-2013-cfe6c2`، risk_off، پیش‌بینی: BTCTMN down/normal, USDTTMN down/normal): Scorecard: my last USDTTMN 'up' and 'flat' calls missed because the price fell. I'm now going with the clear downtrend instead of calling for a bounce.

## مقایسه با دوقلوی قاعده‌محور (به قلم خود Claude)

- **۱۰-۲۳ ۱۲:۲۳** (range): vs twin: it reads the 24 h returns and goes to devaluation_drift at 15%. I agree the toman is weakening, but this rise came in one hour on a Friday after flat, stale-looking prices. With 3.5 h left I keep the small 4% position instead of nearly quadrupling it.
- **۱۰-۲۳ ۱۳:۰۴** (range): vs twin: it reads devaluation_drift at 15% from the +0.60% 24h USDTTMN return. I stay at range 4% because the 5-minute bars show that move reversing within the last half hour, and the 15% position the twin holds is too much to start this close to the sell-off
- **۱۰-۲۳ ۱۳:۴۶** (range): vs twin: it says devaluation_drift at 15% from the 24h USDTTMN return of +0.84%; I agree the toman is weakening, but BTCTMN stayed flat while USDTTMN rose, meaning BTCUSDT is falling and cancelling the toman gain. With 2 h to the sell-off and a sell-heavy book, I keep range at 8%×0.5
- **۱۰-۲۳ ۱۴:۲۷** (range): vs twin: it calls devaluation_drift at 15% because USDTTMN is up 0.83% in 24 h. I agree the toman is drifting weaker, but I stay at range 8% × 0.5 because the sell-off starts in about 1.4 h, the BTCTMN order book is heavily one-sided toward sellers, and adding 11% now would mostly pay fees. The twin's realised P&L of −10k TMN shows what churn costs
- **۱۰-۲۳ ۱۵:۰۹** (range): vs twin: it calls devaluation_drift at 15% from the 24h USDTTMN return of +0.85%. I agree the toman is drifting, but with under an hour of buying left, heavy BTCTMN sell pressure and thin Friday trading, adding about 11% of equity now would mainly add fees and drawdown risk, so I stay at 4%
- **۱۰-۲۳ ۱۵:۵۱** (range): vs twin: it says devaluation_drift at 15% because USDTTMN is up 1.03% over 24 h. I agree the toman is drifting weaker, but the buying window is closed and the twin has lost 10,026 TMN already, so I stay at range with a small size and tactical off.
- **۱۰-۲۳ ۱۶:۳۳** (range): vs twin: it says devaluation_drift at 15% from the 24 h return of about +0.85%. I disagree because the sell-off has started, USDTTMN fell over the last 1 h and 4 h, and the book leans heavily to sellers. Its target cannot be bought in this phase anyway. I lock in the small gain at 0%.
- **۱۰-۲۳ ۱۷:۳۵** (range): vs twin: it says trend_up at 20% from the 24 h returns (USDT +0.34%, BTC +1.04%). I stay at range 8% x 0.75 = 6% because USDTTMN fell 0.39% over 4 h from 273.5k to 272.1k, BTCTMN has stayed in a 22.18-22.36B band for 12 h, and the BTC order book is heavily on the sell side
- **۱۰-۲۳ ۱۷:۵۱** (event_shock): vs twin: it stays in range at 5% x 1.0 and has no BTC position, so its view rests on a mild 24 h return. I differ because the sharp 10-minute break makes this a hold-only moment: no adding and no tactical trades until the tape settles
- **۱۰-۲۳ ۱۸:۰۱** (event_shock): vs twin: it says range at 5%, worked out from 24h returns, which matches our ~6% in size. It does not see the 0.6 BTC dump or the -0.99 book imbalance, so I keep the event_shock label and add nothing until the book stops leaning to sells.
- **۱۰-۲۳ ۱۸:۱۲** (range): vs twin: it says range 5% with down/low forecasts. I agree on range. I keep the existing 6% instead of trimming 1%, because a change that small would be skipped and would cost fees. I call BTC flat rather than down because the price is recovering after the shock
- **۱۰-۲۳ ۱۸:۲۴** (range): vs twin: it is also range, at 5% × 1.0, and its simulated BTC position is about zero. I agree on the regime. I keep our existing 6% instead of adding, because the USDTTMN drift down and the -0.94 ask-heavy book do not justify adding, while exiting would only lock in fees on a quiet evening.
- **۱۰-۲۳ ۱۸:۳۵** (range): vs twin: we agree on range. Its target is 5% but it holds almost no BTC; I keep the existing 6% because selling now locks in a loss plus fees. Its vol forecast is low; I say normal because USDTTMN has dropped sharply in the last 10 minutes.
- **۱۰-۲۳ ۱۸:۴۷** (range): vs twin: it says range 5% with a down/low forecast, but it actually holds no BTC. I agree on the regime and on direction, and I differ on volatility: the last hour's move is well above the 30-day hourly norm. I keep our existing ~6% position instead of selling, because realising the loss now almost guarantees a negative finish, while the stop already limits how much more we can lose
- **۱۰-۲۳ ۱۸:۵۵** (range): vs twin: we agree on range. The twin aims for 5% but actually holds almost no BTC. I keep the existing ~6% position instead of selling, because selling locks in a ~1.2% loss plus fees for less than a 3 h horizon, and the core stop caps the remaining risk at about 0.07% of equity. I am not adding because the toman is still strengthening.
- **۱۰-۲۳ ۱۹:۰۶** (range): vs twin: it also says range at 5% and forecasts down/low. I agree on range. I keep 6% because the gap is under 2% and changing it would only cost fees. Unlike the twin, I see the USDTTMN drop as a normal-volatility move, not a low-volatility one
- **۱۰-۲۳ ۱۹:۱۹** (range): vs twin: we agree on range. It targets 5% and calls both markets down/low vol from the 24h return; I keep the existing 5.93% (any change would be under the 2% minimum anyway) and call flat/normal, because the toman's sharp strengthening has stopped in the last half hour, not turned into a new leg down
- **۱۰-۲۳ ۱۹:۲۹** (risk_off): vs twin: it stays in range at 5% from the 24 h returns (USDTTMN −0.54%). I go risk_off at 0% because its 24 h figure misses the steady fall of the last 4 h (USDTTMN −1.39%, BTCTMN −1.22%). The fall is still running, with fresh lows in the last 5 minutes, and holding a loser into the flatten adds risk with no clear payoff.
- **۱۰-۲۳ ۱۹:۴۱** (risk_off): vs twin: it says range at 5% from the 24 h returns. I agree there is no uptrend but stay at 0% (risk_off) because the toman is still strengthening hour by hour and the order books lean heavily to sellers. A 5% position is also only just above the minimum order and would mostly pay fees.
- **۱۰-۲۳ ۱۹:۵۱** (risk_off): vs twin: it says range 5% from its rule but still forecasts both markets down. I stay risk_off at 0% because the bounce off 268.5k is fading again, order-book selling pressure is strong and little time is left, so a 5% buy would most likely just pay fees
- **۱۰-۲۳ ۲۰:۰۲** (risk_off): vs twin: it says range at 5% × 1.0 because its rule only reads the 24 h return. I stay risk_off at 0% because the 4 h and 1 h tape is still falling, the order book leans to sellers and little time is left. A 5% entry now would mostly pay fees into a downtrend
- **۱۰-۲۳ ۲۰:۱۳** (risk_off): vs twin: the rule says range at 5% because it looks only at the 24 h return. I stay risk_off at 0% because the 4 h trend, the heavy sell side in the order book and the short time left argue against buying. The twin has also lost 10.7k TMN realized so far.

## همه‌ی پیش‌بینی‌ها

| زمان | بازار | Claude گفت | واقعیت | حرکت ۴ ساعته | مبنا گفت | درست؟ |
|---|---|---|---|---|---|---|
| ۱۰-۲۲ ۱۲:۱۰ | BTCTMN | خنثی | بالا | ۰.۸۱٪ | پایین | ✗ |
| ۱۰-۲۲ ۱۲:۱۰ | USDTTMN | بالا | بالا | ۱.۲۳۹٪ | خنثی | ✓ |
| ۱۰-۲۲ ۱۲:۲۲ | BTCTMN | خنثی | بالا | ۰.۸۱٪ | پایین | ✗ |
| ۱۰-۲۲ ۱۲:۲۲ | USDTTMN | خنثی | بالا | ۱.۲۳۹٪ | خنثی | ✗ |
| ۱۰-۲۲ ۱۲:۴۹ | BTCTMN | خنثی | خنثی | -۰.۰۲۲٪ | بالا | ✓ |
| ۱۰-۲۲ ۱۲:۴۹ | USDTTMN | خنثی | بالا | ۰.۲۰۶٪ | بالا | ✗ |
| ۱۰-۲۲ ۱۴:۱۶ | BTCTMN | خنثی | خنثی | -۰.۱۹۲٪ | بالا | ✓ |
| ۱۰-۲۲ ۱۴:۱۶ | USDTTMN | خنثی | خنثی | ۰.۱۹٪ | بالا | ✓ |
| ۱۰-۲۲ ۱۴:۵۷ | BTCTMN | خنثی | خنثی | ۰.۰۹۶٪ | بالا | ✓ |
| ۱۰-۲۲ ۱۴:۵۷ | USDTTMN | خنثی | بالا | ۰.۴۷۱٪ | بالا | ✗ |
| ۱۰-۲۲ ۱۵:۳۸ | BTCTMN | خنثی | بالا | ۰.۲۸۵٪ | بالا | ✗ |
| ۱۰-۲۲ ۱۵:۳۸ | USDTTMN | خنثی | بالا | ۰.۶۳۷٪ | بالا | ✗ |
| ۱۰-۲۲ ۱۷:۳۸ | BTCTMN | خنثی | خنثی | ۰.۱۳۹٪ | خنثی | ✓ |
| ۱۰-۲۲ ۱۷:۳۸ | USDTTMN | بالا | بالا | ۰.۶۰۱٪ | خنثی | ✓ |
| ۱۰-۲۲ ۱۸:۱۹ | BTCTMN | خنثی | خنثی | ۰.۱۳۹٪ | خنثی | ✓ |
| ۱۰-۲۲ ۱۸:۱۹ | USDTTMN | بالا | بالا | ۰.۶۰۱٪ | خنثی | ✓ |
| ۱۰-۲۲ ۱۹:۰۰ | BTCTMN | خنثی | پایین | -۰.۷۴۱٪ | خنثی | ✗ |
| ۱۰-۲۲ ۱۹:۰۰ | USDTTMN | بالا | خنثی | -۰.۰۸۵٪ | بالا | ✗ |
| ۱۰-۲۲ ۲۰:۰۲ | BTCTMN | خنثی | پایین | -۰.۳۵۶٪ | بالا | ✗ |
| ۱۰-۲۲ ۲۰:۰۲ | USDTTMN | بالا | خنثی | -۰.۱۵۴٪ | بالا | ✗ |
| ۱۰-۲۲ ۲۰:۴۳ | BTCTMN | پایین | خنثی | -۰.۰۸۵٪ | پایین | ✗ |
| ۱۰-۲۲ ۲۰:۴۳ | USDTTMN | خنثی | خنثی | -۰.۱۷۳٪ | بالا | ✓ |
| ۱۰-۲۲ ۲۱:۲۵ | BTCTMN | خنثی | خنثی | -۰.۰۸۵٪ | پایین | ✓ |
| ۱۰-۲۲ ۲۱:۲۵ | USDTTMN | بالا | خنثی | -۰.۱۷۳٪ | بالا | ✗ |
| ۱۰-۲۲ ۲۲:۰۷ | BTCTMN | پایین | خنثی | -۰.۰۸۶٪ | خنثی | ✗ |
| ۱۰-۲۲ ۲۲:۰۷ | USDTTMN | خنثی | پایین | -۰.۴۹۵٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۰:۰۲ | BTCTMN | خنثی | خنثی | ۰.۰۷۶٪ | پایین | ✓ |
| ۱۰-۲۳ ۰۰:۰۲ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۱:۱۹ | BTCTMN | خنثی | بالا | ۰.۲۲۹٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۱:۱۹ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۲:۰۰ | BTCTMN | خنثی | خنثی | -۰.۱۵۱٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۲:۰۰ | USDTTMN | خنثی | خنثی | ۰.۰٪ | پایین | ✓ |
| ۱۰-۲۳ ۰۲:۴۱ | BTCTMN | خنثی | بالا | ۰.۳۵۶٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۲:۴۱ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۳:۲۲ | BTCTMN | خنثی | بالا | ۰.۳۵۶٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۳:۲۲ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۴:۰۴ | BTCTMN | خنثی | بالا | ۰.۶۵۹٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۴:۰۴ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۴:۴۶ | BTCTMN | خنثی | بالا | ۰.۳۳۶٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۴:۴۶ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۵:۲۸ | BTCTMN | خنثی | بالا | ۰.۳۳۶٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۵:۲۸ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۶:۱۰ | BTCTMN | خنثی | بالا | ۰.۴۲۹٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۶:۱۰ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۶:۵۲ | BTCTMN | خنثی | خنثی | ۰.۱۹۴٪ | بالا | ✓ |
| ۱۰-۲۳ ۰۶:۵۲ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۷:۳۳ | BTCTMN | خنثی | پایین | -۰.۳۶۹٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۷:۳۳ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۸:۱۵ | BTCTMN | خنثی | پایین | -۰.۳۶۹٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۸:۱۵ | USDTTMN | خنثی | خنثی | ۰.۰٪ | خنثی | ✓ |
| ۱۰-۲۳ ۰۸:۵۶ | BTCTMN | خنثی | بالا | ۰.۵۲۴٪ | بالا | ✗ |
| ۱۰-۲۳ ۰۸:۵۶ | USDTTMN | خنثی | بالا | ۰.۵۸۵٪ | خنثی | ✗ |
| ۱۰-۲۳ ۰۹:۳۷ | BTCTMN | بالا | بالا | ۰.۳۱۸٪ | بالا | ✓ |
| ۱۰-۲۳ ۰۹:۳۷ | USDTTMN | خنثی | بالا | ۰.۵۸۶٪ | خنثی | ✗ |
| ۱۰-۲۳ ۱۰:۱۸ | BTCTMN | خنثی | بالا | ۰.۳۱۸٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۰:۱۸ | USDTTMN | خنثی | بالا | ۰.۵۸۶٪ | خنثی | ✗ |
| ۱۰-۲۳ ۱۰:۵۹ | BTCTMN | خنثی | بالا | ۰.۲۵۵٪ | خنثی | ✗ |
| ۱۰-۲۳ ۱۰:۵۹ | USDTTMN | خنثی | بالا | ۰.۶۷۱٪ | خنثی | ✗ |
| ۱۰-۲۳ ۱۱:۴۰ | BTCTMN | خنثی | بالا | ۰.۴۴۲٪ | پایین | ✗ |
| ۱۰-۲۳ ۱۱:۴۰ | USDTTMN | خنثی | بالا | ۰.۵۳۹٪ | خنثی | ✗ |
| ۱۰-۲۳ ۱۲:۲۲ | BTCTMN | بالا | بالا | ۰.۴۴۲٪ | پایین | ✓ |
| ۱۰-۲۳ ۱۲:۲۲ | USDTTMN | بالا | بالا | ۰.۵۳۹٪ | خنثی | ✓ |
| ۱۰-۲۳ ۱۲:۲۳ | BTCTMN | بالا | بالا | ۰.۴۴۲٪ | پایین | ✓ |
| ۱۰-۲۳ ۱۲:۲۳ | USDTTMN | بالا | بالا | ۰.۵۳۹٪ | خنثی | ✓ |
| ۱۰-۲۳ ۱۳:۰۴ | BTCTMN | خنثی | پایین | -۰.۲۴۴٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۳:۰۴ | USDTTMN | خنثی | پایین | -۰.۲۲۵٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۳:۴۶ | BTCTMN | خنثی | خنثی | -۰.۰۸۷٪ | بالا | ✓ |
| ۱۰-۲۳ ۱۳:۴۶ | USDTTMN | بالا | پایین | -۰.۴۲۴٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۴:۲۷ | BTCTMN | خنثی | خنثی | -۰.۰۸۷٪ | بالا | ✓ |
| ۱۰-۲۳ ۱۴:۲۷ | USDTTMN | بالا | پایین | -۰.۴۲۴٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۵:۰۹ | BTCTMN | خنثی | پایین | -۰.۴۲۲٪ | بالا | ✗ |
| ۱۰-۲۳ ۱۵:۰۹ | USDTTMN | خنثی | پایین | -۰.۷۱٪ | بالا | ✗ |
