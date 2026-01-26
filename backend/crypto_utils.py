from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding

def get_cipher(key):
    return Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())

def encrypt_chunk(data, key):
    """
    Szyfruje dane. Dodaje padding PKCS7, żeby długość była wielokrotnością 16 bajtów.
    """
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data) + padder.finalize()
    
    encryptor = get_cipher(key).encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()

def decrypt_chunk(data, key):
    """
    Odszyfrowuje dane i usuwa padding.
    """
    decryptor = get_cipher(key).decryptor()
    decrypted_padded = decryptor.update(data) + decryptor.finalize()
    
    try:
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(decrypted_padded) + unpadder.finalize()
    except Exception:
        print(" [CRYPTO] Błąd paddingu (może zły klucz?)")
        return b''

if __name__ == "__main__":
    test_key = b'1234567890123456' # 16 bajtów
    msg = b"Tajne haslo Kuli"
    
    encrypted = encrypt_chunk(msg, test_key)
    print(f"Zaszyfrowane: {encrypted.hex()}")
    
    decrypted = decrypt_chunk(encrypted, test_key)
    print(f"Odszyfrowane: {decrypted}")