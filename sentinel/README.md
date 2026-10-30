# Lumi Sentinel Vision

Sistema novo de **visão computacional** para operar junto ao gráfico: o Sentinel
captura a tela, lê a célula de status do TradingView (BUY/SELL), detecta o
**fechamento de cada vela** e, se houver setup, dispara **alerta visual +
sonoro** na janela local e num **dashboard no navegador**, que também mostra a
tela do gráfico ao vivo.

Substitui integralmente a versão anterior do trader-lab (API MT5 + frontend React).

## Como executar

1. Duplo clique em `INICIAR_SENTINELA.bat` (cria o venv, instala `mss` e
   `pillow`, abre a janela do Sentinel).
2. Na janela: **🎯 Calibrar célula de status** → arraste sobre o bloco
   `● AGUARDANDO...` do TradingView. (Opcional: **📈 Calibrar área do
   gráfico** para mostrar a tela no dashboard e confirmar que o gráfico está vivo.)
3. **▶️ Iniciar sentinela** — o relógio de velas passa a rodar.
4. **🌐 Abrir dashboard** → `http://127.0.0.1:8777/` (clique em
   *Ativar som do alerta* para liberar o áudio do navegador).

Para criar o ícone no desktop: `CRIAR_ATALHO_DESKTOP.bat` (rode depois do
primeiro `INICIAR_SENTINELA.bat`, pois o atalho aponta para o venv).

## Regras do setup (todas em `config.json`)

| Chave | Padrão | O que faz |
|---|---|---|
| `timeframe_seg` | 60 | Timeframe monitorado (também trocado pela GUI: 1m…4h) |
| `delay_apos_fechamento_seg` | 0.8 | Espera após a virada para o candle assentar na tela |
| `estavel_ha_seg` | 2.0 | Tempo mínimo que o BUY/SELL deve estar estável |
| `cooldown_seg` | 120 | Intervalo mínimo entre sinais |
| `confirmacao_grafico_vivo` | true | Exige que a faixa direita do gráfico mudou (não sinaliza com aba congelada) |
| `janela_operacional` | off | Bloqueia fora dos horários (fuso padrão Europe/Dublin) |
| `filtro_noticias` | on | Bloqueia ±15 min de notícias High USD/EUR (ForexFactory) |
| `porta_dashboard` | 8777 | Porta local do dashboard (auto-incrementa se ocupada) |

Fluxo: **relógio detecta o fechamento da vela → lê a célula de status via
OpenCV-free (Pillow) → roda os filtros → sinal**. Nada é gravado além de
`data/signals.jsonl`.

## Arquivos

```
sentinel/
  sentinel.py         # GUI executável (calibração, previews, iniciar/parar)
  setup_checker.py    # verificador de setup no fechamento da vela
  captura.py          # captura de tela + classificação de cor + hash do gráfico
  estado.py           # estado compartilhado + config + histórico de sinais
  servidor.py         # servidor HTTP do dashboard (stdlib)
  calibrador.py       # calibrador de ROI via terminal (opcional)
  dashboard/index.html# frontend com alerta visual/sonoro e tela ao vivo
  config.json         # regras do setup (gerado na 1ª execução)
  requirements.txt    # mss + pillow (nada pesado)
```

## Limites

- É um **sinalizador**, não um executor: não envia ordens à corretora.
- Lê a tela do TradingView: mantenha a aba visível e sem escala/zoom mudando
  durante a sessão (a calibração depende da posição da célula).
- O filtro de notícias depende da internet; se o calendário falhar, o cache de
  5 min é mantido e o erro aparece na GUI.
