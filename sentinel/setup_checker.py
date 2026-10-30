"""Checagem de setup no fechamento de cada vela.

Regras (todas configuráveis em config.json):
  1. o relógio dispara no fechamento da vela (epoch % timeframe == 0 + delay);
  2. a célula de status deve estar BUY/SELL e estável há `estavel_ha_seg`;
  3. gráfico precisa estar vivo (faixa direita mudou desde a última checagem);
  4. bloqueio de notícias de alto impacto (calendário ForexFactory);
  5. janela operacional opcional (fuso configurável);
  6. cooldown entre sinais para não martelar o mesmo gatilho.

Retorna (sinal_dict_ou_None, motivo_str).
"""
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import estado as estado_mod
from estado import CONFIG, STATE, adicionar_sinal
import captura

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
class SetupChecker:
    def __init__(self):
        self.ultimo_sinal_epoch = 0.0
        self.ultima_faixa_hash = None
        self.ultima_sinal_vela = 0.0    # fechamento da última vela com sinal
        self.ultima_vela_avaliada = 0.0 # apenas informativo (última checagem)
        self._hash_candle_ts = 0.0      # vela cuja faixa já foi validada
        self._hash_ok = True

    def avaliar_janela(self, fechamento_ts, chart_roi=None, janela_seg=None):
        """Reavalia logo após o fechamento até a janela expirar.

        O gatilho pode estabilizar 1-3s depois da virada; sem isso o setup
        escapava quando a célula mudou junto com a vela.
        """
        limite = time.time() + (janela_seg if janela_seg is not None
                                else CONFIG["retry_apos_fechamento_seg"])
        while True:
            sinal, motivo = self.avaliar_fechamento(fechamento_ts, chart_roi)
            if sinal or time.time() >= limite:
                return sinal, motivo
            time.sleep(0.5)

    def avaliar_fechamento(self, fechamento_ts, chart_roi=None, force_news=False):
        """Roda todas as regras no fechamento da vela. Devolve (sinal|None, motivo).

        Não marca a vela como 'avaliada' quando algo falha: o loop de velas
        re-tenta por alguns segundos (o gatilho pode estabilizar logo após a
        virada). Só trava a vela quando um sinal é emitido nela.
        """
        agora = time.time()
        tf = int(STATE["timeframe_seg"])
        ativo = STATE["ativo"]

        # 0. um sinal por vela (cooldown cobre o caso normal; este é o trava
        #    explícita para configs com cooldown baixo)
        if fechamento_ts == self.ultima_sinal_vela:
            return None, "sinal já emitido nesta vela"

        # 1. estado da célula de status
        estado, estavel_ha = estado_mod.estado_atual()
        if estado not in ("BUY", "SELL"):
            return None, f"célula em '{estado}' — sem gatilho"

        # 2. gatilho estável?
        if estavel_ha < CONFIG["estavel_ha_seg"]:
            return None, f"gatilho instável ({estavel_ha:.1f}s < {CONFIG['estavel_ha_seg']}s)"

        # 3. gráfico vivo? (validado 1x por vela na 1ª tentativa; tentativas
        #    seguintes reaproveitam o veredito — rehash em 0,5s seria ruído)
        if CONFIG["confirmacao_grafico_vivo"] and chart_roi is not None:
            if fechamento_ts != self._hash_candle_ts:
                self._hash_candle_ts = fechamento_ts
                self._hash_ok = True
                try:
                    faixa = captura.hash_faixa_direita(captura.capturar(chart_roi))
                    if faixa and self.ultima_faixa_hash == faixa:
                        self._hash_ok = False
                    elif faixa:
                        self.ultima_faixa_hash = faixa
                except Exception as exc:
                    # falha de captura não condena o gráfico (só registra)
                    STATE["erro"] = f"falha ao capturar gráfico: {exc}"
            if not self._hash_ok:
                return None, "gráfico parado (faixa direita sem mudança)"

        # 4. notícias de alto impacto
        bloqueado, ev = noticias_bloqueando(agora, force=force_news)
        if bloqueado:
            return None, f"notícia {ev['country']} em {ev['titulo'][:40]!r}"

        # 5. janela operacional
        ok, faixa_janela = dentro_da_janela(agora)
        if not ok:
            return None, f"fora da janela {faixa_janela[0]}-{faixa_janela[1]}"

        # 6. cooldown
        falta = CONFIG["cooldown_seg"] - (agora - self.ultimo_sinal_epoch)
        if falta > 0:
            return None, f"cooldown ({falta:.0f}s restantes)"

        sinal = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "epoch": agora,
            "fechamento_ts": fechamento_ts,
            "symbol": ativo,
            "direction": estado,
            "timeframe": f"{tf // 60}m" if tf % 60 == 0 else f"{tf}s",
            "info": f"Fechamento de vela {tf // 60}m — gatilho {estado} estável "
                    f"{estavel_ha:.1f}s",
            "estavel_ha": round(estavel_ha, 1),
        }
        sinal = adicionar_sinal(sinal)
        self.ultimo_sinal_epoch = agora
        self.ultima_sinal_vela = fechamento_ts
        self.ultima_vela_avaliada = fechamento_ts
        return sinal, f"sinal {estado} em {ativo}"


CHECKER = SetupChecker()
