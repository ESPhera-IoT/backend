import json
import asyncio
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import database
import crypto_utils
import session_manager
import time
import os

# --- KONFIGURACJA ---
BROKER = "127.0.0.1"
PORT = 1883

load_dotenv()

MY_LOCAL_IP = os.getenv("LOCAL_IP")
TCP_PORT = 26358             # Port TCP

# Tematy (Topics)
TOPIC_REGISTER = "/esphera/admin/register"
TOPIC_REQUEST_START = "devices/+/request/start" # Wildcard + oznacza dowolne ID
TOPIC_STATE = "devices/+/state"

client = None # Globalna referencja

# --- LOGIKA BIZNESOWA MQTT ---

TOPIC_PROVISION = "devices/provisioning"
def handle_provisioning(payload_bytes):
    """
    Kula wysyła header z ("ESPHERA|" + (8 bajtów czas)timestamp) -> int64 bajotowo
    Topic: devices/provisioning
    """
    try:
        # Wyciągnij ID z tematu
        print(f"[MQTT] Próba parowania (provisioning):")

        # Wypisz payload
        print(f"[MQTT] Payload (bytes): {payload_bytes}")

        # Sprawdzamy od najstarszego urządzenia, która ma status PENDING - czy odszyfrowanie wiadomości uda się kluczem tego urządzenia
        pending_devices = database.get_all_pending_devices() 

        device_id = None
        for device in pending_devices:
            aes_key = device['aes_key']

            try:
                decrypted_text = crypto_utils.encrypt_chunk(payload_bytes, aes_key)
                if "ESPHERA|" in decrypted_text:
                    # sprawdzamy, czy urządzenie ma >5 minut
                    cur_time = int(time.time())
                    timestamp_str = decrypted_text.split('|')[0]
                    if not timestamp_str.isdigit():
                        print("[MQTT] Błąd weryfikacji: Nieprawidłowy timestamp")
                        continue
                    timestamp = int(timestamp_str)
                    if abs(cur_time - timestamp) > 300:
                        print("[MQTT] Błąd weryfikacji: Timestamp poza dozwolonym zakresem")
                        continue
                    device_id = device['device_id']
                    print(f"[MQTT] Dopasowano urządzenie: {device_id}")
                    break
            except:
                continue

        if not device_id:
            print("[MQTT] Parowanie nieudane: Nieznane urządzenie lub błąd weryfikacji")
            return
        # 4. SUKCES - Zmieniamy status na PAIRED
        database.activate_device(device_id)
        print(f"[MQTT] SUKCES! Urządzenie {device_id} sparowane.")
        
        # zwracamy wiadomość zwrotną (MQTT)
        # topic: devices/provisioning/response
        # header: hash(time + "ESPHERA")
        # device_id: hash(device_id)
        response_topic = f"devices/provisioning/response"
        timestamp = int(time.time())
        header = crypto_utils.decrypt_chunk(f"{timestamp}|ESPHERA")
        device_id_crypt = crypto_utils.decrypt_chunk(device_id)
        response_msg = {
            "header": header,
            "device_id": device_id_crypt
        }
        client.publish(response_topic, json.dumps(response_msg))
    except Exception as e:
        print(f"[MQTT] Provisioning Error: {e}")


def handle_registration(payload):
    """Obsługa rejestracji nowej kuli przez Admina (Appkę)"""
    try:
        data = json.loads(payload)
        device_id = data.get("device_id")
        aes_key_str = data.get("aes_key")
        user_email = data.get("user_email", "admin@localhost") # Appka powinna to wysłać

        if not device_id or len(aes_key_str) != 16:
            print("[MQTT] Błąd rejestracji: Nieprawidłowe dane")
            return

        aes_key_bytes = aes_key_str.encode('utf-8')
        
        # Używamy nowej metody z database.py (PRO)
        if database.register_pending_device(device_id, aes_key_bytes, user_email):
             # Od razu aktywujemy dla testów (w produkcji byłaby weryfikacja)
            database.activate_device(device_id)
            print(f"[MQTT] Zarejestrowano i aktywowano: {device_id}")
        else:
            print(f"[MQTT] Nie udało się zarejestrować {device_id} (może brak usera?)")

    except Exception as e:
        print(f"[MQTT] Register error: {e}")

def handle_request_start(topic, payload):
    """
    Kula pyta: "Mogę nadać audio?"
    Topic: devices/{device_id}/request/start
    Payload: { "encrypted_time": "..." }
    """
    try:
        # Wyciągamy device_id z tematu
        # devices/esp1234/request/start -> split('/') -> [1]
        parts = topic.split('/')
        if len(parts) < 3: return
        device_id = parts[1]

        print(f"[MQTT] Żądanie startu od: {device_id}")

        # 1. Sprawdź czy znamy urządzenie
        device = database.get_device_auth(device_id)
        if not device:
            print(f"[MQTT] Odrzucono: Nieznane urządzenie {device_id}")
            return
        
        if device['status'] != 'paired':
             print(f"[MQTT] Odrzucono: Urządzenie nie jest sparowane (status={device['status']})")
             return

        aes_key = device['aes_key']
        
        try:
            req_data = json.loads(payload)
        except:
            print("[MQTT] Błąd JSON w request/start")
            return
        
        # 3. Generujemy sesję
        session_token = session_manager.create_session(device_id, session_type='upload')

        # 4. Wysyłamy odpowiedź
        # Topic: devices/{device_id}/response/setup
        response_topic = f"devices/{device_id}/response/setup"
        response_payload = {
            "ip": MY_LOCAL_IP,
            "port": TCP_PORT,
            "session_token": session_token
        }
        
        client.publish(response_topic, json.dumps(response_payload))
        print(f"[MQTT] Wysłano zgodę do {device_id}. Token: {session_token[:6]}...")
        
        # Logujemy w bazie
        database.log_event(device_id, "SYS", "Rozpoczęto sesję audio (Upload)")
        database.update_last_seen(device_id)

    except Exception as e:
        print(f"[MQTT] Request Start Error: {e}")

def notify_response_ready(device_id, ip, port, session_token, size):
    """
    Wysyła powiadomienie MQTT: devices/{device_id}/response/ready
    """
    topic = f"devices/{device_id}/response/ready"
    payload = {
        "session_token": session_token,
        "host": ip,
        "port": port,
        "size": size
    }
    try:
        client.publish(topic, json.dumps(payload))
        print(f"[MQTT] Wysłano POWIADOMIENIE o odpowiedzi do {device_id} (Size: {size})")
    except Exception as e:
        print(f"[MQTT] Błąd wysyłania notyfikacji: {e}")

def handle_state_update(topic, payload):
    try:
        # Topic: devices/{id}/state
        device_id = topic.split('/')[1]
        data = json.loads(payload)

        if 'rssi' in data:
            rssi = int(data['rssi'])
            print(f"[MQTT] WiFi RSSI od {device_id}: {rssi} dBm")
            database.update_device_rssi(device_id, rssi)

    except Exception as e:
        print(f"[MQTT] Błąd state update: {e}")

        
# --- CALLBACKI PAHO ---
def on_connect(c, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Połączono z brokerem. Subskrybuję...")
        c.subscribe(TOPIC_REGISTER)
        c.subscribe(TOPIC_REQUEST_START)
        c.subscribe(TOPIC_STATE)
        c.subscribe(TOPIC_PROVISION) # <--- DODAJ TO
    else:
        print(f"[MQTT] Błąd połączenia: {rc}")

def on_message(c, userdata, msg):
    topic = msg.topic
    # Dla provisioning payload może być binarny, dla reszty UTF-8 JSON
    if "provisioning" in topic:
        handle_provisioning(msg.payload) # Przekazujemy bajty
    else:
        # Stara logika dla JSON
        try:
            payload = msg.payload.decode('utf-8')
            if topic == TOPIC_REGISTER:
                handle_registration(payload)
            elif "request/start" in topic:
                handle_request_start(topic, payload)
            elif "/state" in topic:
                handle_state_update(topic, payload)
        except Exception as e:
            print(f"[MQTT] Błąd dekodowania msg: {e}")




# --- START ---

async def start_mqtt():
    global client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"[MQTT] CRITICAL ERROR: {e}")