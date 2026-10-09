'''One-file scrapper'''

from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from telebot.async_telebot import AsyncTeleBot
from urllib.parse import quote, urlparse
import argparse
import asyncio
import hashlib
import html
import json
import logging
import os
import pandas as pd
import pickle
import re
import requests

from scrapers.utils import *
from scrapers.bazos import scrape_bazos_flats 
from scrapers.octopus import octopus_login
from scrapers.octopus import parse_xml_login_response
from scrapers.reality_idnes import scrape_idnes
from scrapers.sbazar import scrape_sbazar_nextjs
from scrapers.sreality import fetch_sreality_dataframe

import signal
import sqlite3
import time
import unicodedata
import xml.etree.ElementTree as ET


# Parse command-line arguments
parser = argparse.ArgumentParser(
    description="Run the Telegram bot and scraper.")
parser.add_argument("--debug", action="store_true",
                    help="Enable debug-level logging")
args = parser.parse_args()

# Load configuration from .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

LOCATIONS_PATH = os.getenv('DB_PATH', 'locations')
LOCATIONS_FILE = os.path.join(LOCATIONS_PATH, 'printers.json')
DB_FILE = os.path.join(LOCATIONS_PATH, 'location.db')

LOG_PATH = os.getenv('LOG_PATH', 'logs')
LOG_FILE = os.path.join(LOG_PATH, 'scraper.log')

PERIODIC_SCRAPER_TIMER = os.getenv('PERIODIC_SCRAPER_TIMER', '300')


BASE_URL = "https://monitoring.ikarus21.cz/default.aspx"
OCTOPUS_USERNAME = os.getenv('OCTOPUS_USERNAME', 'USERNAME')
OCTOPUS_PASSWORD = os.getenv('OCTOPUS_PASSWORD', 'PASSWORD')

# ---------------------------------------------------------------------------
# Global Filter Configuration
# ---------------------------------------------------------------------------
@dataclass
class SearchFilter:
    price_min: int = 10000
    price_max: int = 200000
    region: str = "Praha"
    zip_code: str = "11000"
    radius_km: int = 10
    category: str = "byt"
    offer_type: str = "pronajem"
    praha_districts: list[int] = field(
        default_factory=lambda: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    )


GLOBAL_FILTER = SearchFilter()
SOURCE_EMOJIS = {
    "bazos": "🟡",
    "domonaut": "🤖",
    "octopus": "🐙",
    "idnes": "📰",
    "sbazar": "🛍️",
    "sreality": "🔴",
}


try:
    os.mkdir(LOG_PATH)
    os.mkdir(LOCATIONS_PATH)
except FileExistsError as e:
    print(e)
if not (os.path.isfile(LOG_FILE) and os.path.isfile(LOCATIONS_FILE)):
    Path(LOG_FILE).touch()
    Path(LOCATIONS_FILE).touch()

# Set logging level based on the --debug flag
LOG_LEVEL = logging.DEBUG if args.debug else logging.INFO
logging.basicConfig(level=LOG_LEVEL, filename=f'{LOG_FILE}',
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def sanitize(message):
    '''Escape reserved characters'''
    sanitized_message = re.sub(
        r'([+\-*_\[\]()`|{}.!?<>=])', r'\\\1', message)
    return sanitized_message


# ---------------------------------------------------------------------------
# Database Class
# ---------------------------------------------------------------------------


class Database:

    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self.initialize_database()

    def initialize_database(self):
        locations = [f"Praha_{i}" for i in range(1, 23)] + [
            "Vsechno",
            "Prodej",
        ]
        for location in locations:
            self.cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {location} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    header TEXT NOT NULL,
                    link TEXT UNIQUE,
                    price TEXT,
                    details TEXT,
                    location TEXT,
                    date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    contact TEXT,
                    source TEXT,
                    status TEXT DEFAULT 'not sent'
                );
            """)

            # Migration: Ensure existing database tables have the source column
            self.cursor.execute(f"PRAGMA table_info({location})")
            columns = [col[1] for col in self.cursor.fetchall()]
            if "source" not in columns:
                self.cursor.execute(
                    f"ALTER TABLE {location} ADD COLUMN source TEXT"
                )

        self.connection.commit()

    def insert_offer(self, location, offer):
        sanitized_location = "".join(
            e if e.isalnum() or e == "_" else "_" for e in location
        )
        try:
            self.cursor.execute(
                f"""
                SELECT id FROM {sanitized_location} WHERE link = ?
            """,
                (offer[1],),
            )
            existing_entry = self.cursor.fetchone()

            if existing_entry:
                self.cursor.execute(
                    f"""
                    UPDATE {sanitized_location}
                    SET header = ?, price = ?, details = ?, location = ?, date = ?, contact = ?, source = ? WHERE link = ?
                """,
                    (
                        offer[0],
                        offer[2],
                        offer[3],
                        offer[4],
                        offer[5],
                        offer[6],
                        offer[7],
                        offer[1],
                    ),
                )
            else:
                self.cursor.execute(
                    f"""
                    INSERT INTO {sanitized_location} (header, link, price, details, location, date, contact, source, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (*offer, "not sent"),
                )

            self.connection.commit()
        except sqlite3.OperationalError as e:
            logger.error(
                f"Database operation failed for {sanitized_location}: {e}"
            )
        except sqlite3.IntegrityError as e:
            logger.error(
                f"Integrity error in table {sanitized_location}: {e}"
            )

    def fetch_offers(self, location, status="not sent", limit=1):
        query = f"""SELECT header, link, price, location, date, contact, source, status FROM {
            location} WHERE status=? LIMIT ?"""
        self.cursor.execute(query, (status, limit))
        return self.cursor.fetchall()

    def update_status(self, location, link, status):
        self.cursor.execute(
            f"UPDATE {location} SET status=? WHERE link=?", (status, link))
        self.connection.commit()

    def close(self):
        self.connection.close()


# ---------------------------------------------------------------------------
# Database Mapper & Table Router
# ---------------------------------------------------------------------------

def map_row_to_db_offer(row: pd.Series) -> tuple:
    source = str(row.get("source") or "unknown").lower()
    emoji_flag = SOURCE_EMOJIS.get(source, "🏠")

    raw_title = str(row.get("title") or "Neznámý název").strip()
    header = f"{emoji_flag} {raw_title}"

    link = str(row.get("url") or "").strip()

    price_val = row.get("price")
    if pd.notna(price_val) and price_val is not None:
        price = f"{int(price_val):,} Kč".replace(",", " ")
    else:
        price = str(row.get("price_raw") or "Dohodou")

    details_parts = []
    if pd.notna(row.get("layout")):
        details_parts.append(f"Dispozice: {row.get('layout')}")
    if pd.notna(row.get("area_m2")):
        details_parts.append(f"Plocha: {row.get('area_m2')} m²")
    if pd.notna(row.get("description")):
        details_parts.append(str(row.get("description")))

    details = " | ".join(details_parts) if details_parts else "Bez detailů"

    location = str(
        row.get("locality") or row.get("address") or row.get("district") or ""
    ).strip()

    raw_date = (
        row.get("published_at") or row.get("date") or row.get("created_at")
    )
    date_str = (
        str(raw_date) if pd.notna(raw_date) else datetime.now().isoformat()
    )

    contact_parts = []
    if pd.notna(row.get("phone")) and row.get("phone"):
        contact_parts.append(f"Tel: {row.get('phone')}")
    if pd.notna(row.get("email")) and row.get("email"):
        contact_parts.append(f"Email: {row.get('email')}")
    if pd.notna(row.get("contact_person")) and row.get("contact_person"):
        contact_parts.append(f"Kontakt: {row.get('contact_person')}")

    contact = (
        " | ".join(contact_parts) if contact_parts else "Není k dispozici"
    )

    # Return exactly 8 items to match INSERT statement
    return (header, link, price, details, location, date_str, contact, source)


def extract_target_tables(row: pd.Series) -> list[str]:
    tables = set()

    offer_type = str(
        row.get("offer_type") or row.get("category_main") or ""
    ).lower()
    if "prodej" in offer_type:
        tables.add("Prodej")
        return list(tables)

    districts_to_check = []
    praha_districts = row.get("praha_districts")
    if isinstance(praha_districts, (list, tuple, set)):
        districts_to_check.extend(praha_districts)

    for field in [
        "district",
        "praha_district",
        "locality",
        "address",
        "city",
    ]:
        val = row.get(field)
        if pd.notna(val):
            districts_to_check.append(str(val))

    for item in districts_to_check:
        matches = re.findall(r"\bPraha\s*(\d{1,2})\b", item, re.IGNORECASE)
        for num in matches:
            dist_num = int(num)
            if 1 <= dist_num <= 22:
                tables.add(f"Praha_{dist_num}")

    tables.add("Vsechno")
    return list(tables)


def sync_dataframe_to_db(db_manager: Database, df: pd.DataFrame):
    if df.empty:
        logger.warning("[!] DataFrame is empty, skipping DB sync.")
        return

    inserted_count = 0
    for _, row in df.iterrows():
        url = row.get("url")
        if not url or pd.isna(url):
            continue

        offer = map_row_to_db_offer(row)
        target_tables = extract_target_tables(row)

        for table_name in target_tables:
            db_manager.insert_offer(table_name, offer)
            inserted_count += 1

    logger.info(
        f"[+] DB Sync complete: Processed {len(df)} listings across"
        f" {inserted_count} table insertions."
    )


def apply_global_filters(
    df: pd.DataFrame, filters: SearchFilter = GLOBAL_FILTER
) -> pd.DataFrame:
    if df.empty:
        return df

    filtered_df = df.copy()
    if "price" in filtered_df.columns:
        filtered_df = filtered_df[
            filtered_df["price"].isna()
            | (
                (filtered_df["price"] >= filters.price_min)
                & (filtered_df["price"] <= filters.price_max)
            )
        ]

    return filtered_df.reset_index(drop=True)

class IRM_Scraper:
    def __init__(self, driver_path):
        self.driver_path = driver_path

    def setup_driver(self, headless=True):
        CACHE_SIZE = 209715200  # 200 MiB

        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920x1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument("--no-sandbox")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument(f"--disk-cache-size={CACHE_SIZE}")
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
            breakpoint()
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
                        f'Praha_{i}' for i in range(1, 22)]

                    category = 'Prodej' if is_sale else ('Bez_realitky' if is_bezrealitky else
                                                         'Vsechno' if not any(district in location_elem.text.replace(' ', '_') for district in valid_praha_districts)
                                                         else location_elem.text.replace(' ', '_'))

                    if not is_bezrealitky:
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

    async def start(self, chat_id, thread_id):
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
                    await self.bot.send_message(chat_id=chat_id, text=message, message_thread_id=thread_id)
                    self.database.update_status(
                        self.location, offer[1], 'sent')
                except Exception as e:
                    logger.error(f"Failed to send message: {e}")
            await asyncio.sleep(10)

    def stop(self):
        self.is_active = False


class TelegramBot:
    def __init__(self, token, database):
        self.bot = AsyncTeleBot(token, parse_mode='MarkdownV2')
        self.database = database
        self.printers = {}

    def save_printers(self):
        try:
            location_storage = {}
            for location, data in self.printers.items():
                pickle_filename = f'{Path(LOCATIONS_FILE).parent.absolute()}/{location}-printer.pkl'
                with open(pickle_filename, 'wb') as pickle_file:
                    pickle.dump(
                        {'chat_id': data['chat_id'], 'thread_id': data['thread_id']}, pickle_file)
                location_storage[location] = pickle_filename

            with open(LOCATIONS_FILE, 'w') as f:
                json.dump(location_storage, f)
        except Exception as e:
            logger.warning(f"Failed to save printers: {e}")

    def load_printers(self):
        try:
            if os.path.exists(LOCATIONS_FILE):
                with open(LOCATIONS_FILE, 'r') as f:
                    location_storage = json.load(f)

                for formatted_location, pickle_filename in location_storage.items():
                    with open(pickle_filename, 'rb') as pickle_file:
                        data = pickle.load(pickle_file)
                        printer = LocationPrinter(
                            formatted_location, self.database, self.bot)
                        self.printers[formatted_location] = {
                            'printer': printer, 'chat_id': data['chat_id'], 'thread_id': data['thread_id']}
        except Exception as e:
            logger.warning(f"Failed to load printers: {e}")

    async def start(self):
        @self.bot.message_handler(commands=['help'])
        async def help_handler(message):
            await self.bot.reply_to(message, "Use /locate location to search for properties")

        @self.bot.message_handler(commands=['locate'])
        async def locate_handler(message):
            location = "_".join(message.text.split()[1:]).capitalize()
            location = ''.join(e for e in location if e.isalnum() or e == '_')
            thread_id = message.message_thread_id  # Capture the thread ID

            if location in self.printers:
                await self.bot.reply_to(message, f"A printer for {sanitize(location)} is already active", message_thread_id=thread_id)
                return

            printer = LocationPrinter(location, self.database, self.bot)
            self.printers[location] = {
                'printer': printer, 'chat_id': message.chat.id, 'thread_id': thread_id}
            self.save_printers()
            if location == 'Bez_realitky':
                await self.bot.reply_to(message, f"Offers on location 'bez realitky' are not supported", message_thread_id=thread_id)
                return
            await self.bot.reply_to(message, f"Started monitoring {sanitize(location)}", message_thread_id=thread_id)
            asyncio.create_task(printer.start(message.chat.id, thread_id))

        @self.bot.message_handler(commands=['stop'])
        async def stop_handler(message):
            location = "_".join(message.text.split()[1:]).capitalize()
            location = ''.join(e for e in location if e.isalnum() or e == '_')
            thread_id = message.message_thread_id  # Capture the thread ID

            if location not in self.printers:
                await self.bot.reply_to(message, f"No active printer for {sanitize(location)}", message_thread_id=thread_id)
                return

            self.printers[location]['printer'].stop()
            del self.printers[location]
            self.save_printers()
            await self.bot.reply_to(message, f"Stopped monitoring {sanitize(location)}", message_thread_id=thread_id)
        self.load_printers()

        for location, data in self.printers.items():
            asyncio.create_task(data['printer'].start(
                data['chat_id'], data['thread_id']))

        await self.bot.infinity_polling()


class GracefulExit:
    def __init__(self):
        self._stop = asyncio.Event()
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame):
        logger.info(f"Received termination signal ({signum}). Exiting gracefully.")
        self._stop.set()

    async def wait(self):
        await self._stop.wait()


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
        await asyncio.sleep(int(PERIODIC_SCRAPER_TIMER)) # PERIODIC_SCRAPER_TIMER in s


async def main():

    try:
        # Initialize graceful exit handler
        graceful_exit = GracefulExit()

        database = Database(DB_FILE)
        irm_scraper = IRM_Scraper(driver_path=os.getenv('CHROME_DRIVER_PATH'))
        bot = TelegramBot(TOKEN, database)

        # Run bot and scraper tasks
        bot_task = asyncio.create_task(bot.start())
        scraper_task = asyncio.create_task(
            periodic_scraping(irm_scraper, database))
            
        logger.info(
            f"[*] Starting Real Estate Aggregator Pipeline ({GLOBAL_FILTER.price_min}-{GLOBAL_FILTER.price_max} CZK, {GLOBAL_FILTER.region})..."
        )

        # 1. Bazoš
        try:
            df_bazos = scrape_bazos_flats(filters=GLOBAL_FILTER, page=0)
            logger.info(f"[+] Bazoš: {len(df_bazos)} rows")
        except Exception as e:
            logger.error(f"[-] Bazoš failed: {e}")
            df_bazos = pd.DataFrame()

        # 2. Domonaut
        # try:
        #     df_domonaut = fetch_and_parse_offers(filters=GLOBAL_FILTER, limit=50)
        #     logger.info(f"[+] Domonaut: {len(df_domonaut)} rows")
        # except Exception as e:
        #     logger.error(f"[-] Domonaut failed: {e}")
        #     df_domonaut = pd.DataFrame()
        df_domonaut = pd.DataFrame()

        # 3. Octopus
        try:
            login_resp = octopus_login(OCTOPUS_USERNAME, OCTOPUS_PASSWORD, BASE_URL)
            df_octopus = parse_xml_login_response(login_resp.text)
            logger.info(f"[+] Octopus: {len(df_octopus)} rows")
        except Exception as e:
            logger.error(f"[-] Octopus failed: {e}")
            df_octopus = pd.DataFrame()

        # 4. iDNES Reality
        try:
            df_idnes = scrape_idnes(filters=GLOBAL_FILTER)
            logger.info(f"[+] iDNES: {len(df_idnes)} rows")
        except Exception as e:
            logger.error(f"[-] iDNES failed: {e}")
            df_idnes = pd.DataFrame()

        # 5. Sbazar
        try:
            df_sbazar = scrape_sbazar_nextjs(filters=GLOBAL_FILTER)
            logger.info(f"[+] Sbazar: {len(df_sbazar)} rows")
        except Exception as e:
            logger.error(f"[-] Sbazar failed: {e}")
            df_sbazar = pd.DataFrame()

        # 6. Sreality
        try:
            df_sreality = fetch_sreality_dataframe()
            if not df_sreality.empty and "price_czk" in df_sreality.columns:
                df_sreality = df_sreality.rename(columns={"price_czk": "price"})
            logger.info(f"[+] Sreality: {len(df_sreality)} rows")
        except Exception as e:
            logger.error(f"[-] Sreality failed: {e}")
            df_sreality = pd.DataFrame()

        # Combine all 6 scrapers
        sources = [
            df_bazos,
            df_domonaut,
            df_octopus,
            df_idnes,
            df_sbazar,
            df_sreality,
        ]
        all_listings = pd.concat(sources, ignore_index=True)

        # Clean & Deduplicate by URL
        all_listings = all_listings[
            all_listings["url"].notna() & (all_listings["url"] != "")
        ].copy()
        all_listings["normalized_url"] = all_listings["url"].apply(normalize_url)

        clean_listings = all_listings.drop_duplicates(
            subset=["normalized_url"], keep="first"
        ).reset_index(drop=True)

        # Post-filter by price & region bounds
        final_listings = apply_global_filters(clean_listings, GLOBAL_FILTER)

        logger.info(
            f"\n================ Aggregated Pipeline Summary ================\n"
            f"Total raw items fetched : {len(all_listings)}\n"
            f"Total unique URLs       : {len(clean_listings)}\n"
            f"Final filtered listings : {len(final_listings)}\n"
            f"Breakdown by source     :\n"
            f"{final_listings['source'].value_counts()}\n"
            f"==============================================================\n"
        )

        # Sync to SQLite Database
        sync_dataframe_to_db(database, final_listings)

        # Wait for the shutdown signal
        await graceful_exit.wait()
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
    finally:
        logger.info("Shutting down tasks...")
        bot_task.cancel()
        scraper_task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(bot_task, scraper_task, return_exceptions=True)
        database.close()
        logger.info("Graceful shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting...")
    except Exception as e:
        logger.error(f"Critical error: {e}")
