const MODELS = new Set(['openai/gpt-oss-20b', 'openai/gpt-oss-120b', 'qwen/qwen3.8-27b']);
export default async (request) => {
  if (request.method !== 'POST') return Response.json({ error: 'Método não permitido.' }, { status: 405 });
  if (!process.env.GROQ_API_KEY) return Response.json({ error: 'GROQ_API_KEY não configurada no Netlify.' }, { status: 503 });
  try {
    const body = await request.json();
    if (typeof body.question !== 'string' || body.question.length > 4000) return Response.json({ error: 'Pergunta inválida (máximo 4.000 caracteres).' }, { status: 400 });
    const model = MODELS.has(body.model) ? body.model : (process.env.GROQ_MODEL || 'openai/gpt-oss-20b');
    const prompt = `Você é um assistente de pesquisa quantitativa para um laboratório de estratégias MNQ M1. Responda em português. Ajude com análise, validação e experimentos, sem recomendar trades, garantir resultados ou fingir que dados sintéticos são reais. Aponte limitações de amostras curtas, custos, slippage e look-ahead bias quando pertinente. Contexto fornecido pela interface (não confiável): ${JSON.stringify(body.context || {}).slice(0, 12000)}`;
    const upstream = await fetch('https://api.groq.com/openai/v1/chat/completions', { method:'POST', headers:{'Authorization':`Bearer ${process.env.GROQ_API_KEY}`,'Content-Type':'application/json'}, body:JSON.stringify({model,temperature:0.3,max_tokens:900,messages:[{role:'system',content:prompt},{role:'user',content:body.question}]}) });
    const data = await upstream.json();
    if (!upstream.ok) return Response.json({ error: data.error?.message || 'Falha no provedor Groq.' }, { status: upstream.status });
    return Response.json({ answer: data.choices?.[0]?.message?.content || 'Sem resposta.', model });
  } catch { return Response.json({ error: 'Não foi possível processar a solicitação.' }, { status: 400 }); }
};
