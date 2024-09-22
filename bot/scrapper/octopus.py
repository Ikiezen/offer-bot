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
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((
                By.XPATH, '//*[@id="skeletHeader"]/nav/div/div/div'))).click()
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((
                By.XPATH, '//*[@id="skeletHeader"]/nav/ul/li[5]/a'))).click()


            wait = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.advertHolder')))

            headers = driver.find_elements(
                by=By.CSS_SELECTOR, value='.advertHolder .text .header')
            price = driver.find_elements(
                by=By.CSS_SELECTOR, value='.advertHolder .text .price')
            contact = driver.find_elements(
                by=By.CSS_SELECTOR, value='.advertHolder .text .phone')
            source = driver.find_elements(
                by=By.CSS_SELECTOR, value='.advertHolder .text .sourceLink')
        except Exception as e:
            print(f'Not scraped!\n\n{e}')
    except Exception as e:
        print(e)
    finally:
        if not mock:
            driver.quit()
        print('Octopus scrapping is done')
