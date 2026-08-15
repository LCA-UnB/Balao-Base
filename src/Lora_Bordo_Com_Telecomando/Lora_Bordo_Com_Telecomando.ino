/*
  LoraBordo - GY-86 (10DOF) + GPS + Telecomando (Slots ancorados no GPS)
  - Segundo IMPAR = janela de transmissao da telemetria
  - Segundo PAR   = janela de escuta de telecomandos (1000 ms inteiros)
  - SD Card desobstruido para nao travar o RTOS do Radio.
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"
#include <Wire.h>
#include <SparkFun_u-blox_GNSS_v3.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_HMC5883_U.h>
#include <MS5611.h>

// --- LORA ---
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

// Cadencia nominal de um slot completo (par + impar) = 2000 ms.
// Um intervalo minimo de 1500 ms entre disparos garante no maximo um
// evento por slot mesmo se o segundo do GPS oscilar ou retroceder.
#define SLOT_INTERVALO_MIN_MS                       1500

char txpacket[BUFFER_SIZE];
char rxpacket[BUFFER_SIZE];
static RadioEvents_t RadioEvents;

// === FLAGS DE CONTROLE DE RADIO ===
volatile bool txDoneFlag = false;
volatile bool rxDoneFlag = false;
volatile bool rxErrFlag  = false;
volatile int16_t rxRssi = 0;
volatile int8_t  rxSnr  = 0;

// === CONTADORES DE DIAGNOSTICO ===
uint32_t cntTx = 0, cntRxOk = 0, cntRxErr = 0, cntLogPulado = 0;

void OnTxDone( void );
void OnTxTimeout( void );
void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr );
void OnRxTimeout( void );
void OnRxError( void );

// --- OPENLOG ---
#define OPENLOG_RX 3
#define OPENLOG_TX 2
HardwareSerial OpenLogSerial(2);

// --- PINOS E SENSORES ---
#define SDA_GPS_PIN 41
#define SCL_GPS_PIN 42
#define SDA_GY86_PIN 48
#define SCL_GY86_PIN 47
#define REFERENCE_PRESSURE_HPA 1013.25
#define TEMP_CORR (-2)

SFE_UBLOX_GNSS myGNSS;
Adafruit_MPU6050 mpu;
Adafruit_HMC5883_Unified mag = Adafruit_HMC5883_Unified(12345);
MS5611 ms5611(0x77);

// --- VARIAVEIS IMU ---
float lastAccelX = 0, lastAccelY = 0, lastAccelZ = 0;
float lastGyroX = 0, lastGyroY = 0, lastGyroZ = 0;
float lastMagX = 0, lastMagY = 0, lastMagZ = 0;
float lastPitch = 0, lastRoll = 0, lastYaw = 0;
float roll = 0.0, pitch = 0.0, yaw = 0.0;
float pitchOffset = 0.0, rollOffset = 0.0, yawOffset = 0.0;
unsigned long lastTime = 0;
const float alpha = 0.98;
uint32_t imuInterval = 200;
uint32_t lastImuTick = 0;

bool openLogConectado = false;
bool openLogAltaVelocidade = false;
uint32_t loteContador = 0;
char nomeArquivoSD[32];

// --- VARIAVEIS CACHE GPS ---
int32_t t_lat = 0, t_lon = 0;
float t_alt = 0;
uint8_t t_siv = 0, t_fix = 0;
uint8_t t_hora = 0, t_min = 0, t_sec = 0;

// --- RELOGIO TDM ---
uint8_t  tdm_sec_ancora = 0;   // segundo do GPS no instante da ancora
uint32_t tdm_ancora_ms  = 0;   // millis() daquele instante
bool     tdm_valido     = false;
uint8_t  last_tdm_sec   = 255;
uint32_t ultimoTxMs      = 0;
uint32_t ultimaJanelaMs  = 0;

int ack_val = 0;
bool ack_pendente = false;

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

// Coloca o radio em escuta continua a partir de um estado conhecido.
void abrirEscuta() {
  Radio.Standby();
  reconfigurarLoRaRX();
  Radio.Rx(0);
}

// O radio e' um recurso polled: sem IrqProcess() nenhum callback dispara.
// Chamado entre os blocos pesados do loop, nao apenas no fim dele.
inline void servicoRadio() {
  Radio.IrqProcess();
}

// ==========================================================================
//  OPENLOG
// ==========================================================================

// Escreve no SD apenas se a linha couber no buffer da UART. Uma linha de
// 150 bytes a 9600 baud leva ~155 ms para sair; esperar por isso dentro do
// loop custaria a janela de escuta do radio, entao o log e' o que cede.
void logSD(const char *linha) {
  if (!openLogConectado) return;
  size_t necessario = strlen(linha) + 2;
  if ((size_t)OpenLogSerial.availableForWrite() < necessario) {
    cntLogPulado++;
    return;
  }
  OpenLogSerial.println(linha);
}

bool entrarModoComandoOpenLog(bool comSilencio) {
  if (comSilencio) {
    uint32_t tq = millis();
    while (millis() - tq < 1000) {
      while (OpenLogSerial.available()) OpenLogSerial.read();
      delay(10);
    }
  }
  while (OpenLogSerial.available()) OpenLogSerial.read();
  for (int i = 0; i < 3; i++) { OpenLogSerial.write(0x1A); delay(10); }
  OpenLogSerial.write(0x0D);

  bool achou = false;
  uint32_t t0 = millis();
  while (millis() - t0 < 150) {
    while (OpenLogSerial.available()) {
      uint8_t b = OpenLogSerial.read();
      if ((char)b == '<' || (char)b == '>') achou = true;
    }
  }
  return achou;
}

void iniciarOpenLog() {
  delay(2000);
  OpenLogSerial.begin(57600, SERIAL_8N1, OPENLOG_RX, OPENLOG_TX);
  delay(500);
  if (entrarModoComandoOpenLog(true)) {
    openLogConectado = true; openLogAltaVelocidade = true; imuInterval = 25;
    return;
  }
  OpenLogSerial.end();
  OpenLogSerial.begin(9600, SERIAL_8N1, OPENLOG_RX, OPENLOG_TX);
  delay(500);
  if (entrarModoComandoOpenLog(true)) {
    openLogConectado = true; openLogAltaVelocidade = false; imuInterval = 200;
    return;
  }
  openLogConectado = false; openLogAltaVelocidade = false;
}

// ==========================================================================
//  IMU
// ==========================================================================

void atualizarIMU() {
  unsigned long currentTime = micros();
  float dt = (currentTime - lastTime) / 1000000.0;
  lastTime = currentTime;

  sensors_event_t a, g, temp_mpu, m;
  mpu.getEvent(&a, &g, &temp_mpu);
  mag.getEvent(&m);

  lastAccelX = a.acceleration.x; lastAccelY = a.acceleration.y; lastAccelZ = a.acceleration.z;
  lastGyroX = g.gyro.x;          lastGyroY = g.gyro.y;          lastGyroZ = g.gyro.z;
  lastMagX = m.magnetic.x;       lastMagY = m.magnetic.y;       lastMagZ = m.magnetic.z;

  float accRoll = atan2(a.acceleration.y, a.acceleration.z) * 180.0 / PI;
  float accPitch = atan2(-a.acceleration.x, sqrt(a.acceleration.y * a.acceleration.y + a.acceleration.z * a.acceleration.z)) * 180.0 / PI;

  float gyroRollRate = g.gyro.x * 180.0 / PI;
  float gyroPitchRate = g.gyro.y * 180.0 / PI;

  roll = alpha * (roll + gyroRollRate * dt) + (1.0 - alpha) * accRoll;
  pitch = alpha * (pitch + gyroPitchRate * dt) + (1.0 - alpha) * accPitch;

  float rollRad = roll * PI / 180.0;
  float pitchRad = pitch * PI / 180.0;

  float Xh = lastMagX * cos(pitchRad) + lastMagZ * sin(pitchRad);
  float Yh = lastMagX * sin(rollRad) * sin(pitchRad) + lastMagY * cos(rollRad) - lastMagZ * sin(rollRad) * cos(pitchRad);
  yaw = atan2(Yh, Xh) * 180.0 / PI;
  if (yaw < 0) yaw += 360.0;
}

// ==========================================================================
//  RELOGIO TDM
// ==========================================================================

uint8_t tdmSegundoAtual() {
  if (!tdm_valido) return (millis() / 1000) % 60;   // sem fix: relogio livre
  return (uint8_t)((tdm_sec_ancora + ((millis() - tdm_ancora_ms) / 1000)) % 60);
}

// ==========================================================================
//  SETUP
// ==========================================================================

void setup() {
  Serial.begin(115200);
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
  delay(1000);

  RadioEvents.TxDone    = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxDone    = OnRxDone;
  RadioEvents.RxTimeout = OnRxTimeout;
  RadioEvents.RxError   = OnRxError;

  Radio.Init(&RadioEvents);
  Radio.SetChannel(RF_FREQUENCY);
  reconfigurarLoRaTX();
  reconfigurarLoRaRX();

  Wire.begin(SDA_GY86_PIN, SCL_GY86_PIN); Wire.setClock(400000);
  Wire1.begin(SDA_GPS_PIN, SCL_GPS_PIN); Wire1.setClock(100000);

  mpu.begin(0x68, &Wire); mpu.setI2CBypass(true); delay(50);
  mag.begin(); ms5611.begin();
  myGNSS.begin(Wire1); myGNSS.setI2COutput(COM_TYPE_UBX);

  // Sem isto, getPVT() faz um poll explicito e BLOQUEIA o loop por ate
  // kUBLOXGNSSDefaultMaxWait (1100 ms), derrubando a janela de escuta.
  // Com autoPVT o modulo reporta sozinho e getPVT(0) apenas consome.
  myGNSS.setAutoPVT(true);

  iniciarOpenLog();
  snprintf(nomeArquivoSD, sizeof(nomeArquivoSD), "log_%lu.txt", millis());
  if (openLogConectado) {
    OpenLogSerial.print("new "); OpenLogSerial.print(nomeArquivoSD); OpenLogSerial.write(0x0D); delay(30);
    while (OpenLogSerial.available()) OpenLogSerial.read();
    OpenLogSerial.print("append "); OpenLogSerial.print(nomeArquivoSD); OpenLogSerial.write(0x0D); delay(30);
    while (OpenLogSerial.available()) OpenLogSerial.read();
    OpenLogSerial.println(F("# Logs TDM"));
  }

  uint32_t tStart = millis();
  lastTime = micros();
  while (millis() - tStart < 4000) { atualizarIMU(); delay(10); }
  pitchOffset = pitch; rollOffset = roll; yawOffset = yaw;

  lastImuTick = millis();
  lastTime = micros();

  Serial.println(F("[LORA] Slots: segundo IMPAR = TX telemetria | segundo PAR = escuta."));
  abrirEscuta();
}

// ==========================================================================
//  TELEMETRIA
// ==========================================================================

void montarEEnviarTelemetria(uint8_t sec_atual) {
  ms5611.read();
  float temp = ms5611.getTemperature() + TEMP_CORR;
  float press = ms5611.getPressure();
  float altBar = 44330.0F * (1.0F - pow(press / REFERENCE_PRESSURE_HPA, 0.1903F));

  // snprintf devolve o tamanho que SERIA escrito. Sem o teto, pos pode passar
  // de BUFFER_SIZE e (BUFFER_SIZE - pos) vira um size_t enorme na chamada
  // seguinte, anulando a protecao do proprio snprintf.
  int ack_no_pacote = ack_val;

  int pos = 0;
  #define ADD(...) do { \
      if (pos < BUFFER_SIZE - 1) { \
        int _n = snprintf(txpacket + pos, BUFFER_SIZE - pos, __VA_ARGS__); \
        if (_n > 0) pos = (_n >= BUFFER_SIZE - pos) ? BUFFER_SIZE - 1 : pos + _n; \
      } \
    } while (0)

  ADD("PT2UNB\n");
  ADD("Lat:%.7f\n", (double)t_lat / 10000000.0);
  ADD("Lon:%.7f\n", (double)t_lon / 10000000.0);
  ADD("Alt:%.1f\n", t_alt);
  ADD("AltB:%.1f\n", altBar);
  ADD("Sat:%d\n", t_siv);
  ADD("Fix:%d\n", t_fix);
  ADD("T:%.1f\n", temp);
  ADD("P:%.1f\n", press);
  ADD("Time:%02d:%02d:%02d\n", t_hora, t_min, sec_atual);
  ADD("Pitch:%.2f\n", lastPitch);
  ADD("Roll:%.2f\n", lastRoll);
  ADD("Yaw:%.2f\n", lastYaw);
  ADD("Ack:%d\n", ack_no_pacote);
  #undef ADD

  if (openLogConectado) {
    loteContador++;
    char cab[48];
    snprintf(cab, sizeof(cab), "LOTE_%lu,%lu", loteContador, millis());
    logSD(cab);
    logSD(txpacket);
  }

  Serial.printf("[TX slot %02d] telemetria %d bytes | Ack:%d\n", sec_atual, pos, ack_no_pacote);

  Radio.Standby();
  reconfigurarLoRaTX();
  Radio.Send((uint8_t *)txpacket, strlen(txpacket));
  cntTx++;

  if (ack_pendente) {
    ack_val = 0;
    ack_pendente = false;
    Serial.println(F("[ACK] ACK de comando consumido. Estado neutro (0) restaurado."));
  }
}

// ==========================================================================
//  LOOP
// ==========================================================================

void loop() {
  // === EVENTOS DO RADIO ===
  if (txDoneFlag) {
    txDoneFlag = false;
    // O RX ja foi re-armado dentro do callback; aqui so registramos.
    Serial.println(F("[LORA] TX concluido, radio de volta em escuta."));
  }

  if (rxDoneFlag) {
    rxDoneFlag = false;
    cntRxOk++;
    Serial.printf("\n>>> [TELECOMANDO] RSSI:%d dBm | SNR:%d dB | %s\n", rxRssi, rxSnr, rxpacket);

    int cmd_recebido = 0;
    if (sscanf(rxpacket, "CMD:%d", &cmd_recebido) == 1) {
      ack_val = cmd_recebido + 1;
      ack_pendente = true;
      Serial.printf(">>> COMANDO %d ACEITO. Proxima telemetria enviara Ack:%d\n\n", cmd_recebido, ack_val);
      char linha[64];
      snprintf(linha, sizeof(linha), "CMD,%lu,%d,rssi:%d,snr:%d", millis(), cmd_recebido, rxRssi, rxSnr);
      logSD(linha);
    } else {
      Serial.println(F(">>> Pacote recebido nao e' um comando valido. Ignorado.\n"));
    }
    abrirEscuta();
  }

  if (rxErrFlag) {
    rxErrFlag = false;
    cntRxErr++;
    Serial.printf("[LORA] Recepcao falhou (CRC/timeout). Total de erros: %lu\n", cntRxErr);
    abrirEscuta();
  }

  servicoRadio();

  // === 1. IMU E SD ===
  if (millis() - lastImuTick >= imuInterval) {
    lastImuTick = millis();
    atualizarIMU();
    lastPitch = pitch - pitchOffset;
    lastRoll = roll - rollOffset;
    lastYaw = yaw - yawOffset;
    if (lastYaw > 180.0) lastYaw -= 360.0;
    if (lastYaw < -180.0) lastYaw += 360.0;

    if (openLogConectado) {
      char imuLine[140];
      snprintf(imuLine, sizeof(imuLine), "I,%lu,AX:%.2f,AY:%.2f,AZ:%.2f,GX:%.2f,GY:%.2f,GZ:%.2f,MX:%.2f,MY:%.2f,MZ:%.2f,P:%.2f,R:%.2f,Y:%.2f",
               millis(), lastAccelX, lastAccelY, lastAccelZ, lastGyroX, lastGyroY, lastGyroZ, lastMagX, lastMagY, lastMagZ, lastPitch, lastRoll, lastYaw);
      logSD(imuLine);
    }
  }

  servicoRadio();

  // === 2. GPS (nao bloqueante gracas ao setAutoPVT) ===
  // Todos os getters recebem maxWait 0: com autoPVT eles leem o cache, e o 0
  // garante que nenhum caminho interno caia num poll bloqueante.
  if (myGNSS.getPVT(0) == true) {
    t_lat = myGNSS.getLatitude(0); t_lon = myGNSS.getLongitude(0);
    t_alt = myGNSS.getAltitudeMSL(0) / 1000.0F;
    t_siv = myGNSS.getSIV(0); t_fix = myGNSS.getFixType(0);
    t_hora = myGNSS.getHour(0); t_min = myGNSS.getMinute(0); t_sec = myGNSS.getSecond(0);

    // Ancora o relogio TDM apenas em hora de GPS realmente valida. Sem fix,
    // getSecond() devolve 0 de forma constante; re-ancorar a cada PVT nesse
    // estado congelaria current_sec e a telemetria pararia de sair. Sem hora
    // valida o slot roda no relogio livre de millis(), e o enlace continua
    // fechando porque o solo deriva a janela da propria telemetria.
    if (myGNSS.getTimeValid(0)) {
      tdm_sec_ancora = t_sec;
      tdm_ancora_ms = millis();
      tdm_valido = true;
    }
  }

  servicoRadio();

  // === 3. SLOTS TDM ===
  // IMPAR = transmite telemetria | PAR = janela de escuta de 1000 ms.
  // O intervalo minimo protege contra o segundo do GPS oscilar e disparar
  // o mesmo slot duas vezes, o que colidiria com o comando do solo.
  uint8_t current_sec = tdmSegundoAtual();

  if (current_sec != last_tdm_sec) {
    last_tdm_sec = current_sec;

    if (current_sec % 2 == 1) {
      if (millis() - ultimoTxMs >= SLOT_INTERVALO_MIN_MS) {
        ultimoTxMs = millis();
        montarEEnviarTelemetria(current_sec);
      }
    } else {
      if (millis() - ultimaJanelaMs >= SLOT_INTERVALO_MIN_MS) {
        ultimaJanelaMs = millis();
        Serial.printf("[RX slot %02d] escuta aberta por 1000 ms. (tx:%lu rx:%lu err:%lu log_pulado:%lu)\n",
                      current_sec, cntTx, cntRxOk, cntRxErr, cntLogPulado);
        abrirEscuta();
      }
    }
  }

  servicoRadio();
}

// ==========================================================================
//  CALLBACKS
//  Rodam no contexto do loop (via IrqProcess), nao numa ISR real, entao
//  podem chamar a API do radio com seguranca.
// ==========================================================================

void OnTxDone(void) {
  // Volta a escutar IMEDIATAMENTE, sem depender de mais uma volta do loop.
  abrirEscuta();
  txDoneFlag = true;
}

void OnTxTimeout(void) {
  abrirEscuta();
  txDoneFlag = true;
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  if (size >= BUFFER_SIZE) size = BUFFER_SIZE - 1;
  memcpy(rxpacket, payload, size);
  rxpacket[size] = '\0';
  rxRssi = rssi;
  rxSnr = snr;
  rxDoneFlag = true;
}

void OnRxTimeout(void) {
  rxErrFlag = true;
}

void OnRxError(void) {
  rxErrFlag = true;
}
