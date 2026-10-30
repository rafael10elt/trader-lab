# 📈 Código Pine do setup (TradingView)

Cole aqui o seu script do Strategy — **`setup_estrategia.pine`**.

```text
trader-lab/
└── pine/
    └── setup_estrategia.pine   ← cole o seu código aqui
```

Basta criar o arquivo e colar o código inteiro. Depois é só commitar:

```powershell
git add pine/setup_estrategia.pine
git commit -m "Setup Pine: EMAs 9/20/200 + VWAP + FVG M5 + OB"
```

---

## Por que o Sentinel precisa dele

O Sentinel **não** lê o seu script — ele lê a *imagem* do gráfico. Mas o
código Pine é a fonte da verdade das **cores** que a visão computacional
procura (`sentinel/config.json` → `indicadores.cores`):

| Linha no gráfico | Cor esperada pelo Sentinel | Hue (Pillow 0–255) |
|---|---|---|
| EMA 9   | verde  | 50–121 |
| EMA 20  | amarelo| 30–56  |
| EMA 200 | branca | S ≤ 70 e V ≥ 200 |
| VWAP    | laranja| 18–34  |

Se no seu Pine as cores forem outras, ajuste em `sentinel/config.json`:

```json
"indicadores": {
  "ativar": true,
  "exigir": ["ema9", "ema20"],
  "exigir_vwap": true,
  "bloquear_mista": true,
  "cores": {
    "ema9":   { "faixa": [50, 121], "min_s": 110, "min_v": 70 },
    "vwap":   { "faixa": [18, 34],  "min_s": 120, "min_v": 110 }
  }
}
```

> `faixa` é o **hue** na escala do Pillow (0–255, não graus).
> Converta: `hue_pillow = hue_graus × 255 / 360`.

---

## O que o Sentinel já usa do setup

No fechamento de cada vela o pipeline lê EMAs e VWAP **na imagem** e aplica:

1. **EMA9 e EMA20 visíveis** na área do gráfico (`exigir`);
2. **Stack alinhado** — 9 > 20 > 200 para BUY, 9 < 20 < 200 para SELL
   (`bloquear_mista`);
3. **Preço do lado certo do VWAP** — acima para BUY, abaixo para SELL
   (`exigir_vwap`);
4. As 4 features resultantes entram no modelo de ML junto com as 10 da vela.

FVGs do M5 e Order Blocks com % de confiança **ainda não** entram na decisão —
quando você colar o Pine aqui, dá para mapear o desenho deles (retângulos)
por cor na ROI e transformá-los em mais features/portões.
