import binascii
import time
import json
import paho.mqtt.client as mqtt
import os
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
# Konfiguracja brokera (musi być ta sama co w brokerze)
BROKER_ADDRESS = "127.0.0.1"
BROKER_PORT = 1883

class ESPSimulator:
    def __init__(self, device_id, aes_key_input):
        self.device_id = device_id
        # Konwersja klucza na bajty (jeśli jest stringiem hex lub plain)
        self.aes_key_raw = self._convert_to_bytes(aes_key_input)
        print(f"[ESP SIM] Inicjalizacja symulatora {device_id}")
        print(f"[ESP SIM] Klucz RAW (hex): {self.aes_key_raw.hex()}")
        if len(self.aes_key_raw) not in [16, 24, 32]:
            print(f"[ESP SIM] UWAGA! Nieprawidłowa długość klucza: {len(self.aes_key_raw)} bajtów.")
            # Hack naprawczy dla testów: dopełniamy zerami do 16 bajtów
            self.aes_key_raw = self.aes_key_raw.ljust(16, b'\0')[:16]

    def _convert_to_bytes(self, key_input):
        """Inteligentna konwersja do bajtów (obsługuje Base64, Hex i Raw)"""
        if isinstance(key_input, bytes):
            return key_input
            
        if isinstance(key_input, str):
            try:
                if len(key_input) == 32 and all(c in '0123456789abcdefABCDEF' for c in key_input):
                     return bytes.fromhex(key_input)
                
                return base64.b64decode(key_input)
            except (binascii.Error, ValueError):
                pass
            
            return key_input.encode('utf-8')
            
        return b''

    def _encrypt(self, text):
            """
            Symulacja szyfrowania sprzętowego ESP32 (AES-ECB).
            """
            print(f"[ESP SIM] Szyfrowanie tekstu: {text}")

            # 1. Tworzymy obiekt szyfrujący używając KLUCZA RAW
            cipher = AES.new(self.aes_key_raw, AES.MODE_ECB)
            # 2. Przygotowujemy dane (tekst -> bajty)
            data_bytes = text.encode('utf-8')
            
            # 3. Padding (Dopełnienie)
            padded_data = pad(data_bytes, AES.block_size)
            
            # 4. Szyfrowanie
            encrypted_bytes = cipher.encrypt(padded_data)
            print(f"[ESP SIM] Zaszyfrowane bajty (hex): {encrypted_bytes.hex()}")
    
            return encrypted_bytes
    

    def simulate_provisioning(self):
        """
        Symuluje proces parowania: device_id + encrypted(TIME|ESPHERA)
        """
        print(f"--- [ESP SIM] START PROVISIONING ---")
        client = mqtt.Client(client_id=f"esp_sim_{self.device_id}")
        
        try:
            client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
            
            # 1. Symulacja danych z RTC (Zegar czasu rzeczywistego)
            timestamp = int(time.time())
            raw_msg = f"{timestamp}|ESPHERA"
            
            # 2. Szyfrowanie "sprzętowe"
            encrypted_payload = self._encrypt(raw_msg)
            
            # 3. Publikacja (MQTT wysyła czyste bajty payloadu)
            topic = f"devices/provisioning"
            
            print(f"[ESP SIM] Raw Message: {raw_msg}")
            print(f"[ESP SIM] Encrypted Hex: {encrypted_payload.hex().upper()}")
            
            client.publish(topic, encrypted_payload)
            
            time.sleep(4) # Czas na propagację sieci
            client.disconnect()
            print(f"--- [ESP SIM] DONE ---")
            
        except Exception as e:
            print(f"[ESP SIM] Error: {e}")


# Funkcja wrapper do łatwego wywołania asynchronicznego
def run_esp_provisioning_task(aes_key):
    # Tutaj aes_key to String Base64 z JSON-a
    sim = ESPSimulator(None, aes_key)
    # Symulacja opóźnienia połączenia WiFi
    time.sleep(2) 
    sim.simulate_provisioning()