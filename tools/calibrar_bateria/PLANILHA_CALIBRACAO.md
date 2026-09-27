# Planilha de Calibração de Bateria

## Como usar:

1. Abra este arquivo no Excel/Google Sheets
2. Copie os valores de ADC_mV do Monitor Serial
3. Use o multímetro para medir a tensão REAL
4. Preencha a coluna "Multímetro (V)"
5. As outras colunas preenchem automaticamente
6. Copie os valores das últimas duas colunas para o código

---

| Passo | Tensão Alvo (V) | Multímetro (V) | ADC_mV | Tensão Calculada (V) | Status |
|-------|---|---|---|---|---|
| 1 | 3.000 | | 612 | =C2*4.9/1000 | ✓ |
| 2 | 3.050 | | 623 | =C3*4.9/1000 | ✓ |
| 3 | 3.100 | | 633 | =C4*4.9/1000 | ✓ |
| 4 | 3.150 | | 643 | =C5*4.9/1000 | ✓ |
| 5 | 3.200 | | 653 | =C6*4.9/1000 | ✓ |
| 6 | 3.250 | | 663 | =C7*4.9/1000 | ✓ |
| 7 | 3.300 | | 673 | =C8*4.9/1000 | ✓ |
| 8 | 3.350 | | 684 | =C9*4.9/1000 | ✓ |
| 9 | 3.400 | | 694 | =C10*4.9/1000 | ✓ |
| 10 | 3.450 | | 704 | =C11*4.9/1000 | ✓ |
| 11 | 3.500 | | 714 | =C12*4.9/1000 | ✓ |
| 12 | 3.550 | | 724 | =C13*4.9/1000 | ✓ |
| 13 | 3.600 | | 735 | =C14*4.9/1000 | ✓ |
| 14 | 3.650 | | 745 | =C15*4.9/1000 | ✓ |
| 15 | 3.700 | | 755 | =C16*4.9/1000 | ✓ |
| 16 | 3.750 | | 765 | =C17*4.9/1000 | ✓ |
| 17 | 3.800 | | 776 | =C18*4.9/1000 | ✓ |
| 18 | 3.850 | | 786 | =C19*4.9/1000 | ✓ |
| 19 | 3.900 | | 796 | =C20*4.9/1000 | ✓ |
| 20 | 3.950 | | 806 | =C21*4.9/1000 | ✓ |
| 21 | 4.000 | | 816 | =C22*4.9/1000 | ✓ |
| 22 | 4.050 | | 827 | =C23*4.9/1000 | ✓ |
| 23 | 4.100 | | 837 | =C24*4.9/1000 | ✓ |
| 24 | 4.150 | | 847 | =C25*4.9/1000 | ✓ |
| 25 | 4.200 | | 857 | =C26*4.9/1000 | ✓ |

---

## Para copiar para o código:

### Array de Tensões Reais (em mV):
Copie os valores da coluna "Multímetro (V)" multiplicados por 1000

```cpp
const int tensoesReais[] = { 
  3000, 3050, 3100, 3150, 3200, 3250, 3300, 3350, 
  3400, 3450, 3500, 3550, 3600, 3650, 3700, 3750,
  3800, 3850, 3900, 3950, 4000, 4050, 4100, 4150, 4200 
};
```

### Array de Valores Lidos (ADC_mV):
Copie os valores da coluna "ADC_mV"

```cpp
const int valoresLidos[] = { 
  612, 623, 633, 643, 653, 663, 673, 684,
  694, 704, 714, 724, 735, 745, 755, 765,
  776, 786, 796, 806, 816, 827, 837, 847, 857 
};
```

---

## Validação:

✓ Quantos pontos coletados? _____ (deve ser 25)
✓ Erro máximo entre Multímetro e Calculada? _____ (deve ser < ±50 mV)
✓ Fator de divisão verificado? ☐ SIM ☐ NÃO
✓ ADC calibrado em ADC_11db? ☐ SIM ☐ NÃO
✓ Temperatura ambiente registrada? _____ °C

