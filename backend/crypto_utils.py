import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import os
import time


def decrypt_debug(encrypted_data, base64_key: str):
    IV_SIZE = 12
    TAG_SIZE = 16
    
    key = base64.b64decode(base64_key)

    if len(encrypted_data) < IV_SIZE + TAG_SIZE:
        print("Dane za krótkie.")
        return None, None

    iv = encrypted_data[:IV_SIZE]
    tag = encrypted_data[-TAG_SIZE:]
    ciphertext = encrypted_data[IV_SIZE:-TAG_SIZE]

    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv, tag),
        backend=default_backend()
    ).decryptor()

    try:
        # Sam proces deszyfrowania (zwraca bajty)
        decrypted_bytes = decryptor.update(ciphertext) + decryptor.finalize()
        print("\n=== SUKCES KRYPTOGRAFICZNY ===")
        # "ESPHERA|" to 8 znaków. Sprawdźmy co jest po nich.
        print(len(decrypted_bytes))
        if len(decrypted_bytes) > 8:
            prefix = decrypted_bytes[:8]
            time = decrypted_bytes[8:16]
            ota_updated = b'\x00'
            if(len(decrypted_bytes) > 16):
                ota_updated = decrypted_bytes[16]
                print(f"Reszta (jako liczba int?): {int.from_bytes(ota_updated, byteorder='little')}")
            print(f"\nPrefiks: {prefix}")
            print(f"Reszta (jako liczba int?): {int.from_bytes(time, byteorder='little')}")
            return prefix, int.from_bytes(time, byteorder='little'), bool.from_bytes(ota_updated, byteorder='little')

    except Exception as e:
        print(f"BŁĄD: {e}")


def check_header(encrypted_data, base64_key: str) -> bool:
    prefix, mess_time, _ = decrypt_debug(encrypted_data, base64_key)
    # check prefix
    if (prefix != b'ESPHERA|'):
        print("brak prefiksu ESPHERA|")
        return False
    # check if message time was sent at least 5 minutes ago
    print("Spradzamy czas")
    curr_time = int(time.time())
    print(mess_time)
    print(curr_time)

    time_check = bool(curr_time - mess_time < 300)
    return time_check

def check_header_ota(encrypted_data, base64_key: str) -> bool:
    prefix, mess_time, ota_updated = decrypt_debug(encrypted_data, base64_key)
    # check prefix
    if (prefix != b'ESPHERA|'):
        print("brak prefiksu ESPHERA|")
        return False
    # check if message time was sent at least 5 minutes ago
    print("Spradzamy czas")
    curr_time = int(time.time())
    print(mess_time)
    print(curr_time)

    time_check = bool(curr_time - mess_time < 300)
    return time_check, ota_updated
        


def prepare_payload(data_bytes, base64_key: str) -> bytes:
    """
    Szyfruje wiadomość tekstową do formatu akceptowanego przez ESP32 (mbedtls GCM).
    Format: [IV (12)] + [Ciphertext] + [Tag (16)]
    """
    
    # 1. Dekodowanie klucza z Base64 do postaci binarnej (32 bajty)
    key_bytes = base64.b64decode(base64_key)
    # 2. Zamiana tekstu na bajty
    # 3. Generowanie losowego IV (Nonce) - 12 bajtów
    iv = os.urandom(12)
    
    # 4. Konfiguracja szyfrowania AES-GCM
    # W bibliotece cryptography Tag jest generowany automatycznie przy finalize()
    encryptor = Cipher(
        algorithms.AES(key_bytes),
        modes.GCM(iv),
        backend=default_backend()
    ).encryptor()
    
    # 5. Szyfrowanie danych
    ciphertext = encryptor.update(data_bytes) + encryptor.finalize()
    
    # 6. Pobranie Taga autoryzacyjnego (16 bajtów)
    tag = encryptor.tag
    
    # 7. Złożenie finalnego payloadu: IV na początku, Tag na końcu
    payload = iv + ciphertext + tag
    
    print(payload)
    return payload


def decrypt_audio_chunk(encrypted_chunk, base64_key: str) -> bytes:
    IV_SIZE = 12
    TAG_SIZE = 16
    
    key = base64.b64decode(base64_key)

    if len(encrypted_chunk) < IV_SIZE + TAG_SIZE:
        print("Dane za krótkie.")
        return b""
    
    try:
        iv = encrypted_chunk[:IV_SIZE]
        tag = encrypted_chunk[-TAG_SIZE:]
        ciphertext = encrypted_chunk[IV_SIZE:-TAG_SIZE]

        decryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(iv, tag),
            backend=default_backend()
        ).decryptor()

        decrypted_bytes = decryptor.update(ciphertext) + decryptor.finalize()
        return decrypted_bytes

    except Exception as e:
        print(f"BŁĄD podczas deszyfrowania chunku audio: {e}")
        return b""


def encrypt_audio_chunk(data: bytes, base64_key: str) -> bytes:
    """
    Szyfruje dane do formatu: [IV (12)] + [Ciphertext] + [Tag (16)]
    """
    # 1. Generujemy losowe IV (Nonce) - 12 bajtów
    key = base64.b64decode(base64_key)

    iv = os.urandom(12)

    # 2. Szyfrowanie GCM
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv),
        backend=default_backend()
    ).encryptor()

    ciphertext = encryptor.update(data) + encryptor.finalize()
    
    
    # 3. Składamy paczkę: IV + Ciphertext + Tag
    # tag ma zawsze 16 bajtów w GCM
    return iv + ciphertext + encryptor.tag

# # TESTS

# # Klucz i Payload
# base64_key = "AT2gqijyAmKGfIKvnEx7oBeN/rS4hLPNHpNBeDECPbE="
# # Zwróć uwagę, że skopiowałem Twój payload dokładnie
# payload = b'o \xbb\xbe\xa5\xee\xcd\x1b\xf3:\x0f`w\xfd\xb8\x1b\xe8e\xbd,\xb6\x04\xee<\xea\xd20\x07h\xef{\xf0f\xb7\xd1M\xd9\x13S\x83g\xf6]\xc7'
# # payload = b'zE\xa2!\xe6]\xb9\xfcF\x9a\xadE;\x9cvs^\x1a\xd6^\x86\xff\x18B\x95\xdc\xbe\xba\x84iY\x1f^[\x8a}\xee\rd\xa6?i\xa5\xc2\x03)3\xce_\xeb\x0fg\xc5'
# data_bytes = b"ESPHERA|" + (int(time.time())).to_bytes(8, byteorder='little') + b"|" + (int(42).to_bytes(8, byteorder='little'))
# check_header(payload, base64_key)
# prepare_payload(data_bytes, base64_key)