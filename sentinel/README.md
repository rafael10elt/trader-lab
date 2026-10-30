# Lumi Sentinel Vision

Sistema de **visão computacional** que observa o gráfico do TradingView ao
vivo, detecta o **fechamento de cada vela**, filtra a oportunidade com
**machine learning** e pede **confirmação à IA juiz (Groq)** antes de disparar
**alerta visual + sonoro** na janela local e num **dashboard no navegador**.

Não há mais "célula de status": a decisão vem de acompanhar o próprio gráfico
(calibrado uma vez pela área "Área do gráfico").

Substitui integralmente a versão anterior do trader-lab (API MT5 + React).

## Como executar

1. Duplo clique em `INICIAR_SENTINELA.bat` (cria o venv, instala `mss` e
   `pillow`, abre a janela). Também funciona `python sentinel.py` — o
   `bootstrap.py` reabre sozinho no venv se você usar o Python do sistema.
2. Na janela: **📈 Calibrar área do gráfico** → arraste sobre as velas (sem o
   eixo de preços). É a única calibração necessária.
3. **▶️ Iniciar sentinela** → o relógio de velas passa a rodar.
4. **🌐 Abrir dashboard** → `http://127.0.0.1:8777/` (clique em *Ativar som do
   alerta* para liberar o áudio do navegador).

Para criar o ícone no desktop: `CRIAR_ATALHO_DESKTOP.bat` (rode depois do
primeiro `INICIAR_SENTINELA.bat`, pois o atalho aponta para o venv).

## Pipeline de decisão (no fechamento de cada vela)

1. **Visão computacional** (`velas.py`) extrai as velas da área do gráfico —
   corpo, mechas, direção — via máscaras de cor HSV (Pillow puro, sem OpenCV).
2. **Filtro matemático (ML)** (`ml.py`) — regressão logística que pontua
   `P(continuação)` na direção da última vela, com 10 features (corpo
   relativo, decisão, momentum, sequência, posição de fechamento…). Passa só
   se `P >= ml.limiar` (padrão 0.60).
3. **IA juiz (Groq)** (`juiz.py`) recebe a imagem do gráfico + as métricas e
   confirma/rejeita o setup. Mesmo com score bom, a IA pode rejeitar (doji,
   resistência imediata, lateralização). Sem veredito, o sinal é bloqueado
   (configurável).
4. **Filtros tradicionais** — bloqueio de notícias alto impacto (ForexFactory),
   janela operacional, cooldown, confirmação de gráfico vivo.

Fluxo: **relógio detecta o fechamento → visão extrai velas → ML pontua → IA
confirma → filtros → sinal**.

## Machine learning (`ml.py`)

- **Modelo inicial**: regressão logística treinada num arranque sintético
  (corpo decisivo + momentum a favor elevam `P`; mechas longas reduzem).
  Funciona de imediato, mas é apenas o arranque.
- **Aprendizado com dados reais**: cada sinal emitido é acompanhado — o
  Sentinel mede se o preço andou na direção (alvo 1.0× amplitude) ou contra
  (stop 0.6× amplitude) nos fechamentos seguintes, e grava o resultado
  (`label`) com as features em `data/treino.jsonl`.
- **Re-treino automático**: quando acumulam ≥30 exemplos novos rotulados, o
  modelo re-treina sozinho (thread de fundo, sem derrubar a sentinela).

Comandos manuais:

```bash
venv\Scripts\python.exe ml.py status    # vê modelo, pesos e nº de exemplos
venv\Scripts\python.exe ml.py treinar   # re-treina agora (reais + sintéticos)
```

## IA juiz (Groq)

Usa a mesma API do sistema anterior (`api.groq.com`, chat completions). Configure
a chave por uma destas vias:

- variável de ambiente `GROQ_API_KEY` (recomendado), ou
- `config.json` → `"groq_api_key": "..."`.

Modelos tentados em cascata: `llama-4-scout-17b-16e-instruct` (visão),
`llama-4-maverick-17b-128e-instruct` (visão), `qwen3.8-27b` (texto, sem
imagem). Sem chave, o juiz fica desligado e o pipeline segue só com o ML
(marcado no dashboard como "IA não configurada").

Em `config.json`:

```json
"ia_juiz": { "ativar": true, "exigir_confirmacao": true }
```

`exigir_confirmacao: true` bloqueia o sinal se a IA não emitir veredito
(indisponibilidade). `false` deixa o sinal passar sem confirmação da IA.

## Regras do setup (todas em `config.json`)

| Chave | Padrão | O que faz |
|---|---|---|
| `timeframe_seg` | 60 | Timeframe monitorado (também trocado pela GUI: 1m…4h) |
| `delay_apos_fechamento_seg` | 0.8 | Espera após a virada para a vela assentar na tela |
| `cooldown_seg` | 120 | Intervalo mínimo entre sinais |
| `confirmacao_grafico_vivo` | true | Exige que a faixa direita do gráfico mudou |
| `janela_operacional` | off | Bloqueia fora dos horários (fuso Europe/Dublin) |
| `filtro_noticias` | on | Bloqueia ±15 min de notícias High USD/EUR (ForexFactory) |
| `ml.ativar` | true | Liga/desliga o filtro matemático |
| `ml.limiar` | 0.60 | P(continuação) mínima para o sinal seguir |
| `ia_juiz.ativar` | true | Liga/desliga o juiz (requer chave Groq) |
| `ia_juiz.exigir_confirmacao` | true | Sem veredito da IA = sem sinal |
| `porta_dashboard` | 8777 | Porta local do dashboard (auto-incrementa se ocupada) |

Sinais ficam em `data/signals.jsonl`; exemplos de treino em
`data/treino.jsonl`; modelo aprendido em `modelo_ml.json`.

## Arquitetura

| Arquivo | Papel |
|---|---|
| `sentinel.py` | GUI (Tk) + threads (captura, velas, heartbeat) |
| `velas.py` | Visão computacional: extrai velas/features do gráfico |
| `ml.py` | Regressão logística (stdlib) + treino + rotulação |
| `juiz.py` | Confirmação por IA (Groq, com imagem) |
| `setup_checker.py` | Pipeline do fechamento da vela + filtros |
| `captura.py` | Captura de tela, hash de gráfico vivo, preview |
| `estado.py` | Estado compartilhado + config + sinais |
| `servidor.py` | Dashboard HTTP local (stdlib) |
| `bootstrap.py` | Auto-relanço no venv + diagnóstico (`--check`) |
| `calibrador.py` | Calibrador standalone da área do gráfico |
| `dashboard/index.html` | Dashboard (preview, alerta, ML, IA, histórico) |

Dependências: apenas `mss` e `pillow` (OpenCV e numpy não são necessários).

## Diagnóstico

```bash
venv\Scripts\python.exe sentinel.py --check   # ambiente + dependências
venv\Scripts\python.exe ml.py status          # modelo ML + exemplos
```

> ⚠️ É um **sinalizador**, não um executor de ordens: ele avisa, a decisão e a
> execução continuam com você.
