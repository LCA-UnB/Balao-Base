#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_HMC5883_U.h>
#include <MS5611.h>

#define SDA_PIN 19
#define SCL_PIN 18

Adafruit_MPU6050 mpu;
Adafruit_HMC5883_Unified mag = Adafruit_HMC5883_Unified(12345);
MS5611 ms5611(0x77);

// Variáveis do Filtro Complementar
float roll = 0.0;
float pitch = 0.0;
float yaw = 0.0;
unsigned long lastTime = 0;
const float alpha = 0.98; 

// Variáveis de Calibração (Tara)
float pitchOffset = 0.0;
float rollOffset = 0.0;
float yawOffset = 0.0;
bool isCalibrated = false;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000); 

  if (!mpu.begin(0x68, &Wire)) {
    Serial.println("Erro: MPU6050");
    while (1) delay(10);
  }
  
  mpu.setI2CBypass(true);
  delay(50); 

  if (!mag.begin()) {
    Serial.println("Erro: HMC5883L");
    while (1) delay(10);
  }

  if (!ms5611.begin()) { 
    Serial.println("Erro: MS5611");
    while (1) delay(10);
  }

  lastTime = micros();
}

void loop() {
  unsigned long currentTime = micros();
  float dt = (currentTime - lastTime) / 1000000.0;
  lastTime = currentTime;

  sensors_event_t a, g, temp_mpu;
  mpu.getEvent(&a, &g, &temp_mpu);
  
  sensors_event_t m;
  mag.getEvent(&m);
  
  ms5611.read(); 
  float temperatura = ms5611.getTemperature();
  float pressao = ms5611.getPressure();

  // Cálculos do Acelerômetro (em graus)
  float accRoll = atan2(a.acceleration.y, a.acceleration.z) * 180.0 / PI;
  float accPitch = atan2(-a.acceleration.x, sqrt(a.acceleration.y * a.acceleration.y + a.acceleration.z * a.acceleration.z)) * 180.0 / PI;

  // Integração do Giroscópio e Filtro Complementar
  float gyroRollRate = g.gyro.x * 180.0 / PI;
  float gyroPitchRate = g.gyro.y * 180.0 / PI;
  float gyroYawRate = g.gyro.z * 180.0 / PI;

  roll = alpha * (roll + gyroRollRate * dt) + (1.0 - alpha) * accRoll;
  pitch = alpha * (pitch + gyroPitchRate * dt) + (1.0 - alpha) * accPitch;

  // Cálculo do Yaw Absoluto (com compensação de inclinação)
  float rollRad = roll * PI / 180.0;
  float pitchRad = pitch * PI / 180.0;

  float magX = m.magnetic.x;
  float magY = m.magnetic.y;
  float magZ = m.magnetic.z;

  float Xh = magX * cos(pitchRad) + magZ * sin(pitchRad);
  float Yh = magX * sin(rollRad) * sin(pitchRad) + magY * cos(rollRad) - magZ * sin(rollRad) * cos(pitchRad);

  yaw = atan2(Yh, Xh) * 180.0 / PI;
  if (yaw < 0) yaw += 360.0;

  // ==========================================
  // ROTINA DE ZERO/TARA (Primeiros 4 segundos)
  // ==========================================
  if (!isCalibrated) {
    if (millis() < 4000) {
      // Deixa o filtro estabilizar e guarda os últimos valores como offset
      pitchOffset = pitch;
      rollOffset = roll;
      yawOffset = yaw;
      return; // Interrompe o loop aqui para não sujar o gráfico do plotter
    } else {
      isCalibrated = true;
      // Imprime o cabeçalho para o Plotter apenas quando a calibração terminar
      Serial.println("Temp(C),Pressao(mbar),Pitch(deg),Roll(deg),Yaw(deg)");
    }
  }

  // Aplica os offsets para zerar a atitude inicial
  float finalPitch = pitch - pitchOffset;
  float finalRoll = roll - rollOffset;
  float finalYaw = yaw - yawOffset;

  // Normaliza o Yaw relativo para mantê-lo num escopo contínuo entre -180° e +180°
  if (finalYaw > 180.0) finalYaw -= 360.0;
  if (finalYaw < -180.0) finalYaw += 360.0;

  // Imprime os Dados Zerados
  Serial.print(temperatura, 2); Serial.print(",");
  Serial.print(pressao, 2); Serial.print(",");
  Serial.print(finalPitch, 2); Serial.print(",");
  Serial.print(finalRoll, 2); Serial.print(",");
  Serial.println(finalYaw, 2);

  delay(50); 
}