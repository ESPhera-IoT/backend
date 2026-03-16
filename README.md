# 🔮 ESPhera (v1.0)

**Inteligentny Asystent Głosowy IoT oparty na ESP32 i AI** 

**ESPhera** to kompleksowy projekt open-source sprzętowego asystenta głosowego, pozwalającego na płynną konwersację w języku naturalnym. System łączy w sobie niskopoziomowe programowanie mikrokontrolerów z potęgą chmurowych modeli sztucznej inteligencji (LLM). Projekt składa się z oprogramowania wbudowanego (Firmware), serwera backendowego oraz aplikacji mobilnej.

---

## 🚀 Kluczowe Funkcje

**Rozmowa z AI w locie:** Interakcja w czasie rzeczywistym dzięki integracji modeli OpenAI Whisper (rozpoznawanie mowy), GPT-4o-mini (logika konwersacji) oraz ElevenLabs (synteza naturalnego głosu).


**Poufność i Bezpieczeństwo:** Wysoki poziom ochrony poprzez szyfrowanie całego strumienia audio i danych konfiguracyjnych symetrycznym algorytmem AES-256 w trybie GCM (sprzętowa akceleracja mbedTLS).


**Hybrydowa Sieć (Zero Lag):** Architektura sieciowa wykorzystująca protokół MQTT do lekkiego sterowania asynchronicznego oraz dedykowany, niestandardowy tunel TCP (protokół ramkowy TLV) do transmisji dźwięku bez narzutu protokołu HTTP.


**Nowoczesny Firmware:** Oprogramowanie układowe napisane w standardzie C++20 z wykorzystaniem środowiska ESP-IDF, wspierające wielowątkowość (FreeRTOS) i eksperymentalne moduły C++.


**Provisioning BLE:** Bezprzewodowa konfiguracja początkowa realizowana przez Bluetooth Low Energy oraz dedykowaną aplikację mobilną, co eliminuje konieczność zaszywania danych WiFi w kodzie.



---

## 🛠️ Architektura Systemu

System działa w oparciu o pełen przekrój nowoczesnego stosu technologicznego:

**Warstwa Urządzenia (IoT):** Zarządza interfejsami, obsługuje kontroler DMA dla audio i utrzymuje deterministyczną maszynę stanów.

**Warstwa Lokalna (Backend):** Serwer napisany w języku Python (FastAPI, AsyncIO) orkiestrujący zapytania, zajmujący się deszyfrowaniem pakietów i zarządzający lokalną bazą danych SQLite.

**Warstwa Mobilna (Klient):** Aplikacja napisana w języku Dart we frameworku Flutter służąca do rejestracji kluczy AES, parowania urządzenia przez BLE oraz personalizacji np. wyboru głosu czy jasności LED.



---

## 🧰 Komponenty Sprzętowe (Hardware)

Do zbudowania fizycznego asystenta wykorzystano ogólnodostępne moduły:

* Mikrokontroler ESP32 DevKit V1 (architektura dwurdzeniowa).


* Cyfrowy mikrofon MEMS INMP441 komunikujący się przez magistralę I2S.


* Wzmacniacz audio klasy D MAX98357A z wbudowanym przetwornikiem DAC.


* Głośnik o mocy 3W (4 Ohm).


* Wyświetlacz graficzny SSD1306 OLED (0.96") obsługiwany przez całkowicie autorski, lekki sterownik I2C.


* Adresowalny pasek diod LED WS2812B do sygnalizacji wizualnej (np. nasłuchiwanie, przetwarzanie).


* Pojemnościowe elektrody dotykowe pełniące rolę przycisków funkcyjnych (m.in. tryb push-to-talk).



---

## 🎓 Autorzy

Twórcy systemu to Kacper Wojciuch, Kevin Stuka, Igor Wadas oraz Jakub Łabuz. Projekt powstał pod opieką dr Dariusza Pałki na Wydziale Elektrotechniki, Automatyki, Informatyki i Inżynierii Biomedycznej (EAIiIB) krakowskiego AGH.

**Chcesz dowiedzieć się więcej o projekcie?**
Zapoznaj się z [Pełną Dokumentacją Techniczną](https://github.com/ESPhera-IoT/backend/blob/main/ESPheraDocu.pdf).
