import asyncio
import logging
import os
import sqlite3
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from telebot.async_telebot import AsyncTeleBot
from concurrent.futures import ThreadPoolExecutor
import json
import pickle
from dotenv import load_dotenv
import re

# Load configuration from .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DB_PATH = os.getenv('DB_PATH', 'locations/location.db')
LOG_PATH = os.getenv('LOG_PATH', './logs')
LOCATIONS_FILE = os.getenv(
    'LOCATIONS_FILE', 'locations/active_locations_printers.json')

logging.basicConfig(level=logging.INFO, filename=f'{
                    LOG_PATH}/scraper.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def sanitize(message):
    # Escape reserved characters
    sanitized_message = re.sub(
        r'([+\-*_\[\]()`|{}.!?<>])', r'\\\1', message)
    return sanitized_message


class Database:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self.initialize_database()

    def initialize_database(self):
        locations = [f'Praha_{i}' for i in range(
            1, 11)] + ['Vsechno', 'Prodej', 'Bez_realitky']
        for location in locations:
            self.cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {location} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    header TEXT NOT NULL,
                    link TEXT UNIQUE,
                    price TEXT,
                    details TEXT,
                    location TEXT,
                    date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    contact TEXT,
                    status TEXT DEFAULT 'not sent'
                );
            ''')
        self.connection.commit()

    def insert_offer(self, location, offer):
        sanitized_location = ''.join(
            e if e.isalnum() or e == '_' else '_' for e in location)
        try:
            # Check if the entry already exists
            self.cursor.execute(f'''
                SELECT id FROM {sanitized_location} WHERE link = ?
            ''', (offer[1],))
            existing_entry = self.cursor.fetchone()

            if existing_entry:
                # Update existing record
                logger.info(f"Updating existing entry for {
                            offer[1]} in {sanitized_location}.")
                self.cursor.execute(f'''
                    UPDATE {sanitized_location}
                    SET header = ?, price = ?, details = ?, location = ?, date = ?, contact = ?, status = 'not sent'
                    WHERE link = ?
                ''', (offer[0], offer[2], offer[3], offer[4], offer[5], offer[6], offer[1]))
            else:
                # Insert new record
                self.cursor.execute(f'''
                    INSERT INTO {sanitized_location} (header, link, price, details, location, date, contact, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (*offer, 'not sent'))

            self.connection.commit()
        except sqlite3.OperationalError as e:
            logger.error(f"Database operation failed for {
                sanitized_location}: {e}")
        except sqlite3.IntegrityError as e:
            logger.error(f"Integrity error in table {sanitized_location}: {e}")

    def fetch_offers(self, location, status='not sent', limit=1):
        query = f'''SELECT header, link, price, location, date, contact, status FROM {
            location} WHERE status=? LIMIT ?'''
        self.cursor.execute(query, (status, limit))
        return self.cursor.fetchall()

    def update_status(self, location, link, status):
        self.cursor.execute(
            f"UPDATE {location} SET status=? WHERE link=?", (status, link))
        self.connection.commit()

    def close(self):
        self.connection.close()


class Scraper:
    def __init__(self, driver_path):
        self.driver_path = driver_path

    def setup_driver(self, headless=True):
        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920x1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument("--no-sandbox")
        options.add_argument("--remote-debugging-port=9222")
        if headless:
            options.add_argument("--headless")
        return webdriver.Chrome(options=options)

    def login(self, driver, credentials):
        try:
            driver.get("https://www.irmonitor.cz/")
            driver.find_element(By.ID, "inpLogin").send_keys(
                credentials['username'])
            driver.find_element(By.ID, "inpPassword").send_keys(
                credentials['password'] + '\n')
        except NoSuchElementException as e:
            logger.error("Login elements not found")
            raise e

    def apply_filters(self, driver):
        try:
            menu = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="mnNavlist"]/li[1]/a')))
            menu.click()
            age_filter = driver.find_element(
                By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]')
            age_filter.click()
            driver.find_element(
                By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]/div/ul/li[2]').click()
            driver.find_element(
                By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_tbPriceFrom"]').send_keys('10000')
            driver.find_element(
                By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_tbPriceTo"]').send_keys('1000000')
            driver.find_element(
                By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_btShow"]').click()
        except TimeoutException as e:
            logger.error("Timeout while applying filters")
            raise e

    def scrape(self, driver, database, locations):
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "dvAdvertismen")))
            headers = driver.find_elements(By.CLASS_NAME, "advBHeader")
            bodies = driver.find_elements(By.CLASS_NAME, "dvAdvBody")

            for body, header in zip(bodies, headers):
                try:
                    name_elem = header.find_element(By.CLASS_NAME, "advName")
                    link = name_elem.get_attribute('href')
                    price_elem = header.find_element(
                        By.CLASS_NAME, "advBPrice")
                    details = body.find_element(By.CLASS_NAME, "dvAdvDetalL")

                    location_elem, date_elem, contact_elem = details.find_elements(
                        By.CLASS_NAME, 'dvAdvDetItmVal')
                    offer = (
                        name_elem.text, link, price_elem.text, details.text,
                        location_elem.text, date_elem.text, contact_elem.text
                    )

                    is_sale = 'prodam' in name_elem.text.lower() or 'prodej' in name_elem.text.lower()
                    is_bezrealitky = 'bez realitky' in name_elem.text.lower()

                    # Define valid Praha districts
                    valid_praha_districts = [
                        f'Praha_{i}' for i in range(1, 11)]

                    category = 'Prodej' if is_sale else ('Bez_realitky' if is_bezrealitky else
                                                         'Vsechno' if not any(district in location_elem.text for district in valid_praha_districts)
                                                         else location_elem.text.replace(' ', '_'))
                    database.insert_offer(category, offer)

                except Exception as e:
                    logger.warning(f"Failed to process advertisement: {e}")

        except TimeoutException as e:
            logger.error("Timeout while loading results")
            raise e


class LocationPrinter:
    def __init__(self, location, database, bot):
        self.location = location
        self.database = database
        self.bot = bot
        self.is_active = False

    async def start(self, chat_id):
        self.is_active = True

        while self.is_active:
            offers = self.database.fetch_offers(
                self.location, status='not sent', limit=5)
            if not offers:
                await asyncio.sleep(10)
                continue

            for offer in offers:
                message = f'''[{sanitize(offer[0])}]({sanitize(offer[1])})\nCena: {sanitize(offer[2])}\n{
                    sanitize(offer[3])}\n{sanitize(offer[5])}'''
                try:
                    await self.bot.send_message(chat_id=chat_id, text=message)
                    self.database.update_status(
                        self.location, offer[1], 'sent')
                except:
                    pass
            await asyncio.sleep(10)

    def stop(self):
        self.is_active = False


class TelegramBot:
    def __init__(self, token, database):
        self.bot = AsyncTeleBot(token, parse_mode='MarkdownV2')
        self.database = database
        self.printers = {}

    def load_printers(self):
        try:
            if os.path.exists(LOCATIONS_FILE):
                with open(LOCATIONS_FILE, 'r') as f:
                    location_storage = json.load(f)

                for formatted_location, pickle_filename in location_storage.items():
                    with open(pickle_filename, 'rb') as pickle_file:
                        message_obj = pickle.load(pickle_file)
                        printer = LocationPrinter(
                            formatted_location, self.database, self.bot)
                        self.printers[formatted_location] = {
                            'printer': printer, 'chat_id': message_obj}
        except Exception as e:
            logger.warning(f"Failed to load printers: {e}")

    def save_printers(self):
        try:
            location_storage = {}
            for location, data in self.printers.items():
                pickle_filename = f'locations/{location}-printer.pkl'
                with open(pickle_filename, 'wb') as pickle_file:
                    pickle.dump({'chat_id': data['chat_id']}, pickle_file)
                location_storage[location] = pickle_filename

            with open(LOCATIONS_FILE, 'w') as f:
                json.dump(location_storage, f)
        except Exception as e:
            logger.warning(f"Failed to save printers: {e}")

    async def start(self):
        @ self.bot.message_handler(commands=['help'])
        async def help_handler(message):
            await self.bot.reply_to(message, "Use /locate location to search for properties")

        @ self.bot.message_handler(commands=['locate'])
        async def locate_handler(message):
            location = "_".join(message.text.split()[1:]).capitalize()
            location = ''.join(e for e in location if e.isalnum() or e == '_')
            if location in self.printers:
                await self.bot.reply_to(message, f"A printer for {sanitize(location)} is already active")
                return

            printer = LocationPrinter(location, self.database, self.bot)
            self.printers[location] = {
                'printer': printer, 'chat_id': message.chat.id}
            self.save_printers()
            await self.bot.reply_to(message, f"Started monitoring {sanitize(location)}")
            asyncio.create_task(printer.start(message.chat.id))

        @ self.bot.message_handler(commands=['stop'])
        async def stop_handler(message):
            location = "_".join(message.text.split()[1:]).capitalize()
            location = ''.join(e for e in location if e.isalnum() or e == '_')
            if location not in self.printers:
                await self.bot.reply_to(message, f"No active printer for {sanitize(location)}")
                return

            self.printers[location]['printer'].stop()
            del self.printers[location]
            self.save_printers()
            await self.bot.reply_to(message, f"Stopped monitoring {sanitize(location)}")

        self.load_printers()

        for location, data in self.printers.items():
            asyncio.create_task(data['printer'].start(data['chat_id']))

        await self.bot.infinity_polling()


async def periodic_scraping(scraper, database):
    credentials = {"username": os.getenv(
        "IRM_USERNAME"), "password": os.getenv("IRM_PASSWORD")}
    while True:
        logger.info("Starting periodic scraping")
        try:
            driver = scraper.setup_driver()
            scraper.login(driver, credentials)
            scraper.apply_filters(driver)
            # Replace `locations=None` with actual locations if needed
            scraper.scrape(driver, database, locations=None)
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
        finally:
            driver.quit()
        await asyncio.sleep(60)


async def main():
    database = Database(DB_PATH)
    scraper = Scraper(driver_path=os.getenv('CHROME_DRIVER_PATH'))

    bot = TelegramBot(TOKEN, database)
    asyncio.create_task(bot.start())
    asyncio.create_task(periodic_scraping(scraper, database))

    await asyncio.sleep(60 * 60 * 24)  # Keep running for 24 hours
    database.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Critical error: {e}")
