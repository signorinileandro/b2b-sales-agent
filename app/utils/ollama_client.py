import ollama
import os

def ollama_chat(messages, model=os.getenv("OLLAMA_MODEL", "qwen3:8b")):
    """
    Envía una conversación a Ollama y retorna la respuesta.
    messages: lista de dicts [{"role": "system"/"user"/"assistant", "content": "..."}]
    """
    # ✅ CONFIGURAR CLIENT PARA DOCKER
    client = ollama.Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    
    try:
        response = client.chat(model=model, messages=messages)
        return response['message']['content']
    except Exception as e:
        print(f"❌ Error connecting to Ollama: {e}")
        return "Lo siento, el servicio de IA no está disponible en este momento."