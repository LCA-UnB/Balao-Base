/*
  LoraBordo - GY-86 (10DOF) + GPS + Telecomando (Slots ancorados no GPS)
  - Segundo IMPAR = janela de transmissao da telemetria
  - Segundo PAR   = janela de escuta de telecomandos (1000 ms inteiros)
  - SD Card desobstruido para nao travar o RTOS do Radio.
  - Repetidor de mensagens: uma estacao de solo envia "MSG:<id>:<texto>" na
    janela de escuta; no segundo IMPAR seguinte o bordo desce "PT2UNB-R" com
    o texto NO LUGAR da telemetria, para todas as estacoes ouvirem. O LOTE
    daquele segundo e' montado e gravado no SD normalmente, so nao vai ao ar.
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

// --- REPETIDOR DE MENSAGENS ---
// 100 caracteres mantem a subida em ~190 ms e a descida em ~240 ms no ar,
// com folga dentro da janela de 1000 ms.
#define MSG_TEXTO_MAX                               100
#define MSG_ID_TAM                                  8
// Varias estacoes podem enviar em janelas seguidas, mas no maximo uma
// mensagem desce a cada dois segundos IMPARES; a fila absorve a diferenca.
#define MSG_FILA_TAM                                4

char txpacket[BUFFER_SIZE];
char relaypacket[BUFFER_SIZE];
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
uint32_t cntRelay = 0, cntMsgDescartada = 0;

// === FILA DO REPETIDOR ===
struct MensagemRelay {
  char id[MSG_ID_TAM];
  char texto[MSG_TEXTO_MAX + 1];
};
MensagemRelay filaMsg[MSG_FILA_TAM];
uint8_t filaMsgQtd = 0;
// Garante que a telemetria nunca fique dois slots IMPARES seguidos fora do ar.
bool ultimoSlotFoiRelay = false;

void OnTxDone( void );
void OnTxTimeout( void );
void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr );
void OnRxTimeout( void );
void OnRxError( void );

// --- OPENLOG ---
#define OPENLOG_RX 3
#define OPENLOG_TX 2
// Sem buffer de TX, availableForWrite() nunca passa dos 128 bytes da FIFO
// da UART, e logSD() descarta toda linha maior que isso (linha "I" ~150 B,
// pacote de telemetria ~200 B). O buffer precisa ser definido antes do begin().
#define OPENLOG_TX_BUFFER 1024
HardwareSerial OpenLogSerial(2);

// --- PINOS E SENSORES ---
#define SDA_GPS_PIN 41
#define SCL_GPS_PIN 42
#define SDA_GY86_PIN 48
#define SCL_GY86_PIN 47
int referencePressureHpa = 1013;
#define TEMP_CORR (-2)

// --- BATERIA (divisor interno da Heltec WiFi LoRa 32 V3) ---
#define VBAT_ADC_PIN   1    // GPIO1: saída do divisor resistivo da bateria
#define VBAT_CTRL_PIN  37   // GPIO37: controle do ADC_Ctrl da Heltec V3.2
#define VBAT_CTRL_ON   HIGH // V3.2: o circuito de detecção é habilitado em HIGH
#define VBAT_SAMPLES   15   // quantidade ímpar para usar a mediana
#define VBAT_SAMPLE_DELAY_MS 3
#define VBAT_SETTLE_MS 100  // estabilização inicial do circuito

// Medidas reais na fonte de bancada: ADC (mV) -> tensão da bateria (mV).
// A conversão entre os pontos é feita por interpolação linear.
const uint16_t adcCalibradoMv[] = {
  683, 694, 700, 712, 720, 728, 739, 750, 757, 766, 779, 785, 797
};
const uint16_t tensaoRealMv[] = {
  3600, 3650, 3700, 3750, 3800, 3850, 3900, 3950, 4000, 4050, 4100, 4150, 4200
};
const size_t PONTOS_CALIBRACAO = sizeof(adcCalibradoMv) / sizeof(adcCalibradoMv[0]);

float lastBatVolts = 0.0;

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
float gyroBiasX = 0.0, gyroBiasY = 0.0, gyroBiasZ = 0.0; // bias do giroscopio, medido em repouso
// Desvio padrao maximo do giro (graus/s) para considerar a montagem parada.
// Em repouso o ruido do MPU-6050 fica bem abaixo disso.
#define GYRO_REPOUSO_MAX_DPS 1.0
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

// ---- MEDIA DAS ACELARACOES ---
#define SENSOR_AVG_WINDOW_MS                         1000   // pode trocar pra qualquer intervalo
double accelSomaX = 0, accelSomaY = 0, accelSomaZ = 0;
double magSomaX = 0, magSomaY = 0, magSomaZ = 0;
uint32_t sensorAmostras = 0;
uint32_t sensorJanelaInicio = 0;
float avgAccelX = 0, avgAccelY = 0, avgAccelZ = 0;
float avgMagX = 0, avgMagY = 0, avgMagZ = 0;

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
  // end() preserva o tamanho do buffer, entao ele vale tambem para o 9600.
  OpenLogSerial.setTxBufferSize(OPENLOG_TX_BUFFER);
  OpenLogSerial.begin(57600, SERIAL_8N1, OPENLOG_RX, OPENLOG_TX);
  delay(500);
  // 57600 baud escoa ~5,7 KB/s. Com a linha "I" de ~155 B, 40 Hz (25 ms) passaria
  // disso e o pacote de telemetria seria descartado; 25 Hz ocupa ~70% da UART.
  if (entrarModoComandoOpenLog(true)) {
    openLogConectado = true; openLogAltaVelocidade = true; imuInterval = 40;
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
//  BATERIA
// ==========================================================================

float calibrarTensaoBateria(uint32_t adcMv) {
  // Sem esta proteção, ADC=0 é extrapolado pelo primeiro segmento da tabela
  // e aparece incorretamente como aproximadamente 0,5 V.
  if (adcMv == 0) return 0.0f;

  // A tabela não é válida acima do último ponto. Limitar a 4,2 V impede
  // que ruído acima de 797 mV seja extrapolado como 4,3–4,6 V.
  if (adcMv >= adcCalibradoMv[PONTOS_CALIBRACAO - 1]) {
    return tensaoRealMv[PONTOS_CALIBRACAO - 1];
  }

  // Abaixo de 3,60 V ainda prolongamos o primeiro segmento para não esconder
  // uma bateria descarregada; essa região deve ser calibrada futuramente.
  size_t i = 0;
  while (i + 1 < PONTOS_CALIBRACAO && adcMv > adcCalibradoMv[i + 1]) i++;

  const float x0 = adcCalibradoMv[i];
  const float x1 = adcCalibradoMv[i + 1];
  const float y0 = tensaoRealMv[i];
  const float y1 = tensaoRealMv[i + 1];
  return y0 + ((float)adcMv - x0) * (y1 - y0) / (x1 - x0);
}

float lerTensaoBateria() {
  uint16_t amostras[VBAT_SAMPLES];
  for (int i = 0; i < VBAT_SAMPLES; i++) {
    amostras[i] = analogReadMilliVolts(VBAT_ADC_PIN);
    delay(VBAT_SAMPLE_DELAY_MS);
  }

  // Ordenação simples: com 15 valores, a mediana elimina picos do ADC sem
  // atrasar significativamente o loop de telemetria.
  for (int i = 1; i < VBAT_SAMPLES; i++) {
    uint16_t atual = amostras[i];
    int j = i;
    while (j > 0 && amostras[j - 1] > atual) {
      amostras[j] = amostras[j - 1];
      j--;
    }
    amostras[j] = atual;
  }

  uint32_t adcMv = amostras[VBAT_SAMPLES / 2];
  float tensao = calibrarTensaoBateria(adcMv) / 1000.0f;
  Serial.printf("[BAT] ADC mediana: %lu mV | faixa: %u-%u mV | tensao: %.3f V\n",
                adcMv, amostras[0], amostras[VBAT_SAMPLES - 1], tensao);
  return tensao;
}

// =========================================================================
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

  // Acumula a aceleracao instantanea para a media da janela (ver atualizarMediaAceleracao()).
  accelSomaX += lastAccelX;
  accelSomaY += lastAccelY;
  accelSomaZ += lastAccelZ;

  magSomaX += lastMagX;
  magSomaY += lastMagY;
  magSomaZ += lastMagZ;

  sensorAmostras++;

  float accRoll = atan2(a.acceleration.y, a.acceleration.z) * 180.0 / PI;
  float accPitch = atan2(-a.acceleration.x, sqrt(a.acceleration.y * a.acceleration.y + a.acceleration.z * a.acceleration.z)) * 180.0 / PI;

  // Sem descontar o bias, o filtro complementar estabiliza com erro fixo de
  // ~49 * bias * dt, que muda com imuInterval e nao e' anulado pelos offsets.
  float gyroRollRate = (g.gyro.x - gyroBiasX) * 180.0 / PI;
  float gyroPitchRate = (g.gyro.y - gyroBiasY) * 180.0 / PI;

  roll = alpha * (roll + gyroRollRate * dt) + (1.0 - alpha) * accRoll;
  pitch = alpha * (pitch + gyroPitchRate * dt) + (1.0 - alpha) * accPitch;

  float rollRad = roll * PI / 180.0;
  float pitchRad = pitch * PI / 180.0;

  float Xh = lastMagX * cos(pitchRad) + lastMagZ * sin(pitchRad);
  float Yh = lastMagX * sin(rollRad) * sin(pitchRad) + lastMagY * cos(rollRad) - lastMagZ * sin(rollRad) * cos(pitchRad);
  yaw = atan2(Yh, Xh) * 180.0 / PI;
  if (yaw < 0) yaw += 360.0;
}

// Verifica se a janela de tempo (ACCEL_AVG_WINDOW_MS) fechou; se sim, calcula
// a media das amostras acumuladas desde a ultima janela e zera os acumuladores.
// Chamada a cada ciclo do loop, mas so recalcula quando o tempo da janela passa.

void atualizarMediasSensores() {
  if (millis() - sensorJanelaInicio < SENSOR_AVG_WINDOW_MS) return;
  if (sensorAmostras > 0) {
    avgAccelX = accelSomaX / sensorAmostras;
    avgAccelY = accelSomaY / sensorAmostras;
    avgAccelZ = accelSomaZ / sensorAmostras;
    
    avgMagX = magSomaX / sensorAmostras;
    avgMagY = magSomaY / sensorAmostras;
    avgMagZ = magSomaZ / sensorAmostras;
  }
  accelSomaX = 0; accelSomaY = 0; accelSomaZ = 0;
  magSomaX = 0; magSomaY = 0; magSomaZ = 0;
  sensorAmostras = 0;
  sensorJanelaInicio = millis();
}

void calibrarIMU() {
  Serial.println(F("[IMU] Calibrando (mantenha a montagem imovel)..."));

  const int amostrasBias = 200; // ~1s a 5ms/amostra
  double somaGX = 0, somaGY = 0, somaGZ = 0;
  double somaGX2 = 0, somaGY2 = 0, somaGZ2 = 0;
  double somaAccRoll = 0, somaAccPitch = 0;

  for (int i = 0; i < amostrasBias; i++) {
    sensors_event_t a, g, temp_mpu;
    mpu.getEvent(&a, &g, &temp_mpu);

    somaGX += g.gyro.x;
    somaGY += g.gyro.y;
    somaGZ += g.gyro.z;
    somaGX2 += g.gyro.x * g.gyro.x;
    somaGY2 += g.gyro.y * g.gyro.y;
    somaGZ2 += g.gyro.z * g.gyro.z;

    somaAccRoll  += atan2(a.acceleration.y, a.acceleration.z) * 180.0 / PI;
    somaAccPitch += atan2(-a.acceleration.x, sqrt(a.acceleration.y * a.acceleration.y + a.acceleration.z * a.acceleration.z)) * 180.0 / PI;

    delay(5);
  }

  double mediaGX = somaGX / amostrasBias;
  double mediaGY = somaGY / amostrasBias;
  double mediaGZ = somaGZ / amostrasBias;

  // Com a montagem em movimento (ex.: calibracao por telecomando em voo) a
  // media inclui rotacao real; aplicar isso como bias pioraria o filtro.
  // Nesse caso mantem o bias anterior e so refaz o zero de orientacao.
  double dpX = sqrt(max(0.0, somaGX2 / amostrasBias - mediaGX * mediaGX));
  double dpY = sqrt(max(0.0, somaGY2 / amostrasBias - mediaGY * mediaGY));
  double dpZ = sqrt(max(0.0, somaGZ2 / amostrasBias - mediaGZ * mediaGZ));
  float dpMaxDps = max(dpX, max(dpY, dpZ)) * 180.0 / PI;

  if (dpMaxDps <= GYRO_REPOUSO_MAX_DPS) {
    gyroBiasX = mediaGX;
    gyroBiasY = mediaGY;
    gyroBiasZ = mediaGZ;
  } else {
    Serial.printf("[IMU] Montagem em movimento (desvio %.2f dps). Bias anterior mantido.\n", dpMaxDps);
  }

  // Semeia o filtro JA na orientacao real (calculada so pelo acelerometro).
  // E' isso que garante zerar corretamente mesmo ligando na vertical.
  roll  = somaAccRoll  / amostrasBias;
  pitch = somaAccPitch / amostrasBias;

  Serial.printf("[IMU] Bias giro X:%.3f Y:%.3f Z:%.3f | Seed roll:%.2f pitch:%.2f\n",
                gyroBiasX, gyroBiasY, gyroBiasZ, roll, pitch);

  // Deixa o filtro assentar (yaw depende do magnetometro e do roll/pitch
  // ja quase corretos) e tira o offset como media das ultimas amostras.
  lastTime = micros();
  const int amostrasAssentamento = 300; // ~3s a 10ms/amostra
  const int janelaMedia = 50;           // media das ultimas 50 amostras
  double somaRollFinal = 0, somaPitchFinal = 0, somaYawFinal = 0;

  for (int i = 0; i < amostrasAssentamento; i++) {
    atualizarIMU();
    if (i >= amostrasAssentamento - janelaMedia) {
      somaRollFinal  += roll;
      somaPitchFinal += pitch;
      somaYawFinal   += yaw;
    }
    delay(10);
  }

  rollOffset  = somaRollFinal  / janelaMedia;
  pitchOffset = somaPitchFinal / janelaMedia;
  yawOffset   = somaYawFinal   / janelaMedia;

  Serial.printf("[IMU] Offsets finais -> Pitch:%.2f Roll:%.2f Yaw:%.2f\n",
                pitchOffset, rollOffset, yawOffset);
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

  pinMode(VBAT_CTRL_PIN, OUTPUT);
  // O exemplo oficial da Heltec V3.2 mantém ADC_Ctrl em HIGH durante o uso.
  // Mantê-lo ligado evita transientes causados por carregar e descarregar o
  // divisor antes de cada pacote de telemetria.
  digitalWrite(VBAT_CTRL_PIN, VBAT_CTRL_ON);
  analogReadResolution(12);
  analogSetPinAttenuation(VBAT_ADC_PIN, ADC_11db);
  delay(VBAT_SETTLE_MS);
  lastBatVolts = lerTensaoBateria();

  Wire.begin(SDA_GY86_PIN, SCL_GY86_PIN); Wire.setClock(400000);
  Wire1.begin(SDA_GPS_PIN, SCL_GPS_PIN); Wire1.setClock(100000);

  mpu.begin(0x68, &Wire); mpu.setI2CBypass(true); delay(50);
  mag.begin(); ms5611.begin();
  myGNSS.begin(Wire1); myGNSS.setI2COutput(COM_TYPE_UBX);

  if (myGNSS.setDynamicModel(DYN_MODEL_AIRBORNE1g) == false) {
    Serial.println(F("[GNSS] Falha ao configurar Dynamic Model Airborne4g!"));
  } else {
    Serial.println(F("[GNSS] Dynamic Model = Airborne <4g (ok)"));
  }

  uint8_t modeloAtual = myGNSS.getDynamicModel();

  if (modeloAtual == DYN_MODEL_UNKNOWN)
  {
    Serial.println(F("*** Warning: getDynamicModel failed ***"));
  }
  else
  {
    Serial.printf("[GNSS] Dynamic Model lido de volta: %d (esperado: %d = AIRBORNE4g)\n",
              modeloAtual, DYN_MODEL_AIRBORNE4g);
  }

  myGNSS.saveConfiguration();

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

  calibrarIMU();

  lastImuTick = millis();
  lastTime = micros();

  Serial.println(F("[LORA] Slots: segundo IMPAR = TX telemetria | segundo PAR = escuta."));
  abrirEscuta();
}

// ==========================================================================
//  TELEMETRIA
// ==========================================================================

// Monta o pacote de telemetria em txpacket e devolve o tamanho. Nao grava no
// SD nem transmite: quem decide o que vai ao ar e' transmitirSlot().
int montarTelemetria(uint8_t sec_atual, int ack_no_pacote) {
  ms5611.read();
  lastBatVolts = lerTensaoBateria();
  float temp = ms5611.getTemperature() + TEMP_CORR;
  float press = ms5611.getPressure();
  float altBar = 44330.0F * (1.0F - pow(press / referencePressureHpa, 0.1903F));

  // snprintf devolve o tamanho que SERIA escrito. Sem o teto, pos pode passar
  // de BUFFER_SIZE e (BUFFER_SIZE - pos) vira um size_t enorme na chamada
  // seguinte, anulando a protecao do proprio snprintf.
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
  ADD("AXavg:%.3f\n", avgAccelX);
  ADD("AYavg:%.3f\n", avgAccelY);
  ADD("AZavg:%.3f\n", avgAccelZ);
  ADD("Bat:%.2f\n", lastBatVolts);
  ADD("Ack:%d\n", ack_no_pacote);
  #undef ADD
  return pos;
}

// Pacote do repetidor. Msg vem por ultimo porque o texto vai ate o fim da
// linha; Time e Ack seguem no pacote para o solo manter o rodizio das janelas
// e a confirmacao de telecomando mesmo no segundo em que a telemetria nao desce.
int montarRelay(const MensagemRelay &msg, uint8_t sec_atual, int ack_no_pacote) {
  int n = snprintf(relaypacket, sizeof(relaypacket),
                   "PT2UNB-R\nId:%s\nTime:%02d:%02d:%02d\nAck:%d\nMsg:%s\n",
                   msg.id, t_hora, t_min, sec_atual, ack_no_pacote, msg.texto);
  return (n >= (int)sizeof(relaypacket)) ? (int)sizeof(relaypacket) - 1 : n;
}

// Recebe "MSG:<id>:<texto>" e enfileira para descer no proximo slot IMPAR livre.
void receberMensagem() {
  const char *id = rxpacket + 4;
  const char *sep = strchr(id, ':');
  size_t idLen = sep ? (size_t)(sep - id) : 0;
  if (sep == NULL || idLen < 2 || idLen >= MSG_ID_TAM || id[0] < 'A' || id[0] > 'Z' || sep[1] == '\0') {
    Serial.println(F(">>> Mensagem malformada. Ignorada.\n"));
    return;
  }

  MensagemRelay msg;
  memcpy(msg.id, id, idLen);
  msg.id[idLen] = '\0';
  // O texto desce dentro de um pacote de linhas: qualquer caractere de
  // controle quebraria o enquadramento no solo, entao vira espaco.
  size_t n = 0;
  for (const char *p = sep + 1; *p != '\0' && n < MSG_TEXTO_MAX; p++) {
    char c = *p;
    msg.texto[n++] = (c >= 32 && c <= 126) ? c : ' ';
  }
  msg.texto[n] = '\0';

  // A estacao retransmite enquanto nao ouve o relay; se a mensagem ainda
  // esta na fila, a repeticao nao ocupa outra posicao.
  for (uint8_t i = 0; i < filaMsgQtd; i++) {
    if (strcmp(filaMsg[i].id, msg.id) == 0) {
      Serial.printf(">>> MENSAGEM %s ja esta na fila. Repeticao ignorada.\n\n", msg.id);
      return;
    }
  }

  char linha[48 + MSG_ID_TAM + MSG_TEXTO_MAX];
  if (filaMsgQtd >= MSG_FILA_TAM) {
    cntMsgDescartada++;
    Serial.printf(">>> FILA CHEIA. Mensagem %s descartada (a estacao vai retransmitir).\n\n", msg.id);
    snprintf(linha, sizeof(linha), "MSG_DESCARTADA,%lu,%s,fila_cheia", millis(), msg.id);
    logSD(linha);
    return;
  }

  filaMsg[filaMsgQtd++] = msg;
  Serial.printf(">>> MENSAGEM %s ENFILEIRADA (%u na fila): %s\n\n", msg.id, filaMsgQtd, msg.texto);
  snprintf(linha, sizeof(linha), "MSG_RX,%lu,%s,rssi:%d,snr:%d,%s", millis(), msg.id, rxRssi, rxSnr, msg.texto);
  logSD(linha);
}

// Slot IMPAR: a telemetria e' sempre montada e gravada no SD. O que vai ao ar
// e' ela ou, se houver mensagem na fila e o slot anterior nao foi relay, a
// mensagem - nunca as duas, porque nao cabem no mesmo segundo.
void transmitirSlot(uint8_t sec_atual) {
  int ack_no_pacote = ack_val;
  int pos = montarTelemetria(sec_atual, ack_no_pacote);
  bool relay = filaMsgQtd > 0 && !ultimoSlotFoiRelay;

  if (openLogConectado) {
    loteContador++;
    char cab[48 + MSG_ID_TAM];
    if (relay) {
      snprintf(cab, sizeof(cab), "LOTE_%lu,%lu,RELAY:%s", loteContador, millis(), filaMsg[0].id);
    } else {
      snprintf(cab, sizeof(cab), "LOTE_%lu,%lu", loteContador, millis());
    }
    logSD(cab);
    logSD(txpacket);
  }

  const char *pacote = txpacket;
  if (relay) {
    int n = montarRelay(filaMsg[0], sec_atual, ack_no_pacote);
    char linha[32 + MSG_ID_TAM];
    snprintf(linha, sizeof(linha), "MSG_TX,%lu,%s", millis(), filaMsg[0].id);
    logSD(linha);
    Serial.printf("[TX slot %02d] RELAY %s %d bytes (telemetria so no SD) | Ack:%d\n",
                  sec_atual, filaMsg[0].id, n, ack_no_pacote);
    for (uint8_t i = 1; i < filaMsgQtd; i++) filaMsg[i - 1] = filaMsg[i];
    filaMsgQtd--;
    cntRelay++;
    pacote = relaypacket;
  } else {
    Serial.printf("[TX slot %02d] telemetria %d bytes | Ack:%d | Bat:%.2f V\n", sec_atual, pos, ack_no_pacote, lastBatVolts);
  }
  ultimoSlotFoiRelay = relay;

  Radio.Standby();
  reconfigurarLoRaTX();
  Radio.Send((uint8_t *)pacote, strlen(pacote));
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
    bool ehMensagem = strncmp(rxpacket, "MSG:", 4) == 0;
    if (!ehMensagem) sscanf(rxpacket, "CMD:%d", &cmd_recebido);

    if (ehMensagem) {
      receberMensagem();
    } else if (cmd_recebido >= 1 && cmd_recebido <= 50) {
      ack_val = cmd_recebido + 1;
      ack_pendente = true;
      Serial.printf(">>> COMANDO %d ACEITO. Proxima telemetria enviara Ack:%d\n\n", cmd_recebido, ack_val);
      char linha[64];
      snprintf(linha, sizeof(linha), "CMD,%lu,%d,rssi:%d,snr:%d", millis(), cmd_recebido, rxRssi, rxSnr);
      logSD(linha);
    }else if(cmd_recebido >= 900 && cmd_recebido <= 1100){
       referencePressureHpa = cmd_recebido;

       ack_val = cmd_recebido + 1; 
       ack_pendente = true;

       Serial.printf(">>> QNH ATUALIZADO PARA %d hPa. Proxima telemetria confirma via QNHref:.\n\n",
                      cmd_recebido);
        char linha[64];
        snprintf(linha, sizeof(linha), "QNH,%lu,%d,rssi:%d,snr:%d", millis(), cmd_recebido, rxRssi, rxSnr);
        logSD(linha);

    }else if(cmd_recebido > 2000){

      Serial.println(">>> CALIBRACAO IMU INICIADA POR COMANDO.");

      ack_val = cmd_recebido + 1; 
      ack_pendente = true;
      calibrarIMU();

    }else {
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
      char imuLine[256]; // Aumentado para evitar corte de string
      
      // Trocamos lastAccel pelas variáveis avgAccel e as siglas para AXa, AYa, AZa
      snprintf(imuLine, sizeof(imuLine), "I,%lu,AXa:%.2f,AYa:%.2f,AZa:%.2f,GX:%.2f,GY:%.2f,GZ:%.2f,MX:%.2f,MY:%.2f,MZ:%.2f,MXa:%.2f,MYa:%.2f,MZa:%.2f,P:%.2f,R:%.2f,Y:%.2f",
               millis(), avgAccelX, avgAccelY, avgAccelZ, lastGyroX, lastGyroY, lastGyroZ, 
               lastMagX, lastMagY, lastMagZ, avgMagX, avgMagY, avgMagZ, lastPitch, lastRoll, lastYaw);
      logSD(imuLine);
    }
  }

  // Roda todo ciclo do loop; so recalcula de fato quando a janela de tempo fecha.
  atualizarMediasSensores();

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
        transmitirSlot(current_sec);
      }
    } else {
      if (millis() - ultimaJanelaMs >= SLOT_INTERVALO_MIN_MS) {
        ultimaJanelaMs = millis();
        Serial.printf("[RX slot %02d] escuta aberta por 1000 ms. (tx:%lu rx:%lu err:%lu log_pulado:%lu relay:%lu fila:%u msg_descartada:%lu)\n",
                      current_sec, cntTx, cntRxOk, cntRxErr, cntLogPulado, cntRelay, filaMsgQtd, cntMsgDescartada);
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
