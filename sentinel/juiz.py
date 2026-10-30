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

Chave da API (nesta ordem de precedência):
  1. variável de ambiente GROQ_API_KEY;
  2. config.json -> "groq_api_key" (legado);
  3. arquivo local data/groq_key.txt (salvo pela GUI/dashboard — gitignorado).

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

import estado
from estado import CONFIG

URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"
URL_MODELS = "https://api.groq.com/openai/v1/models"
CAMINHO_CHAVE = os.path.join(estado.BASE_DIR, "data", "groq_key.txt")
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


_chave_arquivo = None  # cache da chave lida de data/groq_key.txt


def _ler_chave_arquivo():
    global _chave_arquivo
    if _chave_arquivo is None:
        try:
            with open(CAMINHO_CHAVE, "r", encoding="utf-8") as f:
                _chave_arquivo = f.read().strip()
        except Exception:
            _chave_arquivo = ""
    return _chave_arquivo


def chave_api():
    """Resolve a chave ativa: env > config (legado) > arquivo local."""
    chave = (os.environ.get("GROQ_API_KEY") or "").strip()
    if chave:
        return chave
    cfg = (CONFIG.get("groq_api_key") or "").strip()
    if cfg:
        return cfg
    return _ler_chave_arquivo() or None


def salvar_chave(chave):
    """Salva a chave no arquivo local (gitignorado) e atualiza o cache.

    A chave NÃO vai para o config.json (versionado) para não vazar no git.
    """
    global _chave_arquivo
    chave = (chave or "").strip()
    os.makedirs(os.path.dirname(CAMINHO_CHAVE), exist_ok=True)
    with open(CAMINHO_CHAVE, "w", encoding="utf-8") as f:
        f.write(chave)
    _chave_arquivo = chave
    # limpa eventual chave legada do config para evitar duplicidade/confusão
    if CONFIG.get("groq_api_key"):
        CONFIG["groq_api_key"] = ""
        try:
            estado.salvar_config(CONFIG)
        except Exception:
            pass
    return chave


def testar_chave(chave=None):
    """Valida a chave (grava só se ela passar no teste). Não lança exceção.

    Retorna {"ok": bool, "status": str, "modelo": str, "erro": str, "nota": str}.
    Passos: consulta /models (autentica), checa modelos do juiz e faz uma
    geração mínima para confirmar que a IA responde. Uma chave nova colada
    pelo usuário só é gravada depois de validar — nada de arquivo cheio de
    chave errada.
    """
    nova = (chave or "").strip()
    chave = nova or chave_api()
    if not chave:
        return {"ok": False, "status": "nenhuma chave configurada",
                "modelo": "", "erro": "cole a chave Groq no campo acima",
                "nota": ""}

    def _falha(status, modelo="", erro=""):
        return {"ok": False, "status": status, "modelo": modelo,
                "erro": erro, "nota": ""}

    # 1) autenticação + catálogo de modelos
    try:
        req = urllib.request.Request(
            URL_MODELS, headers={"Authorization": f"Bearer {chave}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id") for m in dados.get("data", [])]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return _falha("chave inválida ou sem permissão", erro=f"HTTP {exc.code}")
        return _falha("falha ao consultar a Groq", erro=f"HTTP {exc.code}")
    except Exception as exc:
        return _falha("sem conexão com a Groq", erro=str(exc))

    # 2) algum modelo do juiz disponível?
    disponivel = next((m for m in MODELOS_VISAO + [MODELO_TEXTO] if m in ids), None)
    if not disponivel:
        return {"ok": True,
                "status": f"chave válida ({len(ids)} modelos), "
                          "mas nenhum modelo do juiz disponível",
                "modelo": "", "erro": "", "nota": ""}

    # 3) geração mínima (confirma que a IA responde de fato)
    try:
        payload = {"model": disponivel, "max_tokens": 8, "temperature": 0,
                   "messages": [{"role": "user", "content": "Responda apenas: OK"}]}
        req = urllib.request.Request(
            URL_GROQ, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {chave}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return _falha("chave válida, mas a geração falhou (crédito/quota?)",
                      modelo=disponivel, erro=str(exc))

    # 4) validou: só agora grava a chave nova
    nota = ""
    if nova:
        try:
            salvar_chave(nova)
        except Exception as exc:
            return _falha("não consegui salvar a chave",
                          modelo=disponivel, erro=str(exc))
        env = (os.environ.get("GROQ_API_KEY") or "").strip()
        if env and env != nova:
            nota = ("atenção: a variável GROQ_API_KEY tem prioridade "
                    "e continua valendo no lugar desta.")
    return {"ok": True, "status": "IA Groq ativa e respondendo",
            "modelo": disponivel, "erro": "", "nota": nota}


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
