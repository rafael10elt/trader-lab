# Lumi Sentinel Vision

Sistema de vigilância por **visão computacional**: captura a tela do
TradingView, lê a célula de status, detecta o fechamento de cada vela e
dispara alerta visual + sonoro (GUI local e dashboard no navegador).

## Início rápido

Duplo clique em [`sentinel/INICIAR_SENTINELA.bat`](sentinel/INICIAR_SENTINELA.bat).

Passo a passo completo, regras do setup e configuração: [`sentinel/README.md`](sentinel/README.md).

## Estrutura

- `sentinel/` — todo o sistema (Python + dashboard HTML).
- `sistema modelo/` — referência original (repomix), mantida como modelo.
