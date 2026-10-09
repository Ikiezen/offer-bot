import json
import re
import urllib.parse
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


def get_sreality_build_id(session: requests.Session) -> str:
    """Extracts buildId from HTML matching: grep -oP '"buildId":"\K[^"]+'"""
    url = "https://www.sreality.cz/hledani/pronajem/byty/praha"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = session.get(url, headers=headers)
    res.raise_for_status()

    match = re.search(r'"buildId"\s*:\s*"([^"]+)"', res.text)
    if not match:
        raise ValueError("Failed to extract buildId from Sreality HTML.")
    return match.group(1)


def build_sreality_url(item: dict) -> str:
    """Constructs valid Sreality detail URL from item metadata."""
    item_id = item.get("id")

    cat_type = (
        item.get("categoryTypeCb", {}).get("name", "pronajem").strip().lower()
    )
    transaction_slug = "pronajem" if "pron" in cat_type else "prodej"

    cat_main = (
        item.get("categoryMainCb", {}).get("name", "byt").strip().lower()
    )
    property_slug = "byt" if "byt" in cat_main else "dum"

    disposition_raw = item.get("categorySubCb", {}).get("name", "").strip()
    disposition_slug = urllib.parse.quote(disposition_raw, safe="+")

    locality = item.get("locality", {})
    city_seo = locality.get("citySeoName", "")
    district_seo = locality.get("districtSeoName", "")

    if city_seo and district_seo and city_seo != district_seo:
        location_slug = f"{city_seo}-{district_seo}"
    else:
        location_slug = city_seo or district_seo or "praha"

    location_slug = re.sub(r"-+", "-", location_slug).strip("-")
    return f"https://www.sreality.cz/detail/{transaction_slug}/{property_slug}/{disposition_slug}/{location_slug}/{item_id}"


def fetch_sreality_dataframe() -> pd.DataFrame:
    session = requests.Session()

    # 1. Get live buildId
    build_id = get_sreality_build_id(session)
    print(f"[+] Active Build ID: {build_id}")

    # 2. Fetch JSON endpoint using build_id
    url = (
        f"https://www.sreality.cz/_next/data/{build_id}/cs/hledani/byty/"
        "praha-1,praha-10,praha-2,praha-3,praha-4,praha-5,praha-6,praha-7,praha-8,praha-9.json"
    )

    params = {
        "cena-od": "10000",
        "cena-do": "200000",
        "slug": [
            "byty",
            "praha-1,praha-10,praha-2,praha-3,praha-4,praha-5,praha-6,praha-7,praha-8,praha-9",
        ],
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101"
            " Firefox/150.0"
        ),
        "Accept": "*/*",
        "x-nextjs-data": "1",
    }

    res = session.get(url, params=params, headers=headers)
    res.raise_for_status()
    payload = res.json()

    # 3. Extract items from dehydratedState
    queries = (
        payload.get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )

    raw_results = []
    for query in queries:
        data = query.get("state", {}).get("data", {})
        if isinstance(data, dict) and "results" in data:
            raw_results = data["results"]
            break

    # 4. Parse records into flat dicts
    parsed_items = []
    for item in raw_results:
        item_id = item.get("id")
        loc = item.get("locality", {})

        locality_str = ", ".join(
            filter(None, [loc.get("street"), loc.get("cityPart"), loc.get("city")])
        ) or loc.get("district", "")

        praha_match = re.search(
            r"Praha\s+\d+", f"{loc.get('city')} {loc.get('district')} {locality_str}"
        )
        praha_district = praha_match.group(0) if praha_match else None

        parsed_items.append(
            {
                "id": item_id,
                "title": item.get("name"),
                "price": item.get("priceCzk"),
                "category_sub": item.get("categorySubCb", {}).get("name"),
                "offer_type": item.get("categoryTypeCb", {}).get("name"),
                "locality": locality_str,
                "praha_district": praha_district,
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "url": build_sreality_url(item),
                "source": "sreality",
                "unique_id": f"sreality_{item_id}",
            }
        )

    return pd.DataFrame(parsed_items)


if __name__ == "__main__":
    df = fetch_sreality_dataframe()
    print(f"\n[+] Extracted {len(df)} rows into DataFrame:")
    print(df[["id", "title", "price", "praha_district", "url"]].head())
