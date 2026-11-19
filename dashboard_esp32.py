import json
import math
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk
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

BIOMASSA_MAX_K = 250.0
CONSTANTE_A = 49.0
TAXA_CRESCIMENTO_R = 0.2116


# ==============================================================================
# PERSISTÊNCIA EM JSON E INTEGRAÇÃO CLOUD
# ==============================================================================
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


def sincronizar_api_react(relatorio: Dict[str, Any]) -> bool:
    if not API_CLOUD_URL:
        return False

    cultura = relatorio["cultura"]
    _, _, _, ciclo_total, tanque_max_l = PERFIS_CULTURAS.get(cultura, PERFIS_CULTURAS["Alface"])
    sensores = relatorio["sensores"]

    payload_react = {
        "cultura": cultura,
        "diaCiclo": relatorio["dia_ciclo"],
        "cicloTotal": ciclo_total,
        "capacidadeTanqueL": tanque_max_l,
        "temperatura": sensores["temp_c"],
        "umidade": sensores["umid_pct"],
        "nivelAguaPct": sensores["agua_pct"],
        "luminosidadePct": sensores["luz_pct"],
        "statusBorda": relatorio["status_borda"],
    }

    try:
        resposta = requests.put(f"{API_CLOUD_URL}/estufa/1", json=payload_react, timeout=5)
        if resposta.status_code in (200, 201):
            return True
        resposta_post = requests.post(f"{API_CLOUD_URL}/estufa", json=payload_react, timeout=5)
        return resposta_post.status_code in (200, 201)
    except requests.RequestException:
        return False


# ==============================================================================
# MOTOR MATEMÁTICO E DIAGNÓSTICO (BASE ORIGINAL)
# ==============================================================================
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


# ==============================================================================
# INTERFACE VISUAL DE TELEMETRIA ESP32 (TKINTER)
# ==============================================================================
class DashboardESP32:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MyFarm — Monitor de Telemetria ESP32")
        self.root.geometry("920x660")
        self.root.minsize(840, 600)
        self.root.configure(bg="#eef5f0")

        self.dados = carregar_banco_dados()
        self.mqtt_client: Optional[mqtt.Client] = None

        self.cultura_var = tk.StringVar(value=self.dados["estufa_atual"].get("cultura", "Alface"))
        self.dia_var = tk.IntVar(value=int(self.dados["estufa_atual"].get("dia_ciclo", 18)))
        self.sinc_nuvem_var = tk.BooleanVar(value=True)

        self._configurar_estilos()
        self._construir_layout()
        self._carregar_estado_inicial()
        self._iniciar_mqtt_background()

        self.root.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    def _configurar_estilos(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TProgressbar", thickness=10, troughcolor="#e2e8f0", background="#15803d", bordercolor="#e2e8f0")
        style.configure("Agua.Horizontal.TProgressbar", thickness=10, troughcolor="#e2e8f0", background="#0284c7")
        style.configure("Luz.Horizontal.TProgressbar", thickness=10, troughcolor="#e2e8f0", background="#d97706")
        style.configure("Temp.Horizontal.TProgressbar", thickness=10, troughcolor="#e2e8f0", background="#16a34a")

        style.configure(
            "Treeview",
            background="#ffffff",
            foreground="#0f172a",
            fieldbackground="#ffffff",
            rowheight=26,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#dcfce7",
            foreground="#14532d",
            font=("Segoe UI", 9, "bold"),
        )

    def _construir_layout(self) -> None:
        # --- TOPO ---
        header = tk.Frame(self.root, bg="#14532d", padx=20, pady=12)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🌱 MyFarm | Monitor de Telemetria ESP32",
            font=("Segoe UI", 14, "bold"),
            fg="#ffffff",
            bg="#14532d",
        ).pack(side="left")

        self.lbl_mqtt_status = tk.Label(
            header,
            text="● Conectando ao Broker MQTT...",
            font=("Segoe UI", 9, "bold"),
            fg="#fef08a",
            bg="#166534",
            padx=10,
            pady=4,
        )
        self.lbl_mqtt_status.pack(side="right")

        # --- BARRA DE CONTROLE DA CULTURA ---
        barra = tk.Frame(self.root, bg="#ffffff", padx=16, pady=10, highlightbackground="#cbd5e1", highlightthickness=1)
        barra.pack(fill="x", padx=16, pady=(12, 8))

        tk.Label(barra, text="Cultura:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left")
        combo = ttk.Combobox(
            barra,
            textvariable=self.cultura_var,
            values=list(PERFIS_CULTURAS.keys()),
            state="readonly",
            width=15,
        )
        combo.pack(side="left", padx=(6, 16))
        combo.bind("<<ComboboxSelected>>", lambda _: self._recalcular_com_parametros_locais())

        tk.Label(barra, text="Dia do Ciclo:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left")
        spin = tk.Spinbox(
            barra,
            from_=1,
            to=60,
            textvariable=self.dia_var,
            width=5,
            command=self._recalcular_com_parametros_locais,
        )
        spin.pack(side="left", padx=(6, 16))

        tk.Checkbutton(
            barra,
            text="Sincronizar MockAPI",
            variable=self.sinc_nuvem_var,
            bg="#ffffff",
            fg="#334155",
            font=("Segoe UI", 9),
        ).pack(side="left")

        self.lbl_status_borda = tk.Label(
            barra,
            text="STATUS: AGUARDANDO",
            font=("Segoe UI", 9, "bold"),
            bg="#e2e8f0",
            fg="#334155",
            padx=10,
            pady=3,
        )
        self.lbl_status_borda.pack(side="right")

        self.lbl_timestamp = tk.Label(
            barra,
            text="Última leitura: --",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
            padx=12,
        )
        self.lbl_timestamp.pack(side="right")

        # --- GRID DE 4 SENSORES ESP32 (2x2) ---
        grid_sensores = tk.Frame(self.root, bg="#eef5f0")
        grid_sensores.pack(fill="x", padx=16, pady=4)
        grid_sensores.columnconfigure(0, weight=1)
        grid_sensores.columnconfigure(1, weight=1)

        self.card_temp = self._criar_card_sensor(grid_sensores, 0, 0, "🌡️ Temperatura Interna", "-- °C", "Temp.Horizontal.TProgressbar")
        self.card_umid = self._criar_card_sensor(grid_sensores, 0, 1, "💧 Umidade Relativa", "-- %", "Agua.Horizontal.TProgressbar")
        self.card_agua = self._criar_card_sensor(grid_sensores, 1, 0, "🧪 Nível do Reservatório", "-- %", "Agua.Horizontal.TProgressbar")
        self.card_luz = self._criar_card_sensor(grid_sensores, 1, 1, "☀️ Luminosidade LED", "-- %", "Luz.Horizontal.TProgressbar")

        # --- PAINEL AGRONÔMICO E AUTONOMIA HÍDRICA ---
        painel_math = tk.Frame(self.root, bg="#eef5f0")
        painel_math.pack(fill="x", padx=16, pady=6)
        painel_math.columnconfigure(0, weight=1)
        painel_math.columnconfigure(1, weight=1)

        # Card Biomassa B(t)
        box_bio = tk.Frame(painel_math, bg="#ffffff", padx=14, pady=10, highlightbackground="#cbd5e1", highlightthickness=1)
        box_bio.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        tk.Label(box_bio, text="📈 Crescimento Logístico B(t)", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#14532d").pack(anchor="w")
        self.lbl_biomassa = tk.Label(box_bio, text="Biomassa: -- g | Taxa B'(t): -- g/dia", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a")
        self.lbl_biomassa.pack(anchor="w", pady=(4, 2))
        self.lbl_colheita = tk.Label(box_bio, text="Progresso do alvo: --% | Colheita em -- dias", font=("Segoe UI", 9), bg="#ffffff", fg="#475569")
        self.lbl_colheita.pack(anchor="w")

        # Card Autonomia Hídrica
        box_hid = tk.Frame(painel_math, bg="#ffffff", padx=14, pady=10, highlightbackground="#cbd5e1", highlightthickness=1)
        box_hid.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        tk.Label(box_hid, text="💦 Autonomia Hídrica (Reserva 15%)", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0369a1").pack(anchor="w")
        self.lbl_tanque = tk.Label(box_hid, text="Volume: -- L / -- L | Consumo: -- mL/dia", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a")
        self.lbl_tanque.pack(anchor="w", pady=(4, 2))
        self.lbl_autonomia = tk.Label(box_hid, text="Autonomia útil restante: -- dias", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#15803d")
        self.lbl_autonomia.pack(anchor="w")

        # --- DIAGNÓSTICO ---
        box_diag = tk.Frame(self.root, bg="#ffffff", padx=14, pady=8, highlightbackground="#cbd5e1", highlightthickness=1)
        box_diag.pack(fill="x", padx=16, pady=4)

        tk.Label(box_diag, text="🩺 Diagnóstico Agronômico:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(anchor="w")
        self.lbl_recomendacao = tk.Label(
            box_diag,
            text="Aguardando telemetria do ESP32 no tópico /myfarm/device01/attrs...",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#1e293b",
            justify="left",
        )
        self.lbl_recomendacao.pack(anchor="w", pady=(2, 0))

        # --- TABELA DE HISTÓRICO DE PACOTES RECEBIDOS ---
        box_hist = tk.Frame(self.root, bg="#ffffff", padx=12, pady=10, highlightbackground="#cbd5e1", highlightthickness=1)
        box_hist.pack(fill="both", expand=True, padx=16, pady=(4, 14))

        tk.Label(
            box_hist,
            text="Histórico de Telemetrias Recebidas (dados_myfarm.json)",
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#334155",
        ).pack(anchor="w", pady=(0, 6))

        colunas = ("timestamp", "cultura", "dia", "temp", "umid", "agua", "luz", "biomassa", "status")
        self.tree = ttk.Treeview(box_hist, columns=colunas, show="headings", height=6)

        cabecalhos = {
            "timestamp": ("Data / Hora", 135),
            "cultura": ("Cultura", 95),
            "dia": ("Dia", 45),
            "temp": ("Temp", 65),
            "umid": ("Umid", 65),
            "agua": ("Água", 65),
            "luz": ("Luz", 65),
            "biomassa": ("Biomassa", 80),
            "status": ("Status Borda", 95),
        }
        for col, (texto, largura) in cabecalhos.items():
            self.tree.heading(col, text=texto)
            self.tree.column(col, width=largura, anchor="center")

        self.tree.pack(fill="both", expand=True)

    def _criar_card_sensor(
        self, parent: tk.Frame, row: int, col: int, titulo: str, valor_inicial: str, estilo_barra: str
    ) -> Dict[str, Any]:
        pad_x = (0, 6) if col == 0 else (6, 0)
        frame = tk.Frame(parent, bg="#ffffff", padx=14, pady=10, highlightbackground="#cbd5e1", highlightthickness=1)
        frame.grid(row=row, column=col, sticky="nsew", padx=pad_x, pady=4)

        topo = tk.Frame(frame, bg="#ffffff")
        topo.pack(fill="x")

        tk.Label(topo, text=titulo, font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#475569").pack(side="left")
        lbl_detalhe = tk.Label(topo, text="", font=("Segoe UI", 8), bg="#ffffff", fg="#64748b")
        lbl_detalhe.pack(side="right")

        lbl_valor = tk.Label(frame, text=valor_inicial, font=("Segoe UI", 18, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_valor.pack(anchor="w", pady=(2, 6))

        barra = ttk.Progressbar(frame, orient="horizontal", mode="determinate", maximum=100, style=estilo_barra)
        barra.pack(fill="x")

        return {"valor": lbl_valor, "detalhe": lbl_detalhe, "barra": barra}

    def _carregar_estado_inicial(self) -> None:
        ultima = self.dados.get("estufa_atual", {}).get("ultima_telemetria")
        if ultima:
            self._atualizar_widgets(ultima)

        for item in self.dados.get("historico_leituras", [])[-15:]:
            self._inserir_linha_historico(item)

    def _inserir_linha_historico(self, relatorio: Dict[str, Any]) -> None:
        s = relatorio["sensores"]
        a = relatorio["agronomia"]
        self.tree.insert(
            "",
            0,
            values=(
                relatorio["timestamp"],
                relatorio["cultura"],
                f"{relatorio['dia_ciclo']}d",
                f"{s['temp_c']:.1f}°C",
                f"{s['umid_pct']:.0f}%",
                f"{s['agua_pct']}%",
                f"{s['luz_pct']}%",
                f"{a['biomassa_estimada_g']}g",
                relatorio["status_borda"],
            ),
        )

    def _atualizar_widgets(self, relatorio: Dict[str, Any]) -> None:
        s = relatorio["sensores"]
        a = relatorio["agronomia"]
        h = relatorio["hidrica"]
        cultura = relatorio["cultura"]
        t_min, t_max, a_min, _, _ = PERFIS_CULTURAS.get(cultura, PERFIS_CULTURAS["Alface"])

        self.lbl_timestamp.config(text=f"Última leitura: {relatorio['timestamp']}")

        status = relatorio["status_borda"]
        cores_status = {
            "IDEAL": ("#dcfce7", "#166534"),
            "ATENCAO": ("#fef3c7", "#92400e"),
            "CRITICO": ("#fee2e2", "#991b1b"),
        }
        bg_st, fg_st = cores_status.get(status, ("#e2e8f0", "#334155"))
        self.lbl_status_borda.config(text=f"STATUS: {status}", bg=bg_st, fg=fg_st)

        # Sensores
        self.card_temp["valor"].config(text=f"{s['temp_c']:.1f} °C")
        self.card_temp["detalhe"].config(text=f"Faixa ideal: {t_min}–{t_max}°C")
        self.card_temp["barra"]["value"] = min(100, max(0, (s["temp_c"] / 40.0) * 100))

        self.card_umid["valor"].config(text=f"{s['umid_pct']:.0f} %")
        self.card_umid["detalhe"].config(text="Umidade do ar")
        self.card_umid["barra"]["value"] = min(100, max(0, s["umid_pct"]))

        self.card_agua["valor"].config(text=f"{s['agua_pct']} %")
        self.card_agua["detalhe"].config(text=f"Alerta ≤ {a_min}%")
        self.card_agua["barra"]["value"] = min(100, max(0, s["agua_pct"]))

        self.card_luz["valor"].config(text=f"{s['luz_pct']} %")
        self.card_luz["detalhe"].config(text="LED Grow" if s["luz_pct"] < 40 else "Iluminação OK")
        self.card_luz["barra"]["value"] = min(100, max(0, s["luz_pct"]))

        # Agronomia & Hídrica
        self.lbl_biomassa.config(
            text=f"Biomassa: {a['biomassa_estimada_g']} g  |  Taxa B'(t): +{a['taxa_crescimento_g_dia']} g/dia"
        )
        self.lbl_colheita.config(
            text=f"Progresso do alvo: {a['progresso_colheita_pct']}%  |  Colheita em {relatorio['dias_restantes_colheita']} dias"
        )

        self.lbl_tanque.config(
            text=f"Volume: {h['litros_atuais']} L / {h['tanque_max_l']} L  |  Consumo: {h['consumo_diario_ml']} mL/dia"
        )
        self.lbl_autonomia.config(text=f"Autonomia útil restante: {h['dias_autonomia']} dias")

        self.lbl_recomendacao.config(text="\n".join(f"• {rec}" for rec in relatorio["recomendacoes"]))

    def _recalcular_com_parametros_locais(self) -> None:
        self.dados["estufa_atual"]["cultura"] = self.cultura_var.get()
        self.dados["estufa_atual"]["dia_ciclo"] = int(self.dia_var.get())
        salvar_banco_dados(self.dados)

        ultima = self.dados["estufa_atual"].get("ultima_telemetria")
        if ultima:
            s = ultima["sensores"]
            pacote = {
                "cultura": self.cultura_var.get(),
                "dia": int(self.dia_var.get()),
                "t": s["temp_c"],
                "h": s["umid_pct"],
                "w": s["agua_pct"],
                "l": s["luz_pct"],
                "s": ultima["status_borda"],
            }
            relatorio = diagnosticar_estufa(pacote)
            self.dados["estufa_atual"]["ultima_telemetria"] = relatorio
            salvar_banco_dados(self.dados)
            self._atualizar_widgets(relatorio)

    def _processar_pacote_esp32(self, pacote: Dict[str, Any]) -> None:
        # Se o ESP32 enviar apenas os sensores (t, h, w, l, s), usa a cultura e o dia ativos no painel
        pacote.setdefault("cultura", self.cultura_var.get())
        pacote.setdefault("dia", int(self.dia_var.get()))

        relatorio = diagnosticar_estufa(pacote)

        self.dados["estufa_atual"]["cultura"] = relatorio["cultura"]
        self.dados["estufa_atual"]["dia_ciclo"] = relatorio["dia_ciclo"]
        self.dados["estufa_atual"]["ultima_telemetria"] = relatorio
        self.dados["historico_leituras"].append(relatorio)
        salvar_banco_dados(self.dados)

        self._atualizar_widgets(relatorio)
        self._inserir_linha_historico(relatorio)

        if self.sinc_nuvem_var.get():
            threading.Thread(target=sincronizar_api_react, args=(relatorio,), daemon=True).start()

    def _iniciar_mqtt_background(self) -> None:
        def on_connect(client, userdata, flags, reason_code, properties=None):
            client.subscribe(MQTT_TOPIC)
            self.root.after(
                0,
                lambda: self.lbl_mqtt_status.config(
                    text=f"● MQTT Online ({MQTT_TOPIC})", fg="#bbf7d0", bg="#166534"
                ),
            )

        def on_disconnect(client, userdata, flags, reason_code, properties=None):
            self.root.after(
                0,
                lambda: self.lbl_mqtt_status.config(
                    text="● Reconectando MQTT...", fg="#fecaca", bg="#991b1b"
                ),
            )

        def on_message(client, userdata, msg):
            try:
                conteudo = json.loads(msg.payload.decode("utf-8"))
                if isinstance(conteudo, dict):
                    self.root.after(0, self._processar_pacote_esp32, conteudo)
            except json.JSONDecodeError:
                pass

        try:
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.mqtt_client = mqtt.Client()

        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_disconnect = on_disconnect
        self.mqtt_client.on_message = on_message

        try:
            self.mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
        except Exception as erro:
            self.lbl_mqtt_status.config(text=f"● Erro MQTT: {erro}", fg="#fecaca", bg="#991b1b")

    def _ao_fechar(self) -> None:
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    janela = tk.Tk()
    app = DashboardESP32(janela)
    janela.mainloop()