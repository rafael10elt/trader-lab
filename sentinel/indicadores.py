"""Detecção dos indicadores do gráfico por visão computacional (Pillow puro).

Le, na ROI calibrada, as linhas do setup do operador:
  EMA9 (verde), EMA20 (amarelo), EMA200 (branca), VWAP (laranja).

Método:
  1. máscaras HSV de cada cor (cores configuráveis em config.json ->
     indicadores.cores; os padrões vêm do layout informado pelo operador);
  2. recorta a "faixa de preço" (a mesma que define as velas) e procura as
     colunas "de intervalo" (sem corpo de vela) — ali só passam as linhas;
  3. nas últimas N colunas de intervalo (perto do preço atual), extrai o y
     mediano de cada cor -> posição atual da linha;
  4. devolve tendência (alinhamento das EMAs), posição do preço vs. VWAP e
     4 features orientadas à direção candidata (entram no ML).

Se uma linha não for encontrada, ela entra em `ausentes` — o setup_checker
decide se bloqueia (padrão) ou segue sem ela.
"""
from statistics import median

from PIL import Image, ImageChops

import velas as velas_mod

# ------------------------------------------------------------- padrões
# H em 0..255 (escala do Pillow). Os padrões foram definidos a partir do
# layout do operador: EMA9 verde, EMA20 amarelo, EMA200 branca, VWAP laranja.
# As faixas precisam ficar DISJUNTAS entre si — senão o amarelo da EMA20
# (H≈34) caía dentro do laranja do VWAP e o y mediano saía torto.
CORES_PADRAO = {
    "ema9":   {"faixa": [50, 121], "min_s": 110, "min_v": 70},   # verde
    "ema20":  {"faixa": [32, 46],  "min_s": 140, "min_v": 150},  # amarelo
    "ema200": {"branca": True, "max_s": 70, "min_v": 200},       # branca
    "vwap":   {"faixa": [12, 29],  "min_s": 120, "min_v": 110},  # laranja
}
LINHAS = ["ema9", "ema20", "ema200", "vwap"]

NOMES_FEATURES = ["tendencia_stack", "preco_vwap", "dist_ema9", "dist_ema200"]

# quantas colunas de intervalo olhar (as mais recentes = perto do preço atual)
AMOSTRAS_LATERAIS = 12
FRACAO_MIN_PRESENCA = 0.55   # linha precisa aparecer em >=55% das colunas


def _cores_efetivas(cfg_usuario=None):
    cores = {k: dict(v) for k, v in CORES_PADRAO.items()}
    if cfg_usuario:
        for nome, cfg in cfg_usuario.items():
            if isinstance(cfg, dict):
                base = cores.get(nome, {})
                base.update(cfg)
                cores[nome] = base
    return cores


def _mascara_linha(cfg, h_ch, s_ch, v_ch):
    """Máscara L (0/255) da cor de uma linha do setup."""
    if cfg.get("branca"):
        return ImageChops.multiply(
            s_ch.point(lambda p: 255 if p <= cfg.get("max_s", 70) else 0),
            v_ch.point(lambda p: 255 if p >= cfg.get("min_v", 200) else 0))
    lo, hi = cfg.get("faixa", [0, 255])
    h_m = h_ch.point(lambda p: 255 if lo <= p <= hi else 0)
    s_m = s_ch.point(lambda p: 255 if p >= cfg.get("min_s", 110) else 0)
    v_m = v_ch.point(lambda p: 255 if p >= cfg.get("min_v", 70) else 0)
    return ImageChops.multiply(ImageChops.multiply(h_m, s_m), v_m)


def _ys_na_coluna(mask, x, h):
    faixa = mask.crop((x, 0, x + 1, h))
    return [i for i, p in enumerate(faixa.getdata()) if p]


def _faixa_busca(img, combined):
    """(y0, y1) da faixa de preço expandida — pega EMAs longe do preço."""
    w, h = img.size
    faixa = velas_mod._faixa_preco(combined, w, h)
    if faixa is None:
        return None
    y0, y1 = faixa
    exp = int((y1 - y0) * 0.35) + 8
    return max(0, y0 - exp), min(h - 1, y1 + exp)


def detectar(img, cfg_cores=None):
    """Detecta as linhas do setup na imagem do gráfico.

    Retorna:
      {"ema9_y": int|None, "ema20_y": ..., "ema200_y": ..., "vwap_y": ...,
       "encontrados": [...], "ausentes": [...], "confianca": 0..1}
    y cresce para baixo (menor y = preço mais alto).
    """
    resultado = {**{f"{n}_y": None for n in LINHAS},
                 "encontrados": [], "ausentes": list(LINHAS), "confianca": 0.0}
    if img is None:
        return resultado
    w, h = img.size
    if w < 24 or h < 24:
        return resultado

    # faixa de preço (mesma do extrator de velas) define onde há corpos
    m_baixa, m_alta = velas_mod._mascaras_cor(img)
    combined = ImageChops.lighter(m_baixa, m_alta)
    busca = _faixa_busca(img, combined)
    if busca is None:
        return resultado
    y0, y1 = busca

    margem_dir = max(4, int(w * 0.04))        # ignora eixo de preços
    x_ini = max(3, int(w * 0.03))             # ignora borda esquerda
    larg = max(8, w - margem_dir)
    alt = y1 + 1 - y0

    contagens = velas_mod._contagens_coluna(
        combined.crop((0, y0, larg, y1 + 1)), larg, alt)

    banda = img.crop((0, y0, larg, y1 + 1))
    hsv = banda.convert("HSV")
    h_ch, s_ch, v_ch = hsv.split()

    # colunas "de intervalo": quase sem corpo de vela (um traço de média tem
    # 1-4 px, um corpo tem dezenas) — ali só passam as linhas do setup
    min_col = max(3, int(alt * 0.05))
    intervalos = [x for x in range(x_ini, len(contagens))
                  if contagens[x] < min_col]
    if len(intervalos) < 4:
        return resultado
    amostras = intervalos[-AMOSTRAS_LATERAIS:]

    # linhas lidas na altura toda da faixa expandida: uma EMA200 longe do
    # preço continua sendo encontrada; o texto/legenda fica fora porque só
    # olhamos as últimas colunas (lado direito, junto ao preço atual)
    cores = _cores_efetivas(cfg_cores)
    encontrados, fracoes = [], []
    for nome in LINHAS:
        cfg = cores.get(nome) or CORES_PADRAO[nome]
        mask = _mascara_linha(cfg, h_ch, s_ch, v_ch)
        meds = []
        for x in amostras:
            ys = _ys_na_coluna(mask, x, alt)
            if ys:
                meds.append(median(ys) + y0)
        if not meds:
            continue
        fracao = len(meds) / len(amostras)
        if fracao >= FRACAO_MIN_PRESENCA:
            resultado[f"{nome}_y"] = int(median(meds))
            encontrados.append(nome)
            fracoes.append(fracao)

    resultado["encontrados"] = encontrados
    resultado["ausentes"] = [n for n in LINHAS if n not in encontrados]
    resultado["confianca"] = round(sum(fracoes) / len(fracoes), 2) if fracoes else 0.0
    return resultado


def tendencia(indic):
    """Alinhamento das EMAs: "ALTA" (9>20>200 em preço) | "BAIXA" | "MISTA".

    Se a EMA200 não está em quadro (fora da área calibrada), usa o par
    9/20 — quem quiser a EMA200 obrigatória basta pôr "ema200" em
    `indicadores.exigir`.
    """
    y9 = indic.get("ema9_y")
    y20 = indic.get("ema20_y")
    y200 = indic.get("ema200_y")
    if y9 is None or y20 is None:
        return "MISTA"
    if y200 is None:
        if y9 < y20:
            return "ALTA"
        if y9 > y20:
            return "BAIXA"
        return "MISTA"
    if y9 < y20 < y200:
        return "ALTA"
    if y9 > y20 > y200:
        return "BAIXA"
    return "MISTA"


def preco_acima_vwap(indic, close_y):
    """True se o preço está ACIMA do VWAP (y menor = preço mais alto)."""
    vw = indic.get("vwap_y")
    if vw is None or close_y is None:
        return None
    return close_y < vw


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def gerar_features(indic, close_y, direcao, amp):
    """4 features orientadas à direção candidata (entram no ML)."""
    if amp is None or amp <= 0:
        amp = 1.0
    s = 1 if direcao == "BUY" else -1

    stack = tendencia(indic)
    f_stack = s * (1 if stack == "ALTA" else -1 if stack == "BAIXA" else 0)

    y9 = indic.get("ema9_y")
    y200 = indic.get("ema200_y")
    vw = indic.get("vwap_y")

    # distância preço<->linha em unidades de amplitude, com sinal da direção
    f_vwap = _clip(s * ((vw - close_y) / amp), -2, 2) if vw is not None else 0.0
    f_ema9 = _clip(s * ((y9 - close_y) / amp), -2, 2) if y9 is not None else 0.0
    f_ema200 = _clip(s * ((y200 - close_y) / amp), -3, 3) if y200 is not None else 0.0

    return [float(f_stack), float(f_vwap), float(f_ema9), float(f_ema200)]
