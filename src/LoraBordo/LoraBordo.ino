/*
  Leitura combinada: u-blox SAM-M10Q + BME280 + BNO086 + Transmissão LoRa
  - Usa Wire nos pinos 41 (SDA) e 42 (SCL) para GPS e Clima
  - Usa Wire1 nos pinos 48 (SDA) e 47 (SCL) para IMU
*/



// 1º - INCLUDES DO LORA (Heltec) - DEVEM VIR PRIMEIRO!
#include "LoRaWan_APP.h"
#include "Arduino.h"

// 2º - INCLUDES DOS SENSORES
#include <Wire.h> 
#include <SparkFun_u-blox_GNSS_v3.h>
#include <SparkFunBME280.h>          
#include "SparkFun_BNO08x_Arduino_Library.h" 

// --- DEFINIÇÕES DO LORA ---
#define RF_FREQUENCY                                915000000 // Hz
#define TX_OUTPUT_POWER                             5         // dBm
#define LORA_BANDWIDTH                              0         // [0: 125 kHz]
#define LORA_SPREADING_FACTOR                       7         // [SF7..SF12]
#define LORA_CODINGRATE                             1         // [1: 4/5]
#define LORA_PREAMBLE_LENGTH                        8         // Same for Tx and Rx
#define LORA_SYMBOL_TIMEOUT                         0         // Symbols
#define LORA_FIX_LENGTH_PAYLOAD_ON                  false
#define LORA_IQ_INVERSION_ON                        false

#define RX_TIMEOUT_VALUE                            1000
#define BUFFER_SIZE                                 250 

char txpacket[BUFFER_SIZE];
bool lora_idle = true;

static RadioEvents_t RadioEvents;
void OnTxDone( void );
void OnTxTimeout( void );

// --- DEFINIÇÕES DOS SENSORES ---
#define SDA_PIN 41
#define SCL_PIN 42
#define SDA_BNO_PIN 48
#define SCL_BNO_PIN 47

#define REFERENCE_PRESSURE_HPA 1023.0 
#define TEMP_CORR (-2)                

SFE_UBLOX_GNSS myGNSS; 
BME280 myBME280; 
BNO08x myIMU; 
#define BNO08X_ADDR 0x4B
#define BNO08X_INT  -1 
#define BNO08X_RST  -1 

// Variáveis globais para armazenar a última leitura do IMU
float lastAccelX = 0, lastAccelY = 0, lastAccelZ = 0;
float lastGyroX = 0, lastGyroY = 0, lastGyroZ = 0;
float lastMagX = 0, lastMagY = 0, lastMagZ = 0;

void configurarSensoresIMU() {
  // Pede os dados tratados (Calibrados) a cada 500ms
  myIMU.enableAccelerometer(500); 
  myIMU.enableGyro(500);
  myIMU.enableMagnetometer(500);
}

void setup()
{
  Serial.begin(115200);
  
  // Inicialização obrigatória do chip Heltec para o LoRa funcionar
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
  
  delay(1000); 
  Serial.println(F("Iniciando Heltec V3 + Sensores + LoRa TX"));

  // ================= LORA SETUP =================
  RadioEvents.TxDone = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  
  Radio.Init( &RadioEvents );
  Radio.SetChannel( RF_FREQUENCY );
  Radio.SetTxConfig( MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                                 LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                                 LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                                 true, 0, 0, LORA_IQ_INVERSION_ON, 3000 ); 

  // ================= SENSORES SETUP =================
  Wire.begin(SDA_PIN, SCL_PIN); 
  Wire1.begin(SDA_BNO_PIN, SCL_BNO_PIN);
  
  // REDUZIDO para 100kHz. Evita quedas de I2C quando os cabos balançam muito.
  Wire1.setClock(100000); 
  
  Serial.println(F("Barramentos I2C configurados!"));

  // --- Inicialização do BME280 ---
  myBME280.setI2CAddress(0x77); 
  if (myBME280.begin() == 0) { 
    Serial.println(F("Falha ao encontrar o BME280!"));
    while (1) delay(10); 
  }
  Serial.println(F("BME280 detectado com sucesso!"));
  
  myBME280.setFilter(4);          
  myBME280.setStandbyTime(0);     
  myBME280.setTempOverSample(2);  
  myBME280.setPressureOverSample(16); 
  myBME280.setHumidityOverSample(1); 
  myBME280.setMode(MODE_NORMAL);  

  // --- Inicialização do u-blox GNSS ---
  while (myGNSS.begin(Wire) == false) 
  {
    Serial.println(F("u-blox GNSS não detectado. Tentando novamente..."));
    delay(1000);
  }
  myGNSS.setI2COutput(COM_TYPE_UBX); 
  Serial.println(F("u-blox configurado com sucesso!"));

  // --- Inicialização do BNO086 ---
  if (myIMU.begin(BNO08X_ADDR, Wire1, BNO08X_INT, BNO08X_RST) == false) {
    Serial.println("BNO086 não detectado no Wire1. Congelando...");
    while (1) delay(10);
  }
  Serial.println("BNO086 encontrado com sucesso!");

  configurarSensoresIMU();

  Serial.println(F("Setup concluído! Iniciando leituras e transmissão..."));
  Serial.println(F("--------------------------------------------------"));
}

void loop()
{
  // 1. Manutenção do IMU
  if (myIMU.wasReset()) {
    configurarSensoresIMU();
  }

  // 2. Leitura do IMU (Atualiza as variáveis globais a cada 500ms)
  while (myIMU.getSensorEvent() == true)
  {
    uint8_t eventID = myIMU.getSensorEventID();
      
    if (eventID == SENSOR_REPORTID_GYROSCOPE_CALIBRATED) {
      lastGyroX = myIMU.getGyroX(); lastGyroY = myIMU.getGyroY(); lastGyroZ = myIMU.getGyroZ();
    }
    else if (eventID == SENSOR_REPORTID_MAGNETIC_FIELD) {
      lastMagX = myIMU.getMagX(); lastMagY = myIMU.getMagY(); lastMagZ = myIMU.getMagZ();
    }
    else if (eventID == SENSOR_REPORTID_ACCELEROMETER) {
      lastAccelX = myIMU.getAccelX(); lastAccelY = myIMU.getAccelY(); lastAccelZ = myIMU.getAccelZ();
    }
  }

  // 3. Leitura do GPS e BME280 + Transmissão LoRa (~1x por segundo)
  if (myGNSS.getPVT() == true)
  {
    int32_t latitude = myGNSS.getLatitude();
    int32_t longitude = myGNSS.getLongitude();
    int32_t altitudeGNSS = myGNSS.getAltitudeMSL();
    uint8_t SIV = myGNSS.getSIV(); 

    float temp = myBME280.readTempC() + TEMP_CORR;
    float press = myBME280.readFloatPressure() / 100.0F;
    float umidade = myBME280.readFloatHumidity();

    // Imprime no Serial para acompanhamento local
    Serial.print(F("GPS Lat:")); Serial.print(latitude);
    Serial.print(F(" Lon:")); Serial.print(longitude);
    Serial.print(F(" || Clima T:")); Serial.print(temp, 1);
    Serial.print(F("C || IMU AX:")); Serial.print(lastAccelX, 1);
    Serial.println(); 

    // ==== TRANSMISSÃO LORA ====
    // Só transmite se o rádio estiver livre
    if (lora_idle == true) 
    {
      // Formata todos os dados numa única string "txpacket"
      snprintf(txpacket, BUFFER_SIZE, 
        "Lat:%ld,Lon:%ld,Alt:%ld,Sat:%d,T:%.1f,P:%.1f,U:%.1f,AX:%.2f,AY:%.2f,AZ:%.2f,GX:%.2f,GY:%.2f,GZ:%.2f", 
        latitude, longitude, altitudeGNSS, SIV, 
        temp, press, umidade, 
        lastAccelX, lastAccelY, lastAccelZ, 
        lastGyroX, lastGyroY, lastGyroZ);

      Serial.printf("Enviando LoRa (%d bytes): %s\r\n", strlen(txpacket), txpacket);

      Radio.Send( (uint8_t *)txpacket, strlen(txpacket) ); 
      lora_idle = false;
    }
  }

  // 4. Processamento obrigatório dos eventos de interrupção do rádio LoRa
  Radio.IrqProcess();
}

// Funções de callback do rádio LoRa
void OnTxDone( void )
{
  Serial.println(F("TX Concluído......"));
  lora_idle = true;
}

void OnTxTimeout( void )
{
  Radio.Sleep( );
  Serial.println(F("TX Timeout......"));
  lora_idle = true;
}