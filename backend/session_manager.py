import uuid
import time

# Słownik w pamięci RAM do przechowywania aktywnych sesji
# Klucz: session_token (str)
# Wartość: { 'device_id': str, 'created_at': timestamp, 'type': 'upload'/'download' }
ACTIVE_SESSIONS = {}

SESSION_TTL = 30  # Ważność tokena w sekundach

def create_session(device_id, session_type='upload'):
    """Generuje unikalny token i zapisuje go w pamięci."""
    token = str(uuid.uuid4())
    ACTIVE_SESSIONS[token] = {
        'device_id': device_id,
        'created_at': time.time(),
        'type': session_type
    }
    print(f"[SESSION] Utworzono token sesji {session_type} dla {device_id}: {token[:8]}...")
    return token

def validate_session(token):
    """
    Sprawdza czy token istnieje i nie wygasł.
    Zwraca device_id jeśli OK, w przeciwnym razie None.
    """
    session = ACTIVE_SESSIONS.get(token)
    
    if not session:
        return None
    
    # Sprawdzenie czasu życia (TTL)
    if time.time() - session['created_at'] > SESSION_TTL:
        print(f"[SESSION] Token wygasł: {token[:8]}...")
        del ACTIVE_SESSIONS[token]
        return None
    
    return session['device_id']

def remove_session(token):
    if token in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[token]