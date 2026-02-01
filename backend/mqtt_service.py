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
# TOPIC_REGISTER = "/esphera/admin/register"
# TOPIC_REQUEST_START = "devices/+/request/start" # Wildcard + oznacza dowolne ID
# TOPIC_STATE = "devices/+/state"
client = None # Globalna referencja

# Provisioning
TOPIC_PROVISIONING = "devices/provisioning"
TOPIC_PROVISIONING_RESPONSE = "devices/provisioning/response"
# Config
TOPIC_DEVICE_ASK_CONFIG = "devices/+/ask_config"
TOPIC_DEVICE_CONFIG = "devices/{device_id}/config"

# --- LOGIKA BIZNESOWA MQTT ---
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

        # console log how many pending devices we have:
        print(f"[MQTT] Liczba oczekujących urządzeń: {len(pending_devices)}")

        device_id = None
        aes_key = None
        for device in pending_devices:
            print(f"[MQTT] Checking device {device['device_id']}")
            aes_key = device['aes_key']
            try:
                print(f"[MQTT] Próba odszyfrowania kluczem urządzenia {device['device_id']}")
                if crypto_utils.check_header(payload_bytes, aes_key):
                    print(f"udało się dla {device['device_id']}")
                    device_id = device['device_id']
                    break
                print(f"nie udało się dla {device['device_id']}")
            except Exception as e:
                print(f"[MQTT] Błąd podczas weryfikacji urządzenia {device['device_id']}: {e}")
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

        response_topic = TOPIC_PROVISIONING_RESPONSE
        payload = b"ESPHERA|" + int(time.time()).to_bytes(8, byteorder='little') + int(device_id).to_bytes(8, byteorder='little')
        crypted_payload = crypto_utils.prepare_payload(payload, aes_key)

        client.publish(response_topic, crypted_payload)


    except Exception as e:
        print(f"[MQTT] Provisioning Error: {e}")


def handle_config_request(payload_bytes, device_id):
    device = database.get_device_auth(device_id)
    aes_key = device['aes_key']
    if not device:
        print(f"[MQTT] Błąd: Nieznane urządzenie {device_id} w żądaniu konfiguracji.")
        return
    
    # check header
    header_ok, ota_updated = crypto_utils.check_header_ota(payload_bytes, aes_key)

    if not header_ok:
        print(f"[MQTT] Błąd: Nieprawidłowy header w żądaniu konfiguracji od urządzenia {device_id}.")
        return
    
    if ota_updated:
        database.set_ota_update_true(device_id)

    print(f"[MQTT] Żądanie konfiguracji od urządzenia {device_id}")

    send_config_update(device_id, aes_key)

def send_config_update(device_id, aes_key):
    config = database.get_device_config(device_id)
    led_intensity = config['led_intensity']
    sleep_timeout = config['sleep_timeout']
    ota_updated = config['updated_ota']

    response_topic = TOPIC_DEVICE_CONFIG.format(device_id=device_id)
    payload = b"ESPHERA|" + int(time.time()).to_bytes(8, byteorder='little') + int(led_intensity).to_bytes(4, byteorder='little') + int(sleep_timeout).to_bytes(4, byteorder='little') + (b'\x01' if ota_updated else b'\x00')
    crypted_payload = crypto_utils.prepare_payload(payload, aes_key)

    client.publish(response_topic, crypted_payload)
        
# --- CALLBACKI PAHO ---
def on_connect(c, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Połączono z brokerem. Subskrybuję...")
        # c.subscribe(TOPIC_REGISTER)
        # c.subscribe(TOPIC_REQUEST_START)
        # c.subscribe(TOPIC_STATE)
        c.subscribe(TOPIC_PROVISIONING) 
        c.subscribe(TOPIC_DEVICE_ASK_CONFIG)
    else:
        print(f"[MQTT] Błąd połączenia: {rc}")

def on_message(c, userdata, msg):
    topic = msg.topic
    try:
        if topic == TOPIC_PROVISIONING:
            handle_provisioning(msg.payload) # Przekazujemy bajty
            return
        elif topic.startswith("devices/") and topic.endswith("/ask_config"):
            device_id = int(topic.split('/')[1])
            handle_config_request(msg.payload, device_id)
            return
    except Exception as e:
        print(f"[MQTT] Błąd w obsłudze provisioning: {e}")
        return
        

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