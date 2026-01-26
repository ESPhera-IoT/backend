import socket
import time
import json
import paho.mqtt.client as mqtt
import crypto_utils
import threading
import random

MY_DEVICE_ID = "esp_nowy"
MY_AES_KEY = b'1234567890123456'
BROKER_IP = "127.0.0.1"
INPUT_FILE = "pytanie.wav"
OUTPUT_FILE = "finalna_odpowiedz.wav"

# Eventy
setup_event = threading.Event()
ready_event = threading.Event()

session_setup = {} # Dane do uploadu
session_ready = {} # Dane do downloadu

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Połączono. Subskrybuję kanały dla {MY_DEVICE_ID}...")
    client.subscribe(f"devices/{MY_DEVICE_ID}/response/setup")
    client.subscribe(f"devices/{MY_DEVICE_ID}/response/ready")

def on_message(client, userdata, msg):
    global session_setup, session_ready
    topic = msg.topic
    payload = msg.payload.decode()
    
    if "response/setup" in topic:
        print(f"[MQTT] Otrzymano SETUP (Można nagrywać)")
        session_setup = json.loads(payload)
        setup_event.set()
        
    elif "response/ready" in topic:
        print(f"[MQTT] Otrzymano READY (Odpowiedź gotowa!)")
        session_ready = json.loads(payload)
        ready_event.set()

def simulate_full_flow():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_IP, 1883, 60)
    client.loop_start()

    time.sleep(1)

    # --- ETAP 1: REQUEST START ---
    print("\n--- [1] Negocjacja ---")
    client.publish(f"devices/{MY_DEVICE_ID}/request/start", json.dumps({"size": 123}))
    
    if not setup_event.wait(5):
        print("Timeout SETUP!"); return

    # --- ETAP 2: UPLOAD ---
    print("\n--- [2] Upload Audio ---")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((session_setup['ip'], session_setup['port']))
    
    # Token + Newline
    s.sendall(f"{session_setup['session_token']}\n".encode())
    
    # Szyfrowane Audio
    with open(INPUT_FILE, "rb") as f:
        encrypted = crypto_utils.encrypt_chunk(f.read(), MY_AES_KEY)
        s.sendall(encrypted)
    
    s.close()
    print("[KULA] Upload zakończony. Czekam na przetworzenie...")

    # --- ETAP 3: OCZEKIWANIE NA AI ---
    if not ready_event.wait(100): # Dajemy 100s na symulację AI
        print("Timeout READY! Backend nie odpowiedział."); return

    # --- ETAP 4: DOWNLOAD ---
    print("\n--- [3] Download Odpowiedzi ---")
    print(f"[KULA] Pobieram z {session_ready['host']}:{session_ready['port']}")
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((session_ready['host'], session_ready['port']))
    
    # Token Downloadu
    s.sendall(f"{session_ready['session_token']}\n".encode())
    
    # Pobieranie strumienia
    encrypted_resp = b""
    while True:
        data = s.recv(4096)
        if not data: break
        encrypted_resp += data
    s.close()
    
    # Deszyfracja
    if len(encrypted_resp) > 0:
        decrypted = crypto_utils.decrypt_chunk(encrypted_resp, MY_AES_KEY)
        with open(OUTPUT_FILE, "wb") as f:
            f.write(decrypted)
        print(f"[KULA] SUKCES! Zapisano odpowiedź w {OUTPUT_FILE}")
    else:
        print("[KULA] Błąd: Pusty plik odpowiedzi")

    # --- ETAP 5: WYSYŁANIE STANU (WIFI) ---
    print("\n--- [4] Aktualizacja Stanu ---")
    fake_rssi = random.randint(-80, -40)
    state_topic = f"devices/{MY_DEVICE_ID}/state"
    state_payload = json.dumps({"rssi": fake_rssi})
    
    client.publish(state_topic, state_payload)
    print(f"[KULA] Wysłano RSSI: {fake_rssi} dBm")
    
    time.sleep(2)
    client.loop_stop()  
    
    client.loop_stop()

if __name__ == "__main__":
    simulate_full_flow()