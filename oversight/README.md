# نظارت انسانی (Human oversight)

> **وضعیت:** ساخته و آزموده شده (`tests/test_oversight.py`)، اما **روی سرور مسابقه نصب نشده است** و هیچ معامله‌ی مسابقه با آن انجام نشد. کدی که معامله کرد با تگ‌های `live-*` مشخص است؛ موتور این پوشه را import یا اجرا نمی‌کند (از `live-v4` به بعد فایل‌هایش روی سرور هستند، اما خاموش).
>
> **Status:** built and tested, **not deployed**; no contest trade ran with it. The code that traded is the `live-*` tags; the engine never imports or starts this folder (from `live-v4` its files are on the server, unused).

## چرا فقط «ترمز» و نه «فرمان»؟
قوانین چالش می‌گویند هر معامله‌ی ارزیابی‌شده باید الگوریتمی باشد و معامله‌ی دستی می‌تواند به حذف تیم برسد. پس انسان اینجا **فقط می‌تواند موتور را محتاط‌تر کند**: هیچ سفارشی ثبت، تأیید یا اندازه‌گذاری نمی‌کند. هر فروش همچنان از کد فروش پایانی (`engine/flatten.py`) یا هسته (`engine/core.py`) می‌آید و از همان دروازه‌ی ریسک رد می‌شود. یک تست (`test_oversight_code_never_trades`) چک می‌کند که این پوشه هیچ کلاینت معامله یا ساخت سفارشی ندارد.

| دستور (در بله) | اثر | تأیید دوم؟ |
|---|---|---|
| `/status` | وضعیت موتور، برنامه‌ی Claude و محدودیت‌های انسانی | — |
| `/pause دلیل` | خرید جدید متوقف؛ سفارش‌های خرید باز در هر تیک لغو می‌شوند (اگر یک لغو خطا بدهد، تیک بعد دوباره)؛ فروش، حد ضرر و فروش پایانی کار می‌کنند | خیر |
| `/risk 0.5 دلیل` | سقف ضریب ریسک Claude (بین ۰ و ۱): `min(Claude, سقف)` — هدف‌ها فقط کوچک می‌شوند | خیر |
| `/flatten دلیل` | فروش پایانی عادی همین حالا شروع می‌شود (مرحله‌ی «market»: لیمیت محافظت‌شده تا ۲٪)؛ تا وقتی دارایی قابل فروش مانده (حتی بعد از ری‌استارت) ادامه می‌دهد، بعد خرید بسته می‌ماند تا `/resume`؛ بررسی پایانی واقعی مسابقه هم سر جایش اجرا می‌شود | بله |
| `/resume دلیل` | برداشتن همه‌ی محدودیت‌های انسانی؛ کنترل به Claude و دروازه‌ی ریسک برمی‌گردد (هرگز بیشتر از آن) | بله |
| `/review یادداشت` | یادداشت بازبینی روزانه (هر شب ساعت ۲۱ تهران ربات خلاصه‌ی روز را می‌فرستد و یادداشت می‌خواهد) | — |

**ایمنی فایل وضعیت:** نوشتن اتمی با fsync و قفل؛ نسخه‌ی قبلی به‌عنوان `.bak` نگه داشته می‌شود؛ اگر فایل خراب باشد، ترمزها از حالت «خرید متوقف» شروع می‌کنند و `/resume` رد می‌شود؛ موتور مقدار نامعتبر (NaN، زمان نامعقول) را رد می‌کند و آخرین وضعیت سالم را نگه می‌دارد.

**ایمنی کانال:** فقط چت خصوصی `BALE_CHAT_ID` (در گروه حتماً `BALE_ADMIN_IDS` را تنظیم کنید) (و اگر `BALE_ADMIN_IDS` تنظیم شود فقط همان کاربرها)؛ پیام‌های دیگر نادیده و بدون متن ثبت می‌شوند. پیام قدیمی‌تر از ۱۰ دقیقه اجرا نمی‌شود؛ در اولین اجرا صف قبلی دور ریخته می‌شود؛ `/flatten` و `/resume` با `/confirm <کد>` از همان شخص (۲ دقیقه) تأیید می‌شوند. توکن فقط در آدرس درخواست است و لاگ نمی‌شود.

**ثبت:** هر دستور با «چه کسی، کی، چرا» در `run/<engine>/logs/oversight.jsonl` ثبت می‌شود؛ موتور هم هنگام اعمال، رویداد `oversight_applied` را در `events.jsonl` می‌نویسد؛ فروش‌های ناشی از `/flatten` با `decided_by = "flatten (human #n)"` و کاهش‌های ناشی از `/risk` با «risk capped at x by human #n» ثبت می‌شوند و `reports/shadow_report.py` بخش «نظارت انسانی» را به `DECISIONS-*.md` اضافه می‌کند.

## Files
| file | what it does |
|---|---|
| `state.py` | the commands, the pure state machine (`parse`, `apply`), the state file and the log |
| `bot.py` | `Controller` (one message → state change + log + reply) and the Bale long-polling `Bot`; `--local` for a terminal |
| `engine.py` | `OverseenEngine(Engine)`: reads the state file each tick; caps the risk multiplier, blocks adds, moves `end_at` for a flatten |
| `run.py` | `python -m oversight.run …` = `engine.run` with `OverseenEngine` (same flags, same safety checks) |
| `wallex-oversight.service` | systemd unit for the bot (deliberately outside `deploy/`, so `deploy/setup_server.sh` never installs it) |

## Try it without Bale (shadow mode, no money, no key)
```bash
uv run python -m oversight.run --mode shadow --manager rules --run-dir run/try --ticks 30   # needs network (the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ public data)
uv run python -m oversight.bot --run-dir run/try --local "/pause testing the brake"
uv run python -m oversight.bot --run-dir run/try --local "/status"
uv run python -m oversight.bot --run-dir run/try --local "/resume done" --yes
```

## If the user decides to deploy it (not done)
1. copy `oversight/wallex-oversight.service` to `/etc/systemd/system/` and enable it;
2. in `deploy/kestrel⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣.service` (installed as `/etc/systemd/system/kestrel⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣.service`) change `-m engine.run` to `-m oversight.run` (same flags);
3. optionally set `BALE_ADMIN_IDS` in `/etc/kestrel⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣/env`.

## Privacy
`oversight.jsonl` stores the Bale id, name and username of whoever sent a command, plus their notes. It stays on the server (`run/`, git-ignored); don't copy it into `delivery/` without the people's consent.
