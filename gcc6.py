import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from concurrent.futures import ThreadPoolExecutor
import threading
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# Lock for thread-safe data access
lock = threading.Lock()
collected_data = []

def setup_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def micro_scroll(driver, steps=10, step_size=50, wait_per_scroll=0.5):
    for _ in range(steps):
        driver.execute_script(f"window.scrollBy(0, {step_size});")
        time.sleep(wait_per_scroll)

def scroll_until_end(driver,
                     micro_scroll_steps=10,
                     micro_scroll_step_size=50,
                     micro_scroll_wait=0.5,
                     big_scroll_step=300,
                     wait_after_big_scroll=2,
                     max_idle_loops=6,
                     max_wait_for_new=20):
    idle_loops = 0
    prev_count = 0
    wait = WebDriverWait(driver, max_wait_for_new)
    
    while idle_loops < max_idle_loops:
        micro_scroll(driver, steps=micro_scroll_steps, step_size=micro_scroll_step_size, wait_per_scroll=micro_scroll_wait)
        driver.execute_script(f"window.scrollBy(0, {big_scroll_step});")
        time.sleep(wait_after_big_scroll)

        try:
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "h3.elementor-heading-title.elementor-size-default a")) > prev_count)
            current_count = len(driver.find_elements(By.CSS_SELECTOR, "h3.elementor-heading-title.elementor-size-default a"))
            print(f"New content loaded: {current_count} titles found.")
            prev_count = current_count
            idle_loops = 0
        except:
            idle_loops += 1
            print(f"No new content loaded. Idle loop {idle_loops} of {max_idle_loops}")

    print("Reached end of content or max idle loops.")

def get_caravan_data(driver):
    titles_elements = driver.find_elements(By.CSS_SELECTOR, "h3.elementor-heading-title.elementor-size-default a")
    prices_elements = driver.find_elements(By.CSS_SELECTOR, "div.jet-listing-dynamic-field__content")
    url_elements = driver.find_elements(By.CSS_SELECTOR, "a.elementor-button.elementor-button-link.elementor-size-sm")
    image_elements = driver.find_elements(By.CSS_SELECTOR, "img.attachment-medium_large")

    titles = [el.text.strip() for el in titles_elements if el.text.strip()]
    prices = [el.text.strip() for el in prices_elements if el.text.strip()]
    urls = [el.get_attribute("href") for el in url_elements if el.get_attribute("href")]
    images = [el.get_attribute("src") for el in image_elements if el.get_attribute("src")]

    length = min(len(titles), len(prices), len(urls), len(images))
    data = []
    for i in range(length):
        data.append({
            "title": titles[i],
            "price": prices[i],
            "url": urls[i],
            "image": images[i]
        })
    return data

def get_caravan_details(caravan, idx, start_num):
    driver = setup_driver()
    driver.get(caravan["url"])
    time.sleep(5)

    try:
        price_elements = driver.find_elements(By.CSS_SELECTOR, "div.jet-listing-dynamic-field__content")
        prices = [el.text.strip() for el in price_elements if "$" in el.text]
        was_price = prices[0] if len(prices) > 0 else "N/A"
        now_price = prices[1] if len(prices) > 1 else "N/A"
    except Exception:
        was_price, now_price = "N/A", "N/A"

    try:
        status_element = driver.find_element(By.CSS_SELECTOR, 'a[href*="/condition/"] .elementor-button-text')
        status = status_element.text.strip()
    except Exception:
        status = "N/A"

    try:
        image_links = driver.find_elements(By.CSS_SELECTOR, "div.elementor-image-carousel a[href]")
        image_urls = [img.get_attribute("href") for img in image_links if img.get_attribute("href").endswith(".jpg")]
    except Exception:
        image_urls = []

    try:
        description_paragraphs = driver.find_elements(By.CSS_SELECTOR, "div.elementor-widget-container p")
        description = " ".join(p.text.strip() for p in description_paragraphs if p.text.strip())
    except Exception:
        description = "N/A"

    specifications = {}
    try:
        spec_rows = driver.find_elements(By.CSS_SELECTOR, "table.jet-table tbody tr")
        for row in spec_rows:
            try:
                key = row.find_element(By.CSS_SELECTOR, "td:nth-child(1)").text.strip()
                value = row.find_element(By.CSS_SELECTOR, "td:nth-child(2)").text.strip()
                specifications[key] = value
            except Exception:
                continue
    except Exception:
        pass

    sku_suffix = "-N" if "new" in status.lower() else "-U"
    sku_number = str(start_num + idx).zfill(4)
    sku_code = f"CFS{sku_suffix}-{sku_number}"

    combined_data = {
        "SKU CODE": sku_code,
        "title": caravan["title"],
        "list_price": caravan["price"],
        "listing_url": caravan["url"],
        "listing_image": caravan["image"],
        "was_price": was_price,
        "now_price": now_price,
        "status": status,
        "description": description,
        "detail_images": ", ".join(image_urls),
    }
    combined_data.update(specifications)

    with lock:
        collected_data.append(combined_data)

    driver.quit()

def save_to_google_sheet(data):
    spreadsheet_id = "1Pbf69WcxQCsejhIbt3ar7k6VIbuLtO88ONxfmMJ4BNk"
    now = datetime.now()
    sheet_title = f"SCRAPPED DATA {now.strftime('%d/%m/%Y %H:%M')}"

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("WLX_Credentials.json", scopes=scope)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
    except Exception as e:
        print("Failed to open spreadsheet:", e)
        return

    try:
        sheet = spreadsheet.add_worksheet(title=sheet_title, rows="1000", cols="50")
    except Exception as e:
        print("Failed to add worksheet:", e)
        return

    headers = list(data[0].keys())
    sheet.append_row(headers)
    rows = [list(row.values()) for row in data]
    sheet.append_rows(rows)

    print(f"✅ Data saved to existing Google Sheet → New tab: {sheet_title}")

def main():
    url = "https://gccaravans.com.au/caravans-for-sale/"
    start_num = int(input("Enter starting SKU number (e.g., 50): "))

    driver = setup_driver()
    driver.get(url)

    print("Scrolling to load all listings...")
    scroll_until_end(driver)

    print("Getting listing data...")
    caravans = get_caravan_data(driver)
    driver.quit()

    print(f"Found {len(caravans)} listings. Scraping with 3 threads...\n")

    with ThreadPoolExecutor(max_workers=3) as executor:
        for idx, caravan in enumerate(caravans):
            executor.submit(get_caravan_details, caravan, idx, start_num)

    # Wait for all threads to finish
    while threading.active_count() > 1:
        time.sleep(1)

    save_to_google_sheet(collected_data)

if __name__ == "__main__":
    main()
