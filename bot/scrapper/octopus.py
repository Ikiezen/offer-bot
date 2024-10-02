from random import randint
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from . import db
import re

def scrapper(database, locations, credentials, mock=False):
    chrome_options = Options()

    if not mock:
        pass
        # chrome_options.add_argument("--headless")  # Enable headless mode
    # Disable GPU acceleration (needed for some OSes)
    chrome_options.add_argument("--disable-gpu")
    # Set window size (optional)
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=chrome_options)

    # # Open the local HTML file
    # if not mock:
    #     driver.get("https://www.irmonitor.cz/")
    # else:
    #     driver.get("file:///home/ikiezen/Downloads/inforeality_mock.html")

    driver.get('https://ikarus21.cz/Login')
    try:
        if not mock:
            login = driver.find_element(By.XPATH, '//*[@id="UserName"]')
            password = driver.find_element(By.XPATH, '//*[@id="Password"]')

            login.send_keys(credentials[0])
            password.send_keys(credentials[1] + '\n')

        try:
            a = WebDriverWait(driver, 10, poll_frequency=1).until(EC.element_to_be_clickable((
                By.XPATH, '//*[@id="skeletHeader"]/nav/div/div/div')))
            a.click()
            b = WebDriverWait(driver, 10, poll_frequency=1).until(EC.element_to_be_clickable((
                By.XPATH, '//*[@id="skeletHeader"]/nav/ul/li[5]/a')))
            b.click()
            driver.switch_to.frame('octopFrame100')
            wait = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="centralPage"]')), "Items not found!")
            #TODO: THIS PIECE OF SHIT IS NOT DETECTED. FIX ASAP

            offers = driver.find_elements(By.CSS_SELECTOR, '.advertHolder')

            for offer in offers:
                header = offer.find_element(
                    by=By.CSS_SELECTOR, value='.header')
                price = offer.find_element(
                    by=By.CSS_SELECTOR, value='.price')
                contact = offer.find_element(
                    by=By.CSS_SELECTOR, value='.phone')
                link = offer.find_element(
                    by=By.CSS_SELECTOR, value='.sourceLink')
                location = re.match(r'.*Praha [0-9]{2}', header.text)


                offer = (header.text, link.text, price.text, ' ',
        location.text, date.text, contact.text)


        except Exception as e:
            print(f'Not scraped!\n{e}')
            # driver.quit()
    except Exception as e:
        print(e)
    finally:
        if not driver.session_id is None:
            print('Octopus scrapping is done')
            # driver.quit()
