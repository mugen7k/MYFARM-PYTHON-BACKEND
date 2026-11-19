# 🌱 MyFarm — Sistema Inteligente de Cultivo Hidropônico (Camada Python & IoT)

> **A horta que conecta a vizinhança.**  
> **Global Solution — FIAP | Engenharia de Software (1ESR)**  
> **Disciplina:** Computational Thinking with Python  
> **Autor:** Felipe Anderson Silva Peres (RM 573654)

---

## 📋 Sobre o Projeto

O **MyFarm** é uma estufa urbana inteligente e compacta voltada para apartamentos e espaços comunitários. A solução integra hardware IoT (**ESP32**), mensageria **MQTT**, infraestrutura em nuvem (**FIWARE**) e processamento analítico em **Python** para automatizar o microclima de cultivo hidropônico *indoor* (iluminação *Grow LED*, microventilação PWM e irrigação).

Além de democratizar o acesso a hortaliças frescas e livres de agrotóxicos com alta eficiência hídrica (reduzindo o consumo diário de uma alface de ~500 ml no cultivo convencional para ~50 ml no sistema fechado), o ecossistema incorpora um **Mural Comunitário** que conecta vizinhos em um raio de até **2 km** para troca e doação solidária de colheitas e sementes — combatendo o isolamento social urbano, especialmente entre idosos.

O projeto está alinhado aos **Objetivos de Desenvolvimento Sustentável (ODS) da ONU**:
* **ODS 2:** Fome Zero e Agricultura Sustentável
* **ODS 11:** Cidades e Comunidades Sustentáveis

---

## 🏗️ Arquitetura da Solução

```text
┌───────────────────────────┐         MQTT (/myfarm/device01/attrs)         ┌──────────────────────────────┐
│      ESP32 (Hardware)     │ ────────────────────────────────────────────► │      Broker MQTT / FIWARE    │
│                           │                                               │   (Orion + IoT Agent MQTT)   │
│ • Temperatura (t)         │ ◄──────────────────────────────────────────── │                              │
│ • Umidade (h)             │         MQTT (/myfarm/device01/cmd)           └──────────────┬───────────────┘
│ • Nível de Água (w)       │                                                              │
│ • Luminosidade (l)        │                                        MQTT (paho-mqtt) + HTTP (requests)
│ • Atuadores (LED/Bomba)   │                                                              │
└───────────────────────────┘                                       ┌──────────────────────┴──────────────────────┐
                                                                    ▼                                             ▼
                                                    ┌───────────────────────────────┐             ┌───────────────────────────────┐
                                                    │       myfarm_engine.py        │             │      dashboard_esp32.py       │
                                                    │    (Monitor CLI & Motor)      │             │   (Dashboard Gráfico Tkinter) │
                                                    │                               │             │                               │
                                                    │ • Menu interativo no terminal │             │ • Telemetria em tempo real    │
                                                    │ • Curva logística de biomassa │             │ • Acionamento de atuadores    │
                                                    │ • Cálculo de autonomia hídrica│             │ • Acessibilidade W3C          │
                                                    │ • Mural comunitário (2 km)    │             │ • Sincronização em nuvem      │
                                                    │ • Persistência em JSON        │             └───────────────────────────────┘
                                                    └───────────────────────────────┘