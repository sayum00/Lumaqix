from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # আপনার সার্ভারের বা ngrok এর HTTPS লিংক এখানে বসাবেন
    web_app_url = "https://your-ngrok-or-hosted-url.com" 
    
    keyboard = [[InlineKeyboardButton("🚀 মিনি অ্যাপ ওপেন করুন", web_app=True, url=web_app_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("নিচের বাটনে ক্লিক করে আমাদের অ্যাপে প্রবেশ করুন:", reply_markup=reply_markup)

if __name__ == '__main__':
    # আপনার টেলিগ্রাম বট টোকেন এখানে দিন
    TOKEN = "YOUR_BOT_TOKEN"
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("টেলিগ্রাম বট চালু হয়েছে...")
    app.run_polling()
