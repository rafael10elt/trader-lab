"""Estado compartilhado do Sentinel Vision.

Todos os módulos (captura, checagem de velas, servidor do dashboard e GUI)
leem/gravam aqui sob um único lock. O servidor HTTP expõe `snapshot()` em JSON.
"""
import json
import os
import threading
import time
from collections import deque

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMINHO_CONFIG = os.path.join(BASE_DIR, "config.json")
CAMINHO_ROI_STATUS = os.path.join(BASE_DIR, "roi_status.json")
CAMINHO_ROI_CHART = os.path.join(BASE_DIR, "roi_chart.json")
CAMINHO_SINAIS = os.path.join(BASE_DIR, "data", "signals.jsonl")
DASH_DIR = os.path.join(BASE_DIR, "dashboard")

# ---------------------------------------------------------------- config
CONFIG_PADRAO = {
    "timeframe_seg": 60,
    "delay_apos_fechamento_seg": 0.8,
    "estavel_ha_seg": 2.0,
    "cooldown_seg": 120,
    "retry_apos_fechamento_seg": 6,
    "confirmacao_grafico_vivo": True,
    "janela_operacional": {
        "ativar": False,
        "fuso": "Europe/Dublin",
        "janelas": [["08:15", "11:00"], ["18:15", "20:45"]],
    },
    "filtro_noticias": {
        "ativar": True,
        "minutos_antes": 15,
        "minutos_depois": 15,
        "paises": ["USD", "EUR"],
        "impacto": "High",
    },
    "porta_dashboard": 8777,
    "som_local": True,
}


def carregar_config():
    cfg = json.loads(json.dumps(CONFIG_PADRAO))  # cópia profunda
    try:
        with open(CAMINHO_CONFIG, "r", encoding="utf-8") as f:
            usuario = json.load(f)
        for chave, valor in usuario.items():
            if isinstance(valor, dict) and isinstance(cfg.get(chave), dict):
                cfg[chave].update(valor)
            else:
                cfg[chave] = valor
    except FileNotFoundError:
        salvar_config(cfg)
    except Exception as exc:  # config corrompido: cai no padrão
        STATE["erro"] = f"config.json inválido, usando padrão ({exc})"
    return cfg


def salvar_config(cfg):
    with open(CAMINHO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def carregar_roi(caminho):
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------- estado
_LOCK = threading.Lock()

STATE = {
    "rodando": False,
    "online": False,               # captura viva (heartbeat recente)
    "estado": "AGUARDANDO FVG",     # BUY / SELL / AGUARDANDO FVG
    "estado_desde": 0.0,            # epoch em que o estado atual começou
    "ativo": "MNQ",
    "timeframe_seg": 60,
    "proxima_vela_ts": 0.0,
    "ultima_vela_ts": 0.0,
    "ultima_checagem": "aguardando início",
    "flash_ate": 0.0,               # banner de alerta visível até este epoch
    "sinal_seq": 0,
    "roi_status": None,
    "roi_chart": None,
    "preview_status_b64": "",
    "preview_chart_b64": "",
    "preview_ts": 0.0,
    "erro": "",
    "porta_dashboard": 8777,
}

SINAIS = deque(maxlen=200)


def lock():
    return _LOCK


def carregar_rois_na_memoria():
    STATE["roi_status"] = carregar_roi(CAMINHO_ROI_STATUS)
    STATE["roi_chart"] = carregar_roi(CAMINHO_ROI_CHART)
    STATE["timeframe_seg"] = int(STATE["timeframe_seg"])


def salvar_roi_status(coords):
    with open(CAMINHO_ROI_STATUS, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=4)
    STATE["roi_status"] = coords


def salvar_roi_chart(coords):
    with open(CAMINHO_ROI_CHART, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=4)
    STATE["roi_chart"] = coords


def _carregar_sinais_historicos():
    try:
        with open(CAMINHO_SINAIS, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if linha:
                    SINAIS.appendleft(json.loads(linha))
        if SINAIS:
            STATE["sinal_seq"] = max(s.get("id", 0) for s in SINAIS)
    except FileNotFoundError:
        pass
    except Exception as exc:
        STATE["erro"] = f"falha ao ler histórico de sinais: {exc}"


def adicionar_sinal(sinal):
    """Grava o sinal em memória + JSONL e dispara o flash visual."""
    with _LOCK:
        STATE["sinal_seq"] += 1
        sinal["id"] = STATE["sinal_seq"]
        STATE["flash_ate"] = time.time() + 8.0
        SINAIS.appendleft(sinal)
    try:
        os.makedirs(os.path.dirname(CAMINHO_SINAIS), exist_ok=True)
        with open(CAMINHO_SINAIS, "a", encoding="utf-8") as f:
            f.write(json.dumps(sinal, ensure_ascii=False) + "\n")
    except Exception as exc:
        STATE["erro"] = f"falha ao gravar sinal: {exc}"
    return sinal


def set_estado(nome):
    """Atualza o estado da célula, registrando há quanto tempo ele vigora."""
    with _LOCK:
        if STATE["estado"] != nome:
            STATE["estado"] = nome
            STATE["estado_desde"] = time.time()


def estado_atual():
    with _LOCK:
        return STATE["estado"], time.time() - STATE["estado_desde"]


def snapshot():
    """Cópia consistente do estado para o dashboard (JSON-serializável)."""
    with _LOCK:
        copia = dict(STATE)
        copia["agora"] = time.time()
        copia["sinais"] = list(SINAIS)[:40]
        copia["flash"] = STATE["flash_ate"] > copia["agora"]
        copia.pop("flash_ate", None)
        copia["roi_status"] = dict(STATE["roi_status"]) if STATE["roi_status"] else None
        copia["roi_chart"] = dict(STATE["roi_chart"]) if STATE["roi_chart"] else None
        return copia


# inicialização (ordem importa: SINAIS preenchido antes do histórico)
carregar_rois_na_memoria()
_carregar_sinais_historicos()
CONFIG = carregar_config()
STATE["timeframe_seg"] = int(CONFIG.get("timeframe_seg", 60))
STATE["porta_dashboard"] = int(CONFIG.get("porta_dashboard", 8777))
