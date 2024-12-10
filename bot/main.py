"""Test bot in Telegram (@dev_ikibot)"""
import typing
import typing_extensions
from telebot.async_telebot import AsyncTeleBot
from concurrent.futures import ThreadPoolExecutor
from location_printer import LocationPrinter
import telebot
import asyncio
import os
from scrapper import inforeality
from scrapper import octopus
from scrapper import db
import time
import json
import pickle
import logging

global logger

logger = logging.getLogger(__name__)

cwd = os.path.dirname(os.path.abspath(__file__))
os.chdir(cwd)
os.makedirs(os.path.join(cwd, 'locations'), exist_ok=True)
locations_filepath = 'locations/active_locations_printers.json'
with open("token.txt", "r", encoding="utf8") as file:
    token = file.readline().strip()


def get_credentials(filename):
    with open(os.path.abspath(filename), 'r') as file:
        credentials = file.readlines()
        credentials[0] = credentials[0].strip()
        credentials[1] = credentials[1].strip()
    return credentials


async def main():
    """Entry point"""

    logging.basicConfig(
        filename=f'marimaxi-{str(time.time()).replace('.', '_')}-.log', level=logging.INFO)

    locations = [f'Praha_{i}' for i in range(1, 11)]
    locations.append('Prodej')
    locations.append('Bez_realitky')
    offerlist = {}
    printers = []
    database = db.Database()

    def save_state(formatted_location, message):
        try:
            pickle_filename = f'locations/{formatted_location}-message.pkl'
            location_storage = {}
            if (os.stat(locations_filepath).st_size == 0):
                raise FileNotFoundError

            with open(locations_filepath, 'r') as f:
                with open(pickle_filename, 'wb') as pickle_file:
                    pickle.dump(message, pickle_file)

                location_storage = json.load(f)
                location_storage[formatted_location] = pickle_filename
                serialized = json.dumps(location_storage)
                with open(locations_filepath, 'w') as f:
                    f.write(serialized)
        except FileNotFoundError:
            with open(locations_filepath, 'w') as f:
                with open(pickle_filename, 'wb') as pickle_file:
                    pickle.dump(message, pickle_file)
                location_storage[formatted_location] = pickle_filename
                serialized = json.dumps(location_storage)
                f.write(serialized)


    async def async_octopus_scrapper(locations, credentials, mock, refresh_rate=30):
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            async def runner():
                while True:
                    octopus.scrapper(
                        database, None, credentials, mock)
                    database.commit()
                    await asyncio.sleep(refresh_rate)  # refresh rate
                    # return
            await loop.run_in_executor(pool, asyncio.run, runner())

    async def async_inforeality_scrapper(locations, credentials, mock, refresh_rate=30):
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            async def runner():
                while True:
                    inforeality.scrapper(
                        database, locations, credentials, logger, mock)
                    database.commit()
                    await asyncio.sleep(refresh_rate)
                    # return
            await loop.run_in_executor(pool, asyncio.run, runner())
            # await loop.run_until_complete(pool, runner())

    async def scan_for_new(location):
        return await database.read_offers(location)

    # for location in locations:
    #     with open(f'locations/inforeality_{location}.txt', 'r', encoding='utf8') as offersfile:
    #         offers = offersfile.read()
    #         offers = offers.split(';;;')

    #     offerlist[location] = offers

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
    logger.info(f"Welcome, your token is: {token}")

    @bot.message_handler(commands=["pomoc", "help"])
    async def help(m):
        async def reply(message):
            await bot.send_message(message.chat.id, "Ahoj\\!Chcete\\-li zahájit vyhledávání nemovitosti, zadejte /locate následovaný názvem čtvrti\\. Například:\\/locate Praha 4")
        await message_queue.put((m, reply))

    @bot.message_handler(commands=["stop"])
    async def stop(m: telebot.types.Message):
        logger.info(printers)

        for printer in printers:
            if printer.check_then_stop(m):
                try:
                    with open(locations_filepath, 'r') as f:
                        location_storage = json.load(f)

                        os.remove(os.path.join(
                            os.getcwd(), location_storage[printer.location]))
                        del location_storage[printer.location]

                        serialized = json.dumps(location_storage)
                    with open(locations_filepath, 'w') as f:
                        f.write(serialized)
                except FileNotFoundError:
                    pass
    @bot.message_handler(commands=['locate'])
    async def locate(telegram_message: telebot.types.Message):
        chat_info = await bot.get_chat(telegram_message.chat.id)
        slow_mode_delay = chat_info.slow_mode_delay
        formatted_location = " ".join(
            telegram_message.text.split()[1:]).replace(' ', '_').capitalize()
        if formatted_location in locations:

            location_printer = LocationPrinter(
                message_queue, bot, database, formatted_location)

            save_state(formatted_location, telegram_message)
            await location_printer.start(telegram_message)
            printers.append(location_printer)

    @bot.message_handler(content_types=["text"])
    def handle_text(message):
        return
        bot.send_message(message.chat.id, f"{
                         message.chat.username} řikal: {message.text}")

    @bot.message_handler(commands=["shutdown"])
    def shutdown(message):
        bot.reply_to(message, 'Bot is stopping...')
        bot.stop_poll()
        bot.reply_to(message, 'Bot is stopped!')


    # @bot.message_reaction_handler(message_reaction_type=-1)
    # async def handle_reactions(update, context):
    #    print(update, context)

    async def restore_last_state():
        location_storage = {}
        try:
            if (os.stat(locations_filepath).st_size == 0):
                raise FileNotFoundError
            location_storage = {}
            with open(locations_filepath, 'r') as f:

                location_storage = json.load(f)
                for formatted_location, pickle_filename in location_storage.items():

                    with open(pickle_filename, 'rb') as pickle_file:
                        message_obj = pickle.load(pickle_file)
                        location_printer = LocationPrinter(
                            message_queue, bot, database, formatted_location)

                        save_state(formatted_location, message_obj)
                        await location_printer.start(message_obj)
                        printers.append(location_printer)
        except FileNotFoundError:
            pass
        # STARTUP WITH LAST STATE

    await restore_last_state()
    # asyncio.create_task(async_octopus_scrapper(
    #     None, get_credentials('octopus-creds.txt'), mock=False))
    # TODO: ENABLE INFOREALITY HERE
    asyncio.create_task(async_inforeality_scrapper(
        locations, get_credentials('inforeality-creds.txt'), mock=False))
    asyncio.create_task(process_queue())
    await bot.infinity_polling(allowed_updates=["message", "message_reaction"], timeout=None)

        

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.warning(e)
