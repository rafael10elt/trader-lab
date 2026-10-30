"""Filtro de oportunidades por Machine Learning (regressão logística pura Python).

Sem numpy/scikit-learn: o modelo é uma regressão logística com 10 features
(extraídas de velas.py), padronização por média/desvio e gradiente descendente
— tudo stdlib, para instalar leve no Python 3.14.

Três fontes de modelo:
  1. bootstrap sintético — modelo inicial treinado numa função-plausível de
     continuação (corpo decisivo + momentum a favor elevam a probabilidade;
     mechas longas contra a direção reduzem). Vale até ter dados reais;
  2. dados reais — o Sentinel grava cada sinal emitido + o resultado
     (win/loss medido em pixels no próprio gráfico) em data/treino.jsonl;
     `python ml.py treinar` re-treina a partir daí;
  3. re-treino automático — quando acumulam >= min_novos exemplos rotulados,
     o filtro re-treina em thread de fundo sem derrubar a sentinela.

Uso (CLI):
    python ml.py status           # info do modelo atual
    python ml.py treinar          # re-treina (reais + sintético p/ complemente)
"""
import json
import math
import os
import random
import threading
import time

import velas as velas_mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMINHO_MODELO = os.path.join(BASE_DIR, "modelo_ml.json")
CAMINHO_TREINO = os.path.join(BASE_DIR, "data", "treino.jsonl")
MIN_NOVOS_PARA_TREINO = 30       # exemplos novos antes do re-treino automático
MIN_AMOSTRAS_REAIS = 60          # mínimo de reais para treinar só com reais


# ------------------------------------------------------- modelo (LR pura)
class ModeloLogistico:
    def __init__(self, pesos=None, bias=0.0, medias=None, desvios=None,
                 meta=""):
        self.n = len(pesos) if pesos else 0
        self.pesos = list(pesos) if pesos else []
        self.bias = float(bias)
        self.medias = list(medias) if medias else []
        self.desvios = list(desvios) if desvios else []
        self.meta = meta

    def _padronizar(self, x):
        if not self.medias:
            return list(x)
        return [(v - m) / (d if d > 1e-9 else 1.0)
                for v, m, d in zip(x, self.medias, self.desvios)]

    def probabilidade(self, x):
        z = self.bias
        xp = self._padronizar(x)
        for w, v in zip(self.pesos, xp):
            z += w * v
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def salvar(self, caminho=CAMINHO_MODELO):
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({
                "versao": 1,
                "pesos": [round(w, 6) for w in self.pesos],
                "bias": round(self.bias, 6),
                "medias": [round(m, 6) for m in self.medias],
                "desvios": [round(d, 6) for d in self.desvios],
                "meta": self.meta,
                "treinado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
                "n_features": len(self.pesos),
            }, f, ensure_ascii=False, indent=2)

    @classmethod
    def carregar(cls, caminho=CAMINHO_MODELO):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                d = json.load(f)
            return cls(pesos=d["pesos"], bias=d["bias"], medias=d["medias"],
                       desvios=d["desvios"], meta=d.get("meta", ""))
        except Exception:
            return None


# ------------------------------------------------------------- treino LR
def _treinar_lr(X, y, epocas=250, lr=0.15, l2=0.002, semente=42):
    """Gradiente descendente em lote com padronização. X: lista de vetores."""
    n_amostras = len(X)
    n_feat = len(X[0])

    medias = [sum(col) / n_amostras for col in zip(*X)]
    desvios = []
    for j, m in enumerate(medias):
        var = sum((linha[j] - m) ** 2 for linha in X) / n_amostras
        desvios.append(math.sqrt(var) if var > 1e-12 else 1.0)
    Xp = [[(v - medias[j]) / desvios[j] for j, v in enumerate(linha)]
          for linha in X]

    pesos = [0.0] * n_feat
    bias = 0.0
    rnd = random.Random(semente)
    for _ in range(epocas):
        g = [0.0] * n_feat
        gb = 0.0
        for linha, alvo in zip(Xp, y):
            z = bias + sum(w * v for w, v in zip(pesos, linha))
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            erro = p - alvo
            for j, v in enumerate(linha):
                g[j] += erro * v
            gb += erro
        bias -= lr * gb / n_amostras
        for j in range(n_feat):
            pesos[j] -= lr * (g[j] / n_amostras + l2 * pesos[j])
        rnd.random()  # mantém determinismo reprodutível
    return ModeloLogistico(pesos, bias, medias, desvios)


def _acuracia(modelo, X, y):
    if not y:
        return 0.0
    acertos = sum(1 for linha, alvo in zip(X, y)
                  if (modelo.probabilidade(linha) >= 0.5) == (alvo == 1))
    return acertos / len(y)


# ------------------------------------------------- dados sintéticos (boot)
def gerar_sinteticos(n=4000, semente=7):
    """Amostras sintéticas com uma função-plausível de continuação de vela.

    Serve só de arranque: o modelo aprende que corpo decisivo + momentum a
    favor + sequência elevam P(continuar), enquanto mechas longas contra a
    direção e amplitude anormal reduzem. Dados reais sobrepõem isso.
    """
    rnd = random.Random(semente)
    X, y = [], []
    for _ in range(n):
        corpo = rnd.uniform(0.2, 3.2)
        amp = max(corpo, corpo + rnd.uniform(0, 2.5))
        decisao = corpo / amp
        mechas = amp - corpo
        seq = rnd.uniform(-1, 1)
        mom = rnd.uniform(-2, 2)
        pct = rnd.uniform(-1, 1)
        cresc = rnd.uniform(0.3, 2.5)
        dist = rnd.uniform(-2.5, 2.5)
        tend = rnd.uniform(-1, 1)
        z = (-2.1 + 1.35 * decisao + 0.55 * corpo + 0.5 * mom + 0.4 * seq
             + 0.35 * pct + 0.3 * tend + 0.25 * cresc - 0.3 * mechas
             + 0.15 * dist + rnd.gauss(0, 0.55))
        p = 1.0 / (1.0 + math.exp(-z))
        X.append([corpo, amp, decisao, mechas, seq, mom, pct, cresc, dist, tend])
        y.append(1 if rnd.random() < p else 0)
    return X, y


# --------------------------------------------------- leitura de exemplos
def ler_treino_real(caminho=CAMINHO_TREINO):
    """Exemplos rotulados pelo próprio Sentinel (data/treino.jsonl)."""
    X, y = [], []
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    d = json.loads(linha)
                    X.append([float(v) for v in d["features"]])
                    y.append(1 if d["label"] else 0)
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return X, y


def treinar(somente_reais=False, salvar=True):
    """Treina o modelo. Reais + sintético (complemento) por padrão."""
    Xr, yr = ler_treino_real()
    if somente_reais and len(yr) >= MIN_AMOSTRAS_REAIS:
        X, y, fonte = Xr, yr, f"reais ({len(yr)})"
    else:
        Xs, ys = gerar_sinteticos()
        X, y = Xr + Xs, yr + ys
        fonte = (f"reais ({len(yr)}) + sintéticos ({len(ys)})"
                 if yr else "sintéticos (arranque)")
    if not y:
        raise ValueError("sem dados para treinar")
    # embaralha (mantém mistura representativa)
    idx = list(range(len(y)))
    random.Random(99).shuffle(idx)
    X = [X[i] for i in idx]
    y = [y[i] for i in idx]
    corte = int(len(y) * 0.85)
    modelo = _treinar_lr(X[:corte], y[:corte])
    acc = _acuracia(modelo, X[corte:], y[corte:])
    modelo.meta = f"{fonte} | acurácia holdout {acc:.1%}"
    if salvar:
        modelo.salvar()
    return modelo, acc


# ------------------------------------------------- rotulação de resultados
class AcompanhadorResultados:
    """Acompanha sinais emitidos e rotula o resultado em pixels.

    Mede o fechamento seguinte no gráfico: alvo atingido na direção do sinal
    -> label 1; stop atingido -> label 0; estourou o tempo -> usa o sinal do
    movimento final. Cada resolução vira uma linha em data/treino.jsonl.
    """

    def __init__(self, alvo_atr=1.0, stop_atr=0.6, max_velas=6):
        self.pendentes = []
        self.alvo_atr = alvo_atr
        self.stop_atr = stop_atr
        self.max_velas = max_velas
        self._novos = 0
        self._lock = threading.Lock()

    def registrar(self, features, direcao, fechamento_y, amp_ref, fechamento_ts):
        with self._lock:
            self.pendentes.append({
                "features": list(features),
                "direcao": direcao,
                "entrada_y": float(fechamento_y),
                "amp_ref": max(float(amp_ref), 1.0),
                "velas": 0,
                "fechamento_ts": fechamento_ts,
            })

    def atualizar(self, fechamento_y_atual):
        """Chamar a cada novo fechamento de vela. Retorna nº de resolvidos."""
        resolvidos = []
        with self._lock:
            sobras = []
            for p in self.pendentes:
                p["velas"] += 1
                delta = (p["entrada_y"] - fechamento_y_atual)  # y cai = sobe
                if p["direcao"] == "SELL":
                    delta = -delta
                dist = delta / p["amp_ref"]  # em unidades de amplitude
                label = None
                if dist >= self.alvo_atr:
                    label = 1
                elif dist <= -self.stop_atr:
                    label = 0
                elif p["velas"] >= self.max_velas:
                    label = 1 if dist > 0 else 0
                if label is None:
                    sobras.append(p)
                else:
                    resolvidos.append((p, label))
            self.pendentes = sobras
            for _, label in resolvidos:
                self._gravar(label)
        return len(resolvidos)

    def _gravar(self, label):
        try:
            os.makedirs(os.path.dirname(CAMINHO_TREINO), exist_ok=True)
            with open(CAMINHO_TREINO, "a", encoding="utf-8") as f:
                f.write(json.dumps({"label": label, "ts": time.time()}) + "\n")
        except Exception:
            return
        self._novos += 1

    def precisa_treinar(self):
        return self._novos >= MIN_NOVOS_PARA_TREINO

    def registrar_treino_feito(self):
        self._novos = 0


# ------------------------------------------- filtro usado pelo checker
class FiltroML:
    """Interface do setup_checker: pontua a vela candidata com o modelo."""

    def __init__(self):
        self._modelo = None
        self._lock = threading.Lock()

    def carregar(self):
        modelo = ModeloLogistico.carregar()
        if modelo is None:
            modelo, _ = treinar(somente_reais=False, salvar=True)
        self._modelo = modelo
        return modelo

    @property
    def modelo(self):
        if self._modelo is None:
            self.carregar()
        return self._modelo

    def pontuar(self, velas, direcao):
        """(probabilidade, features ou None)."""
        feats = velas_mod.extrair_features(velas, direcao)
        if feats is None:
            return None, None
        return self.modelo.probabilidade(feats), feats

    def re_treinar_async(self, somente_reais=False):
        def _rodar():
            try:
                with self._lock:
                    novo, acc = treinar(somente_reais=somente_reais)
                self._modelo = novo
                return acc
            except Exception:
                return None
        t = threading.Thread(target=_rodar, daemon=True, name="ml-treino")
        t.start()
        return t


FILTRO = FiltroML()
ACOMPANHA = AcompanhadorResultados()


def _cli():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "treinar":
        m, acc = treinar(somente_reais=("reais" in sys.argv))
        print(f"[ML] modelo treinado | holdout {acc:.1%} | {m.meta}")
        print(f"[ML] pesos: " + ", ".join(
            f"{n}={w:+.2f}" for n, w in zip(velas_mod.NOMES_FEATURES, m.pesos)))
    else:
        m = ModeloLogistico.carregar()
        if m is None:
            print("[ML] nenhum modelo em modelo_ml.json (será criado no 1º uso)")
        else:
            print(f"[ML] modelo: {m.meta}")
            print("[ML] pesos: " + ", ".join(
                f"{n}={w:+.2f}" for n, w in zip(velas_mod.NOMES_FEATURES, m.pesos)))
        Xr, yr = ler_treino_real()
        print(f"[ML] exemplos reais rotulados: {len(yr)} em {CAMINHO_TREINO}")


if __name__ == "__main__":
    _cli()
