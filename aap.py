import telebot

bot = telebot.TeleBot("7510786889:AAHVZ1O6RHqNQaXPVO7OWTC8F9rqTh3aunE")  # Replace with your token

@bot.message_handler(func=lambda _: True)
def reply(message):
    bot.reply_to(message, "I’m alive 24/7! 🚀")

bot.polling(non_stop=True)  # Keeps the bot running
