import jwt # PyJWT
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, Response, HTTPException, status, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import uvicorn
import asyncio
import database
import backend_async
import mqtt_service
from pydantic import BaseModel

# TO TESTÓW
# import simulate_esp

# --- KONFIGURACJA ---
app = FastAPI(title="ESPhera IoT Server")
templates = Jinja2Templates(directory="templates")

# Konfiguracja JWT
SECRET_KEY = "bardzo_tajny_klucz_zmien_go_w_produkcji"  # ZMIEŃ TO!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# OAuth2 scheme dla Swagger UI i API
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

# --- FUNKCJE POMOCNICZE JWT ---

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Tworzy token JWT z czasem wygasania"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    # Dodajemy 'exp' (expiration) i 'sub' (subject/email)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    """Dekoduje i weryfikuje token. Zwraca email lub None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
        return email
    except jwt.ExpiredSignatureError:
        return None  # Token wygasł
    except jwt.InvalidTokenError:
        return None  # Token nieprawidłowy

def get_current_user_from_cookie(request: Request):
    """Pomocnik dla UI: wyciąga email z ciasteczka JWT"""
    token = request.cookies.get("access_token")
    if not token:
        return None
    return verify_token(token)

async def get_current_user_api(token: str = Depends(oauth2_scheme)):
    """Dependency dla API: weryfikuje token Bearer"""
    email = verify_token(token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return email

# --- ZARZĄDZANIE TŁEM (TCP + MQTT) ---
@app.on_event("startup")
async def startup_event():
    """Uruchamia backend asynchroniczny w tle serwera WWW"""
    database.init_db()
    
    loop = asyncio.get_event_loop()
    loop.create_task(mqtt_service.start_mqtt())
    
    server = await asyncio.start_server(
        backend_async.handle_client, 
        backend_async.HOST, 
        backend_async.PORT
    )
    loop.create_task(server.serve_forever())
    
    print("--- SYSTEM START: WWW (8001) + TCP (26358) + MQTT ---")

# --- OBSŁUGA STRONY WWW (Web UI) ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    # Jeśli użytkownik ma już ważny token, przekieruj od razu do dashboardu
    if get_current_user_from_cookie(request):
        return RedirectResponse(url="/dashboard", status_code=303)

    return """
    <html>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <body class="d-flex justify-content-center align-items-center vh-100 bg-light">
            <div class="card p-4 shadow" style="width: 350px;">
                <h3 class="text-center mb-4">ESPhera Login</h3>
                <form action="/login" method="post">
                    <div class="mb-3">
                        <input type="text" name="username" class="form-control" placeholder="Email" required>
                    </div>
                    <div class="mb-3">
                        <input type="password" name="password" class="form-control" placeholder="Hasło" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Zaloguj</button>
                </form>
                <div class="mt-3 text-center border-top pt-2">
                    <small>Nie masz konta? <a href="/register">Zarejestruj się</a></small>
                </div>
            </div>
        </body>
    </html>
    """

@app.post("/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    user = database.get_user_by_email(username)
    if not user or not database.verify_password(password, user['password_hash']):
        return HTMLResponse("""
            <div style='color:red; text-align:center; margin-top:50px;'>
                <h3>Błędny email lub hasło!</h3>
                <a href='/'>Wróć</a>
            </div>
        """, status_code=400)

    # --- ZMIANA NA JWT ---
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": username}, expires_delta=access_token_expires
    )

    response = RedirectResponse(url="/dashboard", status_code=303)
    # Ustawiamy JWT w ciasteczku zamiast czystego emaila
    # httponly=True zwiększa bezpieczeństwo (JS nie ma dostępu do ciasteczka)
    response.set_cookie(key="access_token", value=access_token, httponly=True)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register_action(email: str = Form(...), name: str = Form(...), password: str = Form(...)):
    if database.create_user(email, name, password):
        return HTMLResponse("<h3>Konto utworzone! <a href='/'>Zaloguj się</a></h3>")
    else:
        return HTMLResponse("<h3>Błąd: Email zajęty! <a href='/register'>Spróbuj ponownie</a></h3>")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Weryfikacja tokenu JWT z ciasteczka
    user_email = get_current_user_from_cookie(request)
    
    if not user_email:
        return RedirectResponse(url="/")
    
    user = database.get_user_by_email(user_email)
    devices = database.get_user_devices(user_email)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "user": user, 
        "devices": devices
    })

@app.post("/claim")
async def claim_device(request: Request, device_name: str = Form(...), aes_key: str = Form(...)):
    user_email = get_current_user_from_cookie(request)
    if not user_email: return RedirectResponse("/")
    
    aes_bytes = aes_key.encode('utf-8')
    if len(aes_bytes) != 16:
        return HTMLResponse("Klucz musi mieć 16 znaków! <a href='/dashboard'>Wróć</a>")

    device_id = database.register_or_claim_device(device_name, aes_bytes, user_email)
    database.activate_device(device_id)
    
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/config")
async def update_config(
    request: Request, 
    device_id: str = Form(...), 
    device_name: str = Form(...),
    led_intensity: int = Form(...),
    sound_model_id: str = Form(...),
    sleep_timeout: int = Form(...),
    system_prompt: str = Form(...)
):
    user_email = get_current_user_from_cookie(request)
    if not user_email: return RedirectResponse("/")
    
    # Zapisz w bazie
    database.update_device_config(device_id, device_name, led_intensity, sound_model_id, sleep_timeout, system_prompt)
    
    # MQTT Live Update
    if mqtt_service.client:
        mqtt_service.send_config_update(device_id, database.get_device_auth(device_id)['aes_key'])
    
    return RedirectResponse(url="/dashboard", status_code=303)

# --- API REST (JWT Bearer Token) ---
class UserRegister(BaseModel):
    name: str
    email: str
    password: str

@app.post("/api/register", status_code=201)
async def api_register(user: UserRegister): # <--- Używamy modelu zamiast Form(...)
    """Rejestracja nowego użytkownika przez API (JSON)"""
    
    # Teraz dostęp do danych jest przez kropkę: user.email, user.name
    
    # Sprawdź czy user istnieje
    if database.get_user_by_email(user.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Utwórz użytkownika
    success = database.create_user(user.email, user.name, user.password)
    
    if not success:
        raise HTTPException(status_code=500, detail="Database error")
    
    return {"message": "User created successfully"}



@app.post("/api/login")
async def api_login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Logowanie dla zewnętrznych aplikacji / Swaggera"""
    user = database.get_user_by_email(form_data.username)
    if not user or not database.verify_password(form_data.password, user['password_hash']):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user['email']}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


class DeviceConfigUpdate(BaseModel):
    # Opcjonalne pola, bo użytkownik może nie chcieć zmieniać wszystkiego
    # (chociaż we Flutter wysyłasz cały obiekt, więc przyjdzie wszystko)
    device_name: Optional[str] = None
    sound_model_id: Optional[str] = None
    sleep_timeout: Optional[int] = None
    led_intensity: Optional[int] = None
    system_prompt: Optional[str] = None


@app.post("/api/devices/{device_id}/config", status_code=200)
async def api_update_device_config(
    config_data: DeviceConfigUpdate,
    device_id: str,
    current_user_email: str = Depends(get_current_user_api)
):
    """
    Endpoint dla aplikacji mobilnej (JSON).
    Aktualizuje konfigurację i wysyła powiadomienie MQTT.
    """

    # 1. Weryfikacja użytkownika i własności urządzenia
    user = database.get_user_by_email(current_user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Sprawdź czy to urządzenie należy do tego użytkownika!
    # To bardzo ważne zabezpieczenie.
    device = database.get_device_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Zakładam, że w obiekcie device masz pole 'user_id' lub 'owner_id'
    if str(device['user_id']) != str(user['id']):
        raise HTTPException(status_code=403, detail="Not authorized to configure this device")

    # 2. Aktualizacja w bazie danych
    # Używamy tych samych funkcji co w wersji webowej
    # Jeśli wartości są None (np. Flutter ich nie wysłał), używamy starych lub domyślnych
    
    # Pobieramy stare wartości jeśli nowe nie przyszły (opcjonalne, zależnie od logiki update_device_config)
    # Tutaj zakładam, że update_device_config obsługuje nadpisywanie.
    
    database.update_device_config(
        device_id=device_id,
        device_name=config_data.device_name,
        led_intensity=config_data.led_intensity,
        sound_model_id=config_data.sound_model_id,
        sleep_timeout=config_data.sleep_timeout,
        system_prompt=config_data.system_prompt
    )


    # 3. MQTT Live Update
    # Wysyłamy wiadomość do urządzenia, żeby natychmiast zmieniło ustawienia
    if mqtt_service.client:
        mqtt_service.send_config_update(device_id, device['aes_key'])

    return {"message": "Configuration updated successfully"}

@app.get("/api/devices/{device_id}/config", status_code=200)
async def api_get_device_config(
    device_id: str,
    current_user_email: str = Depends(get_current_user_api)
):
    """Zwraca konfigurację urządzenia w formacie JSON"""
    # Weryfikacja użytkownika i własności urządzenia
    user = database.get_user_by_email(current_user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    device = database.get_device_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if str(device['user_id']) != str(user['id']):
        raise HTTPException(status_code=403, detail="Not authorized to view this device")

    config = database.get_device_config(device_id)
    return config

@app.get("/api/devices/{device_id}/logs", status_code=200)
async def api_get_device_logs(
    device_id: str,
    current_user_email: str = Depends(get_current_user_api)
):
    """Zwraca konfigurację urządzenia w formacie JSON"""
    # Weryfikacja użytkownika i własności urządzenia
    user = database.get_user_by_email(current_user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    device = database.get_device_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if str(device['user_id']) != str(user['id']):
        raise HTTPException(status_code=403, detail="Not authorized to view this device")

    logs = database.get_device_logs(device_id)
    return logs


@app.get("/api/devices", status_code=200)
async def api_devices(
    current_user_email: str = Depends(get_current_user_api)
):
    # Weryfikacja tokenu JWT z ciasteczka
    devices = database.get_user_devices(current_user_email)
    return [{"id": d['device_id'], "status": d['status'], "device_name": d['device_name'], "user_id": d['user_id']} for d in devices]


class DeviceRegister(BaseModel):
    aes_key: str  # Klucz wysyłany przez aplikację mobilną (base64 lub hex)
    device_name: str = "Nowa Kula" # Opcjonalna nazwa

@app.post("/api/device/register", status_code=201)
async def api_register_device(
    device_data: DeviceRegister, 
    current_user_email: str = Depends(get_current_user_api)
):
    """
    Krok 1 parowania: Mobile wysyła AES i tworzymy rekord 'pending'.
    """
    
    # 1. Pobierz ID użytkownika na podstawie maila z tokena
    user = database.get_user_by_email(current_user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 2. Zapisz urządzenie w bazie ze statusem "PENDING"
    # Funkcja database.create_pending_device musi zostać dodana (opis niżej)
    device_id = database.register_pending_device(
        user_id=user['id'], 
        device_name=device_data.name,
        aes_key=device_data.aes_key, 
    )
    return {"device_id": device_id, "message": "Device registered successfully"}

class DevicePairAgain(BaseModel):
    aes_key: str  # Klucz wysyłany przez aplikację mobilną (base64 lub hex)
    device_id: int

@app.post("/api/device/reconnect/{device_id}", status_code=201)
async def api_register_device(
    device_data: DevicePairAgain, 
    current_user_email: str = Depends(get_current_user_api)
):
    """
    Krok 1 parowania: Mobile wysyła AES i tworzymy rekord 'pending'.
    """
    
    # 1. Pobierz ID użytkownika na podstawie maila z tokena
    user = database.get_user_by_email(current_user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 2. Zapisz urządzenie w bazie ze statusem "PENDING"
    # Funkcja database.create_pending_device musi zostać dodana (opis niżej)
    database.update_device_aes_key_and_status(device_id=device_data.device_id, aes_key=device_data.aes_key)
    return {"message": "Device updated successfully"}


@app.get("/api/device/{device_id}/status")
async def api_check_device_status(
    device_id: int, 
    current_user_email: str = Depends(get_current_user_api)
):
    """
    Krok 2 parowania (Polling): Mobile pyta czy status zmienił się na 'PAIRED'.
    """
    
    # 1. Pobierz usera dla bezpieczeństwa (żeby nie sprawdzać cudzych urządzeń)
    user = database.get_user_by_email(current_user_email)
    
    # 2. Pobierz urządzenie z bazy
    device = database.get_device_by_id(device_id)
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    # 3. Sprawdź czy urządzenie należy do tego użytkownika (Security)
    if device['user_id'] != user['id']:
        raise HTTPException(status_code=403, detail="Not authorized to view this device")

    # 4. Zwróć status ("PENDING" lub "PAIRED")
    return {"device_id": device_id, "status": device['status']}

if __name__ == "__main__":
    uvicorn.run("webapp:app", host="0.0.0.0", port=8001, reload=False)