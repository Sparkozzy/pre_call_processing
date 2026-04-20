import time
from datetime import datetime, timezone
import zoneinfo
from database import supabase
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# Configuração de Fusos Horários e Agendador
UTC = timezone.utc
BR_TIMEZONE = zoneinfo.ZoneInfo("America/Sao_Paulo")
PYTZ_BR = pytz.timezone("America/Sao_Paulo")

# Inicializa o Agendador em Background (RAM)
scheduler = BackgroundScheduler(timezone=PYTZ_BR)
scheduler.start()

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
    text = re.sub(r'_(.*?)_', r'\1', text)
    # Headers
    text = re.sub(r'#+\s*(.*)', r'\1', text)
    # Bullets
    text = re.sub(r'^[\s\t]*[-\*\+]\s+', '', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    return text.strip()

def run_step_with_retry(execution_id: str, workflow_name: str, oqf: str, step_input: dict, max_retries: int, worker_func=None) -> dict:
    """
    Executa a lógica de uma etapa específica seguindo a convenção MindFlow.
    Step Name: {workflow_name}_{oqf}
    """
    step_full_name = f"{workflow_name}_{oqf}"
    attempt = 1
    
    while attempt <= max_retries:
        started_at = get_utc_now()
        br_started_at = get_br_now().isoformat()
        
        try:
            if worker_func:
                # Executa a lógica real se fornecida
                logic_output = worker_func(step_input)
                output = {
                    "status": "ok",
                    "version": "v2-real-logic",
                    "data": logic_output,
                    "processed_at_utc": started_at,
                    "internal_br_log": br_started_at
                }
            else:
                # Fallback para simulação caso não haja função lógica
                time.sleep(0.5)
                output = {
                    "status": "ok",
                    "version": "v2-real-logic",
                    "step": step_full_name, 
                    "processed_at_utc": started_at,
                    "internal_br_log": br_started_at
                }
            
            # Registro de Detalhe (Sucesso)
            supabase.table('workflow_step_executions').insert({
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
            # Registro de Detalhe (Falha imutável)
            supabase.table('workflow_step_executions').insert({
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
            
            attempt += 1
            time.sleep(1)

def schedule_execution_node(execution_id: str, payload: dict):
    """
    Nó de Agendamento (EDW):
    Decide se o workflow continua agora ou se é agendado no APScheduler.
    Regista a ação em workflow_step_executions.
    """
    workflow_name = payload.get('workflow_name', 'envia_ligacao')
    quando_ligar_raw = payload.get('quando_ligar')
    
    if not quando_ligar_raw:
        # Se não houver data, assume execução imediata
        continue_workflow_execution(execution_id, payload)
        return

    # 1. Comparação de datas
    data_agendada = parse_iso_to_br(quando_ligar_raw)
    agora = get_br_now()
    
    step_input = {"quando_ligar_original": quando_ligar_raw, "agora_br": agora.isoformat()}
    
    if data_agendada <= agora:
        # 2a. Execução Imediata
        output = {"decisao": "EXECUCAO_IMEDIATA", "motivo": "Data no passado ou agora"}
        run_step_with_retry(execution_id, workflow_name, 'agendamento_ram', step_input, 1) # Registra o nó
        continue_workflow_execution(execution_id, payload)
    else:
        # 2b. Agendamento em RAM (APScheduler)
        output = {"decisao": "AGENDADO_RAM", "agendado_para": data_agendada.isoformat()}
        
        # Agenda a execução futura em RAM
        scheduler.add_job(
            continue_workflow_execution,
            'date',
            run_date=data_agendada,
            args=[execution_id, payload]
        )
        
        # Garante a rastreabilidade do agendamento (Nó concluído)
        run_step_with_retry(execution_id, workflow_name, 'agendamento_ram', {**step_input, **output}, 1)

def continue_workflow_execution(execution_id: str, payload: dict):
    """
    Continuação do Workflow:
    Executada no momento exato (seja agora ou via scheduler).
    """
    workflow_name = payload.get('workflow_name', 'envia_ligacao')
    
    try:
        # Atualiza status para RUNNING caso tenha vindo do agendador
        supabase.table('workflow_executions').update({'status': 'RUNNING'}).eq('id', execution_id).execute()

        # Step 2: Buscar Prompt no Supabase (Lógica Real)
        prompt_id_raw = payload.get("Prompt_id") or payload.get("prompt_id")
        if not prompt_id_raw:
            raise ValueError("Prompt_id não encontrado para busca.")

        def fetch_prompt_logic(input_data):
            p_id = input_data.get('prompt_id')
            try:
                # Tenta buscar por ID numérico
                id_int = int(p_id)
                res = supabase.table('Prompts').select('*').eq('id', id_int).execute()
            except (ValueError, TypeError):
                # Busca por Nome (Pormpt_Name com erro de digitação da tabela)
                res = supabase.table('Prompts').select('*').eq('Pormpt_Name', p_id).execute()
            
            if not res.data:
                raise ValueError(f"Prompt '{p_id}' não encontrado na tabela Prompts.")
            
            # Prioriza Ligação/txt pois o workflow é pre_call
            return res.data[0].get('Ligação/txt') or res.data[0].get('Prompt_Text')

        prompt_node_res = run_step_with_retry(
            execution_id, 
            workflow_name, 
            'fetch_prompt', 
            {"prompt_id": prompt_id_raw}, 
            3,
            worker_func=fetch_prompt_logic
        )
        prompt_content = prompt_node_res['data']

        # Step 3: Formatação (Nó de transformação)
        def format_payload_logic(input_data):
            raw_prompt = input_data.get('raw_prompt', '')
            payload_ref = input_data.get('payload_ref', {})
            
            # Limpeza inicial
            clean_text = raw_prompt.replace('\r', '') # Remove carriage returns
            clean_text = strip_markdown(clean_text)
            
            # Substituição de Variáveis de Contexto (Suporta {{var}} e {var})
            mapping = {
                "customer_name": payload_ref.get("nome") or payload_ref.get("customer_name") or "Lead",
                "empresa": payload_ref.get("empresa") or "Empresa",
                "segmento": payload_ref.get("segmento") or "Segmento",
                "email": payload_ref.get("email", ""),
                "numero_do_lead": payload_ref.get("numero") or payload_ref.get("numero_do_lead", ""),
                "now": get_br_now().strftime("%d/%m/%Y %H:%M"),
                "data_atual_iso": get_utc_now()
            }
            
            for key, val in mapping.items():
                s_val = str(val)
                # Double braces
                clean_text = clean_text.replace("{{" + key + "}}", s_val)
                clean_text = clean_text.replace("{{ " + key + " }}", s_val)
                # Single braces (Fallback para prompt 24)
                clean_text = clean_text.replace("{" + key + "}", s_val)
                clean_text = clean_text.replace("{ " + key + " }", s_val)
            
            return {
                "agent_prompt": clean_text,
                "applied_mapping": mapping,
                "metadata": {
                    "original_prompt_id": payload_ref.get("Prompt_id")
                }
            }

        formatted_res = run_step_with_retry(
            execution_id, 
            workflow_name, 
            'format_payload', 
            {"raw_prompt": prompt_content, "payload_ref": payload}, 
            1,
            worker_func=format_payload_logic
        )
        final_payload = formatted_res['data']

        # Finaliza Mestre
        supabase.table('workflow_executions').update({
            'status': 'SUCCESS',
            'output_data': {"status": "Workflow finalizado com sucesso", "execution_type": "Scheduled/Immediate"},
            'completed_at': get_utc_now()
        }).eq('id', execution_id).execute()
        
        print(f"Workflow {workflow_name} ({execution_id}) processado pós-agendamento.")

    except Exception as e:
        supabase.table('workflow_executions').update({
            'status': 'FAILED',
            'error_details': str(e),
            'completed_at': get_utc_now()
        }).eq('id', execution_id).execute()
        print(f"Falha na continuação do workflow {execution_id}: {e}")