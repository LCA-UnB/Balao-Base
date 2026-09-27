import re
import os

def mesclar_telemetrias():
    # Lista com os nomes dos arquivos exatamente como fornecidos
    arquivos = [
        "telemetria_carro1.txt",
        "telemetria_carro2.txt",
        "telemetria_solo1.txt",
        "telemetria_solo2.txt"
    ]

    telemetria_mesclada = {}

    # Expressões regulares para capturar blocos completos e o horário
    padrao_bloco = re.compile(r'(------- PT2UNB -------\n.*?\n----------------------)', re.DOTALL)
    padrao_tempo = re.compile(r'Time:(\d{2}:\d{2}:\d{2})')

    for nome_arquivo in arquivos:
        if os.path.exists(nome_arquivo):
            with open(nome_arquivo, 'r', encoding='utf-8', errors='ignore') as f:
                conteudo = f.read()

            # Encontra todos os blocos válidos cercados pelos delimitadores no arquivo
            blocos = padrao_bloco.findall(conteudo)

            for bloco in blocos:
                tempo_match = padrao_tempo.search(bloco)
                if tempo_match:
                    horario = tempo_match.group(1)
                    
                    # Adiciona ao dicionário se esse horário ainda não foi registrado.
                    # Se a estação de solo pegou um horário que o carro não pegou (ou vice-versa),
                    # ele será adicionado aqui, garantindo o máximo de dados possível sem duplicatas.
                    if horario not in telemetria_mesclada:
                        telemetria_mesclada[horario] = bloco
        else:
            print(f"Aviso: O arquivo '{nome_arquivo}' não foi encontrado no diretório.")

    # Ordena cronologicamente usando a chave de horário
    horarios_ordenados = sorted(telemetria_mesclada.keys())

    # Salva o resultado final em um único arquivo mesclado
    arquivo_saida = "telemetria_mesclada.txt"
    with open(arquivo_saida, 'w', encoding='utf-8') as f:
        for horario in horarios_ordenados:
            f.write(telemetria_mesclada[horario] + "\n")

    print(f"Mesclagem concluída com sucesso!")
    print(f"Total de {len(horarios_ordenados)} pacotes únicos salvos em '{arquivo_saida}'.")

if __name__ == "__main__":
    mesclar_telemetrias()