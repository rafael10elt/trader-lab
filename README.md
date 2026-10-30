# Lumi Sentinel Vision

Sistema de **visão computacional** que observa o gráfico do TradingView ao
vivo, detecta o fechamento de cada vela, filtra a oportunidade com
**machine learning** e confirma com **IA juiz (Groq)** antes de disparar
alerta visual + sonoro (GUI local e dashboard no navegador).

Não há "célula de status": a decisão vem de acompanhar o próprio gráfico
(calibrado pela área do gráfico).

## Início rápido

Duplo clique em [`sentinel/INICIAR_SENTINELA.bat`](sentinel/INICIAR_SENTINELA.bat).

Passo a passo completo, pipeline (visão → ML → IA), treino do modelo e
configuração da chave Groq: [`sentinel/README.md`](sentinel/README.md).

## Estrutura

- `sentinel/` — todo o sistema (Python + dashboard HTML).
- `site/` — página estática publicada no Netlify (informativa).
- `netlify/` — configuração e functions do deploy.
