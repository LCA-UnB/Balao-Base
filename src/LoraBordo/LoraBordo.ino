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
#define RF_FREQUENCY                                910500000 // Hz (910.5 MHz)
#define TX_OUTPUT_POWER                             18        // dBm
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

// DEFINIÇÕES DO OPENLOG (CARTÃO SD)
#define OPENLOG_RX 2
#define OPENLOG_TX 3
HardwareSerial OpenLogSerial(2);

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
// Acelerômetro, giroscópio e magnetômetro brutos + orientação (pitch/roll/yaw)
float lastAccelX = 0, lastAccelY = 0, lastAccelZ = 0;
float lastGyroX = 0, lastGyroY = 0, lastGyroZ = 0;
float lastMagX = 0, lastMagY = 0, lastMagZ = 0;
float lastPitch = 0, lastRoll = 0, lastYaw = 0;

// Estado do OpenLog (preenchido pela rotina de auto-detecção em setup()).
// openLogConectado=true se conseguiu falar com o OpenLog em algum baud.
// openLogAltaVelocidade=true se a 57600 (config.txt aplicado); false se a 9600.
bool openLogConectado = false;
bool openLogAltaVelocidade = false;

// Contador de lotes de telemetria (incrementado a cada transmissão de 1 Hz).
// Usado só no log SD para identificar cada lote visualmente.
uint32_t loteContador = 0;

// Controle do flush periódico do OpenLog (força gravação do buffer interno no SD).
// A cada INTERVALO_FLUSH_MS, entramos em modo comando (que dispara o flush) e
// voltamos para o modo append. Reduz a janela de perda em caso de queda de energia.
uint32_t ultimoFlush = 0;
const uint32_t INTERVALO_FLUSH_MS = 60000;  // 60 segundos

void configurarSensoresIMU() {
  // Taxa depende da velocidade detectada no OpenLog.
  // A linha "I" do SD agora carrega 15 campos (AX/AY/AZ/GX/GY/GZ/MX/MY/MZ/P/R/Y),
  // por isso o baud 9600 exige taxa menor pra não estourar a UART.
  //  - 57600 baud (config.txt aplicado): 25 ms = 40 Hz
  //  - 9600 baud (default de fábrica):   200 ms = 5 Hz
  uint16_t intervalo = openLogAltaVelocidade ? 25 : 200;
  myIMU.enableAccelerometer(intervalo);
  myIMU.enableGyro(intervalo);
  myIMU.enableMagnetometer(intervalo);
  // Rotation Vector: o BNO086 faz a fusão interna (Accel+Gyro+Mag) e devolve
  // o quaternion. getPitch/getRoll/getYaw derivam daqui.
  // Obs.: o yaw em valor absoluto depende do magnetômetro calibrado.
  myIMU.enableRotationVector(intervalo);
}

// ---- Funções auxiliares do OpenLog (auto-detecção de baud) ----

// Envia a sequência de escape (3x Ctrl+Z + CR) e verifica se o OpenLog
// responde com o prompt '<' (indica entrada em modo comando).
bool entrarModoComandoOpenLog() {
  while (OpenLogSerial.available()) OpenLogSerial.read();   // drena RX

  for (int i = 0; i < 3; i++) {
    OpenLogSerial.write(0x1A);   // Ctrl+Z
    delay(10);
  }
  OpenLogSerial.write(0x0D);     // CR
  delay(500);

  // Procura o caractere '<' que o OpenLog envia ao entrar em modo comando.
  // Em baud errado, recebemos silêncio (ou eco de garbage, mas nunca '<').
  while (OpenLogSerial.available()) {
    char c = OpenLogSerial.read();
    if (c == '<') return true;
  }
  return false;
}

// Tenta 57600 primeiro; se falhar, cai para 9600 (default de fábrica).
// Preenche openLogConectado e openLogAltaVelocidade.
void iniciarOpenLog() {
  Serial.println(F("OpenLog: aguardando boot (2s)..."));
  delay(2000);  // tempo para o OpenLog montar o SD e estar pronto

  // 1) Tenta 57600 (requer config.txt aplicado no cartão SD)
  Serial.println(F("OpenLog: testando 57600 baud..."));
  OpenLogSerial.begin(57600, SERIAL_8N1, OPENLOG_RX, OPENLOG_TX);
  delay(500);
  if (entrarModoComandoOpenLog()) {
    openLogConectado = true;
    openLogAltaVelocidade = true;
    Serial.println(F("  -> OK a 57600 baud (config.txt aplicado). IMU em 40 Hz."));
    return;
  }

  // 2) Tenta 9600 (default de fábrica)
  Serial.println(F("OpenLog: 57600 sem resposta. Testando 9600 baud..."));
  OpenLogSerial.end();
  OpenLogSerial.begin(9600, SERIAL_8N1, OPENLOG_RX, OPENLOG_TX);
  delay(500);
  if (entrarModoComandoOpenLog()) {
    openLogConectado = true;
    openLogAltaVelocidade = false;
    Serial.println(F("  -> OK a 9600 baud (config.txt NAO aplicado). IMU em 5 Hz."));
    Serial.println(F("     Para habilitar 40 Hz, copie config.txt da raiz do repo para o SD."));
    return;
  }

  // 3) Falha total — não trava, segue sem log SD (só LoRa)
  openLogConectado = false;
  openLogAltaVelocidade = false;
  Serial.println(F("  -> OpenLog NAO respondeu em nenhum baud. Continuando SEM log SD."));
}

// Força o OpenLog a gravar o buffer interno no cartão SD entrando em modo comando
// (o próprio OpenLog faz o flush ao trocar de modo) e depois volta pro append.
// Custa ~0,8s de bloqueio do loop, mas garante que os últimos dados estão salvos
// no SD em caso de queda de energia. Chamada a cada INTERVALO_FLUSH_MS.
void forcarFlushOpenLog() {
  Serial.println(F("OpenLog: flush forçado..."));
  if (entrarModoComandoOpenLog()) {
    OpenLogSerial.print("append logs_balao.txt");
    OpenLogSerial.write(0x0D);
    delay(300);
    while (OpenLogSerial.available()) OpenLogSerial.read();
    Serial.println(F("  -> flush concluido, append reativado."));
  } else {
    Serial.println(F("  -> flush FALHOU (OpenLog nao respondeu)."));
  }
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

  // ----- OpenLog: auto-detecção de baud -----
  // O OpenLog pode estar a 57600 (config.txt aplicado) ou a 9600 (default).
  // A função tenta os dois, preenche openLogConectado e openLogAltaVelocidade.
  // Ver detalhes em config.txt e README.md.
  iniciarOpenLog();

  // Cria o arquivo de log apenas se o OpenLog respondeu
  if (openLogConectado) {
    OpenLogSerial.print("new logs_balao.txt");
    OpenLogSerial.write(0x0D);
    delay(300);
    while (OpenLogSerial.available()) OpenLogSerial.read();

    OpenLogSerial.print("append logs_balao.txt");
    OpenLogSerial.write(0x0D);
    delay(300);
    while (OpenLogSerial.available()) OpenLogSerial.read();

    OpenLogSerial.println(F("# Logs_balao | LOTE_<seq>,<millis>=telemetria 1Hz | I,<millis>=IMU | lote separado por linha em branco"));
    Serial.println(F("OpenLog: cartao SD configurado!"));
  }

  // Configura o IMU só DEPOIS de saber a velocidade do OpenLog (taxa depende disso)
  configurarSensoresIMU();

  Serial.println(F("Setup concluido! Iniciando leituras e transmissão..."));
  Serial.println(F("--------------------------------------------------"));
}

void loop()
{
  // 1. Manutenção do IMU
  if (myIMU.wasReset()) {
    configurarSensoresIMU();
  }

  // 2. Leitura do IMU (atualiza as variáveis globais a ~40 Hz)
  // A cada amostra do acelerômetro, grava no SD (linha "I") com timestamp
  // millis() para análise pós-voo (ex: detectar o pico do estouro do balão).
  while (myIMU.getSensorEvent() == true)
  {
    uint8_t eventID = myIMU.getSensorEventID();

    if (eventID == SENSOR_REPORTID_ACCELEROMETER) {
      lastAccelX = myIMU.getAccelX(); lastAccelY = myIMU.getAccelY(); lastAccelZ = myIMU.getAccelZ();

      // Log de alta frequência no cartão SD (40 Hz se OpenLog a 57600, 5 Hz se a 9600).
      // Inclui accel + gyro + mag + PRY (todos os mais recentes; PRY/gyro/mag podem ter
      // até ~intervalo ms de atraso em relação ao accel).
      if (openLogConectado) {
        char imuLine[140];
        snprintf(imuLine, sizeof(imuLine),
                 "I,%lu,AX:%.2f,AY:%.2f,AZ:%.2f,GX:%.2f,GY:%.2f,GZ:%.2f,MX:%.2f,MY:%.2f,MZ:%.2f,P:%.2f,R:%.2f,Y:%.2f",
                 millis(), lastAccelX, lastAccelY, lastAccelZ,
                 lastGyroX, lastGyroY, lastGyroZ,
                 lastMagX, lastMagY, lastMagZ,
                 lastPitch, lastRoll, lastYaw);
        OpenLogSerial.println(imuLine);
      }
    }
    else if (eventID == SENSOR_REPORTID_GYROSCOPE_CALIBRATED) {
      lastGyroX = myIMU.getGyroX(); lastGyroY = myIMU.getGyroY(); lastGyroZ = myIMU.getGyroZ();
    }
    else if (eventID == SENSOR_REPORTID_MAGNETIC_FIELD) {
      lastMagX = myIMU.getMagX(); lastMagY = myIMU.getMagY(); lastMagZ = myIMU.getMagZ();
    }
    else if (eventID == SENSOR_REPORTID_ROTATION_VECTOR) {
      // getPitch/getRoll/getYaw retornam graus calculados a partir do quaternion
      lastPitch = myIMU.getPitch();
      lastRoll  = myIMU.getRoll();
      lastYaw   = myIMU.getYaw();
    }
  }

  // 3. Leitura do GPS e BME280 + Transmissão LoRa (~1x por segundo)
  if (myGNSS.getPVT() == true)
  {
    int32_t latitude = myGNSS.getLatitude();
    int32_t longitude = myGNSS.getLongitude();
    uint8_t SIV = myGNSS.getSIV();
    // Tipo de fix: 0=sem fix, 2=2D, 3=3D, 5=somente tempo. Indica se os
    // dados de posição/tempo são confiáveis (avoid usar GPS dados com Fix:0).
    uint8_t fixType = myGNSS.getFixType();

    // Tempo UTC vindo do GNSS
    uint8_t hora   = myGNSS.getHour();
    uint8_t minuto = myGNSS.getMinute();
    uint8_t segundo = myGNSS.getSecond();

    float temp = myBME280.readTempC() + TEMP_CORR;
    float press = myBME280.readFloatPressure() / 100.0F;
    float umidade = myBME280.readFloatHumidity();

    // Imprime no Serial (um dado por linha) para acompanhamento local
    Serial.println(F("------- PT2UNB -------"));
    Serial.print(F("Lat:"));   Serial.println(latitude);
    Serial.print(F("Lon:"));   Serial.println(longitude);
    Serial.print(F("Sat:"));   Serial.println(SIV);
    Serial.print(F("Fix:"));   Serial.println(fixType);
    Serial.print(F("T:"));     Serial.println(temp, 1);
    Serial.print(F("P:"));     Serial.println(press, 1);
    Serial.print(F("U:"));     Serial.println(umidade, 1);
    Serial.printf("Time:%02d:%02d:%02d\n", hora, minuto, segundo);
    Serial.print(F("Pitch:")); Serial.println(lastPitch, 2);
    Serial.print(F("Roll:"));  Serial.println(lastRoll, 2);
    Serial.print(F("Yaw:"));   Serial.println(lastYaw, 2);
    Serial.print(F("AX:"));    Serial.println(lastAccelX, 2);
    Serial.print(F("AY:"));    Serial.println(lastAccelY, 2);
    Serial.print(F("AZ:"));    Serial.println(lastAccelZ, 2);
    Serial.print(F("GX:"));    Serial.println(lastGyroX, 2);
    Serial.print(F("GY:"));    Serial.println(lastGyroY, 2);
    Serial.print(F("GZ:"));    Serial.println(lastGyroZ, 2);
    Serial.print(F("MX:"));    Serial.println(lastMagX, 2);
    Serial.print(F("MY:"));    Serial.println(lastMagY, 2);
    Serial.print(F("MZ:"));    Serial.println(lastMagZ, 2);

    // Serialização multi-linha com prefixo do indicativo de radioamadorismo.
    // pos += garante que nunca escrevemos além do buffer.
    int pos = 0;
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "PT2UNB\n");
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Lat:%ld\n", latitude);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Lon:%ld\n", longitude);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Sat:%d\n", SIV);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Fix:%d\n", fixType);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "T:%.1f\n", temp);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "P:%.1f\n", press);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "U:%.1f\n", umidade);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Time:%02d:%02d:%02d\n", hora, minuto, segundo);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Pitch:%.2f\n", lastPitch);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Roll:%.2f\n", lastRoll);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "Yaw:%.2f\n", lastYaw);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "AX:%.2f\n", lastAccelX);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "AY:%.2f\n", lastAccelY);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "AZ:%.2f\n", lastAccelZ);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "GX:%.2f\n", lastGyroX);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "GY:%.2f\n", lastGyroY);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "GZ:%.2f\n", lastGyroZ);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "MX:%.2f\n", lastMagX);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "MY:%.2f\n", lastMagY);
    pos += snprintf(txpacket + pos, BUFFER_SIZE - pos, "MZ:%.2f\n", lastMagZ);

    // gravação no cartão SD via OpenLog.
    // Formato de cada lote:
    //   LOTE_<seq>,<millis>      <- identificação do lote (linha 1)
    //   PT2UNB                   <- primeira linha dos dados
    //   Lat:..
    //   ...
    //   AZ:..
    //   <linha em branco>        <- quebra final separando lotes
    // O txpacket já começa com "PT2UNB\n" e termina com "\n"; o println()
    // adiciona a quebra extra que vira a linha em branco no fim do lote.
    if (openLogConectado) {
      loteContador++;
      OpenLogSerial.print(F("LOTE_"));
      OpenLogSerial.print(loteContador);
      OpenLogSerial.print(F(","));
      OpenLogSerial.println(millis());
      OpenLogSerial.println(txpacket);
    }

    // trasmissão LoRa
    if (lora_idle == true)
    {
      Serial.printf("Enviando LoRa (%d bytes):\n%s\n", strlen(txpacket), txpacket);
      Radio.Send( (uint8_t *)txpacket, strlen(txpacket) );
      lora_idle = false;
    }

    // Flush periódico do OpenLog: a cada 60s, força gravação do buffer no SD.
    // Reduz a janela de perda em caso de queda de energia para no máx 60s.
    if (openLogConectado && (millis() - ultimoFlush > INTERVALO_FLUSH_MS)) {
      forcarFlushOpenLog();
      ultimoFlush = millis();
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