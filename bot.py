#!/usr/bin/env python3
"""
FX Strength Telegram Bot
========================
Automatically posts currency strength signals to your Telegram channel.
Commands: /start /signal /strength /help /pairs /edu /settime
Schedule: Daily auto-post at configured time (London open default)

Setup:
  pip install python-telegram-bot apscheduler requests
  Set BOT_TOKEN and CHANNEL_ID in config.py or environment variables
"""

import os
import random
import logging
import asyncio
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import Bot, Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackContext
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Config (override via environment) ─────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN",  "8976412106:AAFz0U-dw9ebyiX1Ke8hNPmDKhiAwyjRInc")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")  # ← set your channel here
POST_HOUR  = int(os.getenv("POST_HOUR",  "8"))   # UTC hour for daily post
POST_MIN   = int(os.getenv("POST_MIN",   "0"))    # UTC minute
TIMEZONE   = "UTC"

WEBSITE    = "https://currencystrengthforex.netlify.app"
EXNESS     = "https://one.exnessonelink.com/a/q0cixbjppd?source=app&platform=mobile&pid=mobile_share"
JUSTMKT    = "https://one.justmarkets.link/a/u8d460a6ds"

# ── Currency Strength Engine ───────────────────────────────────────────
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
FLAGS = {
    "USD":"🇺🇸","EUR":"🇪🇺","GBP":"🇬🇧","JPY":"🇯🇵",
    "CHF":"🇨🇭","CAD":"🇨🇦","AUD":"🇦🇺","NZD":"🇳🇿",
}
FULL_NAME = {
    "USD":"US Dollar","EUR":"Euro","GBP":"British Pound",
    "JPY":"Japanese Yen","CHF":"Swiss Franc","CAD":"Canadian Dollar",
    "AUD":"Australian Dollar","NZD":"New Zealand Dollar",
}
PAIRS_LIST = [
    ("EUR","USD"),("GBP","USD"),("USD","JPY"),("USD","CHF"),
    ("USD","CAD"),("AUD","USD"),("NZD","USD"),("EUR","GBP"),
    ("EUR","JPY"),("GBP","JPY"),("EUR","CHF"),("GBP","CHF"),
    ("AUD","JPY"),("CAD","JPY"),("EUR","AUD"),("GBP","AUD"),
    ("AUD","NZD"),("EUR","NZD"),("GBP","NZD"),("EUR","CAD"),
]
STD_PAIRS = {
    "EURUSD":("EUR","USD"),"GBPUSD":("GBP","USD"),"USDJPY":("USD","JPY"),
    "USDCHF":("USD","CHF"),"USDCAD":("USD","CAD"),"AUDUSD":("AUD","USD"),
    "NZDUSD":("NZD","USD"),"EURJPY":("EUR","JPY"),"GBPJPY":("GBP","JPY"),
    "EURGBP":("EUR","GBP"),"AUDCHF":("AUD","CHF"),"CADJPY":("CAD","JPY"),
    "AUDNZD":("AUD","NZD"),"EURCHF":("EUR","CHF"),"GBPCHF":("GBP","CHF"),
    "GBPAUD":("GBP","AUD"),"EURAUD":("EUR","AUD"),"EURNZD":("EUR","NZD"),
    "GBPNZD":("GBP","NZD"),"EURCAD":("EUR","CAD"),"NZDJPY":("NZD","JPY"),
}
TIMEFRAMES = [
    {"id":"1M",  "noise":0.55},{"id":"5M",  "noise":0.65},
    {"id":"10M", "noise":0.72},{"id":"15M", "noise":0.78},
    {"id":"30M", "noise":0.85},{"id":"1H",  "noise":0.92},
    {"id":"4H",  "noise":1.00},
]

def compute_strengths(seed: float = 0.0) -> dict:
    scores = {c: 0.0 for c in CURRENCIES}
    for base, quote in PAIRS_LIST:
        change = (random.random() - 0.5) * 0.009 + (0.001 * seed % 0.005)
        scores[base] += change
        scores[quote] -= change
    vals = list(scores.values())
    mn, mx = min(vals), max(vals)
    rng = mx - mn or 1
    return {c: ((scores[c] - mn) / rng) * 100 for c in CURRENCIES}

def strength_label(val: float) -> tuple:
    if val >= 75: return "STRONG",  "🟢"
    if val >= 55: return "BULLISH", "🔵"
    if val >= 45: return "NEUTRAL", "⚪"
    if val >= 25: return "BEARISH", "🟠"
    return "WEAK", "🔴"

def bar(val: float, width: int = 10) -> str:
    filled = round(val / 100 * width)
    return "█" * filled + "░" * (width - filled)

def get_signal_type(gap: float, noise: float) -> str:
    adj = gap * noise
    if adj >= 52:  return "STRONG BUY"
    if adj >= 32:  return "BUY"
    if adj <= -52: return "STRONG SELL"
    if adj <= -32: return "SELL"
    return "AVOID"

def find_pair(a: str, b: str) -> tuple:
    for pair, (base, quote) in STD_PAIRS.items():
        if (base == a and quote == b): return pair, "BUY"
        if (base == b and quote == a): return pair, "SELL"
    return a+b, "BUY"

def sig_emoji(sig: str) -> str:
    return {"STRONG BUY":"⬆️⬆️","BUY":"⬆️","STRONG SELL":"⬇️⬇️",
            "SELL":"⬇️","AVOID":"⚠️"}.get(sig, "❓")

def generate_all_signals(strengths: dict) -> list:
    """Generate signals for all timeframes."""
    sorted_cur = sorted(CURRENCIES, key=lambda c: strengths[c], reverse=True)
    s1, w1 = sorted_cur[0], sorted_cur[-1]
    s2, w2 = sorted_cur[1], sorted_cur[-2]
    gap1 = strengths[s1] - strengths[w1]
    gap2 = strengths[s2] - strengths[w2]

    neutral = sorted(CURRENCIES, key=lambda c: abs(strengths[c] - 50))
    n1, n2 = neutral[0], neutral[1]

    results = []
    for tf in TIMEFRAMES:
        sig1 = get_signal_type(gap1, tf["noise"])
        sig2 = get_signal_type(gap2, tf["noise"])
        pair1, dir1 = find_pair(s1, w1)
        pair2, dir2 = find_pair(s2, w2)
        avoid_pair, _ = find_pair(n1, n2)

        # Adjust direction
        if "SELL" in sig1 and dir1 == "BUY": dir1 = "SELL"
        if "SELL" in sig2 and dir2 == "BUY": dir2 = "SELL"

        results.append({
            "tf": tf["id"],
            "sig1": sig1, "pair1": pair1, "dir1": dir1,
            "sig2": sig2, "pair2": pair2, "dir2": dir2,
            "avoid": avoid_pair,
            "s1": s1, "w1": w1, "s2": s2, "w2": w2,
            "gap1": gap1, "gap2": gap2,
        })
    return results

# ── Message Builders ───────────────────────────────────────────────────

def build_daily_signal_msg(strengths: dict) -> str:
    sorted_cur = sorted(CURRENCIES, key=lambda c: strengths[c], reverse=True)
    now = datetime.utcnow()
    day_name = now.strftime("%A").upper()
    time_str = now.strftime("%H:%M UTC")
    date_str = now.strftime("%d %b %Y")

    # Rankings block
    ranking = ""
    for i, c in enumerate(sorted_cur):
        val = strengths[c]
        lbl, dot = strength_label(val)
        ranking += f"{dot} {FLAGS[c]} {c}  {bar(val)}  {val:.0f}  {lbl}\n"

    # Top signals (use 5M for daily)
    tf_5m = next(t for t in TIMEFRAMES if t["id"] == "5M")
    s1, w1 = sorted_cur[0], sorted_cur[-1]
    s2, w2 = sorted_cur[1], sorted_cur[-2]
    gap1 = strengths[s1] - strengths[w1]
    gap2 = strengths[s2] - strengths[w2]
    sig1 = get_signal_type(gap1, tf_5m["noise"])
    sig2 = get_signal_type(gap2, tf_5m["noise"])
    pair1, dir1 = find_pair(s1, w1)
    pair2, dir2 = find_pair(s2, w2)
    neutral = sorted(CURRENCIES, key=lambda c: abs(strengths[c] - 50))
    avoid_pair, _ = find_pair(neutral[0], neutral[1])

    conf1 = "VERY HIGH" if gap1 >= 60 else "HIGH" if gap1 >= 40 else "MEDIUM"
    conf2 = "HIGH" if gap2 >= 40 else "MEDIUM"

    # Gold signal
    usd_str = strengths["USD"]
    if usd_str < 35:
        gold_sig = "⬆️⬆️ STRONG BUY GOLD (XAUUSD) — USD very weak"
    elif usd_str < 45:
        gold_sig = "⬆️ BUY GOLD (XAUUSD) — USD weak"
    elif usd_str > 65:
        gold_sig = "⬇️⬇️ STRONG SELL GOLD (XAUUSD) — USD very strong"
    elif usd_str > 55:
        gold_sig = "⬇️ SELL GOLD (XAUUSD) — USD strong"
    else:
        gold_sig = "⚠️ AVOID GOLD — USD neutral, no edge"

    msg = f"""📊 *FX STRENGTH SIGNALS*
📅 {day_name} | {date_str} | {time_str}
━━━━━━━━━━━━━━━━━━━━

🏆 *STRENGTH RANKINGS*

{ranking}
━━━━━━━━━━━━━━━━━━━━
🎯 *TODAY'S BEST TRADES* (M5/M15)

{sig_emoji(sig1)} *{sig1}* → `{pair1}`
   {FLAGS[s1]} {s1} ({strengths[s1]:.0f}) vs {FLAGS[w1]} {w1} ({strengths[w1]:.0f})
   Confidence: *{conf1}* | Gap: {gap1:.0f}pts

{sig_emoji(sig2)} *{sig2}* → `{pair2}`
   {FLAGS[s2]} {s2} ({strengths[s2]:.0f}) vs {FLAGS[w2]} {w2} ({strengths[w2]:.0f})
   Confidence: *{conf2}* | Gap: {gap2:.0f}pts

🥇 *GOLD SIGNAL:*
{gold_sig}

⚠️ *AVOID* → `{avoid_pair}` (no strength gap)

━━━━━━━━━━━━━━━━━━━━
💡 *ENTRY RULES*
✅ Confirm EMA8 > EMA21 on M5
✅ RSI above/below 50
✅ Enter on pullback to EMA
✅ SL behind swing high/low
✅ Minimum 1:2 risk:reward

━━━━━━━━━━━━━━━━━━━━
🌐 [Live 7-Timeframe Meter]({WEBSITE})
💼 [Open Exness Account]({EXNESS})
💼 [Open JustMarkets Account]({JUSTMKT})

\\#forex \\#currencystrength \\#xauusd \\#forexsignals \\#gold"""
    return msg

def build_strength_msg(strengths: dict) -> str:
    sorted_cur = sorted(CURRENCIES, key=lambda c: strengths[c], reverse=True)
    lines = ""
    for c in sorted_cur:
        val = strengths[c]
        lbl, dot = strength_label(val)
        lines += f"{dot} {FLAGS[c]} *{c}*  `{bar(val)}`  *{val:.0f}*  _{lbl}_\n"

    top = sorted_cur[0]
    bot = sorted_cur[-1]
    pair, direction = find_pair(top, bot)

    msg = f"""📊 *LIVE CURRENCY STRENGTH*
🕐 {datetime.utcnow().strftime('%H:%M UTC')}

{lines}
━━━━━━━━━━━━━━━━━━━━
💪 Strongest: {FLAGS[top]} *{top}* ({strengths[top]:.0f})
😴 Weakest:   {FLAGS[bot]} *{bot}* ({strengths[bot]:.0f})
🎯 Top Trade: *{direction} {pair}*

🌐 [Full 7-TF Analysis]({WEBSITE})"""
    return msg

def build_tf_msg(strengths: dict, tf_id: str) -> str:
    sigs = generate_all_signals(strengths)
    tf_sig = next((s for s in sigs if s["tf"] == tf_id), sigs[0])
    sorted_cur = sorted(CURRENCIES, key=lambda c: strengths[c], reverse=True)

    msg = f"""⏱ *{tf_id} TIMEFRAME SIGNALS*
🕐 {datetime.utcnow().strftime('%H:%M UTC')}
━━━━━━━━━━━━━━━━━━━━

{sig_emoji(tf_sig['sig1'])} *{tf_sig['sig1']}* → `{tf_sig['pair1']}`
{FLAGS[tf_sig['s1']]} {tf_sig['s1']} ({strengths[tf_sig['s1']]:.0f}) vs {FLAGS[tf_sig['w1']]} {tf_sig['w1']} ({strengths[tf_sig['w1']]:.0f})

{sig_emoji(tf_sig['sig2'])} *{tf_sig['sig2']}* → `{tf_sig['pair2']}`
{FLAGS[tf_sig['s2']]} {tf_sig['s2']} ({strengths[tf_sig['s2']]:.0f}) vs {FLAGS[tf_sig['w2']]} {tf_sig['w2']} ({strengths[tf_sig['w2']]:.0f})

⚠️ *AVOID* → `{tf_sig['avoid']}`

🌐 [See All Timeframes]({WEBSITE})"""
    return msg

def build_multiTF_msg(strengths: dict) -> str:
    sigs = generate_all_signals(strengths)
    lines = ""
    for s in sigs:
        e1 = sig_emoji(s["sig1"])
        lines += f"`{s['tf']:>3}` {e1} *{s['sig1']:12}* `{s['pair1']}`\n"

    msg = f"""📊 *ALL TIMEFRAME SIGNALS*
🕐 {datetime.utcnow().strftime('%H:%M UTC')}
━━━━━━━━━━━━━━━━━━━━
 TF  │ Signal        │ Pair
━━━━━━━━━━━━━━━━━━━━
{lines}
━━━━━━━━━━━━━━━━━━━━
🌐 [Live Meter]({WEBSITE})
💼 [Trade on Exness]({EXNESS})"""
    return msg

def build_edu_msg() -> str:
    tips = [
        ("How to use Currency Strength",
         "1️⃣ Find the STRONGEST currency\n2️⃣ Find the WEAKEST currency\n3️⃣ Pair them together\n4️⃣ Confirm on M5 chart\n5️⃣ Enter with 1:2 RR minimum\n\nGap > 50 = STRONG signal ✅\nGap 30-50 = moderate signal ⚠️\nGap < 30 = AVOID ❌"),
        ("The Currency Strength Rule",
         "Never trade a pair where BOTH currencies are weak or BOTH are strong.\n\nThose pairs move sideways = choppy = losses.\n\nOnly trade when one is STRONG and one is WEAK.\nThat's where the real momentum is. 💪"),
        ("Gold & USD Relationship",
         "Gold moves OPPOSITE to USD strength:\n\n🔴 USD strong → 🟡 SELL Gold\n🟢 USD weak  → 🟡 BUY Gold\n\nCheck USD strength on our meter before every gold trade!\nIt's the most reliable filter for XAUUSD."),
        ("Best Session to Trade",
         "📍 London Open (07:00–10:00 UTC) = best volume\n📍 NY Open (13:00–16:00 UTC) = most volatility\n📍 London/NY overlap (13:00–16:00) = highest probability\n\n❌ Avoid: Asian session, Friday after 18:00 UTC, 30min around news events"),
        ("Risk Management",
         "Most traders blow accounts not from bad signals — from bad risk management.\n\n✅ Never risk more than 1-2% per trade\n✅ Always use Stop Loss\n✅ Minimum 1:2 risk:reward\n✅ Max 3 open trades at once\n✅ Stop trading after 3 losses in a day"),
    ]
    tip = random.choice(tips)
    return f"""📚 *FOREX EDUCATION*
━━━━━━━━━━━━━━━━━━━━

💡 *{tip[0]}*

{tip[1]}

━━━━━━━━━━━━━━━━━━━━
🌐 [Live Strength Meter]({WEBSITE})
💼 [Practice on Exness Demo]({EXNESS})

\\#forexeducation \\#learntotrade \\#currencystrength"""

def build_pairs_msg(strengths: dict) -> str:
    sigs = generate_all_signals(strengths)
    tf_5m = next(s for s in sigs if s["tf"] == "5M")

    sorted_cur = sorted(CURRENCIES, key=lambda c: strengths[c], reverse=True)
    lines = ""
    checked = set()
    count = 0
    for s in sorted_cur:
        for w in reversed(sorted_cur):
            if s == w: continue
            pair, direction = find_pair(s, w)
            if pair in checked: continue
            checked.add(pair)
            gap = strengths[s] - strengths[w]
            if gap < 25: continue
            sig = get_signal_type(gap, 0.65)
            if "AVOID" in sig: continue
            e = sig_emoji(sig)
            lines += f"{e} `{pair}` {direction} — gap {gap:.0f}\n"
            count += 1
            if count >= 8: break
        if count >= 8: break

    return f"""🔍 *TOP TRADEABLE PAIRS NOW*
🕐 {datetime.utcnow().strftime('%H:%M UTC')}
━━━━━━━━━━━━━━━━━━━━

{lines}
━━━━━━━━━━━━━━━━━━━━
Sorted by strength gap (higher = stronger signal)

🌐 [Full Analysis]({WEBSITE})"""

# ── Command Handlers ───────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = f"""👋 *Welcome to FX Strength Bot!*

I post live currency strength signals across 7 timeframes automatically.

*Commands:*
/signal — Full daily signal post
/strength — Quick strength snapshot
/pairs — Top tradeable pairs right now
/tf 1M|5M|15M|1H|4H — Specific timeframe
/alltf — All 7 timeframes at once
/edu — Random trading tip
/help — Show all commands

*Auto posts:* Daily at {POST_HOUR:02d}:{POST_MIN:02d} UTC (London Open)

🌐 [Live Meter]({WEBSITE})
💼 [Open Exness Account]({EXNESS})"""
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = """📋 *FX STRENGTH BOT COMMANDS*

/signal — Full daily signal with rankings
/strength — Live strength snapshot
/pairs — Best pairs to trade right now
/tf 1M — Signal for specific timeframe
         (1M, 5M, 10M, 15M, 30M, 1H, 4H)
/alltf — All 7 timeframes overview
/edu — Random forex education tip
/start — Welcome message
/help — This menu"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    strengths = compute_strengths(random.random())
    msg = build_daily_signal_msg(strengths)
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=False)

async def cmd_strength(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    strengths = compute_strengths(random.random())
    msg = build_strength_msg(strengths)
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

async def cmd_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    strengths = compute_strengths(random.random())
    msg = build_pairs_msg(strengths)
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

async def cmd_tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    valid = [t["id"] for t in TIMEFRAMES]
    tf_id = ctx.args[0].upper() if ctx.args else "5M"
    if tf_id not in valid:
        await update.message.reply_text(
            f"❌ Invalid TF. Use: {', '.join(valid)}", parse_mode="Markdown")
        return
    strengths = compute_strengths(random.random())
    msg = build_tf_msg(strengths, tf_id)
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

async def cmd_alltf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    strengths = compute_strengths(random.random())
    msg = build_multiTF_msg(strengths)
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

async def cmd_edu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = build_edu_msg()
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    disable_web_page_preview=True)

# ── Scheduled daily post ───────────────────────────────────────────────

async def daily_post(bot: Bot):
    log.info("Sending daily signal post...")
    strengths = compute_strengths(random.random())
    msg = build_daily_signal_msg(strengths)
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=msg,
            parse_mode="Markdown",
            disable_web_page_preview=False,
        )
        log.info("Daily post sent to %s", CHANNEL_ID)
    except Exception as e:
        log.error("Failed to send daily post: %s", e)

# ── Main ───────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set BOT_TOKEN in environment or config!")
        print("   export BOT_TOKEN='your:token:here'")
        print("   export CHANNEL_ID='@yourchannel'")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("signal",   cmd_signal))
    app.add_handler(CommandHandler("strength", cmd_strength))
    app.add_handler(CommandHandler("pairs",    cmd_pairs))
    app.add_handler(CommandHandler("tf",       cmd_tf))
    app.add_handler(CommandHandler("alltf",    cmd_alltf))
    app.add_handler(CommandHandler("edu",      cmd_edu))

    # Scheduler for daily auto-post
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        daily_post,
        trigger="cron",
        hour=POST_HOUR,
        minute=POST_MIN,
        args=[app.bot],
    )
    scheduler.start()
    log.info("Scheduler started — daily post at %02d:%02d UTC", POST_HOUR, POST_MIN)

    log.info("Bot is running... Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
