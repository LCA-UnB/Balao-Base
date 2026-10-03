/*
  Receptor/Transmissor de Solo - Slots ancorados no GPS do balao
  - O bordo transmite no segundo IMPAR e escuta o segundo PAR inteiro.
  - O solo nao tem GPS: ele deriva a janela a partir do instante em que o
    pacote do bordo termina de chegar, e mira o CENTRO da janela de escuta.
  - Retransmite o comando ate o campo Ack: da telemetria confirmar.

  ATUALIZACAO: adicionada leitura/impressao dos campos de aceleracao
  enviados pelo bordo (AX, AY, AZ instantaneos e AXavg, AYavg, AZavg,
  medias da janela de 1s calculada no bordo).

  REPETIDOR DE MENSAGENS (varias estacoes):
  - Cada estacao tem uma letra (A, B, C...) e todas conhecem o total N.
    A janela PAR do segundo s pertence a estacao (s / 2) mod N; o solo
    descobre o segundo pelo campo Time: que vem em todo pacote do bordo.
    Assim duas estacoes nunca transmitem na mesma janela.
  - "M <texto>" na serial envia uma mensagem. O bordo a desce no lugar da
    telemetria ("PT2UNB-R") e todas as estacoes a recebem; quem enviou usa
    esse mesmo pacote como confirmacao.
  - "ID <letra> <N>" configura a estacao (gravado na flash); "ID?" consulta.
  - Telecomandos numericos so saem da estacao A.
  - Linhas "[MSG] ...", "[ESTACAO] ..." e "[ACK] ..." sao lidas pela interface.
*/

#include "LoRaWan_APP.h"
#include "Arduino.h"
#include <Preferences.h>

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
// O bordo comeca a transmitir logo apos o topo de um segundo impar. O inicio
// desse segundo e' estimado voltando, a partir do RxDone, o tempo no ar do
// pacote recebido (que muda entre telemetria e relay) e a latencia do bordo
// antes do Send() (leitura do barometro e 15 amostras da bateria, estimada).
// A janela de escuta - o segundo par seguinte - comeca 1000 ms depois disso,
// e o envio e' centralizado nela de acordo com o proprio tempo no ar.
#define LATENCIA_TX_BORDO_MS                        50
#define JANELA_ESCUTA_MS                            1000

// Quantas janelas proprias tentar antes de desistir de um comando/mensagem.
#define MAX_TENTATIVAS                              5

// A telemetria agora carrega 20 campos (14 originais + AX/AY/AZ +
// AXavg/AYavg/AZavg). Com o CRC ligado, um pacote que chega integro tem
// todos. Menos que isso e' corrupcao: descarta em vez de imprimir os
// valores da leitura anterior como se fossem novos.
#define CAMPOS_ESPERADOS                            17

// --- REPETIDOR ---
#define MSG_TEXTO_MAX                               100
#define MSG_ID_TAM                                  8
#define IDS_VISTOS                                  16

char rxpacket[BUFFER_SIZE];
static RadioEvents_t RadioEvents;

// === FLAGS DE CONTROLE DE RADIO ===
volatile bool txDoneFlag = false;
volatile bool rxDoneFlag = false;
volatile bool rxErrFlag  = false;
volatile int16_t rxRssi = 0;
volatile int8_t  rxSnr  = 0;
volatile uint32_t rxFimMs = 0;
volatile uint16_t rxTamanho = 0;

// === AGENDAMENTO DO ENVIO ===
bool     envio_agendado = false;
uint32_t envio_em_ms = 0;
char     txenvio[BUFFER_SIZE];
bool     envio_eh_msg = false;

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

// --- IDENTIDADE DA ESTACAO (gravada na flash) ---
Preferences prefs;
char estacaoId = 'A';
uint8_t estacoesTotal = 1;
uint32_t msgSeq = 0;

// --- MENSAGEM PROPRIA PENDENTE ---
bool has_msg = false;
char msg_id[MSG_ID_TAM];
char msg_texto[MSG_TEXTO_MAX + 1];
uint8_t msg_tentativas = 0;

// --- MENSAGENS JA RECEBIDAS (o bordo repete o relay quando a origem retransmite) ---
char idsVistos[IDS_VISTOS][MSG_ID_TAM];
uint8_t idsVistosPos = 0;

// --- ENTRADA SERIAL NAO BLOQUEANTE ---
char serialBuf[MSG_TEXTO_MAX + 32];
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
// Copia o valor de "\n<chave>" ate o fim da linha.
bool lerTexto(const char *chave, char *dest, size_t tam) {
  char busca[16];
  snprintf(busca, sizeof(busca), "\n%s", chave);
  const char *p = strstr(rxpacket, busca);
  if (p == NULL) return false;
  p += strlen(busca);
  size_t n = 0;
  while (p[n] != '\0' && p[n] != '\n' && n < tam - 1) {
    dest[n] = p[n];
    n++;
  }
  dest[n] = '\0';
  return n > 0;
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

// Tempo no ar (ms) para SF7 / BW125 / CR4-5 / preambulo 8 / header explicito
// / CRC ligado. Ex.: telemetria de ~206 B = ~328 ms; MSG de 111 B = ~190 ms.
uint32_t tempoNoArMs(size_t bytes) {
  const float tSimbolo = 1.024f;   // 2^SF / BW = 128 / 125 kHz
  int simbolos = 8 + ((8 * (int)bytes + 16 + 27) / 28) * 5;
  return (uint32_t)((LORA_PREAMBLE_LENGTH + 4.25f + simbolos) * tSimbolo + 0.5f);
}

// ==========================================================================
//  ESTACAO
// ==========================================================================

// N precisa dividir as 30 janelas pares de um minuto; senao o rodizio
// quebraria na virada do minuto.
bool totalValido(int n) {
  return n == 1 || n == 2 || n == 3 || n == 5 || n == 6;
}

void imprimirEstacao() {
  Serial.printf("[ESTACAO] ID:%c N:%u\n", estacaoId, estacoesTotal);
}

void carregarEstacao() {
  prefs.begin("estacao", false);
  char id = (char)prefs.getUChar("id", 'A');
  uint8_t n = prefs.getUChar("n", 1);
  msgSeq = prefs.getUInt("seq", 0);
  if (totalValido(n) && id >= 'A' && id < 'A' + n) {
    estacaoId = id;
    estacoesTotal = n;
  }
}

bool configurarEstacao(char id, int n) {
  if (id >= 'a' && id <= 'z') id -= 32;
  if (!totalValido(n) || id < 'A' || id >= 'A' + n) {
    Serial.println(F("[ESTACAO] Invalido. Use ID <letra> <N>, com N em 1,2,3,5,6 e letra entre A e a N-esima."));
    return false;
  }
  estacaoId = id;
  estacoesTotal = (uint8_t)n;
  prefs.putUChar("id", (uint8_t)estacaoId);
  prefs.putUChar("n", estacoesTotal);
  // Um envio ja agendado foi calculado para a janela da identidade antiga e
  // colidiria com a estacao que agora e' dona dela; a proxima janela propria
  // reagenda a mensagem pendente.
  envio_agendado = false;
  if (has_cmd && estacaoId != 'A') {
    has_cmd = false;
    Serial.println(F("[ESTACAO] Telecomando pendente descartado: so a estacao A envia telecomandos."));
  }
  return true;
}

// A janela PAR que segue o segundo IMPAR do pacote recebido e' desta estacao?
bool janelaEhMinha(int segundoImpar) {
  if (segundoImpar < 0 || segundoImpar > 59 || segundoImpar % 2 == 0) return false;
  int segundoPar = (segundoImpar + 1) % 60;
  return (segundoPar / 2) % estacoesTotal == (uint8_t)(estacaoId - 'A');
}

bool idJaVisto(const char *id) {
  for (uint8_t i = 0; i < IDS_VISTOS; i++) {
    if (strcmp(idsVistos[i], id) == 0) return true;
  }
  return false;
}

void lembrarId(const char *id) {
  strncpy(idsVistos[idsVistosPos], id, MSG_ID_TAM - 1);
  idsVistos[idsVistosPos][MSG_ID_TAM - 1] = '\0';
  idsVistosPos = (idsVistosPos + 1) % IDS_VISTOS;
}

// ==========================================================================
//  SETUP
// ==========================================================================

void setup() {
  Serial.begin(115200);
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);

  carregarEstacao();

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
  imprimirEstacao();
  Serial.println(F("Envio centralizado na janela de escuta desta estacao (rodizio pelo Time: do bordo)."));
  Serial.println(F("Digite um numero e ENTER para um telecomando (so estacao A),"));
  Serial.println(F("'M <texto>' para uma mensagem, 'ID <letra> <N>' para configurar ou 'ID?' para consultar.\n"));

  abrirEscuta();
}

// ==========================================================================
//  RECEPCAO
// ==========================================================================

bool processarTelemetria() {
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
    return false;
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
  Serial.print(F("AXavg:"));  Serial.println(r_axavg, 3);
  Serial.print(F("AYavg:"));  Serial.println(r_ayavg, 3);
  Serial.print(F("AZavg:"));  Serial.println(r_azavg, 3);
  Serial.print(F("Bat:"));    Serial.println(r_bat, 2);
  Serial.printf("Ack:%ld\n", (long)r_ack);
  Serial.println(F("----------------------"));
  return true;
}

// Pacote "PT2UNB-R": mensagem repetida pelo bordo no lugar da telemetria.
bool processarRelay() {
  char id[MSG_ID_TAM];
  char texto[MSG_TEXTO_MAX + 1];
  int hora, minuto, segundo;
  int32_t ack;
  const char *pt = strstr(rxpacket, "\nTime:");
  if (!lerTexto("Id:", id, sizeof(id)) || !lerTexto("Msg:", texto, sizeof(texto)) ||
      pt == NULL || sscanf(pt, "\nTime:%d:%d:%d", &hora, &minuto, &segundo) != 3 ||
      !lerLong("Ack:", &ack)) {
    cntDescartado++;
    Serial.printf("[AVISO] Relay incompleto. Descartado (total: %lu).\n", cntDescartado);
    return false;
  }
  r_hora = hora; r_minuto = minuto; r_segundo = segundo;
  r_ack = ack;
  // O bordo zera o Ack depois de transmiti-lo; se ele desceu no relay, a
  // telemetria seguinte ja vem com 0. A linha [ACK] leva a confirmacao a
  // interface mesmo quando a mensagem e' uma repeticao ignorada.
  if (ack != 0) Serial.printf("[ACK] Ack:%ld\n", (long)ack);

  bool repetida = idJaVisto(id);
  if (!repetida) {
    lembrarId(id);
    Serial.printf("[MSG] RECEBIDA Id:%s De:%c Hora:%02d:%02d:%02d RSSI:%d SNR:%d Texto:%s\n",
                  id, id[0], hora, minuto, segundo, rxRssi, rxSnr, texto);
  } else {
    Serial.printf("[RELAY] Mensagem %s repetida pelo bordo. Ignorada.\n", id);
  }

  if (has_msg && msg_tentativas > 0 && strcmp(id, msg_id) == 0) {
    Serial.printf("[MSG] ENTREGUE Id:%s Tentativas:%u\n", msg_id, msg_tentativas);
    has_msg = false;
  }
  return true;
}

// Telecomando: o Ack: vem tanto na telemetria quanto no relay.
void verificarComando() {
  if (!has_cmd) return;
  // Exige ao menos uma transmissao antes de aceitar a confirmacao, para nao
  // confundir um Ack: remanescente de uma sessao anterior com uma resposta.
  if (tentativas > 0 && r_ack == pending_cmd + 1) {
    Serial.printf(">>> COMANDO %d CONFIRMADO pelo balao (Ack:%ld) apos %d tentativa(s).\n\n",
                  pending_cmd, (long)r_ack, tentativas);
    has_cmd = false;
  }
}

// Agenda o envio no centro da janela par seguinte, se ela for desta estacao.
// Comando tem prioridade sobre mensagem; uma transmissao por janela.
void agendarNaJanela(uint32_t fimMs, uint16_t bytesRecebidos) {
  if (envio_agendado || !janelaEhMinha(r_segundo)) return;

  if (has_cmd && tentativas >= MAX_TENTATIVAS) {
    Serial.printf(">>> COMANDO %d NAO confirmado apos %d tentativas. Desistindo.\n",
                  pending_cmd, tentativas);
    Serial.println(F("    Verifique alcance, antena e se o bordo esta transmitindo.\n"));
    has_cmd = false;
  }
  if (has_msg && msg_tentativas >= MAX_TENTATIVAS) {
    Serial.printf("[MSG] FALHOU Id:%s Tentativas:%u\n", msg_id, msg_tentativas);
    has_msg = false;
  }

  if (has_cmd) {
    snprintf(txenvio, sizeof(txenvio), "CMD:%d", pending_cmd);
    envio_eh_msg = false;
    Serial.printf("[TDM] Comando %d aguardando janela (tentativa %d de %d).\n",
                  pending_cmd, tentativas + 1, MAX_TENTATIVAS);
  } else if (has_msg) {
    snprintf(txenvio, sizeof(txenvio), "MSG:%s:%s", msg_id, msg_texto);
    envio_eh_msg = true;
  } else {
    return;
  }

  uint32_t inicioImpar = fimMs - tempoNoArMs(bytesRecebidos) - LATENCIA_TX_BORDO_MS;
  uint32_t ar = tempoNoArMs(strlen(txenvio));
  envio_em_ms = inicioImpar + JANELA_ESCUTA_MS + (JANELA_ESCUTA_MS - ar) / 2;
  envio_agendado = true;
}

void processarPacoteBordo() {
  uint32_t fimMs = rxFimMs;
  uint16_t bytes = rxTamanho;
  bool ok = (strncmp(rxpacket, "PT2UNB-R", 8) == 0) ? processarRelay() : processarTelemetria();
  if (ok) {
    verificarComando();
    agendarNaJanela(fimMs, bytes);
  }
  abrirEscuta();
}

// ==========================================================================
//  ENTRADA DO USUARIO
// ==========================================================================

void tratarLinhaSerial(char *linha) {
  if (strcmp(linha, "ID?") == 0) {
    imprimirEstacao();
    return;
  }

  if (strncmp(linha, "ID ", 3) == 0) {
    char letra = 0;
    int n = 0;
    if (sscanf(linha + 3, " %c %d", &letra, &n) == 2 && configurarEstacao(letra, n)) {
      imprimirEstacao();
    }
    return;
  }

  if ((linha[0] == 'M' || linha[0] == 'm') && linha[1] == ' ') {
    const char *texto = linha + 2;
    while (*texto == ' ') texto++;
    if (*texto == '\0') {
      Serial.println(F("[MSG] RECUSADA Motivo:vazia"));
      return;
    }
    if (has_msg) {
      Serial.printf("[MSG] RECUSADA Motivo:ocupada Id:%s\n", msg_id);
      return;
    }
    size_t n = 0;
    for (; texto[n] != '\0' && n < MSG_TEXTO_MAX; n++) {
      char c = texto[n];
      msg_texto[n] = (c >= 32 && c <= 126) ? c : '?';
    }
    msg_texto[n] = '\0';
    // O contador fica na flash para o id nao se repetir depois de um reset;
    // as outras estacoes descartam ids que ja viram.
    msgSeq = msgSeq % 99999 + 1;
    prefs.putUInt("seq", msgSeq);
    snprintf(msg_id, sizeof(msg_id), "%c%lu", estacaoId, (unsigned long)msgSeq);
    has_msg = true;
    msg_tentativas = 0;
    Serial.printf("[MSG] ENFILEIRADA Id:%s Texto:%s\n", msg_id, msg_texto);
    return;
  }

  if (estacaoId != 'A') {
    Serial.println(F("[ESTACAO] Telecomandos numericos so pela estacao A."));
    return;
  }
  // Um CMD ja agendado carrega o numero anterior em txenvio; sem cancelar,
  // ele sairia contado como tentativa do comando novo.
  if (envio_agendado && !envio_eh_msg) envio_agendado = false;
  pending_cmd = atoi(linha);
  has_cmd = true;
  tentativas = 0;
  Serial.println(F("\n====================================="));
  Serial.printf(">> COMANDO [%d] ENFILEIRADO <<\n", pending_cmd);
  Serial.printf("   Sera enviado na proxima janela de escuta do balao.\n");
  Serial.println(F("====================================="));
}

// ==========================================================================
//  LOOP
// ==========================================================================

void loop() {
  if (txDoneFlag) {
    txDoneFlag = false;
    Serial.println(F("[TX] Envio no ar. Voltando a escutar."));
  }

  if (rxDoneFlag) {
    rxDoneFlag = false;
    cntRxOk++;
    processarPacoteBordo();
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
    if (envio_eh_msg) {
      msg_tentativas++;
      Serial.printf("[MSG] ENVIADA Id:%s Tentativa:%u\n", msg_id, msg_tentativas);
    } else {
      tentativas++;
      Serial.printf("[TDM] Disparando '%s' (tentativa %d de %d).\n", txenvio, tentativas, MAX_TENTATIVAS);
    }

    Radio.Standby();
    reconfigurarLoRaTX();
    Radio.Send((uint8_t *)txenvio, strlen(txenvio));
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
        tratarLinhaSerial(serialBuf);
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
  rxFimMs = millis();
  rxTamanho = size;
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
