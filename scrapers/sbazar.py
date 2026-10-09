import re
import unicodedata
import pandas as pd
import requests

from scrapers.utils import *
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


def slugify(text: str) -> str:
    """Converts text into an ASCII URL slug."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


# ---------------------------------------------------------------------------
# 5. Sbazar Scraper
# ---------------------------------------------------------------------------
def build_working_sbazar_url(item: dict) -> str:
    seo_name = item.get("seo_name")
    if seo_name:
        return f"https://www.sbazar.cz/inzerat/{seo_name}"

    item_id = item.get("id")
    title_slug = slugify(item.get("name", ""))

    if item_id and title_slug:
        return f"https://www.sbazar.cz/inzerat/{item_id}-{title_slug}"
    if item_id:
        return f"https://www.sbazar.cz/inzerat/{item_id}"
    return ""


def scrape_sbazar_nextjs(
    filters: SearchFilter = None,
    phrase: str = "byty pronajem",
    category_id: int = 77,
    limit: int = 58,
    locality: str = "municipality,3468",
) -> pd.DataFrame:
    SOURCE = "sbazar"
    endpoint = "https://www.sbazar.cz/api/v1/items/search"

    params = {
        "category_id": category_id,
        "limit": limit,
        "locality": locality,
        "offset": 0,
        "phrase": phrase,
        "price_from": filters.price_min,
        "price_to": filters.price_max,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101"
            " Firefox/150.0"
        ),
        "Accept": "application/json",
        "x-service-version": "v2.0.230",
        "Referer": "https://www.sbazar.cz/",
    }

    response = requests.get(endpoint, params=params, headers=headers)
    response.raise_for_status()

    results = response.json().get("results", [])

    parsed_items = []
    for item in results:
        images = item.get("images", [])
        image_url = images[0].get("url", "") if images else ""
        if image_url.startswith("//"):
            image_url = f"https:{image_url}"

        locality_data = item.get("locality", {})
        locality_parts = [
            locality_data.get("municipality"),
            locality_data.get("district"),
            locality_data.get("region"),
        ]
        locality_str = ", ".join(filter(None, locality_parts))

        item_id = item.get("id")
        listing_url = build_working_sbazar_url(item)

        parsed_items.append(
            {
                "unique_id": generate_unique_id(SOURCE, item_id, listing_url),
                "source": SOURCE,
                "id": item_id,
                "title": item.get("name"),
                "price": item.get("price"),
                "price_by_agreement": item.get("price_by_agreement", False),
                "locality": locality_str,
                "category_id": item.get("category", {}).get("id"),
                "category_name": item.get("category", {}).get("name"),
                "created_at": item.get("create_date"),
                "updated_at": item.get("edit_date"),
                "url": listing_url,
                "image_url": image_url,
            }
        )

    return pd.DataFrame(parsed_items)
