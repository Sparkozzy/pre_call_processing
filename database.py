import os
from dotenv import load_dotenv
from supabase import create_client, create_async_client, Client, AsyncClient

# Carrega as variáveis do arquivo .env
load_dotenv()

# Pegamos a URL e aplicamos strip() para remover espaços, aspas ou quebras de linha acidentais
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().strip('"').strip("'").rstrip('/')
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip().strip('"').strip("'")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("LOG ERROR: SUPABASE_URL ou SUPABASE_KEY não configurados no .env")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    supabase_async: AsyncClient = create_async_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"LOG: Clientes Supabase (Sync e Async) configurados (URL: {SUPABASE_URL})")
except Exception as e:
    print(f"LOG ERROR: Erro ao configurar Supabase: {e}")