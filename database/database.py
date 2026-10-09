import sqlite3
from contextlib import contextmanager

from config import DATA_DIR, DATABASE_PATH, UPLOAD_DIR


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    legacy_images = DATA_DIR / "model_images"
    if legacy_images.is_dir():
        for image_path in legacy_images.iterdir():
            destination = UPLOAD_DIR / image_path.name
            if image_path.is_file() and not destination.exists():
                image_path.replace(destination)
        if not any(legacy_images.iterdir()):
            legacy_images.rmdir()
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                material_type TEXT NOT NULL,
                price_per_kg REAL NOT NULL DEFAULT 0 CHECK(price_per_kg >= 0),
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS printers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                purchase_price REAL NOT NULL DEFAULT 0,
                energy_kw REAL NOT NULL DEFAULT 0,
                depreciation_hours REAL NOT NULL DEFAULT 1,
                maintenance_per_hour REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                sale_price REAL NOT NULL DEFAULT 0,
                image_filename TEXT,
                additional_material_note TEXT NOT NULL DEFAULT '',
                additional_material_cost REAL NOT NULL DEFAULT 0,
                additional_work_hours REAL NOT NULL DEFAULT 0,
                additional_hourly_cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS model_parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                material_id INTEGER NOT NULL,
                printer_id INTEGER NOT NULL,
                weight_g REAL NOT NULL DEFAULT 0,
                print_hours REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                FOREIGN KEY (material_id) REFERENCES materials(id),
                FOREIGN KEY (printer_id) REFERENCES printers(id)
            );
            CREATE TABLE IF NOT EXISTS book_years (
                year INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS expense_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                additional_info TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL CHECK(amount >= 0),
                quantity_kg REAL NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                FOREIGN KEY (year) REFERENCES book_years(year),
                FOREIGN KEY (group_id) REFERENCES expense_groups(id)
            );
            CREATE TABLE IF NOT EXISTS shipping_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                UNIQUE(year, name),
                FOREIGN KEY (year) REFERENCES book_years(year) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                number INTEGER NOT NULL,
                date TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                customer_name TEXT NOT NULL DEFAULT '',
                street TEXT NOT NULL DEFAULT '',
                postal_code TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                model_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                material_id INTEGER,
                printer_id INTEGER,
                item_price REAL NOT NULL DEFAULT 0,
                shipping_method_id INTEGER,
                shipping_price REAL NOT NULL DEFAULT 0,
                packaging_cost REAL NOT NULL DEFAULT 0,
                paid INTEGER NOT NULL DEFAULT 0,
                shipped INTEGER NOT NULL DEFAULT 0,
                printed INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                shipping_date TEXT NOT NULL DEFAULT '',
                UNIQUE(year, number),
                FOREIGN KEY (year) REFERENCES book_years(year),
                FOREIGN KEY (model_id) REFERENCES models(id),
                FOREIGN KEY (material_id) REFERENCES materials(id),
                FOREIGN KEY (printer_id) REFERENCES printers(id),
                FOREIGN KEY (shipping_method_id) REFERENCES shipping_methods(id)
            );
            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL,
                model_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
                material_id INTEGER,
                printer_id INTEGER,
                item_price REAL NOT NULL DEFAULT 0,
                printed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id),
                FOREIGN KEY (material_id) REFERENCES materials(id),
                FOREIGN KEY (printer_id) REFERENCES printers(id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_expenses_year_date ON expenses(year, date);
            CREATE INDEX IF NOT EXISTS idx_sales_year_date ON sales(year, date);
            CREATE INDEX IF NOT EXISTS idx_model_parts_model ON model_parts(model_id);
            CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
            """
        )
        group_translations = (
            ("Printers", "Drucker"),
            ("Tools & Equipment", "Werkzeuge und Ausrüstung"),
            ("Additional material", "Zusatzmaterial"),
        )
        for old_name, new_name in group_translations:
            old_group = connection.execute(
                "SELECT id FROM expense_groups WHERE name=?", (old_name,)
            ).fetchone()
            if old_group is None:
                continue
            new_group = connection.execute(
                "SELECT id FROM expense_groups WHERE name=?", (new_name,)
            ).fetchone()
            if new_group:
                connection.execute(
                    "UPDATE expenses SET group_id=? WHERE group_id=?",
                    (new_group["id"], old_group["id"]),
                )
                connection.execute("DELETE FROM expense_groups WHERE id=?", (old_group["id"],))
            else:
                connection.execute(
                    "UPDATE expense_groups SET name=? WHERE id=?",
                    (new_name, old_group["id"]),
                )
        connection.executemany(
            "INSERT OR IGNORE INTO expense_groups (name) VALUES (?)",
            [(name,) for name in ("Drucker", "Werkzeuge und Ausrüstung", "Filament", "Zusatzmaterial")],
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('electricity_price', '0.35')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('price_calculator_factor', '3')"
        )
        connection.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, '')",
            [(key,) for key in (
                "site_logo", "invoice_logo", "invoice_vendor_name", "invoice_vendor_address",
                "invoice_vendor_phone", "invoice_vendor_email", "invoice_payment_info",
                "invoice_payment_bank", "invoice_payment_iban", "invoice_payment_bic",
                "invoice_tax_number", "invoice_economic_id", "invoice_additional_text",
                "invoice_bold_text",
            )],
        )
        connection.execute(
            "INSERT OR IGNORE INTO book_years (year) VALUES (strftime('%Y', 'now', 'localtime'))"
        )
        expense_columns = {
            column["name"]
            for column in connection.execute("PRAGMA table_info(expenses)").fetchall()
        }
        if "quantity_kg" not in expense_columns:
            connection.execute(
                "ALTER TABLE expenses ADD COLUMN quantity_kg REAL NOT NULL DEFAULT 0"
            )
        sale_columns = {
            column["name"]
            for column in connection.execute("PRAGMA table_info(sales)").fetchall()
        }
        if "shipping_date" not in sale_columns:
            connection.execute("ALTER TABLE sales ADD COLUMN shipping_date TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """UPDATE sales SET printed=1 WHERE id IN (
            SELECT sale_id FROM sale_items GROUP BY sale_id HAVING MIN(printed)=1
            )"""
        )
        model_columns = {
            column["name"]
            for column in connection.execute("PRAGMA table_info(models)").fetchall()
        }
        for column, definition in (
            ("additional_material_note", "TEXT NOT NULL DEFAULT ''"),
            ("additional_material_cost", "REAL NOT NULL DEFAULT 0"),
            ("additional_work_hours", "REAL NOT NULL DEFAULT 0"),
            ("additional_hourly_cost", "REAL NOT NULL DEFAULT 0"),
        ):
            if column not in model_columns:
                connection.execute(f"ALTER TABLE models ADD COLUMN {column} {definition}")