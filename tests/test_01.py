import requests
import random
import string
from datetime import datetime, timezone, timedelta

fuso_brasilia = timezone(timedelta(hours=-3))

def generate_random_id(length=15):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))

test_payload = {
    "workflow_name": "envia_ligacao",
    "execution_id": 'test_' + generate_random_id(),
    "numero": "+5548996027108",
    "nome": "Ryan",
    "empresa": "Mindflow",
    "email": "ryanferrari1234@gmail.com",
    "agent_id": "agent_bddfff26edcbd224f76e6a9bb2",
    "Prompt_id": "22",
    "quando_ligar": datetime.now(fuso_brasilia).isoformat()
}

url = "http://127.0.0.1:8000/webhook"

try:
    print(f"Enviando payload para {url}...")
    response = requests.post(url, json=test_payload)
    print(f"Status Code: {response.status_code}")
    print(f"Resposta FastAPI: {response.json()}")
except Exception as e:
    print(f"❌ Erro ao testar a integração: {e}")