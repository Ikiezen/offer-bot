import re
import pandas as pd
import requests
from bs4 import BeautifulSoup

def fetch_and_parse_offers(page: int = 1) -> pd.DataFrame:
    url = f"https://domonaut.cz/hledani/vysledky/Pron%C3%A1jem/Byt?lokalita=Praha&cenaOd=10000&cenaDo=200000&bezRK=1&page={page}"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://domonaut.cz/hledani/vysledky/Pron%C3%A1jem/Byt?bezRK=1",
        "Sec-GPC": "1",
    }

    # Execute request
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    # Extract HTML string from JSON response
    html_content = response.text
    breakpoint()
    if not html_content:
        return pd.DataFrame()

    # Parse HTML content
    soup = BeautifulSoup(html_content, "html.parser")
    cards = soup.find_all("div", class_="offer-card")
    breakpoint()

    parsed_items = []
    for card in cards:
        a_tag = card.find("a", class_="offer-box")
        if not a_tag:
            continue

        href = a_tag.get("href", "")
        detail_url = f"https://www.bezrealitky.cz{href}" if href else ""
        detail_id = href.rstrip("/").split("/")[-1] if href else None

        img_tag = card.find("img")
        img_url = img_tag.get("src", "") if img_tag else ""

        # Price & Utility Fees
        price_div = card.find("div", class_="offerPrice")
        price_val, fees_val = None, None

        if price_div:
            price_span = price_div.find("span", class_="bold")
            if price_span:
                p_clean = re.sub(r"[^\d]", "", price_span.get_text())
                price_val = int(p_clean) if p_clean else None

            fees_abbr = price_div.find("abbr", title="poplatky")
            if fees_abbr:
                fees_span = fees_abbr.find("span", class_="bold")
                if fees_span:
                    f_clean = re.sub(r"[^\d]", "", fees_span.get_text())
                    fees_val = int(f_clean) if f_clean else None

        # Layout & Area
        details_list = card.find("ul", class_="details-list")
        layout, area_m2 = None, None
        if details_list:
            items = details_list.find_all("li")
            if len(items) >= 1:
                layout = items[0].get_text(strip=True)
            if len(items) >= 2:
                area_text = items[1].get_text(strip=True)
                area_clean = re.sub(r"[^\d]", "", area_text)
                area_m2 = int(area_clean) if area_clean else None

        # Address & District
        address_tag = card.find("address")
        address = address_tag.get_text(strip=True) if address_tag else ""

        district_match = re.search(r"Praha\s*(?:–|-)?\s*([^\n,]+)", address)
        district = district_match.group(0) if district_match else None

        parsed_items.append(
            {
                "id": detail_id,
                "layout": layout,
                "area_m2": area_m2,
                "price": price_val,
                "fees": fees_val,
                "address": address,
                "district": district,
                "url": detail_url,
                "image_url": img_url,
            }
        )

    return pd.DataFrame(parsed_items)


# Fetch and display page 1 results
df = fetch_and_parse_offers(page=1)
print(df)
