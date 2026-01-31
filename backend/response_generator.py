import asyncio
import os
from dotenv import load_dotenv
from pydub import AudioSegment
from openai import AsyncOpenAI
import database
from elevenlabs import ElevenLabs, VoiceSettings


load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=api_key) if api_key else None

ELEVEN_LABS_API_KEY = os.getenv("ELEVEN_LABS_API_KEY")
eleven_labs_client = ElevenLabs(api_key=ELEVEN_LABS_API_KEY)

async def process_audio(input_path, output_path, device_id):
    """Logika AI (lub Symulacja)"""
    print(f" [AI] Przetwarzanie dla {device_id}...")
    
    # 1. Pobierz konfigurację z bazy (Model + Osobowość)
    # Musimy to zrobić w wątku (sqlite lock)
    dev_conf = await asyncio.to_thread(database.get_full_device_info, device_id)
    
    ai_model = "gpt-4.1-nano"
    sys_prompt = "Jesteś pomocnym asystentem. Odpowiadaj zwięźle - w maksymalnie dwóch zdaniach."
    
    if dev_conf:
        sys_prompt = dev_conf['system_prompt'] + "\nOdpowiadaj zwięźle - w maksymalnie dwóch zdaniach."
        sound_model_id = dev_conf['sound_model_id']
    
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
        
        audio_iterator = await asyncio.to_thread(
            eleven_labs_client.text_to_speech.convert,
            text=response_text,
            voice_id=sound_model_id,
            model_id="eleven_multilingual_v2", # Note: double check if 'eleven_v3' is released/supported in your SDK
        )

        with open(temp_mp3, "wb") as f:
            for chunk in audio_iterator:
                f.write(chunk)

        def convert():
            sound = AudioSegment.from_mp3(temp_mp3)
            sound = sound.set_frame_rate(16000).set_channels(1).set_sample_width(3)
            sound.export(output_path, format="wav")
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)

        await asyncio.to_thread(convert)
        return True
    

        # TTS WITH OPENAI
        response = await client.audio.speech.create(
            model="tts-1", 
            voice="alloy", 
            input=response_text
        )
        await asyncio.to_thread(response.stream_to_file, temp_mp3)

        # Konwersja do WAV z odpowiednimi parametrami dla ESP
        def convert():
            sound = AudioSegment.from_mp3(temp_mp3)
            sound = sound.set_frame_rate(44100).set_channels(1).set_sample_width(2)
            sound.export(output_path, format="wav")
            os.remove(temp_mp3)
        
        await asyncio.to_thread(convert)
        return True

    except Exception as e:
        print(f" [ERR] AI Error: {e}")
        return False
