'''One-file scrapper'''

import sys, traceback
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
    "IRM" : "🗞️",
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
                    price TEXT,
                    details TEXT,
                    location TEXT,
                    date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    contact TEXT,
                    source TEXT,
                    link TEXT UNIQUE,
                    content_hash TEXT UNIQUE,
                    status TEXT DEFAULT 'not sent'
                );
            """)

            # Migrations: Ensure columns exist on legacy tables
            self.cursor.execute(f"PRAGMA table_info({location})")
            columns = [col[1] for col in self.cursor.fetchall()]

            if "source" not in columns:
                self.cursor.execute(
                    f"ALTER TABLE {location} ADD COLUMN source TEXT"
                )
            if "content_hash" not in columns:
                self.cursor.execute(
                    f"ALTER TABLE {location} ADD COLUMN content_hash TEXT"
                )

        self.connection.commit()

    def get_existing_hashes(self, location) -> set:
        """Loads all existing content hashes into memory for O(1) duplicate checks."""
        sanitized_location = "".join(
            e if e.isalnum() or e == "_" else "_" for e in location
        )
        try:
            self.cursor.execute(
                f"SELECT content_hash FROM {sanitized_location} WHERE content_hash IS NOT NULL"
            )
            return {row[0] for row in self.cursor.fetchall()}
        except sqlite3.OperationalError:
            return set()

    def insert_offer(self, location, offer: dict):
            sanitized_location = "".join(
                e if e.isalnum() or e == "_" else "_" for e in location
            )
            content_hash = offer.get("content_hash")

            try:
                # 1. Check if this exact URL already exists (for updates)
                self.cursor.execute(
                    f"SELECT id FROM {sanitized_location} WHERE link = ?",
                    (offer["link"],),
                )
                existing_by_link = self.cursor.fetchone()

                if existing_by_link:
                    # Update existing URL record
                    self.cursor.execute(
                        f"""
                        UPDATE {sanitized_location}
                        SET header = ?, price = ?, details = ?, location = ?, date = ?, contact = ?, source = ?, content_hash = ?
                        WHERE link = ?
                    """,
                        (
                            offer["header"],
                            offer["price"],
                            offer["details"],
                            offer["location"],
                            offer["date"],
                            offer["contact"],
                            offer["source"],
                            content_hash,
                            offer["link"],
                        ),
                    )
                else:
                    # 2. Check if a cross-platform duplicate with this content_hash already exists under a DIFFERENT link
                    if content_hash:
                        self.cursor.execute(
                            f"SELECT id FROM {sanitized_location} WHERE content_hash = ?",
                            (content_hash,),
                        )
                        existing_by_hash = self.cursor.fetchone()
                        if existing_by_hash:
                            # Cross-platform duplicate detected in DB — skip insertion entirely
                            return

                    # Insert new unique listing
                    self.cursor.execute(
                        f"""
                        INSERT OR IGNORE INTO {sanitized_location} (header, price, details, location, date, contact, source, link, content_hash, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'not sent')
                    """,
                        (
                            offer["header"],
                            offer["price"],
                            offer["details"],
                            offer["location"],
                            offer["date"],
                            offer["contact"],
                            offer["source"],
                            offer["link"],
                            content_hash,
                        ),
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
        query = f"""SELECT header, price, details, location, date, contact, source,link, content_hash, status FROM {location} WHERE status=? LIMIT ?"""
        self.cursor.execute(query, (status, limit))
        rows = self.cursor.fetchall()
        if rows:
            return pd.DataFrame(
                rows,
                columns=[
                    "header",
                    "price",
                    "details",
                    "location",
                    "date",
                    "contact",
                    "source",
                    "link",
                    "content_hash",
                    "status",
                ],
            )
        return None

    def update_status(self, location, link, status):
        self.cursor.execute(
            f"UPDATE {location} SET status=? WHERE link=?", (status, link)
        )
        self.connection.commit()

    def close(self):
        self.connection.close()


# ---------------------------------------------------------------------------
# Database Mapper & Table Router
# ---------------------------------------------------------------------------
def generate_content_hash(row_or_location, details=None) -> str:
    if isinstance(row_or_location, str):
        # Legacy string fallback
        loc_clean = (
            unicodedata.normalize("NFKD", row_or_location)
            .encode("ascii", "ignore")
            .decode("utf-8")
            .lower()
        )
        desc_clean = (
            unicodedata.normalize("NFKD", str(details or ""))
            .encode("ascii", "ignore")
            .decode("utf-8")
            .lower()
        )
        return hashlib.md5(
            f"{re.sub(r'[^a-z0-9]', '', loc_clean)}_{re.sub(r'[^a-z0-9]', '', desc_clean)}".encode(
                "utf-8"
            )
        ).hexdigest()

    row = row_or_location

    # 1. Price Bucketing (nearest 10,000 Kč)
    raw_price = safe_int(row.get("price")) or 0
    price_bucket = round(raw_price / 10000) * 10000

    # 2. Area Bucketing (nearest 10 m²)
    area = safe_int(row.get("area_m2")) or 0
    if area == 0:
        text_blob = f"{row.get('title', '')} {row.get('description', '')} {row.get('locality', '')} {row.get('details', '')}"
        area_match = re.search(r"(\d+)\s*(?:m2|m²)", text_blob, re.IGNORECASE)
        if area_match:
            area = int(area_match.group(1))
    area_bucket = round(area / 10) * 10

    # 3. Layout normalization
    layout_raw = str(row.get("layout") or row.get("title") or "").lower()
    layout_match = re.search(r"(\d\+?[kk|1-4])", layout_raw)
    layout_str = (
        re.sub(r"[^\w]", "", layout_match.group(1)) if layout_match else "unknown"
    )

    # 4. Smart Location Normalization (strips "praha", district numbers, sorts tokens)
    loc_raw = str(
        row.get("locality")
        or row.get("address")
        or row.get("district")
        or row.get("location")
        or ""
    )
    loc_norm = (
        unicodedata.normalize("NFKD", loc_raw)
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
    )
    loc_cleaned = re.sub(r"[^a-z0-9\s]", " ", loc_norm)
    tokens = loc_cleaned.split()

    # Filter out city and district noise words
    stopwords = {
        "praha",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "20",
        "21",
        "22",
        "cze",
        "czech",
    }
    filtered_tokens = [t for t in tokens if t not in stopwords]
    filtered_tokens.sort()  # Order independence ("Nové Město Příčná" == "Příčná Nové Město")
    loc_key = "".join(filtered_tokens)

    signature = f"{price_bucket}_{area_bucket}_{layout_str}_{loc_key}"
    
    # Fallback if everything else is empty so SQLite UNIQUE constraint never sees a blank hash
    if not loc_key and price_bucket == 0 and area_bucket == 0:
        fallback_blob = f"{row.get('title', '')} {row.get('description', '')}"
        return hashlib.md5(fallback_blob.encode("utf-8")).hexdigest()

    return hashlib.md5(signature.encode("utf-8")).hexdigest()


def map_row_to_db_offer(row: pd.Series) -> dict:
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
    elif pd.notna(row.get("body")):
        details_parts.append(str(row.get("body")))

    details = " | ".join(details_parts) if details_parts else "Bez detailů"

    location = str(
        row.get("locality") or row.get("address") or row.get("district") or ""
    ).strip()

    raw_date = (
        row.get("published_at") or row.get("date") or row.get("created_at")
    )
    date = (
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
        " | ".join(contact_parts) if contact_parts else "Kontakt není k dispozici"
    )

    # Use the pre-calculated hash from the DataFrame row, with a safe fallback
    content_hash = row.get("content_hash") or generate_content_hash(
        location, details
    )

    return {
        "header": header,
        "price": price,
        "details": details,
        "location": location,
        "date": date,
        "contact": contact,
        "source": source,
        "link": link,
        "content_hash": content_hash,
    }


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

    df = df.copy()
    df["content_hash"] = df.apply(generate_content_hash, axis=1)
    df_unique = df.drop_duplicates(subset=["content_hash"], keep="first")

    inserted_count = 0

    for _, row in df_unique.iterrows():
        url = row.get("url")
        if not url or pd.isna(url):
            continue

        offer = map_row_to_db_offer(row)
        target_tables = extract_target_tables(row)

        for table_name in target_tables:
            # Let database handle duplicate prevention via INSERT OR IGNORE
            db_manager.insert_offer(table_name, offer)
            inserted_count += 1

    logger.info(
        f"[+] DB Sync complete: Attempted insertion for {inserted_count}"
        " records (duplicates automatically dropped by SQLite constraints)."
    )

def apply_anti_broker_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    filtered_df = df.copy()

    def get_col(col_name):
        if col_name in filtered_df.columns:
            return filtered_df[col_name].fillna("")
        return pd.Series("", index=filtered_df.index)

    text_blob = (
        get_col("title")
        + " "
        + get_col("description")
        + " "
        + get_col("details")
        + " "
        + get_col("locality")
    ).str.lower()

    text_blob_normalized = (
        text_blob.apply(lambda val: unicodedata.normalize("NFKD", val))
        .str.encode("ascii", "ignore")
        .str.decode("utf-8")
    )

    # Broker/Agency spam markers to strictly drop
    broker_keywords = [
        "rk nevolat",
        "realitky nevolat",
        "rk nekontaktovat",
        "realitni kancelare nevolat",
        "rk prosim nevolat",
        "spekulanty ne",
        # Add common real estate agency terms if they leak in
        "realitni kancelar",
        "provize rk",
        "sluzby rk",
        "Nájemce neplatí provizi RK",
        "Pronajímám byt",
        "NEPLATITE PROVIZI ",
        "Jsem přímý pronajímatel",
        "RK - NEVOLAT ",
        "Pronajmu",
        "Ekospol",
        "Bez provize – pronajímá přímo majitel",
        "Přímý pronájem od majitele, bez provize realitní kanceláři",
        "RK prosím nevolat",
        "Bez provize realitní kanceláři – jednáte přímo se zástupcem majitele",
        "Bez provize realitní kanceláři",
        "Jsem přímý majitel pronájem je ted",
        "Bez provize RK!",
        "Provizi RK neplatíte",
        "Pronájem přímo bez provize",
        "Pouze pro přímí zájemce",
        "Provize se neplatí",
        "RK prosím nevolat",
        "Realitní kanceláře nevolat",
    ]

    broker_pattern = "|".join(re.escape(k) for k in broker_keywords)
    has_broker_spam = text_blob_normalized.str.contains(broker_pattern, regex=True, na=False)

    # Keep everything EXCEPT what triggers the broker spam blacklist
    final_filtered = filtered_df[~has_broker_spam].reset_index(drop=True)
    
    return final_filtered

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
                    
                    # Compute content hash for IRM items so SQLite's UNIQUE constraint catches duplicates
                    content_hash = generate_content_hash(location_elem.text, details.text)

                    offer = {
                            "header" : name_elem.text,
                            "price" : price_elem.text,
                            "details" :  details.text,
                            "location" : location_elem.text,
                            "date" : date_elem.text,
                            "contact" : contact_elem.text,
                            "source" : "IRM",
                            "link" : link,
                            "content_hash": content_hash,
                            }

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
                    traceback.print_exc(file=sys.stdout)


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
            if offers is None or offers.empty:
                await asyncio.sleep(10)
                continue

            for _, offer in offers.iterrows():
                
                message=f'''
[{sanitize(offer["header"])}]({sanitize(offer["link"])})\n
Cena: {sanitize(offer["price"])}\n
{sanitize(offer["location"])}\n
{sanitize("---")}\n
{sanitize(offer["contact"])}\n
                ''' 
                try:
                    await self.bot.send_message(chat_id=chat_id, text=message, message_thread_id=thread_id)
                    self.database.update_status(
                        self.location, offer["link"], 'sent')
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
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
            if not location:
                await self.bot.reply_to(message, "Use /locate location to search for properties")
                return
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
                df_octopus,
                #df_bazos, df_domonaut, df_idnes, df_sbazar, df_sreality,
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
            # Post-filter by keywords (no RK)
            final_listings = apply_anti_broker_filters(final_listings)

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

            # IRM SCRAPER
            driver = scraper.setup_driver()
            scraper.login(driver, credentials)
            scraper.apply_filters(driver)
            # Replace `locations=None` with actual locations if needed
            scraper.scrape(driver, database, locations=None)
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
            traceback.print_exc(file=sys.stdout)
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
