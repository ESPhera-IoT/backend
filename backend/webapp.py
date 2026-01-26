from fastapi import FastAPI, Request, Form, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import uvicorn
import asyncio
import database
import backend_async
import mqtt_service

# --- KONFIGURACJA ---
app = FastAPI(title="ESPhera IoT Server")
templates = Jinja2Templates(directory="templates")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- ZARZĄDZANIE TŁEM (TCP + MQTT) ---
@app.on_event("startup")
async def startup_event():
    """Uruchamia backend asynchroniczny w tle serwera WWW"""
    # Inicjalizacja bazy
    database.init_db()
    
    # Start MQTT i TCP jako zadania w tle
    loop = asyncio.get_event_loop()
    loop.create_task(mqtt_service.start_mqtt())
    
    # Start Serwera TCP (z backend_async.py)
    server = await asyncio.start_server(
        backend_async.handle_client, 
        backend_async.HOST, 
        backend_async.PORT
    )
    loop.create_task(server.serve_forever())
    
    print("--- SYSTEM START: WWW (8000) + TCP (26358) + MQTT ---")

# --- OBSŁUGA STRONY WWW (Web UI) ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
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

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="user_email", value=username)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("user_email")
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
    # Sprawdzenie ciasteczka
    user_email = request.cookies.get("user_email")
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
async def claim_device(request: Request, device_id: str = Form(...), aes_key: str = Form(...)):
    user_email = request.cookies.get("user_email")
    if not user_email: return RedirectResponse("/")
    
    # Logika odsprzedaży / rejestracji
    aes_bytes = aes_key.encode('utf-8')
    if len(aes_bytes) != 16:
        return HTMLResponse("Klucz musi mieć 16 znaków! <a href='/dashboard'>Wróć</a>")

    database.register_or_claim_device(device_id, aes_bytes, user_email)
    
    database.activate_device(device_id)
    
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/config")
async def update_config(
    request: Request, 
    device_id: str = Form(...), 
    led_intensity: int = Form(...),
    ai_model: str = Form(...),
    system_prompt: str = Form(...)
):
    user_email = request.cookies.get("user_email")
    if not user_email: return RedirectResponse("/")
    
    # Zapisz w bazie
    database.update_device_config(device_id, led_intensity, ai_model, system_prompt)
    
    # MQTT Live Update
    if mqtt_service.client:
        import json
        msg = json.dumps({"led": led_intensity})
        mqtt_service.client.publish(f"devices/{device_id}/config", msg)
    
    return RedirectResponse(url="/dashboard", status_code=303)

# --- API REST ---
@app.post("/api/login")
async def api_login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = database.get_user_by_email(form_data.username)
    if not user or not database.verify_password(form_data.password, user['password_hash']):
        return {"error": "Invalid credentials"}
    return {"access_token": form_data.username, "token_type": "bearer"}

@app.get("/api/devices")
async def api_get_devices(token: str = Depends(oauth2_scheme)):
    # Token to po prostu email
    devices = database.get_user_devices(token)
    return [{"id": d['device_id'], "status": d['status']} for d in devices]

if __name__ == "__main__":
    # Uruchamiamy serwer na porcie 8001
    uvicorn.run("webapp:app", host="127.0.0.1", port=8001, reload=False)