import os
from dotenv import load_dotenv
from arq.connections import RedisSettings

# Importa as rotinas que serão processadas em background pelos workers
from services import schedule_execution_node, continue_workflow_execution

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
if "@" in REDIS_URL:
    protocol_user_pass, host_port = REDIS_URL.rsplit("@", 1)
    protocol_user_pass = protocol_user_pass.replace("#", "%23")
    REDIS_URL = f"{protocol_user_pass}@{host_port}"

class WorkerSettings:
    """Configurações do Worker ARQ (Async Redis Queue).
    Inicie o worker no terminal rodando: 'arq worker.WorkerSettings'
    """
    functions = [
        schedule_execution_node, 
        continue_workflow_execution
    ]
    
    # URL de Conexão com o Redis
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    
    # Número de jobs correntes (Ajustar baseado na capacidade de I/O)
    max_jobs = 50
    
    # Garante tentativas em caso de restarts globais (job_timeout)
    job_timeout = 300
