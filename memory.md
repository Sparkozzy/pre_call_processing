# Memory - Diagnóstico e Resolução de Erros

## 🗓️ 2026-05-13 - Erro 400 Retell AI (Dynamic Variables)

### Diagnóstico
As execuções do workflow `pre_call_processing` estavam falhando na etapa `create_retell_call`.
O erro retornado pela API da Retell AI era:
`Erro Retell AI (400): {"error_message":"request/body/retell_llm_dynamic_variables/empresa must be string"}`

A causa raiz foi o envio de valores `null` (None em Python) para os campos `empresa` e `segmento` dentro de `retell_llm_dynamic_variables`. A API da Retell exige que esses campos, se presentes, sejam do tipo string.

### Ação Realizada
- Modificado `services.py` para construir o dicionário `dynamic_vars` de forma condicional.
- Agora, as chaves `empresa` e `segmento` só são inseridas no payload se possuírem valor (não nulos).
- Adicionado tratamento no campo `contexto` para usar `'Não informada'` como fallback em vez de `None`.

### Evidências
- IDs de execução afetados: `89954a7d-ed77-4e78-8470-7be1747916e6`, `59dc550c-5351-4f33-acb8-6cf1de9a4b2d`, etc.
- Erro validado via logs do Supabase.

### Próximos Passos
- Realizar teste de integração com o número padrão `+5548996027108` enviando payload sem empresa/segmento.
