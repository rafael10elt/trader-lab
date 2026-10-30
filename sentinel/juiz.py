"""Juiz confirmador por IA — valida o setup antes do sinal.

Suporta chaves da Groq (gsk_...) e OpenRouter (sk-or-...).
Modelos:
  - qwen/qwen3.8-27b       (Multimodal: visão do gráfico + métricas)
  - openai/gpt-oss-20b     (Texto rápido e econômico)
  - openai/gpt-oss-120b    (Texto de alta capacidade)
"""
import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request

import estado
from estado import CONFIG

URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"
URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
CAMINHO_CHAVE = os.path.join(estado.BASE_DIR, "data", "groq_key.txt")

MODELOS_VISAO = [
    "qwen/qwen3.8-27b",
]

MODELOS_TEXTO = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

TIMEOUT_SEG = 35

HEADERS_PADRAO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}

SYSTEM_PROMPT = (
    "Você é o juiz de confirmação de um sistema de sinais por visão "
    "computacional que observa gráficos de futuros (MNQ/NQ) no TradingView. "
    "Você recebe os dados e a imagem do setup candidato. "
    "Seu papel é REJEITAR setups ruins que passaram pelo filtro numérico: "
    "cruzes de médias sem direção, lateralização, dojis, "
    "suportes/resistências imediatos ou qualquer contraindicação clara.\n"
    "Responda APENAS com JSON válido, sem markdown, no formato:\n"
    '{"veredito":"CONFIRMAR"|"REJEITAR","confianca":0-100,"motivo":"<max 12 palavras>"}'
)

_chave_arquivo = None


def _sanitizar_chave(chave):
    """Remove espaços, quebras de linha, aspas e prefixos como 'Bearer '."""
    if not chave:
        return ""
    c = str(chave).strip().strip('"\'')
    if c.lower().startswith("bearer "):
        c = c[7:].strip()
    return c


def _url_endpoint(chave):
    """Detecta se a chave pertence ao OpenRouter ou à Groq."""
    if chave.startswith("sk-or-"):
        return URL_OPENROUTER
    return URL_GROQ


def _ler_chave_arquivo():
    global _chave_arquivo
    if _chave_arquivo is None:
        try:
            with open(CAMINHO_CHAVE, "r", encoding="utf-8") as f:
                _chave_arquivo = _sanitizar_chave(f.read())
        except Exception:
            _chave_arquivo = ""
    return _chave_arquivo


def chave_api():
    """Resolve a chave ativa: env > config > arquivo local."""
    chave = _sanitizar_chave(os.environ.get("GROQ_API_KEY") or "")
    if chave:
        return chave
    cfg = _sanitizar_chave(CONFIG.get("groq_api_key") or "")
    if cfg:
        return cfg
    return _ler_chave_arquivo() or None


def salvar_chave(chave):
    """Salva a chave no arquivo local (gitignorado) e atualiza o cache."""
    global _chave_arquivo
    chave = _sanitizar_chave(chave)
    os.makedirs(os.path.dirname(CAMINHO_CHAVE), exist_ok=True)
    with open(CAMINHO_CHAVE, "w", encoding="utf-8") as f:
        f.write(chave)
    _chave_arquivo = chave
    if CONFIG.get("groq_api_key"):
        CONFIG["groq_api_key"] = ""
        try:
            estado.salvar_config(CONFIG)
        except Exception:
            pass
    return chave


def testar_chave(chave=None):
    """Valida a chave testando os modelos disponíveis."""
    nova = _sanitizar_chave(chave or "")
    chave_efetiva = nova or chave_api()
    if not chave_efetiva:
        return {
            "ok": False,
            "status": "nenhuma chave configurada",
            "modelo": "",
            "erro": "cole a chave no campo acima",
            "nota": "",
        }

    url_api = _url_endpoint(chave_efetiva)
    provedor = "OpenRouter" if url_api == URL_OPENROUTER else "Groq"

    def _falha(status, modelo="", erro=""):
        return {
            "ok": False,
            "status": status,
            "modelo": modelo,
            "erro": erro,
            "nota": "",
        }

    modelos_para_testar = MODELOS_VISAO + MODELOS_TEXTO
    modelo_aprovado = None
    ultimo_erro = ""

    for mod in modelos_para_testar:
        try:
            payload = {
                "model": mod,
                "max_tokens": 5,
                "temperature": 0,
                "messages": [{"role": "user", "content": "OK"}],
            }
            headers = dict(HEADERS_PADRAO)
            headers["Authorization"] = f"Bearer {chave_efetiva}"

            req = urllib.request.Request(
                url_api,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                json.loads(resp.read().decode("utf-8"))
            modelo_aprovado = mod
            break
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", errors="replace").strip()
            try:
                corpo = json.loads(detalhe)
                detalhe = corpo.get("error", {}).get("message") or detalhe
            except Exception:
                pass
            ultimo_erro = f"HTTP {exc.code}: {detalhe[:250]}"
        except Exception as exc:
            ultimo_erro = str(exc)

    if not modelo_aprovado:
        return _falha(
            f"o provedor ({provedor}) recusou o acesso",
            modelo="",
            erro=ultimo_erro or "verifique se a chave está ativa",
        )

    nota = ""
    if nova:
        try:
            salvar_chave(nova)
        except Exception as exc:
            return _falha("não foi possível gravar a chave", erro=str(exc))
        env = (os.environ.get("GROQ_API_KEY") or "").strip()
        if env and env != nova:
            nota = "Aviso: a variável GROQ_API_KEY no Windows tem prioridade sobre esta."

    return {
        "ok": True,
        "status": f"IA ({provedor}) ativa e respondendo",
        "modelo": modelo_aprovado,
        "erro": "",
        "nota": nota,
    }


def juiz_ativo():
    return bool(chave_api())


def _montar_payload(jpeg_bytes, contexto, com_imagem=True):
    texto = (
        "Setup candidato no fechamento da vela:\n"
        + json.dumps(contexto, ensure_ascii=False)
    )
    conteudo = [{"type": "text", "text": texto}]
    if com_imagem and jpeg_bytes:
        b64 = base64.b64encode(jpeg_bytes).decode()
        conteudo.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "low",
                },
            }
        )
    return {
        "temperature": 0.1,
        "max_tokens": 150,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": conteudo},
        ],
    }


def _parse_veredito(texto):
    if not texto:
        return None
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    veredito = str(d.get("veredito", "")).upper()
    if veredito not in ("CONFIRMAR", "REJEITAR"):
        return None
    return {
        "confirma": veredito == "CONFIRMAR",
        "confianca": int(max(0, min(100, d.get("confianca", 50)))),
        "motivo": str(d.get("motivo", ""))[:120],
    }


def _chamar_modelo(chave, modelo, jpeg_bytes, contexto, com_imagem=True):
    payload = _montar_payload(jpeg_bytes, contexto, com_imagem=com_imagem)
    payload["model"] = modelo
    headers = dict(HEADERS_PADRAO)
    headers["Authorization"] = f"Bearer {chave}"

    url_api = _url_endpoint(chave)

    req = urllib.request.Request(
        url_api,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
    texto = dados["choices"][0]["message"]["content"]
    return _parse_veredito(texto), texto


_lock_juiz = threading.Lock()
_modelo_utilizado = {"nome": None}


def julgar(jpeg_bytes, contexto):
    """Confirma/rejeita o setup com a IA."""
    chave = chave_api()
    if not chave:
        return {
            "ativo": False,
            "confirma": None,
            "confianca": 0,
            "motivo": "IA não configurada",
            "modelo": "",
            "erro": "",
        }

    with _lock_juiz:
        ordem = (
            ([_modelo_utilizado["nome"]] if _modelo_utilizado["nome"] else [])
            + MODELOS_VISAO
            + MODELOS_TEXTO
        )
        vistos = set()
        ultimo_erro = ""

        for modelo in ordem:
            if not modelo or modelo in vistos:
                continue
            vistos.add(modelo)
            com_imagem = modelo in MODELOS_VISAO and bool(jpeg_bytes)
            try:
                veredito, _ = _chamar_modelo(
                    chave, modelo, jpeg_bytes, contexto, com_imagem=com_imagem
                )
                if veredito is None:
                    ultimo_erro = f"{modelo}: formato inválido"
                    continue
                _modelo_utilizado["nome"] = modelo
                return {
                    "ativo": True,
                    "confirma": veredito["confirma"],
                    "confianca": veredito["confianca"],
                    "motivo": veredito["motivo"],
                    "modelo": modelo,
                    "erro": "",
                }
            except urllib.error.HTTPError as exc:
                ultimo_erro = f"{modelo}: HTTP {exc.code}"
            except Exception as exc:
                ultimo_erro = f"{modelo}: {exc}"

    return {
        "ativo": True,
        "confirma": None,
        "confianca": 0,
        "motivo": "IA indisponível",
        "modelo": "",
        "erro": ultimo_erro,
    }