import json
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
import requests

# CONSTANTES E PARÂMETROS AGRONÔMICOS
ARQUIVO_DADOS = "dados_myfarm.json"

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "/myfarm/device01/attrs"

API_CLOUD_URL = "https://6af8bb5b9bdd097c326f1ee6.mockapi.io/api/v1"

PERFIS_CULTURAS: Dict[str, Tuple[float, float, int, int, float]] = {
    "Alface": (18.0, 25.0, 30, 30, 2.02),
    "Rucula": (16.0, 24.0, 25, 25, 1.65),
    "Microgreens": (18.0, 26.0, 35, 12, 0.95),
    "Morango Anao": (15.0, 22.0, 40, 60, 4.10),
}

BIOMASSA_MAX_K = 250.0   # gramas
CONSTANTE_A = 49.0       # biomassa inicial da muda
TAXA_CRESCIMENTO_R = 0.2116


# PERSISTÊNCIA EM JSON E INTEGRAÇÃO CLOUD
def carregar_banco_dados() -> Dict[str, Any]:
    if not os.path.exists(ARQUIVO_DADOS):
        estrutura_inicial: Dict[str, Any] = {
            "estufa_atual": {
                "cultura": "Alface",
                "dia_ciclo": 18,
                "ultima_telemetria": None,
            },
            "historico_leituras": [],
            "mural_comunitario": [
                {
                    "id": 1,
                    "morador": "Dona Helena (Apto 42)",
                    "item": "2 pés de Alface Crespa",
                    "distancia_km": 0.4,
                    "status": "Disponível",
                }
            ],
        }
        salvar_banco_dados(estrutura_inicial)
        return estrutura_inicial

    with open(ARQUIVO_DADOS, "r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def salvar_banco_dados(dados: Dict[str, Any]) -> None:
    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=4, ensure_ascii=False)


def sincronizar_api_react(payload: Dict[str, Any]) -> bool:
    if not API_CLOUD_URL:
        return False
    try:
        resposta = requests.post(API_CLOUD_URL, json=payload, timeout=5)
        return resposta.status_code in (200, 201)
    except requests.RequestException:
        return False

# MOTOR MATEMÁTICO E DIAGNÓSTICO

def calcular_crescimento_logistico(dia: int) -> Tuple[float, float]:
    t = max(0, min(dia, 60))
    exponencial = math.exp(-TAXA_CRESCIMENTO_R * t)
    denominador = 1.0 + (CONSTANTE_A * exponencial)

    biomassa = BIOMASSA_MAX_K / denominador
    taxa_diaria = (BIOMASSA_MAX_K * CONSTANTE_A * TAXA_CRESCIMENTO_R * exponencial) / (denominador ** 2)
    return round(biomassa, 2), round(taxa_diaria, 2)


def analisar_autonomia_hidrica(nivel_agua_pct: int, cultura: str, dia_ciclo: int) -> Dict[str, Any]:
    _, _, _, ciclo_total, tanque_max_l = PERFIS_CULTURAS.get(cultura, PERFIS_CULTURAS["Alface"])

    litros_atuais = (nivel_agua_pct / 100.0) * tanque_max_l
    litros_utilizaveis = max(0.0, litros_atuais - (0.15 * tanque_max_l))
    consumo_medio_diario = tanque_max_l / ciclo_total
    fator_fase = 1.35 if 14 <= dia_ciclo <= 24 else 0.90
    consumo_estimado_dia = consumo_medio_diario * fator_fase
    dias_autonomia = litros_utilizaveis / consumo_estimado_dia if consumo_estimado_dia > 0 else 0.0

    return {
        "litros_atuais": round(litros_atuais, 2),
        "tanque_max_l": tanque_max_l,
        "consumo_diario_ml": round(consumo_estimado_dia * 1000, 1),
        "dias_autonomia": round(dias_autonomia, 1),
    }


def diagnosticar_estufa(telemetria: Dict[str, Any]) -> Dict[str, Any]:
    cultura = telemetria.get("cultura", "Alface")
    if cultura not in PERFIS_CULTURAS:
        cultura = "Alface"

    temp = float(telemetria.get("t", 22.0))
    umid = float(telemetria.get("h", 60.0))
    agua = int(telemetria.get("w", 50))
    luz = int(telemetria.get("l", 70))
    dia = int(telemetria.get("dia", 18))
    status_borda = telemetria.get("s", "IDEAL")

    temp_min, temp_max, agua_min, ciclo_total, _ = PERFIS_CULTURAS[cultura]
    biomassa_g, taxa_g_dia = calcular_crescimento_logistico(dia)
    hidrica = analisar_autonomia_hidrica(agua, cultura, dia)

    alertas: List[str] = []
    if temp > temp_max:
        alertas.append(f"Temperatura elevada ({temp}°C > {temp_max}°C): Exaustão recomendada.")
    elif temp < temp_min:
        alertas.append(f"Temperatura baixa ({temp}°C < {temp_min}°C): Reduzir ventilação.")

    if agua < 15:
        alertas.append("NÍVEL CRÍTICO DE ÁGUA (<15%): Bomba bloqueada por segurança na borda.")
    elif agua <= agua_min:
        alertas.append(f"Reservatório em atenção ({agua}%): Reabastecer em breve.")

    if luz < 40:
        alertas.append(f"Luminosidade baixa ({luz}%): Painel LED Rosa ativo.")

    if not alertas:
        alertas.append("Parâmetros microclimáticos dentro da faixa ótima de desenvolvimento.")

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cultura": cultura,
        "dia_ciclo": dia,
        "dias_restantes_colheita": max(0, ciclo_total - dia),
        "status_borda": status_borda,
        "sensores": {"temp_c": temp, "umid_pct": umid, "agua_pct": agua, "luz_pct": luz},
        "agronomia": {
            "biomassa_estimada_g": biomassa_g,
            "taxa_crescimento_g_dia": taxa_g_dia,
            "progresso_colheita_pct": round((biomassa_g / BIOMASSA_MAX_K) * 100, 1),
        },
        "hidrica": hidrica,
        "recomendacoes": alertas,
    }

# CAPTURA DE TELEMETRIA VIA MQTT

def capturar_pacote_mqtt(timeout_segundos: int = 6) -> Optional[Dict[str, Any]]:
    pacote_recebido: List[Dict[str, Any]] = []
    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(MQTT_TOPIC)
    def on_message(client, userdata, msg):
        try:
            conteudo = json.loads(msg.payload.decode("utf-8"))
            pacote_recebido.append(conteudo)
        except json.JSONDecodeError:
            pass
    try:
        cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        cliente = mqtt.Client()

    cliente.on_connect = on_connect
    cliente.on_message = on_message

    print(f"\n[MQTT] Conectando a {MQTT_BROKER} no tópico '{MQTT_TOPIC}'...")
    try:
        cliente.connect(MQTT_BROKER, MQTT_PORT, 60)
        cliente.loop_start()
        inicio = time.time()
        while (time.time() - inicio) < timeout_segundos and not pacote_recebido:
            time.sleep(0.2)
        cliente.loop_stop()
        cliente.disconnect()
    except Exception as erro:
        print(f"[ERRO MQTT] Falha na comunicação com o broker: {erro}")
        return None
    return pacote_recebido[0] if pacote_recebido else None

# INTERFACE

def exibir_relatorio_diagnostico(relatorio: Dict[str, Any]) -> None:
    s = relatorio["sensores"]
    a = relatorio["agronomia"]
    h = relatorio["hidrica"]

    print("\n" + "=" * 62)
    print(f" MYFARM ENGINE | RELATÓRIO AGRONÔMICO ({relatorio['timestamp']})")
    print("=" * 62)
    print(f" Cultura Ativa : {relatorio['cultura']} (Dia {relatorio['dia_ciclo']} | Faltam {relatorio['dias_restantes_colheita']} dias)")
    print(f" Status Borda  : [{relatorio['status_borda']}]")
    print("-" * 62)
    print(f" Sensores      : Temp {s['temp_c']}°C | Umid {s['umid_pct']}% | Água {s['agua_pct']}% | Luz {s['luz_pct']}%")
    print(f" Biomassa B(t) : {a['biomassa_estimada_g']}g ({a['progresso_colheita_pct']}% do alvo) | Ganho: +{a['taxa_crescimento_g_dia']}g/dia")
    print(f" Reservatório  : {h['litros_atuais']}L / {h['tanque_max_l']}L | Consumo: {h['consumo_diario_ml']} mL/dia | Autonomia: {h['dias_autonomia']} dias")
    print("-" * 62)
    print(" Diagnóstico e Recomendações:")
    for rec in relatorio["recomendacoes"]:
        print(f"  -> {rec}")
    print("=" * 62)


def simular_leitura_manual(cultura_atual: str, dia_atual: int) -> Dict[str, Any]:
    """Permite testar o motor matemático caso a simulação do Wokwi esteja pausada."""
    print("\n--- Simulação Manual de Sensores ---")
    while True:
        try:
            temp = float(input("Temperatura interna (°C) [ex: 23.5]: "))
            umid = float(input("Umidade relativa (%) [ex: 65]: "))
            agua = int(input("Nível do reservatório (0-100%): "))
            luz = int(input("Luminosidade interna (0-100%): "))
            if not (0 <= agua <= 100 and 0 <= luz <= 100):
                raise ValueError("Percentuais devem estar entre 0 e 100.")
            break
        except ValueError as e:
            print(f"[Entrada Inválida] {e}. Tente novamente.")
    status = "CRITICO" if (agua < 15 or temp > 30.0) else ("ATENCAO" if (agua <= 30 or temp > 25.0 or luz < 40) else "IDEAL")
    return {
        "cultura": cultura_atual,
        "dia": dia_atual,
        "t": temp,
        "h": umid,
        "w": agua,
        "l": luz,
        "s": status,
    }

def gerenciar_mural_comunitario(dados: Dict[str, Any]) -> None:
    """Lista e cadastra ofertas de colheita excedente no raio de 2km."""
    print("\n--- Mural Comunitário MyFarm (Raio 2 km) ---")
    for oferta in dados["mural_comunitario"]:
        print(f" #{oferta['id']} | {oferta['morador']} -> {oferta['item']} ({oferta['distancia_km']} km) [{oferta['status']}]")
    opcao = input("\nDeseja publicar uma nova oferta de colheita? (s/n): ").strip().lower()
    if opcao == "s":
        try:
            morador = input("Seu nome / Apartamento: ").strip()
            item = input("Alimento disponível para troca/doação: ").strip()
            dist = float(input("Distância aproximada (km, máx 2.0): "))
            if not morador or not item or not (0 < dist <= 2.0):
                raise ValueError("Dados inválidos ou fora do raio comunitário de 2 km.")

            novo_id = len(dados["mural_comunitario"]) + 1
            dados["mural_comunitario"].append(
                {
                    "id": novo_id,
                    "morador": morador,
                    "item": item,
                    "distancia_km": round(dist, 2),
                    "status": "Disponível",
                }
            )
            salvar_banco_dados(dados)
            print("[OK] Oferta publicada e salva em 'dados_myfarm.json'!")
        except ValueError as e:
            print(f"[Erro] {e}")

def menu_principal() -> None:
    """Loop principal de execução do sistema MyFarm."""
    dados = carregar_banco_dados()

    while True:
        estufa = dados["estufa_atual"]
        print("\n" + "-" * 45)
        print(f" MYFARM PYTHON CORE | Cultura: {estufa['cultura']} (Dia {estufa['dia_ciclo']})")
        print("-" * 45)
        print(" [1] Capturar Telemetria Real do Wokwi (MQTT)")
        print(" [2] Simular Leitura Manual e Rodar Cálculo")
        print(" [3] Alterar Cultura ou Dia do Ciclo")
        print(" [4] Consultar Histórico de Leituras (JSON)")
        print(" [5] Mural Comunitário (Troca entre Vizinhos)")
        print(" [0] Sair")

        escolha = input("\nEscolha uma opção: ").strip()

        match escolha:
            case "1":
                pacote = capturar_pacote_mqtt(timeout_segundos=6)
                if pacote:
                    print("[MQTT] Pacote recebido com sucesso!")
                    relatorio = diagnosticar_estufa(pacote)
                    exibir_relatorio_diagnostico(relatorio)

                    dados["estufa_atual"]["ultima_telemetria"] = relatorio
                    dados["historico_leituras"].append(relatorio)
                    salvar_banco_dados(dados)

                    if sincronizar_api_react(relatorio):
                        print("[CLOUD] Telemetria sincronizada com a API do Dashboard.")
                else:
                    print("[AVISO] Nenhum pacote recebido. Verifique se o Wokwi está rodando.")

            case "2":
                pacote_manual = simular_leitura_manual(estufa["cultura"], estufa["dia_ciclo"])
                relatorio = diagnosticar_estufa(pacote_manual)
                exibir_relatorio_diagnostico(relatorio)

                dados["estufa_atual"]["ultima_telemetria"] = relatorio
                dados["historico_leituras"].append(relatorio)
                salvar_banco_dados(dados)

            case "3":
                print("\nCulturas disponíveis:", ", ".join(PERFIS_CULTURAS.keys()))
                nova_cultura = input("Digite o nome exato da cultura: ").strip()
                if nova_cultura in PERFIS_CULTURAS:
                    try:
                        novo_dia = int(input("Dia atual do ciclo (1 a 60): "))
                        if not (1 <= novo_dia <= 60):
                            raise ValueError("Dia fora do intervalo permitido.")
                        dados["estufa_atual"]["cultura"] = nova_cultura
                        dados["estufa_atual"]["dia_ciclo"] = novo_dia
                        salvar_banco_dados(dados)
                        print(f"[OK] Perfil atualizado para {nova_cultura} (Dia {novo_dia}).")
                    except ValueError as e:
                        print(f"[Erro] {e}")
                else:
                    print("[Erro] Cultura não encontrada no catálogo.")

            case "4":
                historico = dados["historico_leituras"]
                print(f"\n--- Histórico Persistido ({len(historico)} registros) ---")
                for item in historico[-5:]:
                    s = item["sensores"]
                    a = item["agronomia"]
                    print(
                        f" [{item['timestamp']}] {item['cultura']} | "
                        f"Status: {item['status_borda']} | Temp: {s['temp_c']}°C | "
                        f"Água: {s['agua_pct']}% | Biomassa: {a['biomassa_estimada_g']}g"
                    )

            case "5":
                gerenciar_mural_comunitario(dados)

            case "0":
                print("Encerrando MyFarm Python Core. Dados salvos com segurança.")
                break

            case _:
                print("[Aviso] Opção inválida. Digite um número de 0 a 5.")

if __name__ == "__main__":
    menu_principal()