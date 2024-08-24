"""This bot can scrape https://reality.bazos.cz/inzeraty/"""


def sanitize(target : str ) -> str:
    sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'):'\\-',  ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
    return target.translate(sanitizer).replace("<br>", ', CZ\\-')

def scrape_for_offers(scrape_funcs : dict):
    for func,args in scrape_funcs.items():
        func(*args)

def bazos_scrapper(locations, pagenum):
    service = webdriver.ChromeService(port=3213)
    options = webdriver.ChromeOptions()
    options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(service=service, options=options)

    driver.get("https://reality.bazos.cz/")

    def save_offer(offerfile, driver):
        """Scan one page"""
        offers = driver.find_elements(By.CSS_SELECTOR, "div.inzeraty.inzeratyflex")

        for offer in offers:
            link, description, price, location = (
                offer.find_element(By.CSS_SELECTOR, "div.inzeratynadpis a"),
                offer.find_element(By.CSS_SELECTOR, "div.inzeratynadpis h2"),
                offer.find_element(By.CSS_SELECTOR, "div.inzeratycena b"),
                offer.find_element(By.CSS_SELECTOR, "div.inzeratylok"),
            )

            offerfile.write(
                f"""*[{sanitize(description.text)}]({sanitize(link.get_property("href"))})*
            *Cena*:     `{price.text}`
            *Lokalita*: `{sanitize(location.get_attribute("innerHTML"))}`;;;\n"""
        )

    def skip_to_next(driver):

        try:
            next_button = driver.find_element(
                By.CSS_SELECTOR, "div.strankovani a:last-child"
            )
        except:
            next_button = driver.find_element(
                By.CSS_SELECTOR, "div.strankovani span.paction:last-child"
            )

        if next_button.is_displayed():
            next_button.click()


    def scan_full_page(driver):


        for location in locations:
            driver.get("https://reality.bazos.cz/")
            location_searchfield = driver.find_element(by=By.CSS_SELECTOR, value="input#hlokalita")
            try:
                location_searchfield.send_keys(f'{location}\n')
                location_variant = driver.find_element(By.CSS_SELECTOR, 'table.tablenaspsc tr:nth-child(2)')
                location_variant.click()
            except:
                pass
            try:
                reject_cookies_button = driver.find_element(By.CSS_SELECTOR, "button.cc-nb-reject")
                reject_cookies_button.click()
            except:
                pass


            with open(f"offers_{location}.md", "w", encoding="utf8") as offerfile:
                for i in range(pagenum):
                    save_offer(offerfile,driver)
                    skip_to_next(driver)
                    driver.implicitly_wait(randint(1, 20) / 10)


    scan_full_page(driver=driver)
    driver.quit()




if __name__ == "__main__":

    # locations = ('Praha', 'Brno', 'Olomouc')
    # pagenum = 5

    # bazos_scrapper = bazos_scrapper

    # scrape_for_offers({ bazos_scrapper : (locations, pagenum) })

    pass