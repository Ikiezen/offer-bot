'''scrapper/inforeality.py
Scrapper for infoREALITY'''

from random import randint
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from . import db
import time


def sanitize(target: str) -> str:
    sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'): '\\-',
                 ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
    return target.translate(sanitizer)


def scrapper(database, locations, credentials, mock=False):
    chrome_options = Options()

    if not mock:
        chrome_options.add_argument("--headless")  # Enable headless mode
    # Disable GPU acceleration (needed for some OSes)
    chrome_options.add_argument("--disable-gpu")
    # Set window size (optional)
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=chrome_options)

    # Open the local HTML file
    if not mock:
        driver.get("https://www.irmonitor.cz/")
    else:
        driver.get("file:///home/ikiezen/Downloads/inforeality_mock.html")

    try:
        if not mock:
            login = driver.find_element(By.XPATH, '//*[@id="inpLogin"]')
            password = driver.find_element(By.XPATH, '//*[@id="inpPassword"]')

            login.send_keys(credentials[0])
            password.send_keys(credentials[1] + '\n')

            try:
                prev = WebDriverWait(driver, 1).until(EC.presence_of_element_located((
                    By.XPATH, '// *[@id = "ctl00_btShowLast"]')))
                prev.click()
            except:
                a = WebDriverWait(driver, 1).until(EC.presence_of_element_located((
                    By.XPATH, '//*[@id="mnNavlist"]/li[1]/a')))

                a.click()

                select_elem = WebDriverWait(driver, 3).until(EC.presence_of_element_located((
                    By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]')))
                select_elem.click()
                option = driver.find_element(
                    By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_ddlAge_chosen"]/div/ul/li[2]')
                option.click()

                # is rental demand
                # a = driver.find_element(
                #     By.XPATH, '//*[@id = "ctl00_MainContent_tbcFilter_tbpAdvanced_ddlActivity"]/option[5]')
                # a.click()

                a = WebDriverWait(driver, 1).until(EC.presence_of_element_located((
                    By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_tbPriceFrom"]')))

                a.send_keys('10000')

                a = WebDriverWait(driver, 1).until(EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="ctl00_MainContent_tbcFilter_tbpAdvanced_btShow"]')))

                a.click()

        # Wait for results to load
        results = WebDriverWait(driver, 10).until(
            # Replace with actual class name
            EC.presence_of_element_located((By.CLASS_NAME, "dvAdvertismen"))
        )

        # database = db.Database()
        # openfiles = {location: open(
        #     f'locations/inforeality_{location}.txt', 'w') for location in locations}
        # Extract and print results
        for body, header in zip(driver.find_elements(By.CLASS_NAME, "dvAdvBody"), driver.find_elements(By.CLASS_NAME, "advBHeader")):
            name = header.find_element(By.CLASS_NAME, "advName")
            link = name.get_attribute('href')

            price = header.find_element(By.CLASS_NAME, "advBPrice")
            details = body.find_element(By.CLASS_NAME, "dvAdvDetalL")

            location, date, contact = details.find_elements(
                By.CLASS_NAME, 'dvAdvDetItmVal')
            location_sanitized = location.text.replace(' ', '_')

            offer = (name.text, link, price.text, details.text,
                     location.text, date.text, contact.text)

            # for v in openfiles.values():
            #     misc_file = v
            #     break
            is_sale = 'prodam' in name.text or 'prodej' in name.text,lower()
            is_have_no_provision = 'bez realitky' in name.text.lower()
            if location_sanitized in locations:
                if is_have_no_provision:
                    database.insert_offer('Bez_realitky', offer)
                elif is_sale:
                    database.insert_offer('Prodej', offer)

                else:
                # print(f'[{sanitize(name.text)}]({sanitize(link)})\n{sanitize(f'**{price.text}**')}\n{sanitize(location.text)}\n{
                #       sanitize(contact.text)}\n{sanitize('(' + date.text + ')')}', file=openfiles[location_sanitized], end=";;;\n")
                    database.insert_offer(location_sanitized, offer)
            else:
                # print(f'[{sanitize(header.text)}]({sanitize(link)})\n{sanitize(f'**{price.text}**')}\n{sanitize(
                #     location.text)}\n{sanitize(contact.text)}\n{sanitize('(' + date.text + ')')}', file=misc_file, end=";;;\n")
                database.insert_offer('Vsechno', offer)
        # (close(openfile) for openfile in openfiles)
        database.commit()
        # print(database.read_offers("Praha_1"))


    finally:
        # Close the WebDriver
        driver.quit()
        print('Scrapping is done')
