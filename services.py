import asyncio
import httpx
import os
import random
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
            
            retell_payload = {
                "from_number": os.getenv("RETELL_FROM_NUMBER", "iatizeia"),
                "to_number": p.get("numero"),
                "override_agent_id": p.get("agent_id"),
                "metadata": {
                    "workflow_execution_id": execution_id,
                    "workflow_name": workflow_name
                },
                "retell_llm_dynamic_variables": {
                    "customer_name": p.get("nome"),
                    "prompt": fd.get("agent_prompt"),
                    "now": get_utc_now(), # ISO UTC para Retell
                    "contexto": p.get("contexto") or f"Empresa: {p.get('empresa')}\nSegmento: {p.get('segmento')}",
                    "numero_do_lead": p.get("numero"),
                    "empresa": p.get("empresa"),
                    "segmento": p.get("segmento"),
                    "email": p.get("email")
                }
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=retell_payload, headers=headers, timeout=15.0)
            
            if response.status_code not in [200, 201]:
                raise Exception(f"Erro Retell AI ({response.status_code}): {response.text}")
                
            return response.json()

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