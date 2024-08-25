"""Test bot in Telegram (@dev_ikibot)"""

from telebot.async_telebot import AsyncTeleBot
import telebot
import asyncio
from location_printer import LocationPrinter
import os
import scrapper
import scrapper.db
from concurrent.futures import ThreadPoolExecutor
import time

cwd = os.path.dirname(os.path.abspath(__file__))
os.chdir(cwd)
os.makedirs(os.path.join(cwd, 'locations'), exist_ok=True)
with open("token.txt", "r", encoding="utf8") as file:
    token = file.readline().strip()
with open('creds.txt', 'r') as file:
    credentials = file.readlines()
    credentials[0] = credentials[0].strip()
    credentials[1] = credentials[1].strip()


async def main():
    """Entry point"""
    locations = [f'Praha_{i}' for i in range(0, 11)]
    offerlist = {}
    printers = []
    database = scrapper.db.Database()

    async def async_inforeality_scrapper(locations, credentials, mock):
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            async def runner():
                while True:
                    scrapper.inforeality.scrapper(
                        database, locations, credentials, mock)
                    database.commit()
                    await asyncio.sleep(30)
                    # return
            await loop.run_in_executor(pool, asyncio.run, runner())
            # await loop.run_until_complete(pool, runner())

    async def scan_for_new(location):
        return await database.read_offers(location)

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
            # offer_at_location = await scan_for_new(formatted_location)
            location_printer = LocationPrinter(
                message_queue, bot, database, formatted_location)
            await location_printer.start(m)
            printers.append(location_printer)

    @bot.message_handler(content_types=["text"])
    def handle_text(message):
        return
        bot.send_message(message.chat.id, f"{
                         message.chat.username} řikal: {message.text}")

    # @bot.message_reaction_handler(message_reaction_type=-1)
    # async def handle_reactions(update, context):
    #    print(update, context)

    @bot.message_reaction_handler()
    async def handle_reactions(a):
        pass
        # print(a)
    # await async_inforeality_scrapper(locations, credentials, True)
    asyncio.create_task(async_inforeality_scrapper(
        locations, credentials, True))
    asyncio.create_task(process_queue())
    await bot.infinity_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    asyncio.run(main())
