import sqlite3
import datetime
import os
import hashlib
import threading
from passlib.context import CryptContext

DB_NAME = "esphera_pro.db"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
db_lock = threading.Lock()

def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10.0, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def reset_db():
    """Całkowite czyszczenie bazy i re-inicjalizacja"""
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Pobieramy nazwy wszystkich tabel
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = cursor.fetchall()
        
        # Wyłączamy klucze obce na chwilę, żeby móc usunąć tabele w dowolnej kolejności
        cursor.execute("PRAGMA foreign_keys = OFF")
        for table in tables:
            cursor.execute(f"DROP TABLE IF EXISTS {table['name']}")
        
        conn.commit()
        conn.close()
        print("[DB] Wszystkie tabele usunięte.")
    
    # Tworzymy tabele na nowo
    init_db()

def init_db():
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                user_id INTEGER,
                aes_key BLOB,
                status TEXT CHECK(status IN ('pending', 'paired')) DEFAULT 'pending',
                last_seen TIMESTAMP,
                rssi INTEGER DEFAULT -60, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                device_id TEXT PRIMARY KEY,
                wifi_ssid TEXT,
                wifi_password TEXT,
                sound_model_id TEXT DEFAULT 'alloy',
                led_intensity INTEGER DEFAULT 50,
                ai_model TEXT DEFAULT 'gpt-4o-mini',
                system_prompt TEXT DEFAULT 'Jesteś pomocnym asystentem głosowym.',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                type TEXT CHECK(type IN ('IN', 'OUT', 'SYS')),
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
        ''')
        conn.commit()
        conn.close()
        print(f"[DB] Baza {DB_NAME} (PRO v3) gotowa.")

# --- USER FUNCTIONS ---

def create_user(email, name, password):
    with db_lock:
        conn = get_connection()
        try:
            hashed_pw = pwd_context.hash(password)
            conn.execute("INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                         (email, name, hashed_pw))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # Email zajęty
        finally:
            conn.close()

def get_user_by_email(email):
    with db_lock:
        conn = get_connection()
        try:
            cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            return cur.fetchone()
        finally:
            conn.close()

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

# --- DEVICE FUNCTIONS ---
def register_or_claim_device(device_id, aes_key, user_email):
    with db_lock:
        conn = get_connection()
        try:
            cur = conn.execute("SELECT id FROM users WHERE email = ?", (user_email,))
            user = cur.fetchone()
            if not user: return False
            user_id = user['id']

            cur = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            existing = cur.fetchone()

            if existing:
                conn.execute("UPDATE devices SET user_id = ?, aes_key = ?, status = 'pending' WHERE device_id = ?", 
                             (user_id, aes_key, device_id))
                conn.execute("INSERT INTO logs (device_id, type, message) VALUES (?, ?, ?)", 
                             (device_id, "SYS", f"Zmiana właściciela na {user_email}"))
            else:
                conn.execute("INSERT INTO devices (device_id, user_id, aes_key, status) VALUES (?, ?, ?, 'pending')", 
                             (device_id, user_id, aes_key))
                conn.execute("INSERT INTO config (device_id) VALUES (?)", (device_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] Claim error: {e}")
            return False
        finally:
            conn.close()

def update_device_config(device_id, led_val, ai_model, sys_prompt):
    """Zapisuje nową konfigurację (LED + AI)"""
    with db_lock:
        conn = get_connection()
        try:
            conn.execute('''
                UPDATE config 
                SET led_intensity = ?, ai_model = ?, system_prompt = ? 
                WHERE device_id = ?
            ''', (led_val, ai_model, sys_prompt, device_id))
            conn.commit()
        finally:
            conn.close()

def update_device_rssi(device_id, rssi):
    """Aktualizuje siłę sygnału WiFi"""
    with db_lock:
        conn = get_connection()
        try:
            conn.execute("UPDATE devices SET rssi = ?, last_seen = ? WHERE device_id = ?", 
                         (rssi, datetime.datetime.now(), device_id))
            conn.commit()
        finally:
            conn.close()

def get_full_device_info(device_id):
    """Pobiera config + klucze (dla backendu AI)"""
    with db_lock:
        conn = get_connection()
        try:
            cur = conn.execute('''
                SELECT d.aes_key, c.ai_model, c.system_prompt 
                FROM devices d 
                JOIN config c ON d.device_id = c.device_id 
                WHERE d.device_id = ?
            ''', (device_id,))
            return cur.fetchone()
        finally:
            conn.close()

def get_user_devices(user_email):
    with db_lock:
        conn = get_connection()
        try:
            cur = conn.execute('''
                SELECT d.*, c.led_intensity, c.ai_model, c.system_prompt 
                FROM devices d 
                LEFT JOIN config c ON d.device_id = c.device_id
                LEFT JOIN users u ON d.user_id = u.id
                WHERE u.email = ?
            ''', (user_email,))
            return cur.fetchall()
        finally:
            conn.close()

def activate_device(device_id):
    with db_lock:
        conn = get_connection()
        conn.execute("UPDATE devices SET status = 'paired', last_seen = ? WHERE device_id = ?", (datetime.datetime.now(), device_id))
        conn.commit()
        conn.close()

def get_device_auth(device_id):
    with db_lock:
        conn = get_connection()
        cur = conn.execute("SELECT aes_key, status FROM devices WHERE device_id = ?", (device_id,))
        return cur.fetchone()

def update_device_status(device_id, status):
    with db_lock:
        conn = get_connection()
        conn.execute("UPDATE devices SET status = ? WHERE device_id = ?", (status, device_id))
        conn.commit()
        conn.close()

def get_all_pending_devices():
    with db_lock:
        conn = get_connection()
        cur = conn.execute("SELECT device_id, aes_key FROM devices WHERE status = 'pending' ORDER BY created_at ASC")
        devices = cur.fetchall()
        conn.close()
        return devices

def log_event(device_id, type, message):
    with db_lock:
        conn = get_connection()
        conn.execute("INSERT INTO logs (device_id, type, message) VALUES (?, ?, ?)", (device_id, type, message))
        conn.commit()
        conn.close()