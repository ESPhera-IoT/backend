import asyncio
import struct
import os
import wave
from dotenv import load_dotenv
from openai import AsyncOpenAI
import crypto_utils
from elevenlabs import ElevenLabs
import response_generator

# --- TWOJE IMPORTY ---
# (Zakładam, że te pliki istnieją w Twoim projekcie)
import database
import mqtt_service
import time
import socket

load_dotenv()

# --- KONFIGURACJA ---
HOST = '0.0.0.0'
PORT = 26358
INPUT_DIR = "recordings"
OUTPUT_DIR = "responses"
MY_LOCAL_IP = os.getenv("LOCAL_IP")
SEND_CHUNK_SIZE = 1000

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Klienty AI
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=api_key) if api_key else None
eleven_labs_client = ElevenLabs(api_key=os.getenv("ELEVEN_LABS_API_KEY"))


async def save_pcm_to_wav(pcm_data, filename):
    """Zapisuje surowe bajty (PCM) do pliku WAV z nagłówkiem"""
    with wave.open(filename, 'wb') as wav_file:
        wav_file.setnchannels(1)        # Mono
        wav_file.setsampwidth(3)        # 24-bit (3 bajty na próbkę)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data)
    print(f" [FILE] Zapisano plik audio: {filename}")


async def send_audio_response(writer, file_path, aes_key):
    """
    Wysyła plik WAV z powrotem do ESP przez otwarty socket.
    Format: 
    1. Bajt sygnałowy (0x01)
    2. Pętle: [Rozmiar (4b)] + [Zaszyfrowana paczka]
    """
    if not os.path.exists(file_path):
        print(f" [ERR] Plik odpowiedzi nie istnieje: {file_path}")
        return

    print(f" [TCP-OUT] Rozpoczynam wysyłanie odpowiedzi: {file_path}")
    
    try:
        writer.write(b'\x01')
        await writer.drain()

        # Otwieramy plik WAV i czytamy surowe ramki (bez nagłówka pliku WAV)
        with wave.open(file_path, 'rb') as wf:
            while True:
                # Czytamy kawałek czystego audio
                data_chunk = wf.readframes(SEND_CHUNK_SIZE // 3) # dzielone przez 3 bo 24-bit to 3 bajty
                
                if not data_chunk:
                    break # Koniec pliku

                # Szyfrujemy (IV + Cipher + Tag)
                encrypted_packet = crypto_utils.encrypt_data(data_chunk, aes_key)
                
                # Obliczamy rozmiar zaszyfrowanej paczki
                packet_size = len(encrypted_packet)
                
                # Wysyłamy rozmiar (4 bajty, Little Endian)
                writer.write(struct.pack('<I', packet_size))
                
                # Wysyłamy zaszyfrowane dane
                writer.write(encrypted_packet)
                
                # Opcjonalnie: flush co jakiś czas, ale asyncio robi to nieźle samo
                await writer.drain()

        # Koniec transmisji - wysyłamy rozmiar 0
        zero_size = 0
        writer.write(struct.pack('<I', zero_size))
        await writer.drain()
        print(" [TCP-OUT] Zakończono wysyłanie audio.")

    except Exception as e:
        print(f" [TCP-OUT ERR] Błąd wysyłania: {e}")


async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    print(f" [TCP] Nowe połączenie od: {addr}")
    
    try:
        # --- KROK 1: ODBIÓR DEVICE ID ---
        # Czytamy dokładnie 8 bajtów (uint64_t)
        device_id_bytes = await reader.readexactly(8)
        # Rozpakowujemy: < = little-endian (ESP32), Q = unsigned long long (8 bytes)
        device_id = struct.unpack('<Q', device_id_bytes)[0]
        print(f" [TCP] Device ID: {device_id}")

        # --- KROK 2: ODBIÓR AUTORYZACJI ---
        # Czytamy ustalony rozmiar zaszyfrowanego nagłówka

        ENCRYPTED_AUTH_SIZE = (
              12 # IV 
            + 16 # ESPHERA| (8) + timestamp (8)
            + 16 # TAG
        )
        encrypted_auth = await reader.readexactly(ENCRYPTED_AUTH_SIZE)

        device = database.get_device_by_id(device_id)
        if not device:
            print(f" [TCP] Błąd: Nieznane urządzenie {device_id}.")
            return
        aes_key = device['aes_key']
        print(f" [TCP] Sprawdzamy autoryzację dla {device_id}...")
        if not crypto_utils.decrypt_check_header(encrypted_auth, aes_key):
            print(f" [TCP] Błąd: Nieudana autoryzacja dla {device_id}.")
            return 
        
        # --- KROK 3: PĘTLA ODBIORU AUDIO ---
        full_audio_buffer = bytearray()
        
        while True:
            # Czytamy 4 bajty - rozmiar następnej paczki (size_t)
            # Używamy read(), a nie readexactly(), żeby obsłużyć koniec strumienia
            size_bytes = await reader.read(4)
            
            if not size_bytes:
                print(" [TCP] Połączenie zamknięte przez klienta.")
                break

            # Jeśli ESP wyśle 1 bajt (0x00) jako znacznik końca:
            if len(size_bytes) == 1 and size_bytes == b'\x00':
                print(" [TCP] Odebrano znacznik końca transmisji.")
                break
            
            # Jeśli otrzymaliśmy mniej niż 4 bajty i to nie jest zero, to błąd transmisji
            if len(size_bytes) < 4:
                print(f" [ERR] Niekompletny nagłówek rozmiaru: {len(size_bytes)} bajtów.")
                break

            # Konwertujemy bajty na int (wielkość paczki danych)
            chunk_size = struct.unpack('<I', size_bytes)[0]
            
            # Zabezpieczenie przed pustymi paczkami
            if chunk_size == 0:
                continue

            # Czytamy DOKŁADNIE tyle danych, ile zapowiedział nagłówek
            encrypted_chunk = await reader.readexactly(chunk_size)
            
            # Odszyfrowujemy i dodajemy do bufora
            decrypted_chunk = crypto_utils.decrypt_data(encrypted_chunk, aes_key)
            full_audio_buffer.extend(decrypted_chunk)

        # --- KONIEC TRANSMISJI ---
        if len(full_audio_buffer) == 0:
            print(" [TCP] Brak odebranego audio.")
            return  

        timestamp = int(time.time())
        filename = os.path.join(INPUT_DIR, f"{device_id}_{timestamp}.wav")
        output_filename = os.path.join(OUTPUT_DIR, f"{device_id}_{timestamp}_response.wav")
        
        # Zapisz surowe dane jako WAV
        await save_pcm_to_wav(full_audio_buffer, filename)
        
        # Uruchom logikę AI
        success = await asyncio.create_task(response_generator.process_audio(filename, output_filename, device_id))

        if success:
            # 3. Wysyłanie odpowiedzi zwrotnej do ESP
            await send_audio_response(writer, output_filename, aes_key)
        else:
            print(" [AI] Błąd generowania odpowiedzi.")
       
    except asyncio.IncompleteReadError:
        print(" [ERR] Przerwano połączenie w trakcie czytania danych.")
    except Exception as e:
        print(f" [ERR] Błąd obsługi klienta: {e}")
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Set socket options BEFORE binding/listening
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # Bind socket
    sock.bind((HOST, PORT))
    sock.listen()

    server = await asyncio.start_server(handle_client, sock=sock)
    print(f"=== BACKEND READY ({MY_LOCAL_IP}:{PORT}) ===")
    print(f"=== OCZEKIWANIE NA ESP32... ===")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
