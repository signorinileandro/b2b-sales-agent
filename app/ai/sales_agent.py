import os
import json
import google.generativeai as genai
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from ..database import SessionLocal
from .. import models
from .query_agent import query_agent  # ✅ IMPORTAR QUERY AGENT

class SalesAgent:
    def __init__(self):
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.model = None
        self._setup_current_key()
        self.context_memory: Dict[str, Dict] = {}
    
    def _load_api_keys(self):
        api_keys = []
        for i in range(1, 11):
            key = os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                api_keys.append(key.strip())
        
        if not api_keys:
            main_key = os.getenv("GOOGLE_API_KEY")
            if main_key:
                api_keys.append(main_key)
        
        return api_keys
    
    def _setup_current_key(self):
        if self.current_key_index < len(self.api_keys):
            try:
                current_key = self.api_keys[self.current_key_index]
                genai.configure(api_key=current_key)
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                print(f"💬 Sales Agent usando API Key #{self.current_key_index + 1}")
                return True
            except:
                return False
        return False
    
    def _try_next_key(self):
        self.current_key_index += 1
        return self._setup_current_key()

    async def process_message(self, user_id: str, message: str) -> str:
        """Procesa mensaje usando Query Agent + Sales Agent coordinados"""
        
        print(f"🤖 Procesando mensaje de {user_id}: {message}")
        
        # 1. Obtener contexto de conversación
        conversation = await self.get_or_create_conversation(user_id)
        context = self.get_or_create_context(user_id)
        
        # Guardar mensaje del usuario
        await self.save_message(conversation.id, "user", message)
        
        # 2. ✅ EXTRAER INTENCIÓN CON QUERY AGENT
        intent = await query_agent.extract_structured_intent(message, context)
        print(f"🎯 Intención detectada: {intent['intent_type']} (confianza: {intent['confidence']})")
        
        # 3. ✅ EJECUTAR OPERACIÓN EN BASE DE DATOS SI ES NECESARIO
        db_result = None
        if intent['intent_type'] in ['search_products', 'confirm_order', 'edit_order', 'ask_stock']:
            db_result = await query_agent.execute_database_operation(intent, user_id)
            print(f"🗄️ Operación DB: {db_result.get('operation', 'none')} - Éxito: {db_result.get('success', False)}")
            
            # Actualizar contexto con resultados
            if db_result.get('success') and db_result.get('data'):
                if 'products' in db_result['data']:
                    context['last_searched_products'] = db_result['data']['products']
                if intent['intent_type'] == 'confirm_order':
                    context['last_order_created'] = db_result['data']
                if intent['intent_type'] == 'edit_order':
                    context['last_order_edited'] = db_result['data']
        
        # 4. ✅ GENERAR RESPUESTA NATURAL CON SALES AGENT
        sales_response = await self._generate_natural_response(
            message, context, intent, db_result
        )
        
        # 5. Guardar respuesta y actualizar contexto
        await self.save_message(conversation.id, "assistant", sales_response, intent)
        
        context["conversation_history"].append({
            "user": message,
            "assistant": sales_response,
            "intent": intent['intent_type']
        })
        
        # Mantener solo últimas 5 interacciones
        if len(context["conversation_history"]) > 5:
            context["conversation_history"] = context["conversation_history"][-5:]
        
        return sales_response
    
    async def _generate_natural_response(self, user_message: str, context: Dict, intent: Dict, db_result: Dict = None) -> str:
        """Genera respuesta natural basada en intención y resultados de DB"""
        
        # Construir prompt con contexto específico
        prompt = f"""
Eres Ventix, un vendedor B2B argentino experto en textiles con 15 años de experiencia.

SITUACIÓN ACTUAL:
- Mensaje del cliente: "{user_message}"
- Intención detectada: {intent['intent_type']}
- Confianza: {intent['confidence']}

RESULTADOS DE OPERACIONES:
{json.dumps(db_result, indent=2, ensure_ascii=False) if db_result else "No se ejecutaron operaciones en BD"}

CONTEXTO DE CONVERSACIÓN:
- Productos vistos: {len(context.get('last_searched_products', []))} productos
- Historial reciente: {context.get('conversation_history', [])[-2:]}

INSTRUCCIONES SEGÚN INTENCIÓN:

Si intent_type es "search_products" y hay resultados:
- Presentar productos de forma entusiasta y personalizada
- Mencionar stock disponible y crear urgencia si es bajo
- Calcular precios con descuentos por volumen
- Hacer pregunta de seguimiento sobre cantidad

Si intent_type es "confirm_order" y fue exitoso:
- ¡EMPEZAR CON "¡PEDIDO CONFIRMADO!" en mayúsculas!
- Celebrar con entusiasmo
- Mostrar resumen completo
- Mencionar stock restante
- Preguntar si necesita algo más

Si intent_type es "edit_order" y fue exitoso:
- Confirmar cambio exitoso
- Mostrar cambio de cantidad y nuevo precio
- Celebrar la flexibilidad del servicio

Si hubo errores:
- Ser empático y ofrecer alternativas
- Explicar limitaciones (tiempo, stock) de forma natural
- Reconducir hacia solución viable

ESTILO:
- Natural y cálido, nunca robótico
- Usar expresiones argentinas: "¡Perfecto!", "Te tengo justo lo que necesitás"
- Mencionar beneficios: calidad, precio, servicio
- Crear urgencia sutil si stock es limitado
- Siempre hacer pregunta de seguimiento

Responde como Ventix respondería naturalmente:
"""

        try:
            response = await self._make_gemini_request(prompt)
            if response:
                return response
                
        except Exception as e:
            print(f"❌ Error generando respuesta: {e}")
        
        # Fallback response
        return "¡Hola! Estoy teniendo algunos problemas técnicos. ¿Podrías repetir tu consulta? Te ayudo enseguida."
    
    async def _make_gemini_request(self, prompt: str) -> str:
        """Hace request a Gemini con rotación de keys"""
        
        max_attempts = len(self.api_keys)
        
        for attempt in range(max_attempts):
            if not self.model:
                if not self._try_next_key():
                    break
            
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.8,  # Más creativo para respuestas naturales
                        max_output_tokens=800,
                    )
                )
                
                return response.text
                
            except Exception as e:
                error_str = str(e).lower()
                print(f"❌ Error con key #{self.current_key_index + 1}: {e}")
                
                if "429" in error_str or "quota" in error_str:
                    if not self._try_next_key():
                        break
                    continue
                else:
                    break
        
        return None
    
    # ... resto de métodos (get_or_create_conversation, save_message, get_or_create_context) ...
    
    async def get_or_create_conversation(self, user_phone: str):
        """Obtiene o crea conversación en la base de datos"""
        
        db = SessionLocal()
        try:
            conversation = db.query(models.Conversation).filter(
                models.Conversation.user_phone == user_phone,
                models.Conversation.status == "active"
            ).first()
            
            if not conversation:
                conversation = models.Conversation(
                    user_phone=user_phone,
                    status="active"
                )
                db.add(conversation)
                db.commit()
                db.refresh(conversation)
                print(f"💬 Nueva conversación: {conversation.id}")
            
            return conversation
        finally:
            db.close()
    
    async def save_message(self, conversation_id: int, message_type: str, content: str, intent_data: dict = None):
        """Guarda mensaje en la base de datos"""
        
        db = SessionLocal()
        try:
            products_json = None
            intent_detected = None
            
            if intent_data:
                intent_detected = intent_data.get("intent_type", "general")
                if intent_data.get("extracted_data", {}).get("products"):
                    products_json = json.dumps(intent_data["extracted_data"]["products"][:3])
            
            message = models.ConversationMessage(
                conversation_id=conversation_id,
                message_type=message_type,
                content=content,
                products_shown=products_json,
                intent_detected=intent_detected
            )
            
            db.add(message)
            db.commit()
            print(f"💾 Mensaje guardado: {message_type}")
            
        except Exception as e:
            print(f"❌ Error guardando mensaje: {e}")
        finally:
            db.close()
    
    def get_or_create_context(self, user_id: str) -> Dict:
        """Obtiene o crea contexto de conversación"""
        if user_id not in self.context_memory:
            self.context_memory[user_id] = {
                "conversation_history": [],
                "last_searched_products": [],
                "last_search_query": "",
                "last_order_created": None,
                "last_order_edited": None
            }
        return self.context_memory[user_id]

# Instancia global
sales_agent = SalesAgent()