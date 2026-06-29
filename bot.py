import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from analyzer import ScalpingAnalyzer

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

analyzer = ScalpingAnalyzer()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔍 Сканировать рынок", callback_data="scan")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton("📊 Топ монеты", callback_data="top_coins")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 *Scalping Signal Bot*\n\n"
        "Анализирую топ ликвидные крипто-пары и даю сигналы для скальпинга.\n\n"
        "📌 *Что умею:*\n"
        "• Нахожу лучшие монеты для входа прямо сейчас\n"
        "• Указываю точный таймфрейм (5м/15м)\n"
        "• Даю направление: LONG 🟢 или SHORT 🔴\n"
        "• Указываю вход, стоп-лосс и тейк-профит\n"
        "• Держать сделку: 5-10 минут\n\n"
        "Используй /scan для немедленного сканирования",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Сканирую рынок... Подожди 10-15 секунд")
    signals = await analyzer.get_signals()
    await msg.delete()
    
    if not signals:
        await update.message.reply_text("⚠️ Нет чётких сигналов прямо сейчас. Попробуй через 5 минут.")
        return
    
    for signal in signals[:3]:
        await update.message.reply_text(format_signal(signal), parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "scan":
        await query.edit_message_text("🔄 Сканирую рынок... Подожди 10-15 секунд")
        signals = await analyzer.get_signals()
        
        if not signals:
            await query.edit_message_text("⚠️ Нет чётких сигналов прямо сейчас. Попробуй через 5 минут.\n\n/scan — повторить")
            return
        
        await query.edit_message_text(f"✅ Найдено сигналов: *{len(signals)}*", parse_mode='Markdown')
        
        for signal in signals[:3]:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=format_signal(signal),
                parse_mode='Markdown'
            )
    
    elif query.data == "top_coins":
        coins_text = (
            "📊 *Монеты для скальпинга (отфильтрованы):*\n\n"
            "✅ BTC, ETH, SOL, BNB, XRP\n"
            "✅ ADA, AVAX, DOGE, LTC, LINK\n"
            "✅ DOT, MATIC, UNI, ATOM\n\n"
            "❌ *Исключены:* низколиквидные альткоины,\n"
            "мем-коины без объёма, недавние листинги\n\n"
            "_Только монеты с объёмом 24h > $100M_"
        )
        keyboard = [[InlineKeyboardButton("🔍 Сканировать их", callback_data="scan")]]
        await query.edit_message_text(coins_text, parse_mode='Markdown',
                                       reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == "settings":
        settings_text = (
            "⚙️ *Текущие настройки:*\n\n"
            "⏱ Таймфреймы: 5м и 15м\n"
            "💰 Мин. объём 24h: $100M\n"
            "📈 Мин. сила сигнала: 70%\n"
            "⏳ Держать сделку: 5-10 мин\n"
            "🔄 Автосканирование: каждые 15 мин\n\n"
            "Индикаторы: RSI + EMA + MACD + объём"
        )
        await query.edit_message_text(settings_text, parse_mode='Markdown')

def format_signal(signal: dict) -> str:
    direction_emoji = "🟢 LONG" if signal['direction'] == 'LONG' else "🔴 SHORT"
    strength_bar = "█" * int(signal['strength'] / 10) + "░" * (10 - int(signal['strength'] / 10))
    
    return (
        f"{'='*30}\n"
        f"💎 *{signal['symbol']}* | {direction_emoji}\n"
        f"{'='*30}\n\n"
        f"⏱ *Таймфрейм:* {signal['timeframe']}\n"
        f"⏳ *Держать:* {signal['hold_time']}\n\n"
        f"📍 *Вход:* `{signal['entry']}`\n"
        f"🛑 *Стоп-лосс:* `{signal['stop_loss']}` ({signal['sl_pct']}%)\n"
        f"🎯 *Тейк-профит:* `{signal['take_profit']}` ({signal['tp_pct']}%)\n\n"
        f"📊 *Сила сигнала:* {signal['strength']}%\n"
        f"`{strength_bar}`\n\n"
        f"🔍 *Причина:*\n{signal['reason']}\n\n"
        f"⚠️ _Торгуй с умом. Не более 2-3% от депо._"
    )

async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    """Автоматическое сканирование каждые 15 минут"""
    chat_id = context.job.data
    signals = await analyzer.get_signals()
    
    if signals:
        top = signals[0]
        if top['strength'] >= 80:  # только сильные сигналы
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 *АВТОСИГНАЛ!*\n\n" + format_signal(top),
                parse_mode='Markdown'
            )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.job_queue.run_repeating(
        auto_scan,
        interval=900,  # 15 минут
        first=10,
        data=chat_id,
        name=str(chat_id)
    )
    await update.message.reply_text(
        "✅ *Автосигналы включены!*\n\n"
        "Буду присылать сигналы каждые 15 минут,\n"
        "если найду сильный (80%+) сигнал.\n\n"
        "/unsubscribe — отключить",
        parse_mode='Markdown'
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    current_jobs = context.job_queue.get_jobs_by_name(chat_id)
    for job in current_jobs:
        job.schedule_removal()
    await update.message.reply_text("❌ Автосигналы отключены.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
