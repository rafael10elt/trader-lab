# MNQ Market Lab

Laboratório de pesquisa para estratégias no MNQ/CFDs em M1. O frontend estático pode ser publicado no Netlify. O conector MT5 e os backtests rodam em um PC Windows com o MetaTrader 5 aberto e logado; eles não rodam dentro do Netlify.

## Começar

1. Instale Node.js e Python 3.11 ou superior de 64 bits. A biblioteca oficial `MetaTrader5` requer Windows e um terminal MetaTrader instalado.
2. Copie `backend/.env.example` para `backend/.env` e ajuste `MT5_SYMBOL` para o nome exato do símbolo no Market Watch do seu broker (por exemplo, `MNQ`, `US100` ou `NAS100`).
3. No PowerShell, a partir da pasta `backend`:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```

4. No terminal do projeto, instale e inicie o frontend: `npm install` e `npm run dev`.
5. No Netlify, publique este repositório. Configure `GROQ_API_KEY` e opcionalmente `GROQ_MODEL` em Site configuration → Environment variables. A chave que foi colada no chat deve ser revogada e substituída por uma nova antes do deploy.
6. Para usar a API local pelo site publicado, defina `VITE_API_URL` como uma URL HTTPS alcançável do seu PC/servidor e inclua o domínio Netlify em `API_CORS_ORIGINS`. Não exponha a porta do MT5 diretamente à internet; use uma rede privada/túnel autenticado ou hospede a API em infraestrutura protegida.

### Início rápido no Windows

Dê duplo clique em `INICIAR_API_MT5.bat`. Na primeira execução ele cria o ambiente Python, instala as dependências, abre a documentação da API e mantém uma janela de console com os logs. O status da conexão fica em `http://127.0.0.1:8000/health`; a documentação interativa está em `http://127.0.0.1:8000/docs`. Deixe a janela aberta enquanto usar o painel. O frontend ainda precisa ser iniciado em outra janela com `npm install` (uma vez) e `npm run dev`.

## O que existe

- Dashboard com gráfico M1 e modo de demonstração sintético claramente identificado.
- API para status MT5/conta, candles M1 ao vivo, zonas FVG confirmadas em M1/M5/M15/M30 (M5 por padrão), backtest EMA, pesquisa de quatro estratégias e experimento ML. O painel consulta dados automaticamente a cada dois segundos e gera a comparação OOS a cada quinze minutos enquanto o MT5 estiver conectado. Os relatórios permitem janelas de 6 ou 12 meses e mostram as datas e cobertura real da amostra.
- Assistente Groq via Netlify Function, sem transmitir segredo ao navegador. Modelo padrão: `openai/gpt-oss-20b`; há seletor de GPT-OSS 120B e Qwen 3.8 27B.
- Endpoint de execução `/trade/demo`, bloqueado por padrão. Para habilitá-lo, confirme no terminal uma conta demo, defina `ALLOW_AUTOTRADING=true`, mantenha `MT5_DEMO_ONLY=true` e use somente volume pequeno. O endpoint exige que o próprio MT5 reporte `ACCOUNT_TRADE_MODE_DEMO`, além de stop e alvo. Isso não constitui uma estratégia de trading automática: ainda não há gerenciador de posições, limites diários nem supervisão persistente.

## Limites atuais

O backtest EMA usa sinal calculado no fechamento e posição a partir da próxima barra. O resultado é expresso em pontos; custos/slippage são parâmetros simplificados em pontos por fill. O modelo ML é um experimento didático com um holdout cronológico, sem walk-forward, otimização de hiperparâmetros ou valor monetário por ponto. Valor do contrato/CFD, moeda, spread dinâmico, comissão real e regras de margem variam por broker e precisam ser modelados para resultados financeiros confiáveis. M1 também sofre com gaps, execução e qualidade histórica. Não trate métricas de uma amostra como validação de desempenho.

O relatório compara o setup de médias/pullback do Pine, cruzamento EMA, reversão à VWAP e rompimento de 20 candles no holdout cronológico mais recente. A tradução do Pine ainda não é uma reprodução integral do Strategy Tester: stops/alvos, tamanho da posição, limites diários e zeragem por horário precisam de validação específica antes de uso operacional. O FVG desenhado é um desequilíbrio de três candles (gap entre candle atual e o de duas barras atrás); zonas já totalmente mitigadas não são exibidas. A indicação do relatório é uma classificação histórica da amostra, não uma promessa de retorno nem ordem de trading.

Para histórico M1 de 6 a 12 meses, configure o MT5 em **Ferramentas → Opções → Gráficos → Máx. barras no gráfico** para aproximadamente `500000`, reinicie o terminal e abra o gráfico M1 do ativo. Pressione `Home` algumas vezes para solicitar candles antigos ao broker, aguardando o carregamento. O relatório sinaliza quando a cobertura obtida do MT5 é menor que a janela pedida e não recomenda um candidato nessa condição.
