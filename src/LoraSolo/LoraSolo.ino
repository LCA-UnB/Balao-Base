/*
  Receptor LoRa: u-blox SAM-M10Q + BME280 + BNO086
  Lê a string recebida e extrai (faz o parse) de volta para variáveis.

  Formato do pacote (multi-linha "chave:valor", separado por '\n'),
  produzido pelo LoraBordo.ino:
    PT2UNB
    Lat:<double graus>
    Lon:<double graus>
    Alt:<float metros>
    Sat:<int>
    Fix:<int>
    T:<float>
    P:<float>
    U:<float>
    Time:HH:MM:SS
    Pitch:<float>
    Roll:<float>
    Yaw:<float>
    AX:<float> AY:<float> AZ:<float>
    GX:<float> GY:<float> GZ:<float>
    MX:<float> MY:<float> MZ:<float>
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"

#define RF_FREQUENCY                                910500000 // Hz (910.5 MHz)
#define LORA_BANDWIDTH                              0         // [0: 125 kHz]
#define LORA_SPREADING_FACTOR                       7         // [SF7..SF12]
#define LORA_CODINGRATE                             1         // [1: 4/5]
#define LORA_PREAMBLE_LENGTH                        8         // Same for Tx and Rx
#define LORA_SYMBOL_TIMEOUT                         0         // Symbols
#define LORA_FIX_LENGTH_PAYLOAD_ON                  false
#define LORA_IQ_INVERSION_ON                        false

#define RX_TIMEOUT_VALUE                            1000

// Deve ser igual ou maior que o do Transmissor (256 no LoraBordo)
#define BUFFER_SIZE                                 256 

char rxpacket[BUFFER_SIZE];

static RadioEvents_t RadioEvents;
int16_t rssi, rxSize;
bool lora_idle = true;

// Variáveis para guardar os dados desempacotados.
// Lat/Lon em double para preservar as 7 casas decimais (graus) sem perda.
double r_lat, r_lon;
float r_alt;
int32_t r_sat, r_fix;
int r_hora, r_minuto, r_segundo;
float r_temp, r_press, r_umid;
float r_pitch, r_roll, r_yaw;
float r_ax, r_ay, r_az;
float r_gx, r_gy, r_gz;
float r_mx, r_my, r_mz;

// ---- Helpers de parsing (formato multi-linha chave:valor) ----
// Busca "chave" dentro do pacote recebido e converte o texto seguinte.
// Retorna true se a chave foi encontrada. Como cada campo é lido separadamente,
// o receptor tolera campos faltantes/corrompidos no ar e ordem diferente.
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

    // 2. Faz o parse campo a campo. O transmissor (LoraBordo) envia em formato
    // multi-linha "chave:valor" separado por '\n', por isso não dá pra usar um
    // único sscanf com vírgulas. Buscamos cada chave com strstr, o que também
    // aguenta pacotes parcialmente corrompidos no ar.
    int campos = 0;

    if (lerDouble("Lat:", &r_lat))    campos++;
    if (lerDouble("Lon:", &r_lon))    campos++;
    if (lerFloat("Alt:", &r_alt))     campos++;
    if (lerLong("Sat:", &r_sat))      campos++;
    if (lerLong("Fix:", &r_fix))      campos++;
    if (lerFloat("T:", &r_temp))      campos++;
    if (lerFloat("P:", &r_press))     campos++;
    if (lerFloat("U:", &r_umid))      campos++;
    if (lerFloat("Pitch:", &r_pitch)) campos++;
    if (lerFloat("Roll:", &r_roll))   campos++;
    if (lerFloat("Yaw:", &r_yaw))     campos++;
    if (lerFloat("AX:", &r_ax))       campos++;
    if (lerFloat("AY:", &r_ay))       campos++;
    if (lerFloat("AZ:", &r_az))       campos++;
    if (lerFloat("GX:", &r_gx))       campos++;
    if (lerFloat("GY:", &r_gy))       campos++;
    if (lerFloat("GZ:", &r_gz))       campos++;
    if (lerFloat("MX:", &r_mx))       campos++;
    if (lerFloat("MY:", &r_my))       campos++;
    if (lerFloat("MZ:", &r_mz))       campos++;

    // Time:HH:MM:SS tem parse especial (3 inteiros separados por ':')
    const char *pt = strstr(rxpacket, "Time:");
    if (pt != NULL && sscanf(pt, "Time:%d:%d:%d", &r_hora, &r_minuto, &r_segundo) == 3) {
      campos++;
    }

    // 3. Exibe os dados extraídos (um campo por linha, igual ao LoraBordo)
    Serial.println(F("------- PT2UNB -------"));
    Serial.print(F("Lat:"));   Serial.println(r_lat, 7);
    Serial.print(F("Lon:"));   Serial.println(r_lon, 7);
    Serial.print(F("Alt:"));   Serial.println(r_alt, 1);
    Serial.print(F("Sat:"));   Serial.println(r_sat);
    Serial.print(F("Fix:"));   Serial.println(r_fix);
    Serial.print(F("T:"));     Serial.println(r_temp, 1);
    Serial.print(F("P:"));     Serial.println(r_press, 1);
    Serial.print(F("U:"));     Serial.println(r_umid, 1);
    Serial.printf("Time:%02d:%02d:%02d\n", r_hora, r_minuto, r_segundo);
    Serial.print(F("Pitch:")); Serial.println(r_pitch, 2);
    Serial.print(F("Roll:"));  Serial.println(r_roll, 2);
    Serial.print(F("Yaw:"));   Serial.println(r_yaw, 2);
    Serial.print(F("AX:"));    Serial.println(r_ax, 2);
    Serial.print(F("AY:"));    Serial.println(r_ay, 2);
    Serial.print(F("AZ:"));    Serial.println(r_az, 2);
    Serial.print(F("GX:"));    Serial.println(r_gx, 2);
    Serial.print(F("GY:"));    Serial.println(r_gy, 2);
    Serial.print(F("GZ:"));    Serial.println(r_gz, 2);
    Serial.print(F("MX:"));    Serial.println(r_mx, 2);
    Serial.print(F("MY:"));    Serial.println(r_my, 2);
    Serial.print(F("MZ:"));    Serial.println(r_mz, 2);

    // 4. Validação: o pacote completo tem 21 campos (20 chaves + Time)
    Serial.printf(">>> %d/21 campos extraidos.\n", campos);
    if (campos < 21) {
      Serial.println(F("AVISO: alguns campos nao vieram ou estao corrompidos."));
    }

    Serial.println(F("====================================="));
    lora_idle = true; // Libera o rádio para escutar o próximo pacote
}
