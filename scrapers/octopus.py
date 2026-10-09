import html
import re
import xml.etree.ElementTree as ET
import pandas as pd
import requests
from bs4 import BeautifulSoup

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

def octopus_login(email, password, base_url):
    # 1. Initialize session with matched browser headers
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://monitoring.ikarus21.cz",
        "Sec-GPC": "1",
        "Upgrade-Insecure-Requests": "1"
    })

    # 2. Initial GET to fetch cooks/session state & extract hidden fields
    init_response = session.get(base_url)

    # Handle session redirection/rewriting in URL (e.g., /(S(...))/default.aspx)
    target_url = init_response.url

    soup = BeautifulSoup(init_response.text, "html.parser")

    def get_input_val(input_name_or_id):
        field = soup.find("input", {"name": input_name_or_id}) or soup.find("input", {"id": input_name_or_id})
        return field["value"] if field and field.has_attr("value") else ""

    viewstate = get_input_val("__VIEWSTATE")
    viewstate_gen = get_input_val("__VIEWSTATEGENERATOR")
    event_val = get_input_val("__EVENTVALIDATION")

    if not viewstate:
        raise ValueError("Failed to extract __VIEWSTATE from initial page response.")

    # 3. Construct post payload matching the exact browser request structure
    payload = {
        "__EVENTTARGET": "ctl00$cph_left$LogIn1$btn_Internet_LogIn",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstate_gen,
        "__SCROLLPOSITIONX": "0",
        "__SCROLLPOSITIONY": "0",
        "__VIEWSTATEENCRYPTED": "",
        "ctl00$cph_left$LogIn1$txt_UserName": email,
        "ctl00$cph_left$LogIn1$txt_Password": password,
        "ctl00$cph_left$LogIn1$hfReferer": ""
    }

    if event_val:
        payload["__EVENTVALIDATION"] = event_val

    # Update Referer to match exact initial URL context
    session.headers.update({"Referer": target_url})

    # 4. Submit login POST request
    login_response = session.post(target_url, data=payload)

    # 5. Verify authentication state
    if "CURRENT_ID_USER = -1" in login_response.text or "CURRENT_ID_USER=-1" in login_response.text:
        print("Login FAILED: Still unauthenticated (CURRENT_ID_USER = -1).")
    elif "CURRENT_ID_USER" in login_response.text or login_response.status_code == 200:
        print(f"Request completed. Response URL: {login_response.url}")
        if "CURRENT_ID_USER = -1" not in login_response.text:
            print("Login SUCCESSFUL!")
            return login_response
        else:
            print("Login FAILED.")

# ---------------------------------------------------------------------------
# 3. Octopus Scraper
# ---------------------------------------------------------------------------
ESSENTIAL_MAP = {
    "IdHit": "id",
    "HyperlinkText": "title",
    "Price": "price",
    "Area": "area_m2",
    "OfferType": "offer_type",
    "SubCategory": "disposition",
    "Locality": "locality",
    "Obec": "city",
    "DateOfFirstRelease": "published_at",
    "ReleaseDate": "release_date",
    "Hyperlink": "url", "Images": "images", "BodyText": "body",
}

USEFUL_MAP = {
    "lat": "latitude",
    "lon": "longitude",
    "Phone": "phone",
    "Email": "email",
    "IsUserOwner": "is_direct_owner",
    "Portal": "source_portal",
    "PriceInCM": "price_cm",
    "IsPriceChanged": "price_changed",
}

TRASH_KEYS = {
    "iRow",
    "ClientSMS",
    "AllowAnnonce",
    "IsInContainer",
    "CommercialMode",
    "nameRA",
    "IdProcess",
    "PhoneRepeatCount",
    "HitRepeatCount",
    "IdCountry",
    "State",
    "AdvertStatus",
    "DateOfLastAction",
    "DateOfAction",
    "generalNote",
    "IdRealAgencyClient",
    "ReasonForClosingAdvert",
    "UserResponsible_IdUser",
    "UserResponsible_Name",
    "UserLogin_Name",
    "IsReminderHit",
    "IsPairing",
    "NewsUpdatePerDay",
    "SchuzkaView",
    "IsHoldAdvert",
    "IdHolder",
    "IdSender",
    "RenderingMode",
    "RenderingSubMode",
    "jtsx",
    "jtsy",
}


def parse_xml_login_response(xml_text: str) -> pd.DataFrame:
    SOURCE = "octopus"
    match = re.search(r"<Root>(.*)</Root>", xml_text, re.DOTALL)
    if not match:
        logger.error("[-] <Root> element not found in response.")
        return pd.DataFrame()

    root = ET.fromstring(match.group(0))
    parsed_items = []
    praha_pattern = re.compile(r"\b(?:Praha|Praze)\s*(\d{1,2})\b", re.IGNORECASE)

    for row in root.findall("row"):
        raw_row = {
            k: html.unescape(v)
            for k, v in row.attrib.items()
            if k not in TRASH_KEYS
        }

        record = {
            target: raw_row.get(source)
            for source, target in ESSENTIAL_MAP.items()
        }

        if not record["published_at"]:
            record["published_at"] = record.pop("release_date", None)
        else:
            record.pop("release_date", None)

        for source, target in USEFUL_MAP.items():
            record[target] = raw_row.get(source)

        record["price"] = safe_int(record.get("price"))
        record["area_m2"] = safe_int(record.get("area_m2"))
        record["price_cm"] = safe_int(record.get("price_cm"))
        record["is_direct_owner"] = (
            str(record.get("is_direct_owner")).lower() == "true"
        )

        body_text = record.get("body") or ""
        body_districts = {
            f"Praha {num}" for num in praha_pattern.findall(body_text)
        }

        loc_sources = filter(
            None, [record.get("city"), record.get("locality")]
        )
        loc_districts = {
            f"Praha {match.group(1)}"
            for src in loc_sources
            for match in [praha_pattern.search(str(src))]
            if match
        }

        record["praha_districts"] = tuple(
            sorted(body_districts | loc_districts)
        )
        record["unique_id"] = generate_unique_id(
            SOURCE, record.get("id"), record.get("url", "")
        )
        record["source"] = SOURCE

        parsed_items.append(record)

    return pd.DataFrame(parsed_items)
