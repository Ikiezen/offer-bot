'''scrapper/inforeality.py
Scrapper for infoREALITY'''

from random import randint
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from . import db_test
import sqlite3
import time


def sanitize(target: str) -> str:
    sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'): '\\-',
                 ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
    return target.translate(sanitizer)


def inforeality_scrapper(locations, credentials):
    driver = webdriver.Chrome()

    # Open the local HTML file

    driver.get("https://www.irmonitor.cz/")

    try:

        login = driver.find_element(By.XPATH, '//*[@id="inpLogin"]')
        password = driver.find_element(By.XPATH, '//*[@id="inpPassword"]')

        login.send_keys(credentials[0])
        password.send_keys(credentials[1] + '\n')

        # Wait for results to load
        results = WebDriverWait(driver, 10).until(
            # Replace with actual class name
            EC.presence_of_element_located((By.CLASS_NAME, "dvAdvertismen"))
        )

        connection = sqlite3.connect('locations/location.db')
        cursor = connection.cursor()
        db_test.initialize_database(cursor)
        openfiles = {location: open(
            f'locations/inforeality_{location}.txt', 'w') for location in locations}
        # Extract and print results
        for body, header in zip(driver.find_elements(By.CLASS_NAME, "dvAdvBody"), driver.find_elements(By.CLASS_NAME, "advBHeader")):
            name = header.find_element(By.CLASS_NAME, "advName")
            link = name.get_attribute('href')

            price = header.find_element(By.CLASS_NAME, "advBPrice")
            details = body.find_element(By.CLASS_NAME, "dvAdvDetalL")

            location, date, contact = details.find_elements(
                By.CLASS_NAME, 'dvAdvDetItmVal')
            location_sanitized = location.text.replace(' ', '_')

            offer = (f'[{name.text}]', f'({link})', f'**price.text**', details.text,
                     location.text, date.text, contact.text)

            for v in openfiles.values():
                misc_file = v
                break

            if location_sanitized in locations:
                print(f'[{sanitize(name.text)}]({sanitize(link)})\n{sanitize(f'**{price.text}**')}\n{sanitize(location.text)}\n{
                      sanitize(contact.text)}\n{sanitize('(' + date.text + ')')}', file=openfiles[location_sanitized], end=";;;\n")
                db_test.insert_offer(cursor, location_sanitized, offer)
            else:
                print(f'[{sanitize(header.text)}]({sanitize(link)})\n{sanitize(f'**{price.text}**')}\n{sanitize(
                    location.text)}\n{sanitize(contact.text)}\n{sanitize('(' + date.text + ')')}', file=misc_file, end=";;;\n")
                db_test.insert_offer(cursor, 'Praha_0', offer)

    finally:
        # Close the WebDriver
        (close(openfile) for openfile in openfiles)
        connection.commit()
        driver.quit()
