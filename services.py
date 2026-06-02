import asyncio
import httpx
import os
import random
import uuid
import csv
from datetime import datetime, timezone
import zoneinfo
from database import get_supabase_async

# Configuração de Fusos Horários
UTC = timezone.utc
BR_TIMEZONE = zoneinfo.ZoneInfo("America/Sao_Paulo")

def get_utc_now():
    """Retorna o horário atual em UTC para persistência em banco de dados."""
    return datetime.now(UTC).isoformat()

def get_br_now():
    """Retorna o horário atual em fuso Brasília para lógica interna."""
    return datetime.now(BR_TIMEZONE)

def parse_iso_to_br(iso_date: str) -> datetime:
    """Converte uma string ISO 8601 para datetime no fuso de Brasília."""
    try:
        dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
        return dt.astimezone(BR_TIMEZONE)
    except Exception as e:
        raise ValueError(f"Formato de data inválido: {iso_date}. Esperado ISO 8601.")

def strip_markdown(text: str) -> str:
    """Remove caracteres especiais de Markdown para compatibilidade com TTS/Retell."""
    import re
    # Bold/Italic
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    # Headers
    text = re.sub(r'#+\s*(.*)', r'\1', text)
    # Bullets
    text = re.sub(r'^[\s\t]*[-\*\+]\s+', '', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    return text.strip()

async def run_step_with_retry(execution_id: str, workflow_name: str, oqf: str, step_input: dict, max_retries: int, worker_func=None) -> dict:
    """
    Executa a lógica de uma etapa específica de forma assíncrona com exponential backoff.
    Step Name: {workflow_name}_{oqf}
    """
    step_full_name = f"{workflow_name}_{oqf}"
    supabase_async = await get_supabase_async()
    attempt = 1
    
    while attempt <= max_retries:
        started_at = get_utc_now()
        br_started_at = get_br_now().isoformat()
        
        try:
            if worker_func:
                logic_output = await worker_func(step_input)
                output = {
                    "status": "ok",
                    "version": "v3-async-arq",
                    "data": logic_output,
                    "processed_at_utc": started_at,
                    "internal_br_log": br_started_at
                }
            else:
                await asyncio.sleep(0.5)
                output = {
                    "status": "ok",
                    "version": "v3-async-arq",
                    "step": step_full_name, 
                    "processed_at_utc": started_at,
                    "internal_br_log": br_started_at
                }
            
            # Registro de Detalhe (Sucesso) via client async
            await supabase_async.table('workflow_step_executions').insert({
                'execution_id': execution_id,
                'step_name': step_full_name,
                'status': 'SUCCESS',
                'attempt': attempt,
                'input_data': step_input,
                'output_data': output,
                'started_at': started_at,
                'completed_at': get_utc_now()
            }).execute()
            
            return output
            
        except Exception as error:
            # Registro de Detalhe (Falha imutável) via client async
            await supabase_async.table('workflow_step_executions').insert({
                'execution_id': execution_id,
                'step_name': step_full_name,
                'status': 'FAILED',
                'attempt': attempt,
                'input_data': step_input,
                'error_details': str(error),
                'started_at': started_at,
                'completed_at': get_utc_now()
            }).execute()
            
            if attempt == max_retries: 
                raise Exception(f"Etapa {step_full_name} falhou após {max_retries} tentativas: {str(error)}")
            
            # Exponential Backoff com Jitter para evitar thundering herd
            delay = min(2 ** attempt + random.uniform(0, 1), 30)
            await asyncio.sleep(delay)
            attempt += 1

async def schedule_execution_node(ctx, execution_id: str, payload: dict):
    """
    Nó de Agendamento Assíncrono (Redis/ARQ):
    - Se a data for passada ou ausente, continua imediatamente.
    - Se for futura, usa o Redis (ARQ _defer_until) para agendar de forma persistente.
    """
    workflow_name = payload.get('workflow_name', 'envia_ligacao')
    quando_ligar_raw = payload.get('quando_ligar')
    
    if not quando_ligar_raw:
        # Execução imediata
        await continue_workflow_execution(ctx, execution_id, payload)
        return

    # 1. Comparação de datas
    data_agendada = parse_iso_to_br(quando_ligar_raw)
    agora = get_br_now()
    
    step_input = {"quando_ligar_original": quando_ligar_raw, "agora_br": agora.isoformat()}
    
    if data_agendada <= agora:
        # Execução Imediata
        output = {"decisao": "EXECUCAO_IMEDIATA", "motivo": "Data no passado ou agora"}
        await run_step_with_retry(execution_id, workflow_name, 'agendamento_redis', step_input, 1)
        await continue_workflow_execution(ctx, execution_id, payload)
    else:
        # Agendamento persistente via Redis/ARQ
        output = {"decisao": "AGENDADO_REDIS", "agendado_para": data_agendada.isoformat()}
        
        # Enfileira o job com atraso (deferred) para o Redis processar com segurança
        await ctx['redis'].enqueue_job(
            'continue_workflow_execution',
            execution_id,
            payload,
            _defer_until=data_agendada
        )
        
        # Garante a rastreabilidade (Nó concluído)
        await run_step_with_retry(execution_id, workflow_name, 'agendamento_redis', {**step_input, **output}, 1)

async def continue_workflow_execution(ctx, execution_id: str, payload: dict):
    """
    Continuação do Workflow (executada de forma assíncrona pelo Worker ARQ):
    Realizado agora ou deferido via Redis.
    """
    workflow_name = payload.get('workflow_name', 'envia_ligacao')
    supabase_async = await get_supabase_async()
    
    # 0. Checagem do Kill Switch (Cancelamento)
    batch_id = payload.get("from_batch_id")
    is_cancelled = False
    
    try:
        # Checa flag global no Redis
        global_status = await ctx['redis'].get("system:status")
        if global_status in [b"cancelled", "cancelled"]:
            is_cancelled = True
            
        # Checa flag do lote específico no Redis
        if batch_id and not is_cancelled:
            batch_status = await ctx['redis'].get(f"batch:{batch_id}:status")
            if batch_status in [b"cancelled", "cancelled"]:
                is_cancelled = True
    except Exception as re:
        print(f"⚠️ Erro ao consultar flags de cancelamento no Redis: {re}")

    if is_cancelled:
        print(f"⚠️ Chamada para lead {payload.get('numero')} abortada: lote/sistema cancelado.")
        try:
            # Finaliza a execução individual marcando como pulada (SUCCESS por negócio, no-op)
            await supabase_async.table('workflow_executions').update({
                'status': 'SUCCESS',
                'output_data': {
                    "status": "Chamada ignorada (Lote ou sistema cancelado pelo usuário)",
                    "execution_type": "Cancelled/Skipped"
                },
                'completed_at': get_utc_now()
            }).eq('id', execution_id).execute()
        except Exception as se:
            print(f"⚠️ Erro ao atualizar cancelamento do lead no Supabase: {se}")
        return

    try:
        # Atualiza status para RUNNING 
        await supabase_async.table('workflow_executions').update({'status': 'RUNNING'}).eq('id', execution_id).execute()

        # Step 2: Buscar Prompt no Supabase
        prompt_id_raw = payload.get("Prompt_id") or payload.get("prompt_id")
        if not prompt_id_raw:
            raise ValueError("Prompt_id não encontrado para busca.")

        async def fetch_prompt_logic(input_data):
            p_id = input_data.get('prompt_id')
            try:
                id_int = int(p_id)
                res = await supabase_async.table('Prompts').select('*').eq('id', id_int).execute()
            except (ValueError, TypeError):
                res = await supabase_async.table('Prompts').select('*').eq('Pormpt_Name', p_id).execute()
            
            if not res.data:
                raise ValueError(f"Prompt '{p_id}' não encontrado na tabela Prompts.")
            
            return res.data[0].get('Ligação/txt') or res.data[0].get('Prompt_Text')

        prompt_node_res = await run_step_with_retry(
            execution_id, 
            workflow_name, 
            'fetch_prompt', 
            {"prompt_id": prompt_id_raw}, 
            3,
            worker_func=fetch_prompt_logic
        )
        prompt_content = prompt_node_res['data']

        # Step 3: Formatação Automática (Async mas sem I/O pesado, usamos a lógica adaptada)
        async def format_payload_logic(input_data):
            raw_prompt = input_data.get('raw_prompt', '')
            payload_ref = input_data.get('payload_ref', {})
            
            clean_text = raw_prompt.replace('\r', '')
            
            mapping = {
                "customer_name": payload_ref.get("nome") or payload_ref.get("customer_name") or "Lead",
                "empresa": payload_ref.get("empresa") or "Empresa",
                "segmento": payload_ref.get("segmento") or "Segmento",
                "contexto": payload_ref.get("contexto") or "Contexto",
                "email": payload_ref.get("email", ""),
                "numero_do_lead": payload_ref.get("numero") or payload_ref.get("numero_do_lead", ""),
                "now": get_br_now().strftime("%d/%m/%Y %H:%M"),
                "data_atual_iso": get_utc_now()
            }
            
            for key, val in mapping.items():
                s_val = str(val)
                clean_text = clean_text.replace("{{" + key + "}}", s_val)
                clean_text = clean_text.replace("{{ " + key + " }}", s_val)
                clean_text = clean_text.replace("{" + key + "}", s_val)
                clean_text = clean_text.replace("{ " + key + " }", s_val)
            
            clean_text = strip_markdown(clean_text)
            
            return {
                "agent_prompt": clean_text,
                "applied_mapping": mapping,
                "metadata": {
                    "original_prompt_id": payload_ref.get("Prompt_id")
                }
            }

        formatted_res = await run_step_with_retry(
            execution_id, 
            workflow_name, 
            'format_payload', 
            {"raw_prompt": prompt_content, "payload_ref": payload}, 
            1,
            worker_func=format_payload_logic
        )
        final_data = formatted_res['data']

        # Step 4: Criar Chamada na Retell AI (Substituindo request síncrono por httpx assíncrono)
        async def create_retell_call_logic(input_data):
            p = input_data.get('payload_ref', {})
            fd = input_data.get('final_data', {})
            
            api_key = os.getenv("RETELL_API_KEY")
            if not api_key:
                raise ValueError("RETELL_API_KEY não configurada no ambiente.")
            
            url = "https://api.retellai.com/v2/create-phone-call"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            dynamic_vars = {
                "customer_name": p.get("nome"),
                "prompt": fd.get("agent_prompt"),
                "now": get_utc_now(), # ISO UTC para Retell
                "contexto": p.get("contexto") or f"Empresa: {p.get('empresa') or 'Não informada'}\nSegmento: {p.get('segmento') or 'Não informado'}",
                "numero_do_lead": p.get("numero"),
                "email": p.get("email")
            }

            # Apenas envia empresa e segmento se houver valor, evitando erro 400 (must be string) na Retell AI
            if p.get("empresa"):
                dynamic_vars["empresa"] = p.get("empresa")
            if p.get("segmento"):
                dynamic_vars["segmento"] = p.get("segmento")

            retell_payload = {
                "from_number": p.get("from_number") or os.getenv("RETELL_FROM_NUMBER", "iatizeia"),
                "to_number": p.get("numero"),
                "override_agent_id": p.get("agent_id"),
                "metadata": {
                    "workflow_execution_id": execution_id,
                    "workflow_name": workflow_name
                },
                "retell_llm_dynamic_variables": dynamic_vars
            }
            
            max_retries = 5
            attempt = 0
            
            async with httpx.AsyncClient() as client:
                while attempt < max_retries:
                    response = await client.post(url, json=retell_payload, headers=headers, timeout=15.0)
                    
                    if response.status_code == 429:
                        attempt += 1
                        # Backoff exponencial com jitter (espera local) para dispersar requests
                        wait_time = (2 ** attempt) + random.randint(1, 10)
                        print(f"Rate limit (429) na Retell. Tentativa {attempt}/{max_retries}. Aguardando {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                        
                    if response.status_code not in [200, 201]:
                        raise Exception(f"Erro Retell AI ({response.status_code}): {response.text} | Payload enviado: {retell_payload}")
                        
                    return response.json()
            
            raise Exception(f"Falha na Retell AI após {max_retries} tentativas de Rate Limit (429).")

        retell_res = await run_step_with_retry(
            execution_id, 
            workflow_name, 
            'create_retell_call', 
            {"payload_ref": payload, "final_data": final_data}, 
            3, # Até 3 tentativas com exponential backoff
            worker_func=create_retell_call_logic
        )

        # Finaliza Mestre
        await supabase_async.table('workflow_executions').update({
            'status': 'SUCCESS',
            'output_data': {
                "status": "Workflow finalizado com sucesso", 
                "execution_type": "Scheduled/Immediate (Redis/ARQ)",
                "call_id": retell_res['data'].get('call_id')
            },
            'completed_at': get_utc_now()
        }).eq('id', execution_id).execute()
        
        print(f"Workflow {workflow_name} ({execution_id}) processado com sucesso no worker ARQ.")

    except Exception as e:
        await supabase_async.table('workflow_executions').update({
            'status': 'FAILED',
            'error_details': str(e),
            'completed_at': get_utc_now()
        }).eq('id', execution_id).execute()
        print(f"Falha na continuação do workflow {execution_id}: {e}")

async def ingest_csv_batch(ctx, batch_id: str, file_path: str, contexto_global: str, frequencia: float, agent_id: str, prompt_id: str):
    """
    Worker Job:
    1. Abre o arquivo CSV de forma leve e iterativa.
    2. Registra o passo 'csv_scheduling_ingestion' em workflow_step_executions.
    3. Mapeia as colunas extras de cada linha para o contexto do lead.
    4. Enfileira o disparo de cada lead com agendamento temporal indexado (delay = i * frequencia) no Redis.
    5. Atualiza o status mestre do lote na workflow_executions para SUCCESS ao concluir.
    6. Exclui o arquivo CSV temporário do disco de forma limpa.
    """
    print(f"🚀 Iniciando processamento de lote via CSV para o lote {batch_id}...")
    supabase_async = await get_supabase_async()
    started_at = get_utc_now()
    step_full_name = "csv_scheduling_ingestion"
    
    # 1. Cria o registro do passo no Supabase
    try:
        step_record = await supabase_async.table('workflow_step_executions').insert({
            'execution_id': batch_id,
            'step_name': step_full_name,
            'status': 'RUNNING',
            'attempt': 1,
            'input_data': {
                'file_path': file_path,
                'frequencia': frequencia,
                'agent_id': agent_id,
                'prompt_id': prompt_id
            },
            'started_at': started_at
        }).execute()
        step_db_id = step_record.data[0]['id']
    except Exception as e:
        print(f"⚠️ Erro ao registrar passo de ingestão de CSV no Supabase: {e}")
        step_db_id = None

    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Arquivo CSV temporário {file_path} não encontrado no disco.")

        # Lógica de leitura do CSV de forma leve (linha por linha, sem Pandas inteiro na RAM)
        leads = []
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Normaliza as chaves do dicionário para minúsculas
            fieldnames_normalized = [field.strip().lower() for field in reader.fieldnames]
            
            # Mapeamento de índices originais para normalizados
            field_map = {field.strip().lower(): field for field in reader.fieldnames}
            
            required_cols = ["numero", "nome", "email"]
            for col in required_cols:
                if col not in fieldnames_normalized:
                    raise ValueError(f"Coluna obrigatória '{col}' ausente no arquivo CSV.")
            
            col_extra = [field for field in fieldnames_normalized if field not in required_cols]

            for row in reader:
                # Normaliza chaves da linha
                row_norm = {k.strip().lower(): v for k, v in row.items() if k}
                
                num_val = row_norm["numero"].strip()
                nome_val = row_norm["nome"].strip()
                
                # Validação de e-mail vazio/nulo -> fallback para "."
                email_val = row_norm.get("email", "").strip()
                if not email_val or email_val in ["", "."]:
                    email_val = "."
                
                # Mapeia colunas adicionais para o contexto do lead
                contexto_lead = contexto_global or ""
                extra_parts = []
                for extra in col_extra:
                    orig_key = field_map[extra]
                    val = row.get(orig_key, "").strip()
                    if val:
                        extra_parts.append(f"{orig_key}: {val}")
                
                if extra_parts:
                    extra_str = "; ".join(extra_parts) + "; "
                    if contexto_lead and not contexto_lead.endswith(" "):
                        contexto_lead += " "
                    contexto_lead += extra_str
                
                leads.append({
                    "numero": num_val,
                    "nome": nome_val,
                    "email": email_val,
                    "contexto": contexto_lead
                })

        total_leads = len(leads)
        print(f"📊 Lote {batch_id} carregou {total_leads} leads do CSV. Enfileirando...")

        # 2. Enfileira temporalmente no Redis em chunks assíncronos
        from datetime import timedelta
        
        chunk_size = 2000
        for chunk_start in range(0, total_leads, chunk_size):
            # Verifica se o lote foi cancelado emergencialmente enquanto estamos enfileirando
            is_cancelled = False
            try:
                global_status = await ctx['redis'].get("system:status")
                batch_status = await ctx['redis'].get(f"batch:{batch_id}:status")
                if global_status in [b"cancelled", "cancelled"] or batch_status in [b"cancelled", "cancelled"]:
                    is_cancelled = True
            except Exception as re:
                print(f"⚠️ Erro ao checar cancelamento na ingestão: {re}")
                
            if is_cancelled:
                raise Exception("A ingestão do lote foi interrompida devido ao cancelamento ativado pelo usuário.")

            chunk = leads[chunk_start : chunk_start + chunk_size]
            
            enqueue_tasks = []
            for j, lead in enumerate(chunk):
                lead_index = chunk_start + j
                delay_sec = lead_index * frequencia
                data_agendada = get_br_now() + timedelta(seconds=delay_sec)
                
                lead_execution_id = str(uuid.uuid4())
                lead_payload = {
                    "workflow_name": "pre_call_processing",
                    "execution_id": lead_execution_id,
                    "numero": lead["numero"],
                    "nome": lead["nome"],
                    "email": lead["email"],
                    "agent_id": agent_id,
                    "Prompt_id": prompt_id,
                    "quando_ligar": data_agendada.isoformat(),
                    "contexto": lead["contexto"],
                    "from_batch_id": batch_id
                }
                
                # Enfileira passando _job_id customizado para permitir scan e deleção gradual
                enqueue_tasks.append(
                    ctx['redis'].enqueue_job(
                        'schedule_execution_node',
                        lead_execution_id,
                        lead_payload,
                        _defer_until=data_agendada,
                        _job_id=f"job:{batch_id}:{lead_execution_id}"
                    )
                )
                
            # Executa o enfileiramento das chaves no Redis em paralelo controlado
            await asyncio.gather(*enqueue_tasks)
            # Pequena pausa para liberar a CPU do worker
            await asyncio.sleep(0.1)

        # 3. Finaliza com sucesso no Supabase
        if step_db_id:
            await supabase_async.table('workflow_step_executions').update({
                'status': 'SUCCESS',
                'output_data': {
                    'status': 'CSV processado com sucesso',
                    'total_leads': total_leads,
                    'ingested_at': get_utc_now()
                },
                'completed_at': get_utc_now()
            }).eq('id', step_db_id).execute()

        await supabase_async.table('workflow_executions').update({
            'status': 'SUCCESS',
            'output_data': {
                'status': 'Processamento de lote CSV concluído',
                'total_leads': total_leads,
                'completed_at': get_utc_now()
            },
            'completed_at': get_utc_now()
        }).eq('id', batch_id).execute()

        print(f"✅ Lote {batch_id} ingerido com sucesso: {total_leads} ligações agendadas temporalmente.")

    except Exception as err:
        print(f"❌ Falha crítica ao processar lote de CSV {batch_id}: {err}")
        
        # Atualiza o passo para FAILED
        if step_db_id:
            try:
                await supabase_async.table('workflow_step_executions').update({
                    'status': 'FAILED',
                    'error_details': str(err),
                    'completed_at': get_utc_now()
                }).eq('id', step_db_id).execute()
            except Exception:
                pass
                
        # Atualiza o mestre para FAILED
        try:
            await supabase_async.table('workflow_executions').update({
                'status': 'FAILED',
                'error_details': f"Falha na ingestão do CSV: {err}",
                'completed_at': get_utc_now()
            }).eq('id', batch_id).execute()
        except Exception:
            pass

    finally:
        # Exclui o arquivo temporário localmente
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🧹 Arquivo temporário {file_path} excluído do disco.")
        except Exception as e:
            print(f"⚠️ Erro ao remover arquivo CSV temporário do disco: {e}")

async def clean_cancelled_jobs(ctx, batch_id: str):
    """
    Garbage Collector: Varre suavemente o Redis usando SCAN de forma assíncrona
    para encontrar e deletar os jobs agendados que pertencem ao lote cancelado.
    Evita spikes na CPU única do Redis rodando deleções graduais.
    """
    print(f"🧹 Iniciando limpeza física de jobs do lote cancelado {batch_id} no Redis...")
    redis_client = ctx['redis']
    pattern = f"arq:job:job:{batch_id}:*"
    
    count = 0
    cursor = 0
    try:
        while True:
            # Varredura não bloqueante (SCAN) com contagem de 200 chaves por bloco
            cursor_res = await redis_client.scan(cursor=cursor, match=pattern, count=200)
            cursor = int(cursor_res[0])
            keys = cursor_res[1]
            
            if keys:
                # Converte bytes em strings se necessário
                keys_str = [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
                # Deleta em bloco no Redis
                await redis_client.delete(*keys_str)
                count += len(keys_str)
                
                # Pequeno delay de 10ms para dispersar carga no Redis
                await asyncio.sleep(0.01)
                
            if cursor == 0:
                break
                
        print(f"🧹 Limpeza física concluída: {count} chaves de agendamentos removidas do Redis para o lote {batch_id}.")
    except Exception as e:
        print(f"⚠️ Erro durante a varredura e limpeza de jobs cancelados no Redis: {e}")