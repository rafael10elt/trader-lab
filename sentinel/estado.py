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
CAMINHO_ROI_CHART = os.path.join(BASE_DIR, "roi_chart.json")
CAMINHO_SINAIS = os.path.join(BASE_DIR, "data", "signals.jsonl")
DASH_DIR = os.path.join(BASE_DIR, "dashboard")

# ---------------------------------------------------------------- config
CONFIG_PADRAO = {
    "timeframe_seg": 60,
    "delay_apos_fechamento_seg": 0.8,
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
    # filtro matemático (machine learning) no fechamento da vela
    "ml": {
        "ativar": True,
        "limiar": 0.60,          # P(continuação) mínima para o sinal seguir
    },
    # cores das velas no gráfico (a alta foi trocada de verde para azul)
    "visao": {
        "cores_vela": {
            "alta":  {"faixas": [[50, 121], [140, 185]], "min_s": 110, "min_v": 70},
            "baixa": {"faixas": [[0, 17], [238, 255]],   "min_s": 110, "min_v": 70},
        },
    },
    # portões do setup do operador: EMAs 9/20/200 + VWAP lidos no gráfico
    "indicadores": {
        "ativar": True,            # false = ignora o setup e segue só com o ML
        "exigir": ["ema9", "ema20"],  # linhas que precisam estar visíveis
        "exigir_vwap": True,       # preço precisa estar do lado certo do VWAP
        "bloquear_mista": True,    # stack 9/20/200 fora de ordem = sem trade
        "cores": {},               # override das cores (ver indicadores.py)
    },
    # juiz confirmador por IA (Groq). Chave: GROQ_API_KEY ou groq_api_key
    "ia_juiz": {
        "ativar": True,          # se a chave existir, o juiz participa
        "exigir_confirmacao": True,  # sem CONFIRMAR da IA => sem sinal
    },
    "groq_api_key": "",
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
    "ativo": "MNQ",
    "timeframe_seg": 60,
    "proxima_vela_ts": 0.0,
    "ultima_vela_ts": 0.0,
    "ultima_checagem": "aguardando início",
    "flash_ate": 0.0,               # banner de alerta visível até este epoch
    "sinal_seq": 0,
    "roi_chart": None,
    "preview_chart_b64": "",
    "preview_ts": 0.0,
    # pipeline de decisão (portões do setup + ML + IA juiz)
    "velas_reconhecidas": 0,        # nº de velas detectadas na última captura
    "zoom": None,                   # {"nivel": str, "msg": str} do diagnóstico
    "setup": None,                  # EMAs/VWAP lidos no gráfico (indicadores)
    "score_ml": None,               # P(continuação) da última avaliação
    "veredito_ia": None,            # último veredito do juiz (dict) ou None
    "erro": "",
    "porta_dashboard": 8777,
}

SINAIS = deque(maxlen=200)


def lock():
    return _LOCK


def carregar_rois_na_memoria():
    STATE["roi_chart"] = carregar_roi(CAMINHO_ROI_CHART)
    STATE["timeframe_seg"] = int(STATE["timeframe_seg"])


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


def set_estado_velas(n):
    """Atualiza quantas velas a visão reconheceu no gráfico."""
    with _LOCK:
        STATE["velas_reconhecidas"] = int(n)


def apagar_sinais(ids=None, todos=False):
    """Remove sinais do histórico (memória + data/signals.jsonl).

    ids: lista de ids a remover; todos=True remove tudo.
    Retorna o nº de sinais removidos.
    """
    with _LOCK:
        if todos:
            removidos = len(SINAIS)
            SINAIS.clear()
        else:
            conjunto = set(ids or [])
            antes = len(SINAIS)
            restantes = [s for s in SINAIS if s.get("id") not in conjunto]
            removidos = antes - len(restantes)
            SINAIS.clear()
            SINAIS.extend(restantes)
        # regrava o arquivo em ordem cronológica (mais antigo primeiro)
        try:
            os.makedirs(os.path.dirname(CAMINHO_SINAIS), exist_ok=True)
            with open(CAMINHO_SINAIS, "w", encoding="utf-8") as f:
                for s in reversed(list(SINAIS)):
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        except Exception as exc:
            STATE["erro"] = f"falha ao regravar sinais: {exc}"
    return removidos


def snapshot():
    """Cópia consistente do estado para o dashboard (JSON-serializável)."""
    with _LOCK:
        copia = dict(STATE)
        copia["agora"] = time.time()
        copia["sinais"] = list(SINAIS)[:40]
        copia["flash"] = STATE["flash_ate"] > copia["agora"]
        copia.pop("flash_ate", None)
        copia["roi_chart"] = dict(STATE["roi_chart"]) if STATE["roi_chart"] else None
        # campos auxiliares p/ o dashboard (IA + limiar ML)
        try:
            import juiz as _juiz
            copia["ia_ativa"] = _juiz.juiz_ativo()
        except Exception:
            copia["ia_ativa"] = False
        copia["limiar_ml"] = float(CONFIG.get("ml", {}).get("limiar", 0.60))
        return copia


# inicialização (ordem importa: SINAIS preenchido antes do histórico)
carregar_rois_na_memoria()
_carregar_sinais_historicos()
CONFIG = carregar_config()
STATE["timeframe_seg"] = int(CONFIG.get("timeframe_seg", 60))
STATE["porta_dashboard"] = int(CONFIG.get("porta_dashboard", 8777))

# cores das velas (azul na alta / vermelho na baixa) para a visão computacional
try:
    import velas as _velas
    _velas.configurar_cores(CONFIG.get("visao", {}).get("cores_vela"))
except Exception:  # PIL indisponível: quem precisar falha depois, com mensagem
    pass
