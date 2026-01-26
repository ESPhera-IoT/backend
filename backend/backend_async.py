import asyncio
import os
import time
import shutil
from dotenv import load_dotenv
from pydub import AudioSegment
from openai import AsyncOpenAI
import crypto_utils
import database
import mqtt_service
import session_manager

# --- KONFIGURACJA ---
HOST = '0.0.0.0'
PORT = 26358
ESP_SAMPLE_RATE = 44100
INPUT_DIR = "recordings"
OUTPUT_DIR = "responses"

MY_LOCAL_IP = "192.168.1.236"

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=api_key) if api_key else None

async def process_audio(input_path, output_path, device_id):
    """Logika AI (lub Symulacja)"""
    print(f" [AI] Przetwarzanie dla {device_id}...")
    
    # 1. Pobierz konfigurację z bazy (Model + Osobowość)
    # Musimy to zrobić w wątku (sqlite lock)
    dev_conf = await asyncio.to_thread(database.get_full_device_info, device_id)
    
    ai_model = "gpt-4.1-nano"
    sys_prompt = "Jesteś pomocnym asystentem."
    
    if dev_conf:
        ai_model = dev_conf['ai_model']
        sys_prompt = dev_conf['system_prompt']
    
    print(f" [AI] Config: Model={ai_model}, Prompt='{sys_prompt[:20]}...'")

    try:
        # 1. Transkrypcja
        with open(input_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file, 
                language="pl"
            )
        user_text = transcript.text
        print(f" [AI] Tekst: '{user_text}'")
        await asyncio.to_thread(database.log_event, device_id, "IN", user_text)

        # 2. GPT (z dynamicznymi parametrami z bazy)
        print(" [AI] Generowanie odpowiedzi...")
        completion = await client.chat.completions.create(
            model=ai_model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_text}
            ]
        )
        response_text = completion.choices[0].message.content
        print(f" [AI] Odpowiedź: '{response_text}'")
        await asyncio.to_thread(database.log_event, device_id, "OUT", response_text)

        # 3. TTS
        temp_mp3 = output_path.replace(".wav", ".mp3")
        response = await client.audio.speech.create(
            model="tts-1", 
            voice="alloy", 
            input=response_text
        )
        await asyncio.to_thread(response.stream_to_file, temp_mp3)

        # Konwersja do WAV z odpowiednimi parametrami dla ESP
        def convert():
            sound = AudioSegment.from_mp3(temp_mp3)
            sound = sound.set_frame_rate(ESP_SAMPLE_RATE).set_channels(1).set_sample_width(2)
            sound.export(output_path, format="wav")
            os.remove(temp_mp3)
        
        await asyncio.to_thread(convert)
        return True

    except Exception as e:
        print(f" [ERR] AI Error: {e}")
        return False

async def handle_client(reader, writer):
    """Główna obsługa połączenia TCP (Upload i Download)"""
    addr = writer.get_extra_info('peername')
    
    try:
        # 1. CZYTAMY TOKEN
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        
        if not line: return
        session_token = line.decode().strip()
        
        # 2. WERYFIKACJA SESJI
        session = session_manager.ACTIVE_SESSIONS.get(session_token)
        
        if not session:
            print(f" [TCP] Błąd: Nieznany token {session_token[:8]}...")
            return

        device_id = session['device_id']
        session_type = session['type']
        
        # Pobieramy klucz AES
        device_auth = await asyncio.to_thread(database.get_device_auth, device_id)
        if not device_auth: return
        aes_key = device_auth['aes_key']

        print(f" [TCP] Połączenie {session_type.upper()} od {device_id}")

        # --- SCENARIUSZ 1: UPLOAD (Kula mówi) ---
        if session_type == 'upload':
            timestamp = int(time.time())
            decrypted_input = os.path.join(INPUT_DIR, f"rec_{device_id}_{timestamp}.wav")
            output_wav = os.path.join(OUTPUT_DIR, f"resp_{timestamp}.wav")

            # Odbieranie
            encrypted_data = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                    if not chunk: break
                    encrypted_data += chunk
                except asyncio.TimeoutError:
                    break
            
            # Deszyfracja
            if len(encrypted_data) > 0:
                audio_data = crypto_utils.decrypt_chunk(encrypted_data, aes_key)
                with open(decrypted_input, 'wb') as f:
                    f.write(audio_data)
                print(f" [TCP] Odebrano audio ({len(audio_data)} B)")
                
                # Uruchamiamy AI w tle
                asyncio.create_task(
                    run_ai_pipeline(decrypted_input, output_wav, device_id)
                )
            else:
                print(" [TCP] Puste dane.")

        # --- SCENARIUSZ 2: DOWNLOAD (Kula słucha) ---
        elif session_type == 'download':
            # Pobieramy ścieżkę do pliku, który mamy wysłać
            file_to_send = session.get('file_path')
            
            if file_to_send and os.path.exists(file_to_send):
                print(f" [TCP] Wysyłanie pliku: {file_to_send}")
                with open(file_to_send, 'rb') as f:
                    clear_audio = f.read()
                
                # Szyfrujemy
                encrypted_response = crypto_utils.encrypt_chunk(clear_audio, aes_key)
                
                # Wysyłamy
                writer.write(encrypted_response)
                await writer.drain()
                print(f" [TCP] Wysłano {len(encrypted_response)} zaszyfrowanych bajtów.")
            else:
                print(" [TCP] Błąd: Brak pliku do wysłania!")

    except Exception as e:
        print(f" [TCP] Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        session_manager.remove_session(session_token)

async def run_ai_pipeline(input_path, output_path, device_id):
    """Po udanym AI, tworzymy sesję DOWNLOAD i wołamy MQTT"""
    success = await process_audio(input_path, output_path, device_id)
    
    if success:
        # 1. Tworzymy sesję na pobieranie
        dl_token = session_manager.create_session(device_id, session_type='download')
        
        # dopisujemy ścieżkę pliku do sesji, żeby handler wiedział co wysłać
        session_manager.ACTIVE_SESSIONS[dl_token]['file_path'] = output_path
        
        file_size = os.path.getsize(output_path) # Rozmiar czystego pliku
        
        # 2. Wysyłamy MQTT
        mqtt_service.notify_response_ready(device_id, MY_LOCAL_IP, PORT, dl_token, file_size)
    else:
        print(" [AI] Niepowodzenie - brak odpowiedzi.")

async def main():
    server = await asyncio.start_server(handle_client, HOST, PORT)
    print(f"=== BACKEND READY ({MY_LOCAL_IP}:{PORT}) ===")
    await asyncio.gather(
        server.serve_forever(),
        mqtt_service.start_mqtt()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass