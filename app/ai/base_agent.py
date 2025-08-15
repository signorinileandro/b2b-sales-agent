import os
import time
import json
from typing import List
from datetime import datetime
import google.generativeai as genai
from ..utils.logger import log
from ..utils.ollama_client import ollama_chat

class BaseAgent:
    """
    Clase base para todos los agentes de IA.
    Maneja la rotación de API keys y la cascada de modelos de Gemini.
    """
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.api_keys = self._load_api_keys()
        self.key_retry_delays = {}
        
        # ✅ CASCADA DE MODELOS: De más potente a más rápido/con más cuota
        self.model_cascade = [
            'gemini-1.5-pro-latest',
            'gemini-1.5-flash-latest',
        ]
        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Inicializar con la primera key y el primer modelo
        self.current_key_index = 0
        self.current_model_index = 0
        self._configure_gemini()

        log(f"🤖 {self.agent_name} inicializado con {len(self.api_keys)} API keys y {len(self.model_cascade)} modelos.")

    def _load_api_keys(self) -> List[str]:
        """Carga todas las API keys disponibles desde el .env"""
        keys = []
        for i in range(1, 10):
            key = os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                keys.append(key)
        return keys

    def _configure_gemini(self):
        """Configura Gemini con la API key y el modelo actual."""
        if self.current_key_index < len(self.api_keys):
            current_key = self.api_keys[self.current_key_index]
            genai.configure(api_key=current_key)
            
            model_name = self.model_cascade[self.current_model_index]
            self.model = genai.GenerativeModel(model_name)
            log(f"🔧 {self.agent_name} configurado: Key #{self.current_key_index + 1}, Modelo: {model_name}")

    def _switch_to_next_model(self):
        """Cambia al siguiente modelo en la cascada."""
        self.current_model_index = (self.current_model_index + 1)
        if self.current_model_index >= len(self.model_cascade):
            # Si se acabaron los modelos, cambia de key y resetea los modelos
            self.current_model_index = 0
            self._switch_to_next_key()
        else:
            self._configure_gemini()

    def _switch_to_next_key(self):
        """Cambia a la siguiente API key y resetea al primer modelo."""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self.current_model_index = 0
        self._configure_gemini()

    async def _make_gemini_request_with_fallback(self, prompt: str, **kwargs):
        """
        Hace petición a Ollama con fallback automático entre modelos y API keys.
        """
        initial_key_index = self.current_key_index
        
        while True:
            key_id = f"key_{self.current_key_index}"
            model_name = self.model_cascade[self.current_model_index]
            
            # Verificar si la key está en cooldown
            if key_id in self.key_retry_delays and time.time() < self.key_retry_delays[key_id]:
                log(f"⏰ {self.agent_name}: Key #{self.current_key_index + 1} en cooldown. Cambiando de key.")
                self._switch_to_next_key()
                if self.current_key_index == initial_key_index: # Si dimos toda la vuelta
                    raise Exception("Todas las API keys están en cooldown.")
                continue

            try:
                log(f"🔍 {self.agent_name}: Intentando con Key #{self.current_key_index + 1} y Modelo '{model_name}'")
                response = await self.model.generate_content_async(prompt, **kwargs)
                return response

            except Exception as e:
                error_str = str(e).lower()
                log(f"❌ {self.agent_name}: Error con Key #{self.current_key_index + 1} y Modelo '{model_name}': {error_str[:150]}")

                if "quota" in error_str or "429" in error_str:
                    # El error de cuota puede ser por modelo o por key.
                    # Asumimos que es por modelo y probamos el siguiente.
                    log(f"📉 Cuota agotada para '{model_name}'. Cambiando al siguiente modelo.")
                    self._switch_to_next_model()
                elif "api key not valid" in error_str:
                    log(f"🔑 Key #{self.current_key_index + 1} inválida. Poniendo en cooldown y cambiando.")
                    self.key_retry_delays[key_id] = time.time() + 86400 # Cooldown de 24h
                    self._switch_to_next_key()
                else:
                    # Otro tipo de error, probamos el siguiente modelo
                    log(f"🔄 Error general. Cambiando al siguiente modelo.")
                    self._switch_to_next_model()

                # Si después de cambiar, volvemos al punto de partida, significa que probamos todo.
                if self.current_key_index == initial_key_index and self.current_model_index == 0:
                    raise Exception(f"{self.agent_name}: Todas las combinaciones de keys y modelos han fallado.")

    def call_ollama(self, messages, model="qwen3:8b"):
        return ollama_chat(messages, model=model)
    
    def _extract_json_from_response(self, response_text: str) -> str:
        """Extrae JSON de la respuesta de Ollama que puede contener texto adicional"""
        
        # ✅ CASO 1: Respuesta directa es JSON
        if response_text.strip().startswith('{') and response_text.strip().endswith('}'):
            return response_text.strip()
        
        # ✅ CASO 2: JSON dentro de markdown
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            if end != -1:
                return response_text[start:end].strip()
        
        # ✅ CASO 3: JSON después de texto explicativo (como <think>)
        # Buscar el primer { y el último }
        first_brace = response_text.find('{')
        last_brace = response_text.rfind('}')
        
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_candidate = response_text[first_brace:last_brace + 1]
            
            # Verificar que sea JSON válido básico
            if json_candidate.count('{') == json_candidate.count('}'):
                return json_candidate
        
        # ✅ CASO 4: No se encontró JSON válido
        return None