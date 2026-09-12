/*
  Receptor/Transmissor de Solo - Slots ancorados no GPS do balao
  - O bordo transmite no segundo IMPAR e escuta o segundo PAR inteiro.
  - O solo nao tem GPS: ele deriva a janela a partir do instante em que a
    telemetria termina de chegar, e mira o CENTRO da janela de escuta.
  - Retransmite o comando ate o campo Ack: da telemetria confirmar.

  ATUALIZACAO: adicionada leitura/impressao dos campos de aceleracao
  enviados pelo bordo (AX, AY, AZ instantaneos e AXavg, AYavg, AZavg,
  medias da janela de 1s calculada no bordo).
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"

#define RF_FREQUENCY                                910500000
#define TX_OUTPUT_POWER                             18
#define LORA_BANDWIDTH                              0
#define LORA_SPREADING_FACTOR                       7
#define LORA_CODINGRATE                             1
#define LORA_PREAMBLE_LENGTH                        8
#define LORA_SYMBOL_TIMEOUT                         0
#define LORA_FIX_LENGTH_PAYLOAD_ON                  false
#define LORA_IQ_INVERSION_ON                        false
#define RX_TIMEOUT_VALUE                            1000
#define BUFFER_SIZE                                 256

// --- GEOMETRIA DO SLOT ---
// O bordo comeca a transmitir no topo de um segundo impar; a telemetria
// (~150 bytes em SF7/BW125/CR4-5) ocupa ~250 ms no ar. Logo, quando o
// RxDone dispara aqui, estamos ~250 ms dentro do segundo impar.
// O segundo par seguinte - a janela de escuta - comeca 750 ms depois e
// dura 1000 ms. Miramos o centro dela para ter ~500 ms de folga dos dois
// lados, absorvendo qualquer jitter de um lado ou do outro.
#define AIRTIME_TELEMETRIA_MS                       250
#define JANELA_ESCUTA_MS                            1000
#define ATRASO_COMANDO_MS  ((JANELA_ESCUTA_MS - AIRTIME_TELEMETRIA_MS) + (JANELA_ESCUTA_MS / 2))

// Quantas janelas tentar antes de desistir de um comando.
#define MAX_TENTATIVAS                              5

// A telemetria agora carrega 20 campos (14 originais + AX/AY/AZ +
// AXavg/AYavg/AZavg). Com o CRC ligado, um pacote que chega integro tem
// todos. Menos que isso e' corrupcao: descarta em vez de imprimir os
// valores da leitura anterior como se fossem novos.
#define CAMPOS_ESPERADOS                            20

char rxpacket[BUFFER_SIZE];
static RadioEvents_t RadioEvents;

// === FLAGS DE CONTROLE DE RADIO ===
volatile bool txDoneFlag = false;
volatile bool rxDoneFlag = false;
volatile bool rxErrFlag  = false;
volatile int16_t rxRssi = 0;
volatile int8_t  rxSnr  = 0;

// === AGENDAMENTO DO COMANDO ===
bool     envio_agendado = false;
uint32_t envio_em_ms = 0;

// === CONTADORES DE DIAGNOSTICO ===
uint32_t cntRxOk = 0, cntRxErr = 0, cntDescartado = 0, cntTx = 0;

// --- VARIAVEIS DE DADOS ---
double r_lat, r_lon;
float r_alt, r_alt_b;
int32_t r_sat, r_fix, r_ack = 0;
int r_hora, r_minuto, r_segundo;
float r_temp, r_press, r_bat;
float r_pitch, r_roll, r_yaw;
float r_ax, r_ay, r_az;
float r_axavg, r_ayavg, r_azavg;

// --- VARIAVEIS DE TELECOMANDO ---
int pending_cmd = 0;
bool has_cmd = false;
uint8_t tentativas = 0;

// --- ENTRADA SERIAL NAO BLOQUEANTE ---
char serialBuf[32];
uint8_t serialLen = 0;

void OnTxDone( void );
void OnTxTimeout( void );
void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr );
void OnRxTimeout( void );
void OnRxError( void );

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

// ==========================================================================
//  RADIO
// ==========================================================================

void reconfigurarLoRaTX() {
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000);
}

void reconfigurarLoRaRX() {
  Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                    LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                    LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                    0, true, 0, 0, LORA_IQ_INVERSION_ON, true);
}

void abrirEscuta() {
  Radio.Standby();
  reconfigurarLoRaRX();
  Radio.Rx(0);
}

inline void servicoRadio() {
  Radio.IrqProcess();
}

void agendarEnvio() {
  envio_agendado = true;
  envio_em_ms = millis() + ATRASO_COMANDO_MS;
}

// ==========================================================================
//  SETUP
// ==========================================================================

void setup() {
  Serial.begin(115200);
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);

  RadioEvents.RxDone    = OnRxDone;
  RadioEvents.TxDone    = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxTimeout = OnRxTimeout;
  RadioEvents.RxError   = OnRxError;

  Radio.Init(&RadioEvents);
  Radio.SetChannel(RF_FREQUENCY);
  reconfigurarLoRaTX();
  reconfigurarLoRaRX();

  Serial.println(F("Estacao de Solo iniciada."));
  Serial.printf("Comando sera disparado %d ms apos cada telemetria (centro da janela de escuta).\n", ATRASO_COMANDO_MS);
  Serial.println(F("Digite um numero e ENTER para enviar um telecomando.\n"));

  abrirEscuta();
}

// ==========================================================================
//  RECEPCAO
// ==========================================================================

void processarTelemetria() {
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
  if (lerFloat("AX:", &r_ax))       campos++;
  if (lerFloat("AY:", &r_ay))       campos++;
  if (lerFloat("AZ:", &r_az))       campos++;
  if (lerFloat("AXavg:", &r_axavg)) campos++;
  if (lerFloat("AYavg:", &r_ayavg)) campos++;
  if (lerFloat("AZavg:", &r_azavg)) campos++;
  if (lerFloat("Bat:", &r_bat))     campos++;
  if (lerLong("Ack:", &r_ack))      campos++;

  const char *pt = strstr(rxpacket, "Time:");
  if (pt != NULL && sscanf(pt, "Time:%d:%d:%d", &r_hora, &r_minuto, &r_segundo) == 3) {
    campos++;
  }

  if (campos < CAMPOS_ESPERADOS) {
    cntDescartado++;
    Serial.printf("[AVISO] Pacote incompleto: %d/%d campos. Descartado (total: %lu).\n",
                  campos, CAMPOS_ESPERADOS, cntDescartado);
    abrirEscuta();
    return;
  }

  Serial.println(F("\n------- PT2UNB -------"));
  Serial.printf("RSSI:%d dBm | SNR:%d dB\n", rxRssi, rxSnr);
  Serial.print(F("Lat:"));    Serial.println(r_lat, 7);
  Serial.print(F("Lon:"));    Serial.println(r_lon, 7);
  Serial.print(F("Alt:"));    Serial.println(r_alt, 1);
  Serial.print(F("AltB:"));   Serial.println(r_alt_b, 1);
  Serial.print(F("Sat:"));    Serial.println(r_sat);
  Serial.print(F("Fix:"));    Serial.println(r_fix);
  Serial.print(F("T:"));      Serial.println(r_temp, 1);
  Serial.print(F("P:"));      Serial.println(r_press, 1);
  Serial.printf("Time:%02d:%02d:%02d\n", r_hora, r_minuto, r_segundo);
  Serial.print(F("Pitch:"));  Serial.println(r_pitch, 2);
  Serial.print(F("Roll:"));   Serial.println(r_roll, 2);
  Serial.print(F("Yaw:"));    Serial.println(r_yaw, 2);
  Serial.print(F("AX:"));     Serial.println(r_ax, 3);
  Serial.print(F("AY:"));     Serial.println(r_ay, 3);
  Serial.print(F("AZ:"));     Serial.println(r_az, 3);
  Serial.print(F("AXavg:"));  Serial.println(r_axavg, 3);
  Serial.print(F("AYavg:"));  Serial.println(r_ayavg, 3);
  Serial.print(F("AZavg:"));  Serial.println(r_azavg, 3);
  Serial.print(F("Bat:"));    Serial.println(r_bat, 2);
  Serial.printf("Ack:%ld\n", (long)r_ack);
  Serial.println(F("----------------------"));

  if (!has_cmd) {
    abrirEscuta();
    return;
  }

  // Exige ao menos uma transmissao antes de aceitar a confirmacao, para nao
  // confundir um Ack: remanescente de uma sessao anterior com uma resposta.
  if (tentativas > 0 && r_ack == pending_cmd + 1) {
    Serial.printf(">>> COMANDO %d CONFIRMADO pelo balao (Ack:%ld) apos %d tentativa(s).\n\n",
                  pending_cmd, (long)r_ack, tentativas);
    has_cmd = false;
    abrirEscuta();
    return;
  }

  if (tentativas >= MAX_TENTATIVAS) {
    Serial.printf(">>> COMANDO %d NAO confirmado apos %d tentativas. Desistindo.\n",
                  pending_cmd, tentativas);
    Serial.println(F("    Verifique alcance, antena e se o bordo esta transmitindo.\n"));
    has_cmd = false;
    abrirEscuta();
    return;
  }

  Serial.printf("[TDM] Comando %d aguardando janela (tentativa %d de %d, em %d ms).\n",
                pending_cmd, tentativas + 1, MAX_TENTATIVAS, ATRASO_COMANDO_MS);
  agendarEnvio();
}

// ==========================================================================
//  LOOP
// ==========================================================================

void loop() {
  if (txDoneFlag) {
    txDoneFlag = false;
    Serial.println(F("[TX] Comando no ar. Voltando a escutar."));
  }

  if (rxDoneFlag) {
    rxDoneFlag = false;
    cntRxOk++;
    processarTelemetria();
  }

  if (rxErrFlag) {
    rxErrFlag = false;
    cntRxErr++;
    Serial.printf("[LORA] Recepcao falhou (CRC/timeout). Total de erros: %lu\n", cntRxErr);
    abrirEscuta();
  }

  servicoRadio();

  // === DISPARO NA JANELA DE ESCUTA DO BORDO ===
  // Comparacao com sinal para sobreviver ao rollover de millis().
  if (envio_agendado && (int32_t)(millis() - envio_em_ms) >= 0) {
    envio_agendado = false;
    tentativas++;

    char txcmd[32];
    snprintf(txcmd, sizeof(txcmd), "CMD:%d", pending_cmd);
    Serial.printf("[TDM] Disparando '%s' (tentativa %d de %d).\n", txcmd, tentativas, MAX_TENTATIVAS);

    Radio.Standby();
    reconfigurarLoRaTX();
    Radio.Send((uint8_t *)txcmd, strlen(txcmd));
    cntTx++;
  }

  servicoRadio();

  // === ENTRADA DO USUARIO (NAO BLOQUEANTE) ===
  // readStringUntil() bloquearia ate 1 s no timeout padrao de Stream, o que
  // custaria telemetria e deslocaria o disparo do comando.
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLen > 0) {
        serialBuf[serialLen] = '\0';
        pending_cmd = atoi(serialBuf);
        has_cmd = true;
        tentativas = 0;
        Serial.println(F("\n====================================="));
        Serial.printf(">> COMANDO [%d] ENFILEIRADO <<\n", pending_cmd);
        Serial.printf("   Sera enviado na proxima janela de escuta do balao.\n");
        Serial.println(F("====================================="));
        serialLen = 0;
      }
    } else if (serialLen < sizeof(serialBuf) - 1) {
      serialBuf[serialLen++] = c;
    }
  }

  servicoRadio();
}

// ==========================================================================
//  CALLBACKS
// ==========================================================================

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  if (size >= BUFFER_SIZE) size = BUFFER_SIZE - 1;
  memcpy(rxpacket, payload, size);
  rxpacket[size] = '\0';
  rxRssi = rssi;
  rxSnr = snr;
  rxDoneFlag = true;
}

void OnTxDone(void) {
  abrirEscuta();
  txDoneFlag = true;
}

void OnTxTimeout(void) {
  abrirEscuta();
  txDoneFlag = true;
}

void OnRxTimeout(void) {
  rxErrFlag = true;
}

void OnRxError(void) {
  rxErrFlag = true;
}
