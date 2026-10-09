from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import html
import logging
import re
import sqlite3
import time
import unicodedata
from urllib.parse import quote, urlparse
import xml.etree.ElementTree as ET
from scrapers.utils import *

from bs4 import BeautifulSoup
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 4. iDNES Scraper
# ---------------------------------------------------------------------------
def scrape_idnes(filters: SearchFilter = None) -> pd.DataFrame:
    SOURCE = "idnes"
    url = f"https://reality.idnes.cz/s/byty/cena-nad-{filters.price_min}-do-{filters.price_max}/{slugify(filters.region)}/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101"
            " Firefox/150.0"
        )
    }
    res = requests.get(url, headers=headers)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    items = soup.find_all("div", class_=re.compile(r"c-products__item"))

    parsed_items = []
    for item in items:
        if "c-products__item--advertisement" in item.get("class", []):
            continue

        title_elem = item.find("h2", class_="c-products__title") or item.find(
            "a", class_="c-products__link"
        )
        if not title_elem:
            continue

        link_elem = (
            item.find("a", class_="c-products__link")
            or title_elem.find("a")
            or title_elem
        )
        title = title_elem.get_text(strip=True)
        href = link_elem.get("href", "")
        listing_url = (
            f"https://reality.idnes.cz{href}"
            if href.startswith("/")
            else href
        )

        item_id_match = re.search(
            r"/detail/[^/]+/[^/]+/([a-f0-9]+)", listing_url
        )
        item_id = item_id_match.group(1) if item_id_match else None

        price_elem = item.find("p", class_="c-products__price")
        price_text = price_elem.get_text(strip=True) if price_elem else ""
        price_val = safe_int(price_text)

        info_elem = item.find("p", class_="c-products__info")
        locality = info_elem.get_text(strip=True) if info_elem else ""

        district_match = re.search(r"Praha\s+\d+", locality)
        district = district_match.group(0) if district_match else None

        img_elem = item.find("img")
        img_url = ""
        if img_elem:
            img_url = (
                img_elem.get("data-src")
                or img_elem.get("src")
                or img_elem.get("srcset", "").split(" ")[0]
            )

        parsed_items.append(
            {
                "unique_id": generate_unique_id(SOURCE, item_id, listing_url),
                "source": SOURCE,
                "id": item_id,
                "title": title,
                "price_raw": price_text,
                "price": price_val,
                "locality": locality,
                "district": district,
                "url": listing_url,
                "image_url": img_url,
            }
        )

    return pd.DataFrame(parsed_items)

