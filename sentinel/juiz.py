"""Juiz confirmador por IA (Groq) — valida visualmente o setup antes do sinal.

Mesmo provedor e mesmo formato do sistema anterior:
  POST https://api.groq.com/openai/v1/chat/completions
  Authorization: Bearer <GROQ_API_KEY>

Recebe a captura JPEG do gráfico + as features do ML e devolve
CONFIRMAR / REJEITAR com justificativa curta (JSON do próprio modelo).

Modelos candidatos (fallback em cascata — ordem do sistema anterior):
  meta-llama/llama-4-scout-17b-16e-instruct     (visão)
  meta-llama/llama-4-maverick-17b-128e-instruct (visão)
  qwen/qwen3.8-27b                              (texto — sem imagem)

Chave da API: variável de ambiente GROQ_API_KEY ou config.json -> "groq_api_key".
Sem chave: o juiz fica desligado e o pipeline segue só com o filtro ML
(marcado como "IA não configurada", nunca quebra o Sentinel).
"""
import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request

from estado import CONFIG

URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"
MODELOS_VISAO = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]
MODELO_TEXTO = "qwen/qwen3.8-27b"
TIMEOUT_SEG = 45

SYSTEM_PROMPT = (
    "Você é o juiz de confirmação de um sistema de sinais por visão "
    "computacional que observa gráficos de futuros (MNQ/NQ) no TradingView. "
    "Você recebe uma imagem do gráfico e as métricas quantitativas do setup. "
    "Seu trabalho é REJEITAR setups ruins que passaram pelo filtro numérico: "
    "cruzes de médias sem direção, lateralização, velas de indecisão (dojis), "
    "suportes/resistências imediatos no caminho, ou qualquer coisa que "
    "invalide a continuação sugerida pela última vela.\n"
    "Responda APENAS com JSON válido, sem markdown, no formato:\n"
    '{"veredito":"CONFIRMAR"|"REJEITAR","confianca":0-100,"motivo":"<max 12 palavras>"}'
)


def chave_api():
    chave = (os.environ.get("GROQ_API_KEY") or CONFIG.get("groq_api_key") or "").strip()
    return chave or None


def juiz_ativo():
    return bool(chave_api())


def _montar_payload(jpeg_bytes, contexto, com_imagem=True):
    texto = ("Setup candidato no fechamento da vela:\n"
             + json.dumps(contexto, ensure_ascii=False))
    conteudo = [{"type": "text", "text": texto}]
    if com_imagem:
        b64 = base64.b64encode(jpeg_bytes).decode()
        conteudo.append({"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                       "detail": "low"}})
    return {
        "temperature": 0.2,
        "max_tokens": 160,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": conteudo},
        ],
    }


def _parse_veredito(texto):
    """Extrai o JSON do veredito (tolerante a cercas de código)."""
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
    req = urllib.request.Request(
        URL_GROQ,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {chave}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
    texto = dados["choices"][0]["message"]["content"]
    return _parse_veredito(texto), texto


_lock_juiz = threading.Lock()
_modelo_utilizado = {"nome": None}


def julgar(jpeg_bytes, contexto):
    """Confirma/rejeita o setup. Retorna dict:
      {"ativo": bool, "confirma": bool|None, "confianca": int,
       "motivo": str, "modelo": str, "erro": str}

    Nunca lança exceção: falha de rede/API vira `confirma=None` + erro, para
    o setup_checker decidir (por padrão, sem confirmação da IA = sem sinal).
    """
    chave = chave_api()
    if not chave:
        return {"ativo": False, "confirma": None, "confianca": 0,
                "motivo": "IA não configurada (GROQ_API_KEY)", "modelo": "",
                "erro": ""}
    if not jpeg_bytes:
        return {"ativo": True, "confirma": None, "confianca": 0,
                "motivo": "sem imagem do gráfico para o juiz", "modelo": "",
                "erro": "imagem ausente"}

    with _lock_juiz:
        ordem = ([_modelo_utilizado["nome"]] if _modelo_utilizado["nome"]
                 else []) + MODELOS_VISAO + [MODELO_TEXTO]
        vistos = set()
        ultimo_erro = ""
        for modelo in ordem:
            if not modelo or modelo in vistos:
                continue
            vistos.add(modelo)
            com_imagem = modelo in MODELOS_VISAO
            try:
                veredito, _texto = _chamar_modelo(
                    chave, modelo, jpeg_bytes, contexto, com_imagem=com_imagem)
                if veredito is None:
                    ultimo_erro = f"{modelo}: resposta sem veredito válido"
                    continue
                _modelo_utilizado["nome"] = modelo
                return {"ativo": True, "confirma": veredito["confirma"],
                        "confianca": veredito["confianca"],
                        "motivo": veredito["motivo"], "modelo": modelo,
                        "erro": ""}
            except urllib.error.HTTPError as exc:
                ultimo_erro = f"{modelo}: HTTP {exc.code}"
            except Exception as exc:  # rede/timeout/parse
                ultimo_erro = f"{modelo}: {exc}"
    return {"ativo": True, "confirma": None, "confianca": 0,
            "motivo": "IA indisponível — sinal bloqueado", "modelo": "",
            "erro": ultimo_erro}
