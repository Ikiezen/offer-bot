'''DB module'''

import sqlite3


class Database:

    def __init__(self):
        self.connection = sqlite3.connect(
            '/home/ikiezen/dev/dev-ikiezen/hlam/pydir/telegram_bot/bot/locations/location.db', check_same_thread=False)
        self.cursor = self.connection.cursor()

        self.initialize_database()
        self.connection.commit()

    def initialize_database(self):
        locations = [f'Praha_{i}' for i in range(0, 11)]
        for location in locations:
            create_location_tables = f'''
            CREATE TABLE IF NOT EXISTS {location} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    header TEXT NOT NULL,
                    link TEXT UNIQUE,
                    price TEXT,
                    details TEXT,
                    location TEXT,
                    date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    contact TEXT,
                    status TEXT
            );
            '''
            self.cursor.execute(create_location_tables)

    def insert_offer(self, location, offer):
        try:
            insert_query = f'''
            INSERT INTO {location} (header, link, price, details, location, date, contact, status) VALUES ( ? , ?, ?, ? ,?, ?, ? , ? )
            '''
            raw = list(offer)
            raw.append('not sent')
            # print(raw)
            self.cursor.execute(insert_query, tuple(raw))
        except Exception as e:
            pass
            # print(e)

    async def read_offers(self, location: str) -> list:
        read_query = f'''SELECT header, link, price, location, date, contact from {
            location}'''
        self.cursor.execute(read_query)
        result = self.cursor.fetchall()
        return result

    async def get_new(self, location: str) -> list:
        read_query = f'''SELECT header, link, price, location, date, contact, status from {
            location} where status='not sent' LIMIT 5'''
        self.cursor.execute(read_query)
        result = self.cursor.fetchall()
        return result

    async def invalidate(self, offers: list, location: str) -> None:
        for offer in offers:
            invalidate_query = f'''UPDATE {location.replace(
                ' ', '_')} set status = 'sent' where link = "{offer[1]}"'''
            # print("location", invalidate_query)
            self.cursor.execute(invalidate_query)
        self.commit()

    async def make_valid(self, offers: list, location: str) -> None:
        for offer in offers:
            invalidate_query = f'''UPDATE {location.replace(
                ' ', '_')} set status = 'not sent' where link = "{offer[1]}"'''
            # print("location", invalidate_query)
            self.cursor.execute(invalidate_query)
        self.commit()

    def close(self):
        self.connection.close()

    def commit(self):
        self.connection.commit()
