# Pesquisa exploratória MNQ/US100 M1

## Evidência histórica disponível nesta tarefa

Os candles M1 não estão salvos no repositório. A evidência quantitativa disponível é a captura do painel após uma consulta ao MT5; o relatório novo precisa consultar o terminal para recomputar ou detalhar esses números.

| Item da captura | Resultado |
|---|---:|
| Símbolo | US100.cash (CFD do broker) |
| Janela real | 2026-04-27 a 2026-10-26; 6,01 meses |
| Barras M1 no histórico | 177.737 |
| Holdout OOS | 53.322 candles, aproximadamente 30% cronológicos finais |
| Regime identificado | Tendência de alta, volatilidade normal, ATR 10,5391 |
| Custo configurado | 1,0 ponto por fill |

O teste selecionado para **Rompimento · 20 candles** mostrou 1.217 operações, 55,8% de acerto, +24.625,44 pontos, PF 3,92 e drawdown máximo 627,41 pontos. A tabela do painel usa uma segunda segmentação: barras do holdout em regime parecido com o atual.

| Gatilho no regime semelhante ao atual | Entradas | Win | Pontos líquidos | PF | DD máx. (pts) |
|---|---:|---:|---:|---:|---:|
| Rompimento · 20 candles | 782 | 64,2% | +8.447,2 | 3,17 | 227,6 |
| EMA 9/20 · tendência | 739 | 56,6% | +4.905,0 | 2,93 | 152,2 |
| VWAP · reversão à média | 84 | 70,2% | +1.240,7 | 3,30 | 198,3 |
| Lumi MSFVG · pullback | 3 | 66,7% | +8,3 | 1,62 | 43,1 |

## Leitura provisória

- O rompimento de 20 candles é o candidato com maior resultado líquido e mais entradas no regime observado. É o gatilho padrão da estratégia Pine atual e da v8.
- EMA 9/20 também foi positivo nesse recorte, com drawdown em pontos menor que o rompimento; é uma alternativa de tendência que merece permanecer no comparativo.
- VWAP teve taxa de acerto e PF altos, mas apenas 84 operações no regime. A amostra menor pede cautela.
- Lumi pullback teve apenas três entradas no regime: não há base para inferir que funcione ou não funcione.
- Os números não são equivalentes ao TradingView: o backtest do painel entra no próximo open, sai no sinal oposto e não modela sizing, stops/alvos Pine, travas diárias, flatten, spread variável ou regras específicas do CFD.

## Análises adicionadas ao relatório do projeto

Ao gerar relatório conectado ao MT5, o sistema também discrimina no holdout:

- desempenho por cada hora local (24 faixas) e dia da semana para cada gatilho;
- ranking das faixas com pelo menos 15 trades, PF mínimo de 1,05 e resultado positivo;
- métricas por estratégia no regime atual e no holdout completo;
- exportação CSV das tabelas horárias e semanais.

Os horários usam Europe/Dublin e ajuste automático de horário de verão. O ranking por hora é exploratório e sujeito a viés de seleção; não é uma promessa de repetição nem um sinal automático.

## Recálculo via MT5 em 26/10/2026

Consultei novamente o MT5 para `US100.cash`, M1, com custo de 1 ponto por fill. O snapshot agregado está em `reports/US100.cash-M1-6m-2026-10-26.json`.

| Dados | Valor |
|---|---:|
| Período coberto | 2026-04-27 01:05 UTC → 2026-10-26 23:08 UTC |
| Cobertura | 6,01 meses; 177.757 barras; 53.328 barras OOS |
| Regime no fechamento da amostra | Tendência de baixa; volatilidade normal; ATR 11,8372 |
| Melhor candidato para regime semelhante | EMA 9/20 · tendência |

| Gatilho no regime atual semelhante | Trades | Win | Pontos | PF | DD pts |
|---|---:|---:|---:|---:|---:|
| EMA 9/20 · tendência | 763 | 56,6% | +5.377,77 | 2,905 | 131,55 |
| Rompimento · 20 candles | 796 | 62,3% | +7.631,13 | 2,786 | 382,76 |
| VWAP · reversão à média | 91 | 71,4% | +748,35 | 1,905 | 219,20 |
| Lumi MSFVG · pullback | 4 | 50,0% | +73,55 | 3,256 | 52,50 |

O rompimento fez mais pontos no regime filtrado, mas teve quase 3x o drawdown em pontos da EMA. O ranking seleciona EMA por retorno ajustado ao drawdown e volume mínimo de trades, não por lucro bruto. No holdout completo, o rompimento teve +24.660,43 pts / PF 3,933 / DD 627,41; EMA teve +20.205,55 pts / PF 3,442 / DD 345,10. Isso deixa claro o compromisso entre retorno e risco.

### Janelas horárias com melhor score ajustado

Horários são Europe/Dublin e agregam o holdout completo, sem filtro de regime. As faixas abaixo passaram o mínimo de 15 trades, PF 1,05 e lucro líquido positivo.

| Estratégia · hora | Trades | Win | Pontos | EV/trade | PF | DD pts |
|---|---:|---:|---:|---:|---:|---:|
| Rompimento · 03:00–03:59 | 45 | 66,7% | +820,00 | 18,22 | 6,52 | 20,65 |
| Rompimento · 12:00–12:59 | 40 | 72,5% | +1.093,65 | 27,34 | 7,89 | 39,80 |
| Rompimento · 16:00–16:59 | 60 | 58,3% | +1.930,67 | 32,18 | 6,67 | 74,40 |
| Rompimento · 21:00–21:59 | 60 | 60,0% | +1.116,45 | 18,61 | 5,09 | 50,35 |
| Rompimento · 05:00–05:59 | 54 | 63,0% | +1.146,10 | 21,22 | 5,19 | 54,60 |
| EMA 9/20 · 16:00–16:59 | 90 | 56,7% | +2.172,54 | 24,14 | 5,07 | 104,20 |
| Rompimento · 09:00–09:59 | 52 | 59,6% | +1.103,15 | 21,21 | 5,91 | 54,65 |
| Rompimento · 14:00–14:59 | 53 | 52,8% | +877,35 | 16,55 | 4,39 | 48,35 |
| EMA 9/20 · 18:00–18:59 | 62 | 62,9% | +1.893,22 | 30,54 | 5,85 | 105,80 |
| EMA 9/20 · 02:00–02:59 | 40 | 62,5% | +669,26 | 16,73 | 5,32 | 38,10 |

No perfil semanal da amostra OOS, a quinta-feira foi o melhor dia por lucro bruto para ambos os principais gatilhos: rompimento 248 trades / +6.215,73 pts / PF 4,28; EMA 283 trades / +4.374,70 pts / PF 3,32. Ainda é um recorte retrospectivo, não uma regra de operação. O CFD registrou algumas barras no sábado: os resultados de fim de semana têm amostra pequena e não entram no ranking mínimo.

O relatório do servidor que já estava aberto ainda não contém esses novos campos; foi necessário executar o handler atualizado contra o mesmo terminal para gerar o snapshot. Reinicie a janela do serviço Python para a tela receber as tabelas horárias e semanais.

## Nova versão Pine

`Lumi MSFVG v8.txt` agora inclui EMA 9/20, rompimento de 20 candles e o pullback original. A EMA 9/20 vem como padrão por ter sido o candidato aprovado no regime atual desta recaptura; a seleção no Pine é manual e o ranking pode mudar. Desenha marcadores de compra/venda apenas no fechamento confirmado, produz alertas configuráveis pelo TradingView e mostra no HUD a posição, o gatilho e a janela. Stops, alvos e travas permanecem sob a lógica Pine existente. A estratégia ainda precisa ser compilada e validada no TradingView e comparada no Strategy Tester com os custos corretos do instrumento.
