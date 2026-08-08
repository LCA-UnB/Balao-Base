/*
  Receptor/Transmissor de Solo: u-blox SAM-M10Q + Event-Driven TDM
  - Margem de resposta aumentada para 150ms.
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"

#define RF_FREQUENCY                                910500000 
#define LORA_BANDWIDTH                              0         
#define LORA_SPREADING_FACTOR                       7         
#define LORA_CODINGRATE                             1         
#define LORA_PREAMBLE_LENGTH                        8         
#define LORA_SYMBOL_TIMEOUT                         0         
#define LORA_FIX_LENGTH_PAYLOAD_ON                  false
#define LORA_IQ_INVERSION_ON                        false
#define RX_TIMEOUT_VALUE                            1000
#define BUFFER_SIZE                                 256 

char rxpacket[BUFFER_SIZE];
static RadioEvents_t RadioEvents;

// === FLAGS DE CONTROLE DE RÁDIO ===
volatile bool txDoneFlag = false;
volatile bool rxDoneFlag = false;

// === CONTROLE DE TIMEOUT NÃO BLOQUEANTE ===
bool delay_cmd_send = false;
uint32_t cmd_send_timer = 0;

// --- VARIÁVEIS DE DADOS ---
double r_lat, r_lon;
float r_alt, r_alt_b;
int32_t r_sat, r_fix, r_ack = 0;
int r_hora, r_minuto, r_segundo;
float r_temp, r_press; 
float r_pitch, r_roll, r_yaw;

// --- VARIÁVEIS DE TELECOMANDO ---
int pending_cmd = 0;
bool has_cmd = false;

bool lerLong(const char *chave, int32_t *dest) {
  const char *p = strstr(rxpacket, chave);
  if (p == NULL) return false;
  *dest = (int32_t)atol(p + strlen(chave));
  return true;
}
bool lerFloat(const char *chave, float *dest) {
  const char *p = strstr(rxpacket, chave);
  if (p == NULL) return false;
  *dest = atof(p + strlen(chave));
  return true;
}
bool lerDouble(const char *chave, double *dest) {
  const char *p = strstr(rxpacket, chave);
  if (p == NULL) return false;
  *dest = strtod(p + strlen(chave), NULL);
  return true;
}

void setup() {
  Serial.begin(115200);
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
  
  RadioEvents.RxDone = OnRxDone;
  RadioEvents.TxDone = OnTxDone;         
  RadioEvents.TxTimeout = OnTxTimeout;   
  
  Radio.Init( &RadioEvents );
  Radio.SetChannel( RF_FREQUENCY );
  
  Radio.SetRxConfig( MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                             LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                             LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                             0, true, 0, 0, LORA_IQ_INVERSION_ON, true );
                             
  Radio.SetTxConfig(MODEM_LORA, 18, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000); 

  Serial.println(F("Receptor de Solo Iniciado. Monitorando telemetria..."));
  Radio.Rx(0); 
}

void loop() {
  // === MÁQUINA DE ESTADOS DO RÁDIO ===
  if (txDoneFlag) {
    txDoneFlag = false;
    Serial.println(F("[TX] Comando Transmitido. Base retornando ao RX.\n"));
    Radio.Rx(0); 
  }

  if (rxDoneFlag) {
    rxDoneFlag = false;
    
    int campos = 0;
    if (lerDouble("Lat:", &r_lat))    campos++;
    if (lerDouble("Lon:", &r_lon))    campos++;
    if (lerFloat("Alt:", &r_alt))     campos++;
    if (lerFloat("AltB:", &r_alt_b))  campos++;
    if (lerLong("Sat:", &r_sat))      campos++;
    if (lerLong("Fix:", &r_fix))      campos++;
    if (lerFloat("T:", &r_temp))      campos++;
    if (lerFloat("P:", &r_press))     campos++;
    if (lerFloat("Pitch:", &r_pitch)) campos++;
    if (lerFloat("Roll:", &r_roll))   campos++;
    if (lerFloat("Yaw:", &r_yaw))     campos++;
    if (lerLong("Ack:", &r_ack))      campos++; 

    const char *pt = strstr(rxpacket, "Time:");
    if (pt != NULL && sscanf(pt, "Time:%d:%d:%d", &r_hora, &r_minuto, &r_segundo) == 3) {
      campos++;
    }

    Serial.println(F("\n------- PT2UNB -------"));
    Serial.print(F("Lat:"));   Serial.println(r_lat, 7);
    Serial.print(F("Lon:"));   Serial.println(r_lon, 7);
    Serial.print(F("Alt:"));   Serial.println(r_alt, 1);
    Serial.print(F("AltB:"));  Serial.println(r_alt_b, 1);
    Serial.print(F("Sat:"));   Serial.println(r_sat);
    Serial.print(F("Fix:"));   Serial.println(r_fix);
    Serial.print(F("T:"));     Serial.println(r_temp, 1);
    Serial.print(F("P:"));     Serial.println(r_press, 1);
    Serial.printf("Time:%02d:%02d:%02d\n", r_hora, r_minuto, r_segundo);
    Serial.print(F("Pitch:")); Serial.println(r_pitch, 2);
    Serial.print(F("Roll:"));  Serial.println(r_roll, 2);
    Serial.print(F("Yaw:"));   Serial.println(r_yaw, 2);
    
    Serial.println(F("----------------------"));
    Serial.printf("ACK (Comando Bordo): %d\n", r_ack);
    Serial.println(F("----------------------"));

    if (has_cmd) {
      delay_cmd_send = true;
      cmd_send_timer = millis();
      Serial.println(F(">>> Aguardando 150ms para o balão abrir a escuta..."));
    } else {
      Radio.Rx(0); 
    }
  }

  // === DISPARO TEMPORIZADO DO COMANDO (150ms) ===
  if (delay_cmd_send && (millis() - cmd_send_timer >= 150)) {
    delay_cmd_send = false; 
    char txcmd[32];
    snprintf(txcmd, sizeof(txcmd), "CMD:%d", pending_cmd);
    
    Serial.printf("[TDM] Disparando Comando: %s\n", txcmd);
    Radio.Send((uint8_t*)txcmd, strlen(txcmd));
    has_cmd = false; 
  }

  // === MONITORA ENTRADA DE DADOS DO USUÁRIO ===
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    if (input.length() > 0) {
      pending_cmd = input.toInt();
      has_cmd = true;
      Serial.println(F("\n====================================="));
      Serial.printf(">> COMANDO [%d] ENFILEIRADO PARA ENVIO <<\n", pending_cmd);
      Serial.println(F("====================================="));
    }
  }

  Radio.IrqProcess(); 
}

void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr ) {
  memcpy(rxpacket, payload, size);
  rxpacket[size] = '\0'; 
  rxDoneFlag = true;
}

void OnTxDone(void) {
  txDoneFlag = true;
}

void OnTxTimeout(void) {
  txDoneFlag = true;
}