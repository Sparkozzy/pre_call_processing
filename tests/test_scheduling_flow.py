import time
import requests
from database import supabase
from datetime import datetime, timedelta, timezone

def test_workflow_scheduling_real_server():
    # 1. Preparar Payload com data futura (+10 segundos)
    # Usando UTC para garantir consistência no envio
    future_date = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    
    payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": f"REAL-TEST-{int(time.time())}",
        "prompt_id": "22",
        "numero": "+5548991027108",
        "nome": "Ryan Real Test",
        "email": "ryan@real.com",
        "agent_id": "agent_test_real",
        "quando_ligar": future_date
    }

    url = "http://127.0.0.1:8080/webhook"
    print(f"\nLOG: Disparando Webhook para servidor local em: {url}")
    print(f"LOG: Agendado para: {future_date}")
    
    # 2. Enviar Webhook Real
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 202:
            print(f"LOG ERROR: Servidor retornou erro {response.status_code}: {response.text}")
            return
            
        data = response.json()
        db_id = data["execution_db_id"]
        print(f"LOG: Webhook aceito pelo servidor. ID no banco: {db_id}")

        # 3. Verificar Registro Mestre no Supabase (usando o cliente do banco diretamente)
        time.sleep(2)
        master = supabase.table('workflow_executions').select("*").eq('id', db_id).execute()
        if master.data:
            print(f"LOG: Registro Mestre confirmado no banco. Status: {master.data[0]['status']}")
        else:
            print("LOG ERROR: Registro não encontrado no Supabase.")

        # 4. Verificar Nó de Agendamento (Step)
        steps = supabase.table('workflow_step_executions').select("*").eq('execution_id', db_id).execute()
        scheduling_step = [s for s in steps.data if 'agendamento_ram' in s['step_name']]
        if scheduling_step:
            print(f"LOG: No 'agendamento_ram' registrado com SUCESSO via Servidor Real.")
        else:
            print("LOG: Aguardando registro do nó de agendamento...")

        print("\nLOG: Aguardando execução do agendador no servidor (12 segundos)...")
        time.sleep(12)

        # 5. Verificar se o servidor finalizou o workflow
        master_final = supabase.table('workflow_executions').select("*").eq('id', db_id).execute()
        print(f"🏁 Status final no Supabase: {master_final.data[0]['status']}")
        
        if master_final.data[0]['status'] == 'SUCCESS':
            print("\n✨ TESTE REAL CONCLUÍDO COM SUCESSO! ✨")
        else:
            print(f"\n❌ O workflow ainda está como: {master_final.data[0]['status']}")

    except Exception as e:
        print(f"\nLOG ERROR: Erro de conexão com o servidor local: {e}")
        print("DICA: Certifique-se de que o uvicorn está rodando na porta 8080.")

if __name__ == "__main__":
    test_workflow_scheduling_real_server()
