'''DB module'''

import sqlite3

connection = sqlite3.connect(
    '/home/ikiezen/dev/dev-ikiezen/hlam/pydir/telegram_bot/bot/locations/location.db')

cursor = connection.cursor()


def initialize_database(cursor):
    locations = [f'Praha_{i}' for i in range(0, 11)]
    for location in locations:
        create_location_tables = f'''
        CREATE TABLE IF NOT EXISTS {location} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                header TEXT NOT NULL,
                link UNIQUE TEXT,
                price TEXT,
                details TEXT,
                location TEXT,
                date DATETIME DEFAULT CURRENT_TIMESTAMP,
                contact TEXT
        );
        '''
        cursor.execute(create_location_tables)


def insert_offer(cursor, location, offer):
    insert_query = f'''
    INSERT INTO {location} (header, link, price, details, location, date, contact) VALUES ( ? , ?, ?, ? ,?, ?, ? )
    '''
    cursor.execute(insert_query, offer)


def read_offers(location: list, cursor=cursor) -> list:
    read_query = f'''SELECT header, link, price, location, date, contact from {
        location}'''
    cursor.execute(read_query)
    result = cursor.fetchall()
    return result


def close():
    connection.close()


cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

connection.commit()
