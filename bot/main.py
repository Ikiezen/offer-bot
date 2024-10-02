"""Test bot in Telegram (@dev_ikibot)"""

from telebot.async_telebot import AsyncTeleBot
from concurrent.futures import ThreadPoolExecutor
from location_printer import LocationPrinter
import telebot
import asyncio
import os
import scrapper
import scrapper.db
import time
import json
import pickle

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


def save_state(formatted_location):
    try:
        pickle_filename = f'locations/{formatted_location}-message.pkl'

        with open(locations_filepath, 'rw') as f:
            with open(pickle_filename, 'wb') as pickle_file:
                pickle.dump(m, pickle_file)

            location_storage = json.load(f.read())
            location_storage[formatted_location] = pickle_filename
            serialized = json.dumps(location_storage)
            f.write(serialized)
    except FileNotFoundError:
        with open(locations_filepath, 'w') as f:
            with open(pickle_filename, 'wb') as pickle_file:
                pickle.dump(m, pickle_file)
            location_storage[formatted_location] = pickle_filename
            serialized = json.dumps(location_storage)
            f.write(serialized)


async def main():
    """Entry point"""
    locations = [f'Praha_{i}' for i in range(1, 11)]
    locations.append('Prodej')
    locations.append('Bez_realitky')
    offerlist = {}
    printers = []
    database = scrapper.db.Database()

    async def async_octopus_scrapper(locations, credentials, mock, refresh_rate=30):
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            async def runner():
                while True:
                    scrapper.octopus.scrapper(
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
                    scrapper.inforeality.scrapper(
                        database, locations, credentials, mock)
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
    print(f"Welcome, your token is: {token}")

    t_ids = [2, 3, 8, 4, 9, 10, 11, 12, 13, 14]
    chat_id = 2183077169

    list_msg = [telebot.types.Message(message_id=0,
                                      message_thread_id=t_id,
                                      from_user=0,
                                      date='',
                                      chat=telebot.types.Chat(chat_id),
                                      content_type='text',
                                      options=[],
                                      json_string='') for t_id in t_ids]


    @bot.message_handler(commands=["pomoc", "help"])
    async def help(m):
        async def reply(message):
            await bot.send_message(message.chat.id, "Ahoj\\!Chcete\\-li zahájit vyhledávání nemovitosti, zadejte /locate následovaný názvem čtvrti\\. Například:\\/locate Praha 4")
        await message_queue.put((m, reply))

    @bot.message_handler(commands=["stop"])
    async def stop(m: telebot.types.Message):
        print(printers)

        for printer in printers:
            if printer.check_then_stop(m):
                try:
                    with open(locations_filepath, 'rw') as f:
                        location_storage = json.load(f.read())

                        os.remove(os.path.join(
                            os.getcwd(), location_storage[printer.location]))
                        del location_storage[printer.location]

                        serialized = json.dumps(location_storage)
                        f.write(serialized)
                except FileNotFoundError:
                    pass
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

            save_state(formatted_location)
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

    def restore_last_state():
        try:
            with open(locations_filepath, 'r') as f:
                location_storage = json.load(f.read())
                for pickle_filename in location_storage.values():
                    with open(pickle_filename, 'rb') as pickle_file:
                        message_obj = pickle.load(pickle_file)

        except FileNotFoundError:
            pass
        # STARTUP WITH LAST STATE

    restore_last_state()
    # asyncio.create_task(async_octopus_scrapper(
    #     None, get_credentials('octopus-creds.txt'), mock=False))
    # TODO: ENABLE INFOREALITY HERE
    asyncio.create_task(async_inforeality_scrapper(
        locations, get_credentials('inforeality-creds.txt'), mock=False))
    asyncio.create_task(process_queue())
    await bot.infinity_polling(allowed_updates=["message", "message_reaction"], timeout=None)

if __name__ == "__main__":
    asyncio.run(main())
