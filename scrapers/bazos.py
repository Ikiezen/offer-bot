from dataclasses import dataclass, field
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote, urlparse
from scrapers.utils import *
from bs4 import BeautifulSoup

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



# ---------------------------------------------------------------------------
# 1. Bazoš Scraper
# ---------------------------------------------------------------------------
def scrape_bazos_flats(
    filters: SearchFilter, page: int = 0
) -> pd.DataFrame:
    SOURCE = "bazos"
    base_path = f"/byt/{page * 20}/" if page > 0 else "/byt/"
    url = f"https://reality.bazos.cz/pronajmu{base_path}"

    params = {
        "hledat": "",
        "rubriky": "reality",
        "hlokalita": filters.zip_code,
        "humkreis": filters.radius_km,
        "cenaod": filters.price_min,
        "cenado": filters.price_max,
        "Submit": "Hledat",
        "order": "",
        "crp": "",
        "kitx": "ano",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101"
            " Firefox/150.0"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://reality.bazos.cz/pronajmu/byt/",
        "Sec-GPC": "1",
    }

    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.find_all("div", class_="inzeraty")

    parsed_items = []
    for card in cards:
        a_tag = card.select_one(".inzeratynadpis h2.nadpis a") or card.select_one(
            ".inzeratynadpis a"
        )
        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        listing_url = (
            f"https://reality.bazos.cz{href}" if href.startswith("/") else href
        )

        id_match = re.search(r"/inzerat/(\d+)/", href)
        item_id = int(id_match.group(1)) if id_match else None

        img_tag = card.select_one(".inzeratynadpis img.obrazek")
        image_url = img_tag.get("src", "") if img_tag else ""

        is_top = card.select_one(".ztop") is not None
        date_elem = card.select_one(".velikost10")
        date_str = ""
        if date_elem:
            date_match = re.search(r"\[([\d\.\s]+)\]", date_elem.get_text())
            date_str = date_match.group(1).strip() if date_match else ""

        desc_elem = card.select_one(".popis")
        description = desc_elem.get_text(strip=True) if desc_elem else ""

        price_elem = card.select_one(".inzeratycena")
        price_raw = price_elem.get_text(strip=True) if price_elem else ""
        price_val = safe_int(price_raw)

        loc_elem = card.select_one(".inzeratylok")
        locality_raw = (
            loc_elem.get_text(separator="\n", strip=True) if loc_elem else ""
        )
        loc_parts = [p.strip() for p in locality_raw.split("\n") if p.strip()]

        district = loc_parts[0] if len(loc_parts) > 0 else ""
        zip_code_val = loc_parts[1] if len(loc_parts) > 1 else ""

        praha_match = re.search(r"Praha\s+\d+", district)
        praha_district = praha_match.group(0) if praha_match else None

        views_elem = card.select_one(".inzeratyview")
        views_raw = views_elem.get_text(strip=True) if views_elem else ""
        views_count = safe_int(views_raw)

        parsed_items.append(
            {
                "unique_id": generate_unique_id(SOURCE, item_id, listing_url),
                "source": SOURCE,
                "id": item_id,
                "title": title,
                "price": price_val,
                "price_raw": price_raw,
                "district": district,
                "praha_district": praha_district,
                "zip_code": zip_code_val,
                "is_top": is_top,
                "date": date_str,
                "views": views_count,
                "description": description,
                "url": listing_url,
                "image_url": image_url,
            }
        )

    return pd.DataFrame(parsed_items)
