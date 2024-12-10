from random import randint
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from . import db
import time


def sanitize(target: str) -> str:
    """Sanitizes strings for safe usage."""
    sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'): '\\-',
                 ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
    return target.translate(sanitizer)


def setup_driver(mock: bool) -> webdriver.Chrome:
    """Sets up the Chrome WebDriver with necessary options."""
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=chrome_options)
    driver.get(
        "file:///home/ikiezen/Downloads/inforeality_mock.html" if mock else "https://www.irmonitor.cz/"
    )
    return driver


def login(driver, credentials, logger):
    """Handles the login process."""
    try:
        login_field = driver.find_element(By.XPATH, '//*[@id="inpLogin"]')
        password_field = driver.find_element(
            By.XPATH, '//*[@id="inpPassword"]')
        login_field.send_keys(credentials[0])
        password_field.send_keys(credentials[1] + '\n')
    except NoSuchElementException as e:
        logger.error("Login elements not found. Aborting.")
        raise e


def apply_filters(driver, logger):
    """Applies search filters on the website."""
    try:
        menu = WebDriverWait(driver, 3).until(EC.presence_of_element_located(
            (By.XPATH, '//*[@id="mnNavlist"]/li[1]/a')))
        menu.click()

        # Apply additional filters (age range, price range, etc.)
        age_filter = WebDriverWait(driver, 3).until(EC.presence_of_element_located(
            (By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]')))
        age_filter.click()

        age_option = driver.find_element(
            By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]/div/ul/li[2]')
        age_option.click()

        min_price = driver.find_element(
            By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_tbPriceFrom"]')
        max_price = driver.find_element(
            By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_tbPriceTo"]')
        min_price.send_keys('10000')
        max_price.send_keys('1000000')

        show_button = driver.find_element(
            By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_btShow"]')
        show_button.click()

    except TimeoutException as e:
        logger.error("Timeout while applying filters.")
        raise e


def scrape_results(driver, database, locations, logger):
    """Scrapes property data from the website."""
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located(
            (By.CLASS_NAME, "dvAdvertismen")))

        headers = driver.find_elements(By.CLASS_NAME, "advBHeader")
        bodies = driver.find_elements(By.CLASS_NAME, "dvAdvBody")

        for body, header in zip(bodies, headers):
            try:
                name_elem = header.find_element(By.CLASS_NAME, "advName")
                link = name_elem.get_attribute('href')
                price_elem = header.find_element(By.CLASS_NAME, "advBPrice")
                details = body.find_element(By.CLASS_NAME, "dvAdvDetalL")

                location_elem, date_elem, contact_elem = details.find_elements(
                    By.CLASS_NAME, 'dvAdvDetItmVal')

                location_sanitized = location_elem.text.replace(' ', '_')
                offer = (
                    name_elem.text, link, price_elem.text, details.text,
                    location_elem.text, date_elem.text, contact_elem.text
                )

                name_cmp = name_elem.text.lower()
                is_sale = 'prodam' in name_cmp or 'prodej' in name_cmp
                is_have_no_provision = 'bez realitky' in name_cmp

                category = 'Bez_realitky' if is_have_no_provision else 'Prodej' if is_sale else location_sanitized
                database.insert_offer(category, offer)

            except Exception as e:
                logger.warning(f"Failed to process an advertisement: {e}")

        database.commit()

    except TimeoutException as e:
        logger.error("Timeout while loading results.")
        raise e


def scrapper(database, locations, credentials, logger, mock=False):
    """Main function to scrape data."""
    driver = setup_driver(mock)
    try:
        if not mock:
            login(driver, credentials, logger)
            apply_filters(driver, logger)
        scrape_results(driver, database, locations, logger)
    except Exception as e:
        logger.error(f"An error occurred during scraping: {e}")
    finally:
        driver.quit()
        logger.info('Scraping completed.')
