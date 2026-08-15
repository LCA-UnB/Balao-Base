/*
  SKETCH DE CALIBRAÇÃO - LEITOR DE BATERIA
  ==========================================
  
  Use este sketch para coletar dados de calibração com uma fonte de bancada.
  
  Instruções:
  1. Desconecte a bateria de lítio
  2. Conecte a fonte de bancada (3.0V a 4.2V)
  3. Carregue este sketch
  4. Abra Monitor Serial a 115200 baud
  5. Ajuste a fonte e anote os valores de ADC_mV
  6. Teste de 50 em 50 mV de 3000V até 4200V
*/

#define VBAT_ADC_PIN   1    // GPIO1: entrada do divisor
#define VBAT_CTRL_PIN  37   // GPIO37: controle do divisor (LOW = ligado)
#define VBAT_DIVIDER   4.9f // Fator de divisão
#define VBAT_SAMPLES   8    // Número de amostras para média

// Contadores
uint32_t leituraNum = 0;
uint32_t ultimeLeiturasADC[VBAT_SAMPLES];

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  // Configurar pinos
  pinMode(VBAT_CTRL_PIN, OUTPUT);
  digitalWrite(VBAT_CTRL_PIN, HIGH);  // Começa desligado
  analogSetPinAttenuation(VBAT_ADC_PIN, ADC_11db);
  
  // Cabeçalho
  Serial.println("\n\n");
  Serial.println("╔════════════════════════════════════════════════════════════╗");
  Serial.println("║           CALIBRADOR DE BATERIA - HELTEC V3               ║");
  Serial.println("╚════════════════════════════════════════════════════════════╝");
  Serial.println();
  Serial.println("INSTRUÇÕES:");
  Serial.println("1. Desconecte a bateria de lítio");
  Serial.println("2. Conecte fonte de bancada (ajuste cada 5 segundos)");
  Serial.println("3. Use multímetro para medir tensão real nos pinos VBAT");
  Serial.println("4. Anote: Tensão Real (V) | ADC_mV | Valor_ADC");
  Serial.println();
  Serial.println("INICIANDO LEITURAS...");
  Serial.println("════════════════════════════════════════════════════════════");
  Serial.println();
}

void loop() {
  // Liga o divisor e espera estabilização
  digitalWrite(VBAT_CTRL_PIN, LOW);
  delay(5);
  
  // Coleta 8 amostras
  uint32_t soma = 0;
  for (int i = 0; i < VBAT_SAMPLES; i++) {
    uint32_t amostra = analogReadMilliVolts(VBAT_ADC_PIN);
    soma += amostra;
    ultimeLeiturasADC[i] = amostra;
  }
  
  // Desliga o divisor (economiza bateria)
  digitalWrite(VBAT_CTRL_PIN, HIGH);
  
  // Calcula média
  uint32_t mediaADC_mV = soma / VBAT_SAMPLES;
  
  // Calcula tensão sem divisor (valor bruto)
  float tensaoSemDivisor = (float)mediaADC_mV / VBAT_DIVIDER;
  
  // Calcula tensão com divisor (valor real)
  float tensaoComDivisor = (float)mediaADC_mV * VBAT_DIVIDER / 1000.0f;
  
  // Número da leitura
  leituraNum++;
  
  // Exibe resultado formatado
  Serial.print("┌─ Leitura ");
  Serial.print(leituraNum);
  Serial.println(" ─────────────────────────────────────────────┐");
  
  Serial.print("│ Tensão Calculada (com divisor):  ");
  Serial.print(tensaoComDivisor, 3);
  Serial.println(" V                       │");
  
  Serial.print("│ ADC Lido (média de 8 amostras):  ");
  Serial.print(mediaADC_mV);
  Serial.println(" mV                           │");
  
  Serial.print("│ Amostras ADC:                    ");
  for (int i = 0; i < VBAT_SAMPLES; i++) {
    Serial.print(ultimeLeiturasADC[i]);
    if (i < VBAT_SAMPLES - 1) Serial.print(", ");
  }
  Serial.println("   │");
  
  Serial.println("│                                                                 │");
  Serial.print("│ 🔹 COLE NO EXCEL:  Tensão Real (V) = ");
  Serial.print(tensaoComDivisor, 3);
  Serial.print(" | ADC_mV = ");
  Serial.println(mediaADC_mV);
  Serial.println("└─────────────────────────────────────────────────────────────────┘");
  
  Serial.println();
  
  // Aguarda 3 segundos
  delay(3000);
}

/*
  TABELA ESPERADA DE VALORES:
  
  Tensão Real (V)  | ADC_mV
  3.000            | 612
  3.050            | 623
  3.100            | 633
  3.150            | 643
  3.200            | 653
  3.250            | 663
  3.300            | 673
  3.350            | 684
  3.400            | 694
  3.450            | 704
  3.500            | 714
  3.550            | 724
  3.600            | 735
  3.650            | 745
  3.700            | 755
  3.750            | 765
  3.800            | 776
  3.850            | 786
  3.900            | 796
  3.950            | 806
  4.000            | 816
  4.050            | 827
  4.100            | 837
  4.150            | 847
  4.200            | 857
  
  ⚠️ IMPORTANTE: Sua medição pode variar. Use o multímetro como referência!
*/
