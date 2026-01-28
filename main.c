#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "driver/touch_sens.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "mbedtls/aes.h"
#include "led_strip.h"

// --- KONFIGURACJA ---
#define WIFI_SSID       "iPhone Kevin"
#define WIFI_PASSWORD   "haselko1"
#define MQTT_BROKER_URL "mqtt://172.20.10.4" // <--- IP BACKENDU

#define DEVICE_ID       "esp_nowy"
#define AES_KEY         "1234567890123456"

// KONFIGURACJA LED
#define LED_STRIP_GPIO  32
#define LED_STRIP_LEDS  30
#define LED_RMT_RES_HZ  (10 * 1000 * 1000)

// PINY AUDIO / DOTYKU
#define MIC_SCK_GPIO    17
#define MIC_WS_GPIO     16
#define MIC_SD_GPIO     5
#define SPK_BCLK_GPIO   26
#define SPK_LRC_GPIO    27
#define SPK_DIN_GPIO    25
#define SPK_SD_GPIO     14
#define TOUCH_GPIO      4
#define TOUCH_THRESH    500
#define SAMPLE_RATE     44100
#define BUFF_SIZE       1024

static const char *TAG = "ESPhera_WS2812";

// STANY SYSTEMU (Dla LEDów)
typedef enum {
    STATE_BOOT,
    STATE_WIFI_CONNECTING,
    STATE_WIFI_OK,
    STATE_WIFI_FAIL,
    STATE_LISTENING, // Nagrywanie
    STATE_THINKING,  // Czekanie na AI
    STATE_SPEAKING   // Odtwarzanie
} system_state_t;

static volatile system_state_t current_state = STATE_BOOT;
static volatile int g_led_brightness = 50;
static led_strip_handle_t led_strip = NULL;

// Zmienne sieciowe
static esp_mqtt_client_handle_t mqtt_client = NULL;
static bool mqtt_connected = false;
static EventGroupHandle_t s_flow_event_group;
#define FLOW_SETUP_RECEIVED_BIT   BIT0
#define FLOW_READY_RECEIVED_BIT   BIT1

typedef struct { char ip[16]; int port; char token[64]; } session_config_t;
typedef struct { char ip[16]; int port; char token[64]; int size; } download_config_t;
static session_config_t upload_cfg;
static download_config_t download_cfg;

static i2s_chan_handle_t tx_chan = NULL;
static i2s_chan_handle_t rx_chan = NULL;
static touch_channel_handle_t touch_handle = NULL;

// --- OBSŁUGA LED STRIP (Zintegrowana) ---

// Pomocnicza funkcja: ustawia kolor z uwzględnieniem globalnej jasności
static void set_strip_color(uint8_t r, uint8_t g, uint8_t b) {
    if (!led_strip) return;
    
    // Skalowanie jasności (proste mnożenie)
    uint8_t scale_r = (r * g_led_brightness) / 100;
    uint8_t scale_g = (g * g_led_brightness) / 100;
    uint8_t scale_b = (b * g_led_brightness) / 100;

    for (int i = 0; i < LED_STRIP_LEDS; i++) {
        led_strip_set_pixel(led_strip, i, scale_r, scale_g, scale_b);
    }
    led_strip_refresh(led_strip);
}

static void strip_off(void) {
    if (!led_strip) return;
    led_strip_clear(led_strip);
    led_strip_refresh(led_strip);
}

// Animacja Startowa (Knight Rider)
static void anim_startup(void) {
    if (!led_strip) return;
    for (int i = 0; i < LED_STRIP_LEDS; i++) {
        led_strip_set_pixel(led_strip, i, 50, 0, 100); // Fiolet
        led_strip_refresh(led_strip);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    for (int i = LED_STRIP_LEDS - 1; i >= 0; i--) {
        led_strip_set_pixel(led_strip, i, 0, 0, 0);
        led_strip_refresh(led_strip);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// Główny Wątek LED (Działa w tle)
static void led_task(void* arg) {
    while (1) {
        switch (current_state) {
            case STATE_BOOT:
                strip_off();
                vTaskDelay(pdMS_TO_TICKS(100));
                break;

            case STATE_WIFI_CONNECTING: // Żółte miganie
                set_strip_color(100, 80, 0);
                vTaskDelay(pdMS_TO_TICKS(500));
                strip_off();
                vTaskDelay(pdMS_TO_TICKS(500));
                break;

            case STATE_WIFI_OK: // Zielony (Standby)
                set_strip_color(0, 50, 0); // Lekko zielony
                vTaskDelay(pdMS_TO_TICKS(1000));
                break;

            case STATE_WIFI_FAIL: // Czerwony Flash
                set_strip_color(100, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(200));
                strip_off();
                vTaskDelay(pdMS_TO_TICKS(200));
                break;

            case STATE_LISTENING: // CZERWONY (Nagrywanie)
                set_strip_color(255, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(100));
                break;

            case STATE_THINKING: // NIEBIESKI PULS (Myślenie)
                set_strip_color(0, 0, 255);
                vTaskDelay(pdMS_TO_TICKS(300));
                strip_off();
                vTaskDelay(pdMS_TO_TICKS(300));
                break;

            case STATE_SPEAKING: // FIOLETOWY (Mówienie)
                set_strip_color(120, 0, 200);
                vTaskDelay(pdMS_TO_TICKS(100));
                break;
        }
    }
}

void init_leds() {
    led_strip_config_t strip_config = {
        .strip_gpio_num = LED_STRIP_GPIO,
        .max_leds = LED_STRIP_LEDS,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = LED_RMT_RES_HZ,
        .flags.with_dma = false,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip));
    
    // Uruchomienie taska w tle
    xTaskCreate(led_task, "led_task", 4096, NULL, 5, NULL);
    
    anim_startup();
}


// --- SZYFROWANIE ---
void encrypt_buffer(uint8_t *buffer, size_t len, const uint8_t *key) {
    mbedtls_aes_context aes; mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_enc(&aes, key, 128);
    for (size_t i = 0; i < len; i += 16) mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, buffer+i, buffer+i);
    mbedtls_aes_free(&aes);
}
void decrypt_buffer(uint8_t *buffer, size_t len, const uint8_t *key) {
    mbedtls_aes_context aes; mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_dec(&aes, key, 128);
    for (size_t i = 0; i < len; i += 16) mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_DECRYPT, buffer+i, buffer+i);
    mbedtls_aes_free(&aes);
}

// --- MQTT ---
static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    if (event_id == MQTT_EVENT_CONNECTED) {
        mqtt_connected = true;
        char topic[128];
        snprintf(topic, sizeof(topic), "devices/%s/response/setup", DEVICE_ID);
        esp_mqtt_client_subscribe(mqtt_client, topic, 0);
        snprintf(topic, sizeof(topic), "devices/%s/response/ready", DEVICE_ID);
        esp_mqtt_client_subscribe(mqtt_client, topic, 0);
        snprintf(topic, sizeof(topic), "devices/%s/config", DEVICE_ID);
        esp_mqtt_client_subscribe(mqtt_client, topic, 0);
        
    } else if (event_id == MQTT_EVENT_DATA) {
        cJSON *root = cJSON_ParseWithLength(event->data, event->data_len);
        if (!root) return;

        if (strstr(event->topic, "response/setup")) {
            cJSON *ip = cJSON_GetObjectItem(root, "ip");
            cJSON *port = cJSON_GetObjectItem(root, "port");
            cJSON *token = cJSON_GetObjectItem(root, "session_token");
            if (ip && port && token) {
                strncpy(upload_cfg.ip, ip->valuestring, sizeof(upload_cfg.ip));
                upload_cfg.port = port->valueint;
                strncpy(upload_cfg.token, token->valuestring, sizeof(upload_cfg.token));
                xEventGroupSetBits(s_flow_event_group, FLOW_SETUP_RECEIVED_BIT);
            }
        } else if (strstr(event->topic, "response/ready")) {
            cJSON *ip = cJSON_GetObjectItem(root, "host");
            cJSON *port = cJSON_GetObjectItem(root, "port");
            cJSON *token = cJSON_GetObjectItem(root, "session_token");
            if (ip && port && token) {
                strncpy(download_cfg.ip, ip->valuestring, sizeof(download_cfg.ip));
                download_cfg.port = port->valueint;
                strncpy(download_cfg.token, token->valuestring, sizeof(download_cfg.token));
                xEventGroupSetBits(s_flow_event_group, FLOW_READY_RECEIVED_BIT);
            }
        } else if (strstr(event->topic, "config")) {
            // OBSŁUGA LIVE CONFIG (JASNOŚĆ)
            cJSON *led = cJSON_GetObjectItem(root, "led");
            if (led) {
                g_led_brightness = led->valueint; // 0-100
                ESP_LOGI(TAG, "Nowa jasność LED: %d%%", g_led_brightness);
            }
        }
        cJSON_Delete(root);
    } else if (event_id == MQTT_EVENT_DISCONNECTED) {
        mqtt_connected = false;
        current_state = STATE_WIFI_FAIL; // Lub reconnecting
    }
}

// --- TCP ---
int connect_tcp(const char *ip, int port) {
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(ip);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(port);
    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (sock < 0) return -1;
    if (connect(sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr)) != 0) {
        close(sock); return -1;
    }
    return sock;
}

// --- ROZMOWA ---
void run_conversation_cycle() {
    xEventGroupClearBits(s_flow_event_group, FLOW_SETUP_RECEIVED_BIT | FLOW_READY_RECEIVED_BIT);

    // 1. START
    char topic[128];
    snprintf(topic, sizeof(topic), "devices/%s/request/start", DEVICE_ID);
    esp_mqtt_client_publish(mqtt_client, topic, "{}", 0, 0, 0);

    if (!(xEventGroupWaitBits(s_flow_event_group, FLOW_SETUP_RECEIVED_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(5000)) & FLOW_SETUP_RECEIVED_BIT)) {
        ESP_LOGE(TAG, "Timeout SETUP"); return;
    }

    // 2. UPLOAD (Nagrywanie)
    int sock = connect_tcp(upload_cfg.ip, upload_cfg.port);
    if (sock < 0) return;
    char token_msg[70]; snprintf(token_msg, sizeof(token_msg), "%s\n", upload_cfg.token);
    send(sock, token_msg, strlen(token_msg), 0);

    size_t bytes_read = 0;
    int32_t *raw_buff = calloc(BUFF_SIZE, sizeof(int32_t));
    uint8_t *send_buff = calloc(BUFF_SIZE * 3, sizeof(uint8_t));

    ESP_LOGI(TAG, "Nagrywanie...");
    current_state = STATE_LISTENING; // --> LED CZERWONY

    while (1) {
        uint32_t touch_val = 0;
        touch_channel_read_data(touch_handle, TOUCH_CHAN_DATA_TYPE_SMOOTH, &touch_val);
        if (touch_val > TOUCH_THRESH) break; // Puszczono
        
        if (i2s_channel_read(rx_chan, raw_buff, BUFF_SIZE * 4, &bytes_read, 100) == ESP_OK) {
            int samples = bytes_read / 4;
            for (int i=0; i<samples; i++) {
                uint32_t u = raw_buff[i];
                send_buff[i*3+0]=(u>>8)&0xFF; send_buff[i*3+1]=(u>>16)&0xFF; send_buff[i*3+2]=(u>>24)&0xFF;
            }
            encrypt_buffer(send_buff, samples*3, (uint8_t*)AES_KEY);
            send(sock, send_buff, samples*3, 0);
        }
    }
    free(raw_buff); free(send_buff); close(sock);
    
    ESP_LOGI(TAG, "Czekam na AI...");
    current_state = STATE_THINKING; // --> LED NIEBIESKI PULSUJĄCY

    // 3. CZEKANIE (Maks 60s)
    if (!(xEventGroupWaitBits(s_flow_event_group, FLOW_READY_RECEIVED_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(60000)) & FLOW_READY_RECEIVED_BIT)) {
        ESP_LOGE(TAG, "Timeout READY"); 
        current_state = STATE_WIFI_OK; // Wróć do normy
        return;
    }

    // 4. DOWNLOAD (Odtwarzanie)
    sock = connect_tcp(download_cfg.ip, download_cfg.port);
    if (sock < 0) { current_state = STATE_WIFI_OK; return; }
    snprintf(token_msg, sizeof(token_msg), "%s\n", download_cfg.token);
    send(sock, token_msg, strlen(token_msg), 0);

    gpio_set_level(SPK_SD_GPIO, 1);
    uint8_t *rx_buff = malloc(4096);
    size_t w_bytes;
    
    current_state = STATE_SPEAKING; // --> LED FIOLETOWY

    while (true) {
        int len = recv(sock, rx_buff, 4096, 0);
        if (len <= 0) break;
        size_t safe_len = (len / 16) * 16;
        if (safe_len > 0) {
            decrypt_buffer(rx_buff, safe_len, (uint8_t*)AES_KEY);
            i2s_channel_write(tx_chan, rx_buff, safe_len, &w_bytes, 1000);
        }
    }
    free(rx_buff); close(sock); gpio_set_level(SPK_SD_GPIO, 0);
    
    // 5. UPDATE RSSI + POWRÓT
    wifi_ap_record_t ap_info;
    if(esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
        char state_topic[128]; char state_payload[64];
        snprintf(state_topic, sizeof(state_topic), "devices/%s/state", DEVICE_ID);
        snprintf(state_payload, sizeof(state_payload), "{\"rssi\": %d}", ap_info.rssi);
        esp_mqtt_client_publish(mqtt_client, state_topic, state_payload, 0, 0, 0);
    }
    current_state = STATE_WIFI_OK;
}

// --- INIT ---
void init_hardware() {
    // --- 1. NAJPIERW LEDY (Żeby I2S nie ukradł zasobów RMT) ---
    init_leds(); 
    // Krótkie opóźnienie dla stabilizacji
    vTaskDelay(pdMS_TO_TICKS(100)); 

    // 2. Inicjalizacja Pinu SD dla głośnika
    gpio_set_direction(SPK_SD_GPIO, GPIO_MODE_OUTPUT); 
    gpio_set_level(SPK_SD_GPIO, 0);

    // 3. Inicjalizacja WiFi i NVS
    nvs_flash_init(); 
    esp_netif_init(); 
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT(); 
    esp_wifi_init(&cfg);
    esp_wifi_set_mode(WIFI_MODE_STA);
    
    wifi_config_t w_cfg = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASSWORD } };
    esp_wifi_set_config(WIFI_IF_STA, &w_cfg);
    esp_wifi_start(); 
    esp_wifi_connect();

    // 4. I2S TX (Głośnik) - Kontroler 0
    i2s_chan_config_t tx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    tx_cfg.auto_clear = true; 
    i2s_new_channel(&tx_cfg, &tx_chan, NULL);

    i2s_std_config_t tx_std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(16, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SPK_BCLK_GPIO,
            .ws = SPK_LRC_GPIO,
            .dout = SPK_DIN_GPIO,
            .din = -1,
        },
    };
    i2s_channel_init_std_mode(tx_chan, &tx_std);
    i2s_channel_enable(tx_chan);

    // 5. I2S RX (Mikrofon) - Kontroler 1
    i2s_chan_config_t rx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    i2s_new_channel(&rx_cfg, NULL, &rx_chan);

    i2s_std_config_t rx_std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(32, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_SCK_GPIO,
            .ws = MIC_WS_GPIO,
            .dout = -1,
            .din = MIC_SD_GPIO,
        },
    };
    i2s_channel_init_std_mode(rx_chan, &rx_std);
    i2s_channel_enable(rx_chan);

    // 6. Touch Sensor
    touch_sensor_sample_config_t s_cfg = TOUCH_SENSOR_V1_DEFAULT_SAMPLE_CONFIG(5.0, TOUCH_VOLT_LIM_L_0V5, TOUCH_VOLT_LIM_H_1V7);
    touch_sensor_config_t t_cfg = TOUCH_SENSOR_DEFAULT_BASIC_CONFIG(TOUCH_SAMPLE_CFG_NUM, &s_cfg);
    touch_sensor_handle_t h = NULL; 
    touch_sensor_new_controller(&t_cfg, &h);
    
    touch_channel_config_t ch_cfg = { 
        .abs_active_thresh = {1000}, 
        .charge_speed = TOUCH_CHARGE_SPEED_7, 
        .init_charge_volt = TOUCH_INIT_CHARGE_VOLT_DEFAULT, 
        .group = TOUCH_CHAN_TRIG_GROUP_BOTH 
    };
    touch_sensor_new_channel(h, TOUCH_GPIO, &ch_cfg, &touch_handle);
    
    touch_sensor_filter_config_t f_cfg = TOUCH_SENSOR_DEFAULT_FILTER_CONFIG();
    touch_sensor_config_filter(h, &f_cfg);
    touch_sensor_enable(h); 
    touch_sensor_start_continuous_scanning(h);
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        current_state = STATE_WIFI_CONNECTING;
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        current_state = STATE_WIFI_FAIL;
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        current_state = STATE_WIFI_OK;
    }
}

void app_main(void) {
    s_flow_event_group = xEventGroupCreate();
    init_hardware();
    
    // Rejestracja callbacka wifi do zmiany koloru LED
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL);

    vTaskDelay(pdMS_TO_TICKS(5000));
    
    esp_mqtt_client_config_t mqtt_cfg = { .broker.address.uri = MQTT_BROKER_URL };
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(mqtt_client);

    while (1) {
        uint32_t val = 0;
        touch_channel_read_data(touch_handle, TOUCH_CHAN_DATA_TYPE_SMOOTH, &val);
        if (val < TOUCH_THRESH && mqtt_connected) {
            vTaskDelay(pdMS_TO_TICKS(100));
            touch_channel_read_data(touch_handle, TOUCH_CHAN_DATA_TYPE_SMOOTH, &val);
            if (val < TOUCH_THRESH) {
                run_conversation_cycle();
                vTaskDelay(pdMS_TO_TICKS(1000));
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}