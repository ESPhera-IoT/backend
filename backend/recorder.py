import sounddevice as sd
from scipy.io.wavfile import write
import os

FS = 44100
SECONDS = 6
FILENAME = "pytanie.wav"

print("---------------------------------------")
print(f"Przygotuj się... Nagrywanie za 3 sekundy!")
print("---------------------------------------")
sd.sleep(1000)
print("3...")
sd.sleep(1000)
print("2...")
sd.sleep(1000)
print("1...")

print(f"🔴 NAGRYWANIE ({SECONDS}s) - Mów teraz!")
myrecording = sd.rec(int(SECONDS * FS), samplerate=FS, channels=1)
sd.wait()

print("✅ Koniec nagrywania.")
write(FILENAME, FS, myrecording)
print(f"Zapisano jako: {FILENAME}")