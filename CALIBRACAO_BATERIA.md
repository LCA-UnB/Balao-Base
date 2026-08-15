# Guia de Calibração da Bateria com Fonte de Bancada

## 1. Material Necessário

- Fonte de alimentação de bancada (ajustável 3.0V - 4.2V)
- Multímetro (para medir a tensão real)
- Cabo USB para programação da placa
- Computador com Arduino IDE
- Cabo de dados USB

## 2. Configuração do Hardware

### Conexão Física

```
Fonte de Bancada (+)  ──→  VBAT (entrada de bateria)
Fonte de Bancada (-)  ──→  GND
```

**IMPORTANTE:** 
- Desconecte a bateria de lítio original antes de conectar a fonte
- Use o multímetro para confirmar a tensão ANTES de conectar à placa

### Pinos Utilizados

- **GPIO1** (ADC): entrada do divisor resistivo
- **GPIO37**: controle do divisor (puxa para LOW para ligar)
- **Fator de divisão**: 4.9x (importante para calibração)

## 3. Procedimento de Calibração

### Passo 1: Preparar o Código de Calibração

Carregue este sketch na placa (em vez do código normal):

```cpp
#define VBAT_ADC_PIN   1
#define VBAT_CTRL_PIN  37
#define VBAT_DIVIDER   4.9f
#define VBAT_SAMPLES   8

void setup() {
  Serial.begin(115200);
  delay(1000);
  
  pinMode(VBAT_CTRL_PIN, OUTPUT);
  digitalWrite(VBAT_CTRL_PIN, HIGH);
  analogSetPinAttenuation(VBAT_ADC_PIN, ADC_11db);
  
  Serial.println("=== CALIBRACAO DE BATERIA ===");
  Serial.println("Tensão_Real_V | ADC_mV | Valor_ADC");
  Serial.println("====================================");
}

void loop() {
  // Liga o divisor
  digitalWrite(VBAT_CTRL_PIN, LOW);
  delay(5);
  
  // Coleta 8 amostras
  uint32_t soma = 0;
  for (int i = 0; i < 8; i++) {
    soma += analogReadMilliVolts(VBAT_ADC_PIN);
  }
  
  // Desliga o divisor
  digitalWrite(VBAT_CTRL_PIN, HIGH);
  
  int adcMV = soma / 8;
  int adcRaw = (int)(adcMV / VBAT_DIVIDER);
  
  Serial.printf("              | %d | %d\n", adcMV, adcRaw);
  
  delay(2000); // Lê a cada 2 segundos
}
```

### Passo 2: Coletar Dados

1. Abra o **Monitor Serial** (115200 baud)
2. Ajuste a fonte de bancada para **3.000V**
3. Meça com o multímetro a tensão real nos pinos de bateria
4. Anote o valor de `ADC_mV` que aparecer no monitor serial
5. Repita de 50 em 50 mV até 4.200V:
   - 3.000V
   - 3.050V
   - 3.100V
   - ... (até 4.200V)

### Passo 3: Planilha de Coleta

Faça uma tabela assim no Excel/Google Sheets:

| Tensão Real (V) | Multímetro (V) | ADC_mV | Valor_ADC |
|---|---|---|---|
| 3.000 | 3.001 | 612 | 125 |
| 3.050 | 3.051 | 623 | 127 |
| 3.100 | 3.102 | 633 | 129 |
| ... | ... | ... | ... |
| 4.200 | 4.201 | 857 | 175 |

**Importante:** Use a coluna "Multímetro (V)" para cálculos finais, não a "Tensão Real (V)"

### Passo 4: Calcular os Valores Corretos

Para cada ponto de medição:

```
Tensão_em_mV_para_tabela = Leitura_Multímetro * 1000
ADC_para_tabela = ADC_mV_lido (usar a coluna ADC_mV, não Valor_ADC)
```

**Exemplo:**
- Multímetro lê 3.001V
- ADC_mV leu 612 mV
- Na tabela: `tensoesReais = 3001` e `valoresLidos = 612`

## 4. Atualizar o Código com Dados Reais

Após coletar todos os dados, atualize as arrays no arquivo:

```cpp
// Substitua pelos SEUS valores reais
const int tensoesReais[] = { 
  3001, 3051, 3101, 3151, 3201, 3251, 3301, 3351, 
  3401, 3451, 3501, 3551, 3601, 3651, 3701, 3751,
  3801, 3851, 3901, 3951, 4001, 4051, 4101, 4151, 4201 
};

const int valoresLidos[] = { 
  612, 623, 633, 643, 653, 663, 673, 684,
  694, 704, 714, 724, 735, 745, 755, 765,
  776, 786, 796, 806, 816, 827, 837, 847, 857 
};
```

## 5. Validação da Calibração

Após atualizar o código:

1. Teste com uma bateria de lítio real (3S LiPo = 3.0V mín, 4.2V máx)
2. Compare leituras do serial com multímetro
3. O erro deve ser < ±50 mV

### Script de Validação

```cpp
float calibrarTensao(float adcMV) {
  // Procura o ponto mais próximo na tabela
  int melhorIdx = 0;
  int menorDiferenca = abs(valoresLidos[0] - (int)adcMV);
  
  for (int i = 1; i < 25; i++) {
    int diff = abs(valoresLidos[i] - (int)adcMV);
    if (diff < menorDiferenca) {
      menorDiferenca = diff;
      melhorIdx = i;
    }
  }
  
  // Interpolação linear entre pontos
  if (melhorIdx > 0 && adcMV > valoresLidos[melhorIdx - 1]) {
    float x0 = valoresLidos[melhorIdx - 1];
    float y0 = tensoesReais[melhorIdx - 1];
    float x1 = valoresLidos[melhorIdx];
    float y1 = tensoesReais[melhorIdx];
    
    return y0 + (adcMV - x0) * (y1 - y0) / (x1 - x0);
  }
  
  return (float)tensoesReais[melhorIdx];
}
```

## 6. Checklist de Calibração

- [ ] Fonte de bancada ajustada e testada com multímetro
- [ ] Bateria desconectada da placa
- [ ] Código de calibração carregado
- [ ] Monitor Serial aberto a 115200 baud
- [ ] Dados coletados de 3.0V a 4.2V em incrementos de 50 mV
- [ ] Tabelas preenchidas com valores reais
- [ ] Código principal atualizado com novos valores
- [ ] Teste de validação realizado
- [ ] Erro de calibração documentado

## 7. Dicas Importantes

1. **Estabilidade da Fonte:** Aguarde 5 segundos após ajustar a tensão para a leitura estabilizar
2. **Temperatura:** Calibre em temperatura ambiente. Bateria de lítio sofre variação com temperatura
3. **Resolução ADC:** 12 bits = 4095, com atenuação 11dB oferece boa precisão
4. **Proteção:** Nunca ultrapasse 4.2V em bateria LiPo (risco de incêndio)
5. **Nota Histórica:** Os dados da tabela anterior eram aproximados. Estes são seus dados reais!

## 8. Troubleshooting

| Problema | Causa | Solução |
|---|---|---|
| Valores ADC instáveis | GPIO37 não ligando divisor | Verificar pino e lógica (LOW = ligado) |
| Diferença > ±100 mV | Divisor resistivo degradado | Revisar resistores em laboratório |
| ADC sempre no máximo | Atenuação ADC incorreta | Usar `ADC_11db` |
| Sem saída serial | Placa não comunicando | Verificar cabo USB e porta COM |

---

**Calibração crítica para:**
- Telemetria precisa em voo
- Proteção de bateria (não deixar drená abaixo de 3.0V)
- Alertas de bateria fraca confiáveis

