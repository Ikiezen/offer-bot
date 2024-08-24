"""Test bot in Telegram (@dev_ikibot)"""

from pprint import pp
from telebot.async_telebot import AsyncTeleBot
import telebot
import asyncio
from location_printer import LocationPrinter
import os
import scrapper
import scrapper.db_test

cwd = os.path.dirname(os.path.abspath(__file__))
os.chdir(cwd)
os.makedirs(os.path.join(cwd, 'locations'), exist_ok=True)
with open("token.txt", "r", encoding="utf8") as file:
    token = file.readline().strip()
with open('creds.txt', 'r') as file:
    credentials = file.readlines().strip()


async def main():
    """Entry point"""
    locations = [f'Praha_{i}' for i in range(0, 11)]
    offerlist = {}
    printers = []

    scrapper.inforeality.inforeality_scrapper(locations, credentials)

    for location in locations:
        with open(f'locations/inforeality_{location}.txt', 'r', encoding='utf8') as offersfile:
            offers = offersfile.read()
            offers = offers.split(';;;')

        offerlist[location] = offers

    message_queue = asyncio.Queue()

    async def process_queue():
        while True:
            message, response_func = await message_queue.get()
            # try:
            await response_func(message)
            # except Exception as ex:
            #    print(f'ERROR: {ex}')
            message_queue.task_done()

    bot = AsyncTeleBot(
        token=token,  parse_mode='MarkdownV2', colorful_logs=True)
    print(f"Welcome, your token is: {token}")

    @bot.message_handler(commands=["start", "sayahoj"])
    async def start(m):
        async def reply(message):
            await bot.send_message(message.chat.id, "Ahoj\\! Pro zahájení vyhledávání nemovitosti zadejte /locate a název města\\. Například:\\ /locate Praha")
        await message_queue.put((m, reply))

    @bot.message_handler(commands=["stop"])
    async def stop(m: telebot.types.Message):
        print(printers)
        for printer in printers:
            if printer.check_then_stop(m):
                printers.remove(printer)

    @bot.message_handler(commands=['locate'])
    async def locate(m: telebot.types.Message):
        chat_info = await bot.get_chat(m.chat.id)
        slow_mode_delay = chat_info.slow_mode_delay
        formatted_location = " ".join(
            m.text.split()[1:]).replace(' ', '_').capitalize()
        if formatted_location in locations:
            # offer_at_location = offerlist[formatted_location]
            offer_at_location = scrapper.db_test.read_offers(
                formatted_location)
            location_printer = LocationPrinter(
                message_queue, bot, offer_at_location)
            await location_printer.start(m)
            printers.append(location_printer)

    @bot.message_handler(content_types=["text"])
    def handle_text(message):
        return
        bot.send_message(message.chat.id, f"{
                         message.chat.username} řikal: {message.text}")

    asyncio.create_task(process_queue())
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())
    scrapper.db_test.close()
