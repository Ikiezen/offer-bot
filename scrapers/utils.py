from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
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
import unicodedata


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def generate_unique_id(
    source: str, item_id: str | int | None, url: str
) -> str:
    if item_id is not None and str(item_id).strip():
        return f"{source}_{str(item_id).strip()}"
    clean_url = str(url).strip()
    if clean_url:
        url_hash = hashlib.md5(clean_url.encode("utf-8")).hexdigest()[:12]
        return f"{source}_{url_hash}"
    return f"{source}_unknown"


def safe_int(value) -> int | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d]", "", str(value))
    return int(cleaned) if cleaned else None


def slugify(text: str) -> str:
    if not text:
        return ""
    text = (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("utf-8")
    )
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).lower()
    return re.sub(r"[\s-]+", "-", text).strip("-")


def normalize_url(url: str) -> str:
    if not url or pd.isna(url):
        return ""
    parsed = urlparse(str(url).strip())
    clean_path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}"
