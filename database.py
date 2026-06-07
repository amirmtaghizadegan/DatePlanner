import sqlite3

DB_NAME = "database.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS activities(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        location_id INTEGER,
        active INTEGER DEFAULT 1,
        image_path TEXT,
        FOREIGN KEY(location_id) REFERENCES locations(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS locations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        image_path TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        username TEXT,

        date_id INTEGER,
        activity_id INTEGER,
        location_id INTEGER,

        day_choice TEXT,
        time_choice TEXT,
        date_choice TEXT,
        activity_choice TEXT,
        location_choice TEXT,

        custom_date TEXT,
        custom_activity TEXT,
        custom_location TEXT,

        expires_at TIMESTAMP,
        status TEXT DEFAULT 'active',

        created_at TIMESTAMP
        DEFAULT CURRENT_TIMESTAMP
    )
    """)

    migrate_schema(cur)

    conn.commit()
    conn.close()


def column_exists(cur, table_name, column_name):
    columns = cur.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return any(column["name"] == column_name for column in columns)


def migrate_schema(cur):
    migrations = [
        (
            "activities",
            "location_id",
            "ALTER TABLE activities ADD COLUMN location_id INTEGER"
        ),
        (
            "activities",
            "active",
            "ALTER TABLE activities ADD COLUMN active INTEGER DEFAULT 1"
        ),
        (
            "locations",
            "active",
            "ALTER TABLE locations ADD COLUMN active INTEGER DEFAULT 1"
        ),
        (
            "activities",
            "image_path",
            "ALTER TABLE activities ADD COLUMN image_path TEXT"
        ),
        (
            "locations",
            "image_path",
            "ALTER TABLE locations ADD COLUMN image_path TEXT"
        ),
        (
            "submissions",
            "date_id",
            "ALTER TABLE submissions ADD COLUMN date_id INTEGER"
        ),
        (
            "submissions",
            "activity_id",
            "ALTER TABLE submissions ADD COLUMN activity_id INTEGER"
        ),
        (
            "submissions",
            "location_id",
            "ALTER TABLE submissions ADD COLUMN location_id INTEGER"
        ),
        (
            "submissions",
            "day_choice",
            "ALTER TABLE submissions ADD COLUMN day_choice TEXT"
        ),
        (
            "submissions",
            "time_choice",
            "ALTER TABLE submissions ADD COLUMN time_choice TEXT"
        ),
        (
            "submissions",
            "expires_at",
            "ALTER TABLE submissions ADD COLUMN expires_at TIMESTAMP"
        ),
        (
            "submissions",
            "status",
            "ALTER TABLE submissions ADD COLUMN status TEXT DEFAULT 'active'"
        )
    ]

    for table_name, column_name, statement in migrations:
        if not column_exists(cur, table_name, column_name):
            cur.execute(statement)
