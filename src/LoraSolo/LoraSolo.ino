/*
  Receptor LoRa: u-blox SAM-M10Q + BME280 + BNO086
  Lê a string recebida e extrai (faz o parse) de volta para variáveis.
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"

#define RF_FREQUENCY                                915000000 // Hz
#define LORA_BANDWIDTH                              0         // [0: 125 kHz]
#define LORA_SPREADING_FACTOR                       7         // [SF7..SF12]
#define LORA_CODINGRATE                             1         // [1: 4/5]
#define LORA_PREAMBLE_LENGTH                        8         // Same for Tx and Rx
#define LORA_SYMBOL_TIMEOUT                         0         // Symbols
#define LORA_FIX_LENGTH_PAYLOAD_ON                  false
#define LORA_IQ_INVERSION_ON                        false

#define RX_TIMEOUT_VALUE                            1000

// AUMENTADO para 250 (Deve ser igual ou maior que o do Transmissor)
#define BUFFER_SIZE                                 250 

char rxpacket[BUFFER_SIZE];

static RadioEvents_t RadioEvents;
int16_t rssi, rxSize;
bool lora_idle = true;

// Variáveis para guardar os dados desempacotados
int32_t r_lat, r_lon, r_alt;
int r_sat;
float r_temp, r_press, r_umid;
float r_ax, r_ay, r_az;
float r_gx, r_gy, r_gz;

void setup() {
    Serial.begin(115200);
    Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
    
    rssi = 0;
  
    RadioEvents.RxDone = OnRxDone;
    Radio.Init( &RadioEvents );
    Radio.SetChannel( RF_FREQUENCY );
    Radio.SetRxConfig( MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                               LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                               LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                               0, true, 0, 0, LORA_IQ_INVERSION_ON, true );

    Serial.println(F("Receptor LoRa Iniciado. Aguardando pacotes..."));
}

void loop()
{
  if(lora_idle)
  {
    lora_idle = false;
    Radio.Rx(0); // Coloca o rádio em modo de escuta contínua
  }
  Radio.IrqProcess(); // Processa os eventos do rádio
}

void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr )
{
    rxSize = size;
    // Copia o payload recebido para o nosso array de caracteres
    memcpy(rxpacket, payload, size);
    rxpacket[size] = '\0'; // Garante que a string termina aqui
    Radio.Sleep();
    
    Serial.println(F("\n====================================="));
    Serial.printf("Pacote Recebido! Tamanho: %d bytes | RSSI: %d | SNR: %d\n", rxSize, rssi, snr);
    
    // 1. Imprime a string crua exatamente como chegou
    Serial.print(F("Texto Bruto: "));
    Serial.println(rxpacket);

    // 2. Tenta fatiar (parse) o texto de volta para variáveis numéricas
    // A máscara do sscanf deve ser EXATAMENTE igual ao snprintf do transmissor
    int lidos = sscanf(rxpacket, "Lat:%ld,Lon:%ld,Alt:%ld,Sat:%d,T:%f,P:%f,U:%f,AX:%f,AY:%f,AZ:%f,GX:%f,GY:%f,GZ:%f", 
           &r_lat, &r_lon, &r_alt, &r_sat, 
           &r_temp, &r_press, &r_umid, 
           &r_ax, &r_ay, &r_az, 
           &r_gx, &r_gy, &r_gz);

    // Verifica se conseguiu ler todas as 13 variáveis com sucesso
    if (lidos == 13) {
      Serial.println(F(">>> Dados extraídos com sucesso:"));
      Serial.printf("  GPS: Lat: %ld | Lon: %ld | Alt: %ld mm | Sat: %d\n", r_lat, r_lon, r_alt, r_sat);
      Serial.printf("Clima: Temp: %.1f C | Pressão: %.1f hPa | Umidade: %.1f %%\n", r_temp, r_press, r_umid);
      Serial.printf(" IMU Accel (X,Y,Z): %.2f, %.2f, %.2f\n", r_ax, r_ay, r_az);
      Serial.printf(" IMU Gyro  (X,Y,Z): %.2f, %.2f, %.2f\n", r_gx, r_gy, r_gz);
    } else {
      // Se algum pacote chegou corrompido no ar, ele avisa
      Serial.println(F("ERRO: Pacote incompleto ou formato incorreto."));
    }

    Serial.println(F("====================================="));
    lora_idle = true; // Libera o rádio para escutar o próximo pacote
}