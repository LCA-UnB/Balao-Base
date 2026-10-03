# Missões, logs e reprodução

A nova estação de solo fica em `src/tracker.py`. A versão `src/trackerV1.2.py` foi preservada como alternativa legada. Nenhum firmware foi alterado.

## Instalação e execução

Requer Python 3.10 ou superior e Tkinter. No Linux, o Tk deve estar instalado junto ao Python; no Windows, selecione Tcl/Tk no instalador do Python.

```bash
python -m venv .venv
# Linux
.venv/bin/python -m pip install -r requirements-tracker.txt
.venv/bin/python src/tracker.py
# Windows
.venv\Scripts\python -m pip install -r requirements-tracker.txt
.venv\Scripts\python src\tracker.py
```

O executável Windows versionado anteriormente não é atualizado por esta alteração.

## Operação de uma missão

1. Clique em **Nova missão**, informe o nome e selecione a pasta de destino. A sugestão inicial é a pasta `logs/` na raiz do projeto, criada automaticamente. Cada missão recebe uma subpasta com data UTC e identificador único.
2. Em **Configurar tracker**, informe latitude, longitude e altitude MSL da antena em solo. Também é possível escolher a posição com o botão direito no mapa e preencher a altitude. A configuração e suas alterações ficam registradas.
3. Selecione a porta USB e conecte. O aplicativo aceita os formatos das linhas A e B, incluindo bateria, aceleração média e ACK da linha B atual. Campos não enviados permanecem vazios; não são completados com valores de pacotes anteriores.
4. A barra da missão informa os pacotes salvos, o tamanho da fila e eventuais perdas. O cabeçalho mostra a última sincronização com o disco. **Detalhes** exibe o caminho, o erro e o período abrangido por descartes.
5. Desconectar o rádio **não encerra a missão**. Reconectar continua no mesmo arquivo. Fechar o aplicativo salva o que estiver pendente e mantém a missão aberta para retomada. Se houver falha persistente, o aplicativo permite continuar tentando ou fechar após confirmar a perda dos dados que ainda estão na RAM.
6. Depois de reiniciar o programa, use **Retomar missão** e escolha `mission.sqlite3`. A posição/orientação do tracker é restaurada. O painel ao vivo aguarda novos pacotes; use Reprodução para consultar o histórico anterior.
7. **Encerrar missão** desconecta o rádio, salva os registros pendentes e marca a missão como encerrada. Ela continua disponível para reprodução e exportação, mas não aceita nova aquisição.

Uma missão só pode ter um processo gravador por vez. O bloqueio é liberado pelo sistema operacional se o processo morrer; um arquivo `.writer.lock` remanescente não impede a retomada.

## Armazenamento e recuperação

`mission.sqlite3` é a fonte de verdade: um banco SQLite em modo WAL, com `synchronous=FULL`. O gravador trabalha em uma thread exclusiva e confirma transações aproximadamente a cada **1 segundo**, em funcionamento normal. Uma escrita bloqueada pelo sistema operacional pode ultrapassar esse intervalo. Não há garantia de perda zero em quedas de energia ou falhas de hardware.

A recepção serial não escreve diretamente no disco. Uma fila guarda os registros prontos para escrita, com orçamento de **64 MiB** para payloads e uma estimativa de overhead por item. Um lote adicional de até aproximadamente **1 MiB** pode estar em escrita, além dos buffers internos do Python/SQLite e da interface. Esses valores não constituem um limite para toda a RAM do processo.

Se o disco falhar:

- A recepção, o mapa e o apontamento continuam funcionando.
- A escrita é tentada novamente a cada segundo, reabrindo a conexão com o banco quando necessário.
- Quando a fila enche, os registros **mais antigos ainda pendentes** são descartados para preservar os recentes. O lote em escrita não é interrompido no meio de uma transação.
- Os totais de registros, pacotes estruturados e bytes brutos descartados permanecem visíveis. O período informado abrange as perdas, podendo conter trechos preservados entre elas.
- Após recuperação, o banco recebe os eventos `storage_recovered` e, se necessário, `buffer_loss`. Eles guardam início da falha, erro, tentativas, contadores e intervalo de perda. Registros possuem identificadores locais estáveis para evitar duplicação em novas tentativas de uma escrita cujo resultado foi incerto.
- Dados já confirmados no banco não são descartados pela política da fila. Dados ainda na RAM deixam de existir se o processo ou computador for desligado.

A meta de **6 horas** corresponde à duração de uma missão com gravação normal, não a seis horas garantidas de armazenamento temporário sem disco. Não há encerramento automático após seis horas. Os históricos gráficos mantêm até 21.600 amostras recentes, com no máximo aproximadamente 1.000 pontos renderizados por curva/trajeto; o banco mantém todas as amostras gravadas.

## Dados registrados

O banco tem duas tabelas:

| Tabela | Conteúdo |
|---|---|
| `metadata` | Versão do esquema, ID, nome, estado, criação/encerramento e última configuração do tracker |
| `records` | ID local, identificador estável para repetição de escrita, tipo, recebimento UTC, tempo decorrido de missão e payload |

Tipos de registros:

| Tipo | Payload |
|---|---|
| `raw` | Bytes exatos lidos da serial, incluindo quebras de linha, espaços, mensagens do firmware e bytes não UTF-8 |
| `packet` | JSON: campos tipados, horário GPS separado, indicativo, identificação local do quadro, completude, validade GPS, problemas encontrados, configuração do tracker e geometria calculada |
| `event` | JSON: início/retomada de sessão, conexão/desconexão, falha serial, envio de telecomando, mensagens do repetidor (`message_requested`, `message_queued`, `message_sent`, `message_delivered`, `message_failed`, `message_refused`, `message_received`), identidade da estação (`station_requested`, `station_identity`), Ack recebido num relay (`relay_ack`), alteração de configuração, recuperação da escrita, descartes e encerramento |

O horário de recepção inclui data, UTC e milissegundos. O campo GPS `Time` permanece separado, pois o firmware não fornece a data. O tempo decorrido é baseado no relógio monotônico durante cada execução; na retomada, o intervalo entre execuções é estimado pelo relógio UTC do PC. Mudanças incorretas no relógio enquanto o programa estiver fechado podem afetar essa estimativa.

A linha A imprime o pacote bruto e uma cópia já interpretada. O parser usa os delimitadores dessa impressão para produzir um único registro estruturado por recepção, preservando ambas as impressões na captura bruta. Pacotes iguais recebidos novamente pelo rádio continuam sendo registros distintos. Sem contador de transmissão no firmware, os IDs e contadores do PC **não medem perdas no enlace**.

Pacotes incompletos são registrados ao detectar o próximo quadro, desconectar ou ficar três segundos sem novos bytes. Valores inválidos/não finitos viram `null` no registro estruturado, acompanhados de flags. Os bytes originais continuam disponíveis na captura bruta. O apontamento exige Lat/Lon/Alt válidos, `Fix:3` e atualização em até dez segundos.

## Exportação

- **Exportar CSV:** um registro por pacote estruturado, incluindo os dois horários, sensores, flags, posição do tracker e distância/azimute/elevação. UTF-8 com BOM, separador vírgula, ponto decimal e valores ausentes em branco. Os nomes indicam unidades nos campos derivados; `Lat`/`Lon` são graus, `Alt`/`AltB` são metros MSL, `T` é °C, `P` é hPa, `U` é %, `Bat` é V, RSSI é dBm e SNR é dB. Na importação em planilhas, selecione esse separador e convenção decimal. Texto que poderia ser interpretado como fórmula é exportado como texto literal.
- **Exportar bruto:** concatena os BLOBs seriais salvos, sem decodificar ou alterar bytes, em um arquivo `.bin`. É uma captura textual quando a serial contém apenas texto, mas preserva também os bytes corrompidos. Contadores de perda ficam nos eventos do banco, sem inserir conteúdo artificial nessa exportação.

As janelas de exportação (CSV, KML e bruto) sugerem a mesma pasta `logs/` na raiz do projeto, com o nome da subpasta da missão como nome do arquivo.

A exportação usa uma leitura consistente dos dados já gravados; registros ainda na fila não fazem parte dela. Pode ser feita durante a recepção. O aplicativo aguarda uma exportação terminar antes de fechar. Para transportar uma missão, prefira fechar normalmente o aplicativo e copiar a pasta completa; enquanto o banco está aberto, o arquivo `-wal` pode conter transações confirmadas que ainda não estão no arquivo principal.

## Reprodução

Clique em **Reproduzir**, com o rádio desconectado, e selecione uma missão criada nesta versão. Pode ser uma missão encerrada ou ainda aberta. A reprodução não grava novos dados na missão e não envia telecomandos.

- Controles de pausa, velocidade de 0,5× a 60× e busca pela barra temporal.
- Mapa, gráficos e antena 3D usam dados e configurações registrados no momento reproduzido.
- Intervalos sem recepção são preservados. O indicador de idade e a validade do apontamento usam o relógio da reprodução, sem apresentar um dado antigo como recepção ao vivo.
- **Voltar ao vivo** restaura a configuração anterior da antena e aguarda uma nova conexão; a missão aberta não é encerrada pela reprodução.
- Os TXT antigos não são importados nesta etapa.

A visualização da antena usa os eixos locais leste/norte/cima, Terra esférica de raio médio 6.371 km, `h = hypot(leste, norte)` e `d = hypot(h, cima)`. Azimute é relativo ao norte verdadeiro; elevação é relativa ao horizonte local. A componente vertical considera a curvatura. Não há compensação de refração ou declinação magnética. A orientação atual é manual e o desenho não aciona motores.

## Testes

```bash
python -m unittest discover -s tests -v
# Com dependências gráficas e um display (real ou Xvfb), também executa os testes Tk.
python tools/ensaio_missao/soak_mission.py --packets 21600 --rate 100
```

O ensaio de carga usa 21.600 pacotes com horários cobrindo seis horas, enviados a uma taxa acelerada. Ele verifica integridade do SQLite, quantidade de registros, fila, memória e busca/reprodução no fim da missão. **Isso não substitui seis horas de operação real com rádio e alimentação de campo.** O script de medição de RSS usa `resource` (Linux/macOS); a aplicação não depende desse módulo.
