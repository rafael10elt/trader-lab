"""Extração de velas do gráfico por visão computacional (Pillow puro).

Recebe a imagem da área do gráfico calibrada (RGB) e devolve as velas
detectadas (corpo/mechas/altura em pixels) e as features numéricas usadas
pelo filtro de machine learning (sentinel/ml.py).

Sem OpenCV e sem numpy: o trabalho pesado (perfis por coluna/linha) é feito
com `resize` BOX do Pillow, que roda em C.

Pipeline:
  1. máscaras HSV de vermelho/verde (mesmos limiares do sistema anterior);
  2. recorta a "faixa de preço" (o cluster de linhas com mais pixels coloridos
     — ignora painel de volume/legendas mesmo que entrem na ROI);
  3. perfis por coluna -> segmentos = velas (espaçamento natural do TradingView);
  4. geometria por vela: alto/baixo, corpo, mechas e direção;
  5. `extrair_features` converte as últimas velas em vetor numérico.
"""
from PIL import Image, ImageChops

# --------------------------------------------------------------- máscaras
def _mascaras_cor(img):
    """(vermelho, verde) como imagens L 0/255. H do Pillow: 0..255."""
    hsv = img.convert("HSV")
    h, s, v = hsv.split()
    base = ImageChops.multiply(
        s.point(lambda p: 255 if p >= 110 else 0),
        v.point(lambda p: 255 if p >= 70 else 0),
    )
    vermelho = ImageChops.multiply(
        ImageChops.lighter(h.point(lambda p: 255 if p <= 17 else 0),
                           h.point(lambda p: 255 if p >= 238 else 0)),
        base)
    verde = ImageChops.multiply(
        h.point(lambda p: 255 if 50 <= p <= 121 else 0), base)
    return vermelho, verde


def _contagens_coluna(mask, w, h):
    """Pixels coloridos por coluna (BOX resize = média da coluna em C)."""
    faixa = mask.resize((w, 1), Image.Resampling.BOX)
    return [round(p * h / 255) for p in faixa.getdata()]


def _contagens_linha(mask, w, h):
    """Pixels coloridos por linha (BOX resize = média da linha em C)."""
    faixa = mask.resize((1, h), Image.Resampling.BOX)
    return [round(p * w / 255) for p in faixa.getdata()]


# ---------------------------------------------------- faixa de preço
def _faixa_preco(combined, w, h):
    """Retorna (y_topo, y_base) da faixa com velas.

    Agrupa linhas com pixels coloridos; se houver vários blocos (ex.: painel
    de volume embaixo), fica com o de maior massa de cor (preço domina).
    """
    linhas = _contagens_linha(combined, w, h)
    minimo = max(2, int(h * 0.004))
    blocos = []
    inicio = None
    for y, c in enumerate(linhas):
        if c >= minimo:
            if inicio is None:
                inicio = y
        elif inicio is not None:
            blocos.append((inicio, y - 1))
            inicio = None
    if inicio is not None:
        blocos.append((inicio, h - 1))
    if not blocos:
        return None

    def massa(b):
        return sum(linhas[b[0]:b[1] + 1])

    topo, base = max(blocos, key=massa)
    # funde blocos separados por <=3 linhas (corpo/mecha podem ter micro-buracos)
    fundidos = [(topo, base)]
    for b in blocos:
        t0, t1 = b
        if t0 > fundidos[-1][1] and t0 - fundidos[-1][1] <= 3:
            fundidos[-1] = (fundidos[-1][0], t1)
        elif (t0, t1) != (topo, base):
            pass  # bloco descartado
    return fundidos[0]


# ------------------------------------------------------------- velas
def extrair_velas(img, excluir_borda_dir=0.04):
    """Detecta as velas da imagem do gráfico.

    Retorna lista (mais antiga -> mais recente) de dicts:
      dir      +1 (alta, verde) / -1 (baixa, vermelha)
      alto, baixo, corpo_topo, corpo_base   coordenadas y (0 = topo)
      corpo, amplitude                      pixels
      largura                               pixels
      fechamento_y                          y do fechamento (preço)
    A vela em formação (última, muito mais estreita) é descartada.
    """
    w, h = img.size
    if w < 24 or h < 24:
        return []

    vermelho, verde = _mascaras_cor(img)
    combined = ImageChops.lighter(vermelho, verde)

    faixa = _faixa_preco(combined, w, h)
    if faixa is None:
        return []
    y0, y1 = faixa

    # recorta a faixa de preço (também remove eixo de preços à direita)
    margem_dir = max(4, int(w * excluir_borda_dir))
    largura_scan = w - margem_dir
    box = (0, y0, largura_scan, y1 + 1)
    vr = vermelho.crop(box)
    vg = verde.crop(box)
    cc = combined.crop(box)
    sw, sh = cc.size

    cnt_vr = _contagens_coluna(vr, sw, sh)
    cnt_vg = _contagens_coluna(vg, sw, sh)
    cnt_cc = [a + b for a, b in zip(cnt_vr, cnt_vg)]

    min_col = max(2, int(sh * 0.008))
    segmentos = []
    inicio = None
    for x, c in enumerate(cnt_cc):
        if c >= min_col:
            if inicio is None:
                inicio = x
        elif inicio is not None:
            segmentos.append((inicio, x - 1))
            inicio = None
    if inicio is not None:
        segmentos.append((inicio, sw - 1))
    if not segmentos:
        return []

    # descarta vela em formação (última muito estreita vs. as anteriores)
    larguras = [b - a + 1 for a, b in segmentos]
    if len(segmentos) > 1:
        corpo_larguras = sorted(larguras[:-1])
        mediana = corpo_larguras[len(corpo_larguras) // 2]
        if larguras[-1] < max(2, mediana * 0.4):
            segmentos = segmentos[:-1]
            larguras = larguras[:-1]

    velas = []
    for (x0, x1), largura in zip(segmentos, larguras):
        if largura < 2:
            continue
        box_v = (x0, 0, x1 + 1, sh)
        linhas = _contagens_linha(cc.crop(box_v), largura, sh)
        pintados = [y for y, c in enumerate(linhas) if c >= 1]
        if not pintados:
            continue
        alto, baixo = pintados[0], pintados[-1]
        # corpo = linhas com >=55% da largura colorida (mechas têm 1-2px)
        limite_corpo = max(1, int(largura * 0.55))
        corpo_rows = [y for y, c in enumerate(linhas) if c >= limite_corpo]
        if corpo_rows:
            corpo_topo, corpo_base = corpo_rows[0], corpo_rows[-1]
        else:  # doji: corpo ~0
            meio = (alto + baixo) // 2
            corpo_topo = corpo_base = meio
        corpo = corpo_base - corpo_topo
        amplitude = baixo - alto
        n_verm = sum(1 for p in vr.crop(box_v).getdata() if p)
        n_verde = sum(1 for p in vg.crop(box_v).getdata() if p)
        dir_ = 1 if n_verde >= n_verm else -1
        fechamento_y = corpo_topo if dir_ == 1 else corpo_base
        velas.append({
            "dir": dir_,
            "alto": alto + y0,
            "baixo": baixo + y0,
            "corpo_topo": corpo_topo + y0,
            "corpo_base": corpo_base + y0,
            "corpo": corpo,
            "amplitude": max(amplitude, 1),
            "largura": largura,
            "fechamento_y": fechamento_y + y0,
        })
    return velas


# ------------------------------------------------------------- features
def extrair_features(velas, direcao):
    """Vetor numérico (orientado à direção candidata) das últimas velas.

    Todas as features direcionais são espelhadas para `direcao`, então um
    único modelo serve BUY e SELL. Requer >= 5 velas; retorna None se não.
    """
    if not velas or len(velas) < 5:
        return None
    s = 1 if direcao == "BUY" else -1

    def _clip(x, lo, hi):
        return max(lo, min(hi, x))

    amps = sorted(v["amplitude"] for v in velas)
    med_amp = max(amps[len(amps) // 2], 1)
    ultima = velas[-1]

    # 1-4: corpo/amplitude/mechas da última vela (normalizados pela mediana)
    corpo_norm = _clip(ultima["corpo"] / med_amp, 0, 4)
    amplitude_norm = _clip(ultima["amplitude"] / med_amp, 0, 4)
    decisao = _clip(ultima["corpo"] / ultima["amplitude"], 0, 1)
    mechas_norm = _clip((ultima["amplitude"] - ultima["corpo"]) / med_amp, 0, 4)

    # 5: sequência de fechamentos na mesma direção (limitada, com sinal)
    seq = 0
    for v in reversed(velas):
        if v["dir"] * s > 0:
            seq += 1
        else:
            break
    sequencia = _clip(seq, 0, 5) * s

    # 6: momentum médio (corpo com sinal) das últimas 5 velas
    ult5 = velas[-5:]
    momentum5 = sum(v["corpo"] * v["dir"] * s for v in ult5) / (med_amp * 5)
    momentum5 = _clip(momentum5, -3, 3)

    # 7: fração de fechamentos a favor nas últimas 5
    pct5 = sum(1 for v in ult5 if v["dir"] * s > 0) / 5.0
    pct5 = pct5 * 2 - 1  # -1..1

    # 8: corpo crescendo (última vs. anterior)
    anterior = velas[-2]
    corpo_crescendo = _clip(ultima["corpo"] / max(anterior["corpo"], 1), 0, 3)

    # 9: distância do fechamento à mediana dos fechamentos (posição relativa)
    fechs = sorted(v["fechamento_y"] for v in velas)
    med_fech = fechs[len(fechs) // 2]
    dist_media = _clip((med_fech - ultima["fechamento_y"]) * s / med_amp, -3, 3)

    # 10: tendência das últimas 10 (média de direções)
    ult10 = velas[-10:]
    tendencia10 = sum(v["dir"] for v in ult10) / len(ult10) * s

    return [
        corpo_norm, amplitude_norm, decisao, mechas_norm, sequencia,
        momentum5, pct5, corpo_crescendo, dist_media, tendencia10,
    ]


NOMES_FEATURES = [
    "corpo_norm", "amplitude_norm", "decisao", "mechas_norm", "sequencia",
    "momentum5", "pct5", "corpo_crescendo", "dist_media", "tendencia10",
]
