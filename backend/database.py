import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "invoices.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            filename TEXT,
            vendor_name TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            due_date TEXT,
            total_amount REAL DEFAULT 0,
            currency TEXT DEFAULT 'EUR',
            line_items TEXT DEFAULT '[]',
            tax_amount REAL DEFAULT 0,
            subtotal REAL DEFAULT 0,
            payment_terms TEXT,
            notes TEXT,
            status TEXT DEFAULT 'PENDING',
            approver TEXT,
            approver_comment TEXT,
            created_at TEXT,
            updated_at TEXT,
            raw_text TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("✅ Database initialized at", DB_PATH)