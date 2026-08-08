/*
  LoraBordo - GY-86 (10DOF) + GPS + Telecomando (Flags Architecture)
  - SD Card desobstruído para não travar o RTOS do Rádio.
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

char txpacket[BUFFER_SIZE];
char rxpacket[BUFFER_SIZE]; 
static RadioEvents_t RadioEvents;

// === FLAGS DE CONTROLE DE RÁDIO ===
volatile bool txDoneFlag = false;
volatile bool rxDoneFlag = false;

void OnTxDone( void );
void OnTxTimeout( void );
void OnRxDone( uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr );

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

// --- VARIÁVEIS IMU ---
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

// --- VARIÁVEIS CACHE GPS E TDM ---
int32_t t_lat = 0, t_lon = 0;
float t_alt = 0;
uint8_t t_siv = 0, t_fix = 0;
uint8_t t_hora = 0, t_min = 0, t_sec = 0;
uint32_t gps_last_millis = 0;

uint8_t last_tdm_sec = 255;
int ack_val = 0;

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

void setup() {
  Serial.begin(115200);
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
  delay(1000); 

  RadioEvents.TxDone = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxDone = OnRxDone;
  
  Radio.Init(&RadioEvents);
  Radio.SetChannel(RF_FREQUENCY);
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000); 
  Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                    LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                    LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                    0, true, 0, 0, LORA_IQ_INVERSION_ON, true);

  Wire.begin(SDA_GY86_PIN, SCL_GY86_PIN); Wire.setClock(400000); 
  Wire1.begin(SDA_GPS_PIN, SCL_GPS_PIN); Wire1.setClock(100000); 

  mpu.begin(0x68, &Wire); mpu.setI2CBypass(true); delay(50);
  mag.begin(); ms5611.begin();
  myGNSS.begin(Wire1); myGNSS.setI2COutput(COM_TYPE_UBX); 

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

  Radio.Rx(0);
}

void loop() {
  // === MÁQUINA DE ESTADOS DO RÁDIO ===
  if (txDoneFlag) {
    txDoneFlag = false;
    Serial.println(F("[LORA] Rádio livre. Abrindo janela de escuta (RX)..."));
    Radio.Rx(0); 
  }

  if (rxDoneFlag) {
    rxDoneFlag = false;
    int cmd_recebido = 0;
    if (sscanf(rxpacket, "CMD:%d", &cmd_recebido) == 1) {
      ack_val = cmd_recebido + 1; 
      Serial.println(F("\n================================="));
      Serial.printf(">>> COMANDO %d RECEBIDO! <<<\n", cmd_recebido);
      Serial.printf("Próximo pacote enviará ACK: %d\n", ack_val);
      Serial.println(F("=================================\n"));
    }
    Radio.Rx(0); 
  }
  // ===========================================

  // 1. Manutenção IMU e SD
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
      OpenLogSerial.println(imuLine);
    }
  }

  // 2. Leitura GPS
  if (myGNSS.getPVT() == true) {
    t_lat = myGNSS.getLatitude(); t_lon = myGNSS.getLongitude();
    t_alt = myGNSS.getAltitudeMSL() / 1000.0F;
    t_siv = myGNSS.getSIV(); t_fix = myGNSS.getFixType();
    t_hora = myGNSS.getHour(); t_min = myGNSS.getMinute(); t_sec = myGNSS.getSecond();
    gps_last_millis = millis();
  }

  // 3. ENVIO DE TELEMETRIA
  uint8_t current_sec = (t_sec + ((millis() - gps_last_millis) / 1000)) % 60;

  if (current_sec != last_tdm_sec) {
    last_tdm_sec = current_sec;

    if (current_sec % 2 == 0) {
      ms5611.read();
      float temp = ms5611.getTemperature() + TEMP_CORR;
      float press = ms5611.getPressure();
      float altBar = 44330.0F * (1.0F - pow(press / REFERENCE_PRESSURE_HPA, 0.1903F));

      int pos = 0;
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "PT2UNB\n");
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Lat:%.7f\n", (double)t_lat / 10000000.0);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Lon:%.7f\n", (double)t_lon / 10000000.0);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Alt:%.1f\n", t_alt);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "AltB:%.1f\n", altBar);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Sat:%d\n", t_siv);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Fix:%d\n", t_fix);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "T:%.1f\n", temp);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "P:%.1f\n", press);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Time:%02d:%02d:%02d\n", t_hora, t_min, current_sec); 
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Pitch:%.2f\n", lastPitch);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Roll:%.2f\n", lastRoll);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Yaw:%.2f\n", lastYaw);
      pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Ack:%d\n", ack_val); 

      if (openLogConectado) {
        loteContador++;
        OpenLogSerial.print(F("LOTE_")); OpenLogSerial.print(loteContador); OpenLogSerial.print(F(",")); OpenLogSerial.println(millis());
        OpenLogSerial.println(txpacket);
        // NOTA: Removido o forcarFlushOpenLog() para não atrasar a placa! 
      }

      Serial.println(F("------- TRANSMITINDO (PAR) -------"));
      Serial.printf("Enviando telemetria + ACK:%d\n", ack_val);
      Radio.Send((uint8_t *)txpacket, strlen(txpacket));
    } 
  }

  Radio.IrqProcess();
}

void OnTxDone(void) {
  txDoneFlag = true;
}

void OnTxTimeout(void) {
  txDoneFlag = true;
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  memcpy(rxpacket, payload, size);
  rxpacket[size] = '\0';
  rxDoneFlag = true; 
}