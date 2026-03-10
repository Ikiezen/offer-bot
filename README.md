# Revision Scraper Bot

A Selenium-based automation tool that monitors **irmonitor.cz** for property listings and dispatches categorized alerts to Telegram threads.

## 🛠 Features

* **Asynchronous Engine:** Uses `asyncio` to run the Telegram bot and the Selenium scraper concurrently without blocking.
* **Stateful Persistence:** Uses SQLite to track listings. Each Praha district and category (Prodej, Vsechno) has its own table to manage status (`sent`/`not sent`).
* **Telegram Threading:** Fully supports Telegram's "Topics" (threads), allowing different locations to be streamed into specific sub-channels.
* **Graceful Shutdown:** Implements `signal` handling for `SIGINT` and `SIGTERM` to ensure the database and Selenium drivers close properly.
* **Pickled Printers:** Active "printers" (location monitors) are pickled to disk, allowing the bot to resume monitoring exactly where it left off after a restart.

## 📁 Structure

* `bot/revision.py`: The monolith containing Database, Scraper, LocationPrinter, and TelegramBot classes.
* `locations/`: Stores the SQLite DB and pickled printer states.
* `logs/`: Continuous execution logs.

## <> Execution


1. Ensure a `.env` file exists with `TELEGRAM_BOT_TOKEN`, `IRM_USERNAME`, and `IRM_PASSWORD`.
2. Install Chrome and the matching `chromedriver`.
3. Run the bot:
```bash
python bot/revision.py [--debug]

```
---
