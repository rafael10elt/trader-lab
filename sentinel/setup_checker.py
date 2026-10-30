"""Checagem de setup no fechamento de cada vela — pipeline ML + IA juiz.

Novo fluxo (sem célula de status):
  1. o relógio dispara no fechamento da vela (epoch % timeframe == 0 + delay);
  2. gráfico precisa estar vivo (faixa direita mudou desde a última checagem);
  3. visão computacional extrai as velas da ROI do gráfico (velas.py) e
     lê os indicadores do setup — EMAs 9/20/200 e VWAP (indicadores.py);
  4. portões do setup: linhas visíveis, alinhamento das EMAs com a direção
     e preço do lado certo do VWAP;
  5. filtro matemático (regressão logística, ml.py) pontua P(continuação)
     com as features das velas + dos indicadores — precisa >= `ml.limiar`;
  6. juiz confirmador por IA (Groq, juiz.py) recebe a imagem + métricas e
     pode rejeitar o setup mesmo com score bom;
  7. bloqueio de notícias de alto impacto (ForexFactory) e janela operacional;
  8. cooldown entre sinais.

Retorna (sinal_dict_ou_None, motivo_str).
"""
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from estado import CONFIG, STATE, adicionar_sinal
import captura
import indicadores
import juiz
import ml
import velas as velas_mod

CACHE_NOTICIAS_SEG = 300  # 5 min
_cache_noticias = {"ts": 0.0, "lista": []}


# ------------------------------------------------------------------ velas
def proximo_fechamento(timeframe_seg, agora=None):
    """Epoch do próximo fechamento de vela para o timeframe atual."""
    agora = agora if agora is not None else time.time()
    tf = max(int(timeframe_seg), 10)
    return (int(agora // tf) + 1) * tf


def segundos_para_fechamento(timeframe_seg, agora=None):
    agora = agora if agora is not None else time.time()
    return max(0.0, proximo_fechamento(timeframe_seg, agora) - agora)


# --------------------------------------------------------------- notícias
def _buscar_noticias():
    cfg = CONFIG["filtro_noticias"]
    agora = time.time()
    if agora - _cache_noticias["ts"] < CACHE_NOTICIAS_SEG and _cache_noticias["lista"]:
        return _cache_noticias["lista"]
    try:
        req = urllib.request.Request(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "LumiSentinel/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        hoje = datetime.now(timezone.utc).date()
        self_semana = hoje + timedelta(days=(7 - hoje.weekday()))  # domingo
        lista = []
        for item in dados:
            if item.get("impact") != cfg["impacto"]:
                continue
            if item.get("country") not in cfg["paises"]:
                continue
            try:
                ts = datetime.fromisoformat(item["date"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            data_item = datetime.fromtimestamp(ts, timezone.utc).date()
            if hoje <= data_item <= self_semana:
                lista.append({"ts": ts, "titulo": item.get("title", ""),
                              "country": item.get("country", "")})
        _cache_noticias["ts"] = agora
        _cache_noticias["lista"] = lista
        return lista
    except Exception as exc:
        STATE["erro"] = f"calendário de notícias indisponível: {exc}"
        return _cache_noticias["lista"]


def noticias_bloqueando(agora=None, force=False):
    """(True, evento) se estiver na janela de bloqueio; senão (False, None)."""
    cfg = CONFIG["filtro_noticias"]
    if not cfg["ativar"]:
        return False, None
    agora = agora if agora is not None else time.time()
    if force:
        _cache_noticias["ts"] = 0.0
    for ev in _buscar_noticias():
        diff = ev["ts"] - agora
        if -cfg["minutos_depois"] * 60 <= diff <= cfg["minutos_antes"] * 60:
            return True, ev
    return False, None


# ------------------------------------------------ janela operacional
def dentro_da_janela(agora=None):
    cfg = CONFIG["janela_operacional"]
    if not cfg["ativar"]:
        return True, None
    agora = agora if agora is not None else time.time()
    try:
        from zoneinfo import ZoneInfo
        local = datetime.fromtimestamp(agora, ZoneInfo(cfg["fuso"]))
    except Exception:
        local = datetime.fromtimestamp(agora)
    hhmm = local.strftime("%H:%M")
    for inicio, fim in cfg["janelas"]:
        if inicio <= hhmm <= fim:
            return True, (inicio, fim)
    return False, (cfg["janelas"][0][0], cfg["janelas"][-1][1])


# ------------------------------------------------------------- checker
IND_PADRAO = {
    "ativar": True,
    "exigir": ["ema9", "ema20"],   # linhas que precisam estar visíveis
    "exigir_vwap": True,           # preço precisa estar do lado certo do VWAP
    "bloquear_mista": True,        # stack 9/20/200 fora de ordem = sem trade
}


def _amp_setup(velas):
    """Mediana das amplitudes recentes — normaliza distâncias até as linhas."""
    amps = sorted(v["amplitude"] for v in velas[-10:])
    return max(amps[len(amps) // 2], 1)


def portoes_do_setup(indic, direcao, close_y, cfg_ind, amp):
    """Regras do setup do operador. Retorna (features|None, motivo|None).

    features entram no ML (só quando o setup passa nos portões);
    motivo != None => sinal bloqueado neste fechamento.
    """
    cfg = {**IND_PADRAO, **(cfg_ind or {})}
    if not cfg.get("ativar", True):
        return None, None

    exigidas = cfg.get("exigir") or []
    ausentes = [n for n in exigidas if n not in indic.get("encontrados", [])]
    if ausentes:
        return None, f"linha do setup ausente: {', '.join(ausentes)}"

    tend = indicadores.tendencia(indic)
    esperada = "ALTA" if direcao == "BUY" else "BAIXA"
    if tend != esperada:
        if not (tend == "MISTA" and not cfg.get("bloquear_mista", True)):
            motivo = (f"EMAs 9/20/200 sem alinhamento" if tend == "MISTA"
                      else f"tendência {tend} contra {direcao}")
            return None, motivo

    if cfg.get("exigir_vwap", True):
        acima = indicadores.preco_acima_vwap(indic, close_y)
        if acima is None:
            return None, "VWAP não encontrado no gráfico"
        esperado_vw = direcao == "BUY"
        if acima != esperado_vw:
            return None, (f"preço está {'abaixo' if esperado_vw else 'acima'}"
                          f" do VWAP")

    return indicadores.gerar_features(indic, close_y, direcao, amp), None


class SetupChecker:
    def __init__(self):
        self.ultimo_sinal_epoch = 0.0
        self.ultima_faixa_hash = None
        self.ultima_sinal_vela = 0.0    # fechamento da última vela com sinal
        self.ultima_vela_avaliada = 0.0 # apenas informativo (última checagem)
        self._hash_candle_ts = 0.0      # vela cuja faixa já foi validada
        self._hash_ok = True
        self._img_chart = None          # última captura (o juiz usa a mesma)

    def avaliar_janela(self, fechamento_ts, chart_roi=None, janela_seg=None):
        """Reavalia logo após o fechamento até a janela expirar.

        A visão/IA podem precisar de 1-3s para enxergar a vela recém-fechada;
        sem isso o setup escapava por um frame atrasado.
        """
        limite = time.time() + (janela_seg if janela_seg is not None
                                else CONFIG.get("retry_apos_fechamento_seg", 6))
        while True:
            sinal, motivo = self.avaliar_fechamento(fechamento_ts, chart_roi)
            if sinal or time.time() >= limite:
                return sinal, motivo
            time.sleep(0.5)

    def avaliar_fechamento(self, fechamento_ts, chart_roi=None, force_news=False):
        """Roda todas as regras no fechamento da vela. Devolve (sinal|None, motivo).

        Não trava a vela quando algo falha: o loop de velas re-tenta por
        alguns segundos. Só trava quando um sinal é emitido nela.
        """
        agora = time.time()
        tf = int(STATE["timeframe_seg"])
        ativo = STATE["ativo"]

        # 0. um sinal por vela
        if fechamento_ts == self.ultima_sinal_vela:
            return None, "sinal já emitido nesta vela"

        # 1. gráfico vivo? (validado 1x por vela na 1ª tentativa)
        if CONFIG["confirmacao_grafico_vivo"] and chart_roi is not None:
            if fechamento_ts != self._hash_candle_ts:
                self._hash_candle_ts = fechamento_ts
                self._hash_ok = True
                try:
                    self._img_chart = captura.capturar(chart_roi)
                    faixa = captura.hash_faixa_direita(self._img_chart)
                    if faixa and self.ultima_faixa_hash == faixa:
                        self._hash_ok = False
                    elif faixa:
                        self.ultima_faixa_hash = faixa
                except Exception as exc:
                    STATE["erro"] = f"falha ao capturar gráfico: {exc}"
            if not self._hash_ok:
                return None, "gráfico parado (faixa direita sem mudança)"

        # 2. visão computacional: extrai as velas
        if self._img_chart is None and chart_roi is not None:
            try:
                self._img_chart = captura.capturar(chart_roi)
            except Exception as exc:
                return None, f"captura falhou: {exc}"
        velas = velas_mod.extrair_velas(self._img_chart) if self._img_chart is not None else []
        STATE["velas_reconhecidas"] = len(velas)
        nivel_zoom, msg_zoom = velas_mod.diagnostico_zoom(velas)
        STATE["zoom"] = {"nivel": nivel_zoom, "msg": msg_zoom}
        if len(velas) < 5:
            return None, f"visão reconheceu {len(velas)} velas (mín. 5) — {msg_zoom}"

        # 3. direção da vela fechada (última vela completa)
        vela_atual = velas[-1]
        direcao = "BUY" if vela_atual["dir"] > 0 else "SELL"
        close_y = vela_atual["fechamento_y"]

        # 3b. indicadores do setup (EMAs 9/20/200 + VWAP) lidos no gráfico
        cfg_ind = CONFIG.get("indicadores", {})
        amp_setup = _amp_setup(velas)
        indic = indicadores.detectar(self._img_chart, cfg_ind.get("cores"))
        setup = {
            "tendencia": indicadores.tendencia(indic),
            "preco_vwap": indicadores.preco_acima_vwap(indic, close_y),
            "linhas": indic["encontrados"],
            "ausentes": indic["ausentes"],
            "confianca": indic["confianca"],
            "amplitude_px": amp_setup,
        }
        STATE["setup"] = setup
        extras, motivo_setup = portoes_do_setup(indic, direcao, close_y,
                                                cfg_ind, amp_setup)
        if motivo_setup:
            return None, motivo_setup

        # 4. filtro matemático (ML) — features das velas + dos indicadores
        score, feats = ml.FILTRO.pontuar(velas, direcao, extras)
        with_estado_ml = CONFIG.get("ml", {}).get("ativar", True)
        if not with_estado_ml:
            score = 1.0
        STATE["score_ml"] = None if score is None else round(score, 3)
        if score is None:
            return None, "ML sem features suficientes"
        limiar = CONFIG.get("ml", {}).get("limiar", 0.60)
        if score < limiar:
            return None, (f"ML rejeitou ({direcao}): P={score:.2f} < {limiar:.2f}")

        # 5. juiz confirmador (IA Groq) — mesma imagem p/ o veredito
        veredito = {"ativo": False, "confirma": None, "confianca": 0,
                    "motivo": "IA desligada na config", "modelo": "", "erro": ""}
        cfg_ia = CONFIG.get("ia_juiz", {})
        if cfg_ia.get("ativar", True) and juiz.juiz_ativo():
            if self._img_chart is not None:
                jpeg = captura.preview_jpeg(self._img_chart, max_largura=900,
                                            qualidade=45)
                contexto = {
                    "ativo": ativo, "timeframe": f"{tf // 60}m" if tf % 60 == 0 else f"{tf}s",
                    "direcao_sugerida": direcao,
                    "score_ml": round(score, 3),
                    "limiar_ml": limiar,
                    "setup": {k: setup[k] for k in
                              ("tendencia", "preco_vwap", "linhas", "confianca")},
                    "vela_atual": {
                        "corpo_px": vela_atual["corpo"],
                        "amplitude_px": vela_atual["amplitude"],
                        "decisao": round(vela_atual["corpo"] / max(vela_atual["amplitude"], 1), 2),
                    },
                    "velas_reconhecidas": len(velas),
                    "features": dict(zip(ml.NOMES_FEATURES,
                                         [round(f, 3) for f in feats])) if feats else {},
                }
                veredito = juiz.julgar(jpeg, contexto)
            STATE["veredito_ia"] = veredito
            if veredito["confirma"] is None:
                motivo_ia = veredito["erro"] or veredito["motivo"]
                if cfg_ia.get("exigir_confirmacao", True):
                    return None, f"IA juiz sem veredito — sinal bloqueado ({motivo_ia})"
                # sem exigência: segue sem confirmação, mas registra
            elif not veredito["confirma"]:
                return None, (f"IA rejeitou ({veredito['confianca']}%): "
                              f"{veredito['motivo']}")

        # 6. notícias de alto impacto
        bloqueado, ev = noticias_bloqueando(agora, force=force_news)
        if bloqueado:
            return None, f"notícia {ev['country']} em {ev['titulo'][:40]!r}"

        # 7. janela operacional
        ok, faixa_janela = dentro_da_janela(agora)
        if not ok:
            return None, f"fora da janela {faixa_janela[0]}-{faixa_janela[1]}"

        # 8. cooldown
        falta = CONFIG["cooldown_seg"] - (agora - self.ultimo_sinal_epoch)
        if falta > 0:
            return None, f"cooldown ({falta:.0f}s restantes)"

        info = (f"Fechamento {tf // 60}m • vela {direcao} • ML {score:.2f}"
                f" • EMAs {setup['tendencia']}")
        if setup.get("preco_vwap") is not None:
            info += f" • preço {'acima' if setup['preco_vwap'] else 'abaixo'} VWAP"
        if veredito.get("confirma"):
            info += f" • IA {veredito['confianca']}%"
        sinal = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "epoch": agora,
            "fechamento_ts": fechamento_ts,
            "symbol": ativo,
            "direction": direcao,
            "timeframe": f"{tf // 60}m" if tf % 60 == 0 else f"{tf}s",
            "info": info,
            "score_ml": round(score, 3),
            "setup": {k: setup.get(k) for k in
                      ("tendencia", "preco_vwap", "linhas", "confianca")},
            "ia": {"confirma": veredito.get("confirma"),
                   "confianca": veredito.get("confianca", 0),
                   "motivo": veredito.get("motivo", ""),
                   "modelo": veredito.get("modelo", "")},
        }
        sinal = adicionar_sinal(sinal)
        self.ultimo_sinal_epoch = agora
        self.ultima_sinal_vela = fechamento_ts
        self.ultima_vela_avaliada = fechamento_ts
        self._img_chart = None  # próxima vela captura de novo

        # rotula o resultado para o re-treino do ML
        if feats is not None:
            amp_ref = sum(v["amplitude"] for v in velas[-10:]) / min(len(velas), 10)
            ml.ACOMPANHA.registrar(feats, direcao, vela_atual["fechamento_y"],
                                   amp_ref, fechamento_ts)

        # re-treino automático quando há exemplos novos rotulados
        if ml.ACOMPANHA.precisa_treinar():
            ml.ACOMPANHA.registrar_treino_feito()
            ml.FILTRO.re_treinar_async(somente_reais=True)

        return sinal, f"sinal {direcao} em {ativo} (ML {score:.2f})"


CHECKER = SetupChecker()
