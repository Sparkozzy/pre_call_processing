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

### Segunda Tentativa - 2026-05-13
- Adicionado o payload enviado na mensagem de exceção para confirmar se o servidor está executando a versão correta do código.
- Suspeita de delay no deploy do Easypanel.

## 🗓️ 2026-07-08 - Erro 502 Bad Gateway (Docker Swarm VIP/IPVS Mesh Failure)

### Diagnóstico
As requisições enviadas ao formulário de `call_form_front` retornavam erro 502 (Bad Gateway) ao tentar enviar dados para o backend `call_github` na URL `https://call-github.bkpxmb.easypanel.host/webhook`.
Os logs do container do BFF mostravam `[BFF ERROR] Erro na integração com API Python: Bad Gateway`. 

Ao inspecionar o servidor via SSH:
1. O container do backend Python (`call_github`) estava ativo e ouvindo normalmente na porta `8000` (testado via conexões diretas ao IP do container e ao DNS `tasks.call_github:8000`).
2. Conexões ao nome de serviço `call_github:8000` (que resolve para o Virtual IP `10.11.0.27` do Swarm) falhavam com erro de conexão recusada/timeout.
3. Traefik estava tentando repassar os pacotes para `http://call_github:8000/`, mas como o VIP do Swarm estava inacessível, a requisição falhava e o Traefik retornava `502 Bad Gateway` para o cliente.

A causa raiz foi uma falha/corrupção no roteamento IPVS (IP Virtual Server) do Docker Swarm para a rede overlay `easypanel`, impedindo que os VIPs dos serviços fizessem o balanceamento para as réplicas reais.

### Ação Realizada
- Atualizado o modo de endpoint dos serviços `call_github` e `call_predict_github` no Docker Swarm de `vip` para `dnsrr` (DNS Round Robin):
  ```bash
  docker service update --endpoint-mode dnsrr call_github
  docker service update --endpoint-mode dnsrr call_predict_github
  ```
- Com o modo `dnsrr`, o DNS interno resolve o nome do serviço diretamente para o IP do container, ignorando o VIP/IPVS com problemas e permitindo conexão imediata.

### Evidências
- As conexões via curl a `https://call-github.bkpxmb.easypanel.host/webhook` e `http://call_github:8000/` agora funcionam com sucesso e retornam status válidos do uvicorn (202 / 404).
- Resolução de hosts validada nos containers do BFF.

### Lição
Sempre que ocorrerem erros de rede/conexão persistentes entre microsserviços em um ambiente Docker Swarm sem alterações de código aparentes, verifique a conectividade usando `tasks.<nome_servico>` para contornar o VIP do Swarm. Se funcionar, indica que a malha IPVS está corrompida. Mudar o endpoint do serviço para `dnsrr` é uma solução robusta para contornar gargalos do IPVS.


## 🗓️ 2026-07-24 - Erro 404 Retell AI (Caracteres Unicode Invisíveis no Agent ID)

### Diagnóstico
As execuções do workflow `pre_call_processing` falharam na etapa `create_retell_call` com o erro:
`Erro Retell AI (404): {"status":"error","message":"Not Found"}` ou `"Item ⁠agent_f1603ca4baa2d88297d1ae9c40⁠ not found from agent"`.

A causa raiz foi a presença de caracteres unicode invisíveis de junção de palavras (zero-width joiner `\u2060`) ao redor do `agent_id` enviado no payload. Por exemplo, o ID estava sendo enviado como `'\u2060agent_f1603ca4baa2d88297d1ae9c40\u2060'`, o que fazia a API do Retell AI falhar ao não encontrar o agente correspondente.

### Ação Recomendada
- Limpar e sanitizar os campos de identificadores (`agent_id`) usando `.strip()` e removendo caracteres invisíveis (`\u2060`, `\u200b`, etc.) antes de fazer requisições externas.

### Evidências
- IDs de execução afetados: `9271248f-6ba2-4f7c-8451-cf79dc693b90`, `40eb89f4-d7cb-47a7-8da2-3ce87d13563b`, `391d131b-dc7e-45b5-bfef-34beb4d21fb7`, etc.


## 🗓️ 2026-07-24 - Timeout de Consulta no Supabase (Erro 57014 no call_predict_get_rows)

### Diagnóstico
O workflow `call_predict` falhou no passo `call_predict_get_rows` com a mensagem de timeout do banco de dados:
`{'message': 'canceling statement due to statement timeout', 'code': '57014', 'hint': None, 'details': None}`

A causa raiz é a ausência de um índice na coluna `to_number` da tabela `Retell_calls_Mindflow`. Como o volume de chamadas cresceu significativamente, a consulta de histórico para cada lead realiza um full table scan (busca sequencial em toda a tabela), excedendo o tempo limite de execução da query definido no Supabase.

### Ação Recomendada
- Executar a criação do índice de pesquisa no banco de dados Supabase:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_retell_calls_mindflow_to_number ON "Retell_calls_Mindflow"(to_number);
  ```

### Evidências
- IDs de execução afetados: `ecc1f6b6-a572-4231-a332-975648765442`, `315265f9-5c59-427e-bb20-95b6e54de285`.

## 🗓️ 2026-07-24 - Chave system:status Bloqueada no Redis (Chamada Ignorada)

### Diagnóstico
As chamadas via API estavam sendo ignoradas e gravadas no Supabase com o status: `"Chamada ignorada (Lote ou sistema cancelado pelo usuário)"` e tipo de execução `"Cancelled/Skipped"`.

A causa raiz foi o acionamento anterior do endpoint `/webhook/csv/cancel` sem o parâmetro `batch_id`. Isso ativou a flag global `system:status` no Redis com o valor `"cancelled"`. Como o sistema não possuía nenhum endpoint ou mecanismo automático para limpar/redefinir essa chave, todas as execuções posteriores foram abortadas indefinidamente.

### Ação Realizada
- Conectado ao Redis no servidor de produção e deletada a chave `system:status`.
- Disparada nova chamada de teste com sucesso.

### Lição
Sempre implemente um endpoint ou processo de reversão (ex: `/resume` ou `/reset`) para flags de cancelamento global persistentes (Panic Button), para evitar o travamento contínuo das rotinas sem intervenção direta no Redis.


## 🗓️ 2026-07-24 - Erro 400 Retell AI (override_agent_id must be string)

### Diagnóstico
Ao disparar novas chamadas onde o `agent_id` não era especificado (valor nulo/None), a API da Retell AI retornava erro 400:
`{"error_message":"request/body/override_agent_id must be string"}`

A causa raiz foi o envio incondicional do campo `override_agent_id` no payload da requisição com valor `None` (null). A API do Retell exige que esse campo, se enviado, contenha obrigatoriamente um ID válido em formato string.

### Ação Realizada
- Modificado o construtor do payload da Retell em `services.py` para incluir a chave `"override_agent_id"` apenas de forma condicional se `agent_id` for fornecido e não nulo.

### Lição
Não envie chaves de mapeamento com valores nulos para APIs externas estritas se as mesmas exigirem tipos específicos (como string) para esses campos mesmo quando vazios. Condicione o payload antes do envio.
