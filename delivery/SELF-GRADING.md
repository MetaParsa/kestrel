# هوش مصنوعی‌ای که به خودش نمره می‌دهد

Claude در هر برنامه، جهت و نوسان ۴ ساعت بعدِ هر بازار را پیش‌بینی می‌کند. چهار ساعت بعد، کد پیش‌بینی را با بازار واقعی مقایسه می‌کند (جهت با باند خنثیِ ±۰٫۲٪؛ نوسان در برابر نوسان ۳۰ روزه)، کنارش دو مبنای ساده را هم نمره می‌دهد، و کارنامه را در چرخه‌ی بعد به خود Claude برمی‌گرداند. همه‌چیز بدون نگاه به آینده: فقط کندل‌هایی که بسته شده بودند.

**۳۲ برنامه، ۴۶ پیش‌بینی ارزیابی‌شده.**

| | Claude | مبنای ساده («مثل ۴ ساعت قبل») | «همیشه خنثی» |
|---|---|---|---|
| همه | ۵۲٪ | ۴۱٪ | ۵۶٪ |
| USDTTMN (۲۳) | ۶۵٪ | ۵۲٪ | ۶۵٪ |
| BTCTMN (۲۳) | ۳۹٪ | ۳۰٪ | ۴۸٪ |

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

## مقایسه با دوقلوی قاعده‌محور (به قلم خود Claude)

- **۱۰-۲۳ ۱۲:۲۳** (range): vs twin: it reads the 24 h returns and goes to devaluation_drift at 15%. I agree the toman is weakening, but this rise came in one hour on a Friday after flat, stale-looking prices. With 3.5 h left I keep the small 4% position instead of nearly quadrupling it.

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
