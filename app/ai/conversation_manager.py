import google.generativeai as genai
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models
import json
import os
from dotenv import load_dotenv
import time
from .stock_agent import stock_agent
from .order_agent import order_agent
from .modify_agent import modify_agent
from .sales_agent import sales_agent

# Cargar variables de entorno
load_dotenv()

class ConversationManager:
    def __init__(self):
        self.memory_cache = {}  # Cache en memoria por número de teléfono
        
        # ✅ SISTEMA DE MÚLTIPLES API KEYS CON ROTACIÓN
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.key_retry_delays = {}  # Para tracking de delays por key
        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Configurar Gemini con la primera key válida
        self._configure_gemini()
        
        print(f"🔑 ConversationManager inicializado con {len(self.api_keys)} API keys")

    
    def _load_api_keys(self) -> List[str]:
        """Carga todas las API keys disponibles desde el .env"""
        api_keys = []
        
        # Buscar todas las keys que sigan el patrón GOOGLE_API_KEY_X
        for i in range(1, 10):  # Buscar hasta GOOGLE_API_KEY_9
            key = os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                api_keys.append(key)
                print(f"🔑 API Key #{i} cargada: {key[:10]}...")
        
        # También buscar la key genérica por compatibilidad
        generic_key = os.getenv("GEMINI_API_KEY")
        if generic_key and generic_key not in api_keys:
            api_keys.append(generic_key)
            print(f"🔑 API Key genérica cargada: {generic_key[:10]}...")
        
        return api_keys
    
    def _configure_gemini(self):
        """Configura Gemini con la API key actual"""
        current_key = self.api_keys[self.current_key_index]
        genai.configure(api_key=current_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        print(f"🔧 Gemini configurado con API key #{self.current_key_index + 1}")
    
    def _switch_to_next_key(self):
        """Cambia a la siguiente API key disponible"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._configure_gemini()
        print(f"🔄 Cambiado a API key #{self.current_key_index + 1}")
    
    async def _make_gemini_request_with_fallback(self, prompt: str, **kwargs):
        """Hace petición a Gemini con fallback automático entre API keys"""
        
        max_retries = len(self.api_keys)  # Intentar con todas las keys
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                current_key_num = self.current_key_index + 1
                print(f"🔍 Query Agent usando API Key #{current_key_num}")
                
                # Verificar si esta key tiene delay de retry
                key_id = f"key_{self.current_key_index}"
                if key_id in self.key_retry_delays:
                    retry_time = self.key_retry_delays[key_id]
                    if time.time() < retry_time:
                        print(f"⏰ API Key #{current_key_num} en cooldown hasta {datetime.fromtimestamp(retry_time)}")
                        self._switch_to_next_key()
                        retry_count += 1
                        continue
                
                # Intentar la petición
                response = self.model.generate_content(prompt, **kwargs)
                
                # Si llegamos aquí, la petición fue exitosa
                # Limpiar cualquier delay previo para esta key
                if key_id in self.key_retry_delays:
                    del self.key_retry_delays[key_id]
                
                return response
                
            except Exception as e:
                error_str = str(e).lower()
                print(f"❌ Error con API key #{current_key_num}: {e}")
                
                # Verificar si es error de cuota
                if "quota" in error_str or "exceeded" in error_str or "429" in error_str:
                    print(f"🚫 API Key #{current_key_num} agotó su cuota")
                    
                    # Poner esta key en cooldown por 1 hora
                    self.key_retry_delays[key_id] = time.time() + 3600
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                elif "rate limit" in error_str or "rate_limit" in error_str:
                    print(f"⏳ API Key #{current_key_num} tiene rate limiting")
                    
                    # Cooldown más corto para rate limiting (5 minutos)
                    self.key_retry_delays[key_id] = time.time() + 300
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                else:
                    # Error no relacionado con cuota, intentar una vez más con la siguiente key
                    print(f"🔄 Error general, intentando con siguiente key")
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
        
        # Si llegamos aquí, todas las keys fallaron
        raise Exception(f"Todas las API keys ({len(self.api_keys)}) han fallado o están en cooldown")

    async def process_message(self, phone: str, message: str) -> str:
        """Punto de entrada principal para procesar mensajes"""
        try:
            print(f"🤖 ConversationManager procesando: {phone} - {message}")
            
            # 1. Obtener conversación completa
            conversation = await self.get_full_conversation(phone)
            
            # 2. Analizar intención con contexto completo (ahora retorna Dict)
            intent_analysis = await self.analyze_intent_with_context(message, conversation)  # ✅ CAMBIO
            intent = intent_analysis["intent"]  # ✅ EXTRAER intent
            
            # 3. Derivar al agente especializado
            response = await self.dispatch_to_specialized_agent(intent, message, conversation)
            
            # ✅ AGREGAR reasoning al response si es modo debug
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                response += f"\n\n🤖 **DEBUG INFO:**\n"
                response += f"• Intención: {intent_analysis['intent']}\n"
                response += f"• Confianza: {intent_analysis['confidence']:.1f}\n"
                response += f"• Método: {intent_analysis['method']}\n" 
                response += f"• Reasoning: {intent_analysis['reasoning']}"
            
            # 4. Actualizar conversación (pasar reasoning también)
            await self.update_conversation(phone, message, response, intent, intent_analysis.get('reasoning'))  # ✅ CAMBIO
            
            return response
            
        except Exception as e:
            print(f"❌ Error en ConversationManager: {e}")
            return "Disculpa, tuve un problema técnico. ¿Podrías repetir tu consulta?"
    
    async def get_full_conversation(self, phone: str) -> Dict:
        """Obtiene conversación completa de BD + memoria"""
        
        # Verificar cache en memoria primero
        if phone in self.memory_cache:
            cached_conversation = self.memory_cache[phone]
            # Si el cache es reciente (menos de 10 minutos), usarlo
            if (datetime.now() - cached_conversation.get('last_updated', datetime.now())).seconds < 600:
                print(f"💾 Usando conversación en memoria para {phone}")
                return cached_conversation
        
        # Obtener de base de datos
        db = SessionLocal()
        try:
            # Buscar conversación existente
            conversation_record = db.query(models.Conversation).filter(
                models.Conversation.user_phone == phone
            ).order_by(models.Conversation.created_at.desc()).first()
            
            # Buscar mensajes recientes (últimos 50)
            recent_messages = db.query(models.Message).filter(
                models.Message.user_phone == phone
            ).order_by(models.Message.timestamp.desc()).limit(50).all()
            
            # Buscar productos vistos recientemente (última hora)
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent_searches = db.query(models.Message).filter(
                models.Message.user_phone == phone,
                models.Message.timestamp >= one_hour_ago,
                models.Message.role == 'assistant'
            ).limit(5).all()
            
            # Buscar pedidos recientes (últimos 7 días)  
            week_ago = datetime.now() - timedelta(days=7)
            recent_orders = db.query(models.Order).filter(
                models.Order.user_phone == phone,
                models.Order.created_at >= week_ago
            ).order_by(models.Order.created_at.desc()).limit(10).all()
            
            # Construir objeto de conversación
            conversation = {
                'phone': phone,
                'conversation_id': conversation_record.id if conversation_record else None,
                'messages': [
                    {
                        'role': msg.role,
                        'content': msg.content,
                        'timestamp': msg.timestamp.isoformat(),
                        'intent': getattr(msg, 'intent', None)
                    } 
                    for msg in reversed(recent_messages)  # Orden cronológico
                ],
                'recent_searches': [
                    {
                        'content': msg.content,
                        'timestamp': msg.timestamp.isoformat()
                    }
                    for msg in recent_searches
                ],
                'recent_orders': [
                    {
                        'id': order.id,
                        'product_id': order.product_id,
                        'quantity': order.qty,
                        'status': order.status,
                        'created_at': order.created_at.isoformat(),
                        'buyer': order.buyer
                    }
                    for order in recent_orders
                ],
                'last_updated': datetime.now()
            }
            
            # Guardar en cache de memoria
            self.memory_cache[phone] = conversation
            
            print(f"📚 Conversación cargada para {phone}: {len(conversation['messages'])} mensajes, {len(recent_orders)} pedidos")
            
            return conversation
            
        except Exception as e:
            print(f"❌ Error obteniendo conversación: {e}")
            return {
                'phone': phone,
                'conversation_id': None,
                'messages': [],
                'recent_searches': [],
                'recent_orders': [],
                'last_updated': datetime.now()
            }
        finally:
            db.close()
    
    async def analyze_intent_with_context(self, message: str, conversation: Dict) -> Dict:  # ✅ CAMBIO: retorna Dict en lugar de str
        """Analiza intención del usuario con contexto completo Y reasoning"""
        
        try:
            # Crear prompt con contexto completo
            prompt = self.create_intent_analysis_prompt_with_reasoning(message, conversation)  # ✅ NUEVO método
            
            # ✅ USAR SISTEMA DE FALLBACK DE API KEYS
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=150,  # ✅ AUMENTAR para incluir reasoning
                )
            )
            
            # ✅ PARSEAR JSON RESPONSE
            response_text = response.text.strip()
            
            try:
                # Limpiar respuesta JSON si viene con markdown
                if response_text.startswith("```json"):
                    response_text = response_text[7:-3]
                elif response_text.startswith("```"):
                    response_text = response_text[3:-3]
                
                parsed_response = json.loads(response_text)
                
                intent = parsed_response.get("intent", "general_chat")
                reasoning = parsed_response.get("reasoning", "No reasoning provided")
                confidence = parsed_response.get("confidence", 0.8)
                
            except json.JSONDecodeError:
                # Si no es JSON válido, extraer solo la intención como antes
                intent = response_text.lower().strip()
                reasoning = f"Respuesta de Gemini no fue JSON válido: {response_text}"
                confidence = 0.5
            
            # ✅ VALIDAR intenciones
            valid_intents = ['check_stock', 'create_order', 'modify_order', 'sales_advice', 'general_chat']
            
            if intent not in valid_intents:
                # Usar análisis de fallback
                fallback_result = self._analyze_intent_fallback_with_reasoning(message, conversation)
                intent = fallback_result["intent"]
                reasoning = f"Fallback usado. Original: {reasoning}. Fallback: {fallback_result['reasoning']}"
                confidence = 0.6
            
            result = {
                "intent": intent,
                "reasoning": reasoning,
                "confidence": confidence,
                "method": "gemini" if "Fallback" not in reasoning else "fallback"
            }
            
            print(f"🎯 Intención detectada: {intent} (confianza: {confidence:.1f})")
            print(f"🧠 Reasoning: {reasoning}")
            
            return result
            
        except Exception as e:
            print(f"❌ Error analizando intención: {e}")
            fallback_result = self._analyze_intent_fallback_with_reasoning(message, conversation)
            fallback_result["reasoning"] = f"Error en Gemini: {str(e)}. {fallback_result['reasoning']}"
            return fallback_result

    # ✅ NUEVO método para prompt con reasoning:
    def create_intent_analysis_prompt_with_reasoning(self, message: str, conversation: Dict) -> str:
        """Crea prompt para análisis de intención CON reasoning"""
        
        # Formatear mensajes recientes para contexto
        recent_messages = ""
        for msg in conversation.get('messages', [])[-5:]:  # Últimos 5 mensajes
            role = "Usuario" if msg['role'] == 'user' else "Bot"
            recent_messages += f"{role}: {msg['content']}\n"
        
        # Información de pedidos recientes
        recent_orders_info = ""
        if conversation.get('recent_orders'):
            recent_orders_info = f"Pedidos recientes: {len(conversation['recent_orders'])} en la última semana"
        
        return f"""Eres un dispatcher inteligente para un sistema de ventas B2B textil.

CONVERSACIÓN RECIENTE:
{recent_messages}

ÚLTIMO MENSAJE DEL USUARIO: "{message}"

{recent_orders_info}

Analiza la intención y responde SOLO con JSON válido:

{{
    "intent": "check_stock|create_order|modify_order|sales_advice|general_chat",
    "reasoning": "explicación_detallada_de_por_qué_elegiste_esta_intención",
    "confidence": 0.0-1.0
}}

INTENCIONES DISPONIBLES:

check_stock - Si pregunta por:
- Stock disponible, inventario, cantidades
- Colores, talles, tipos de productos  
- "¿qué tenés?", "cuánto stock?", "qué colores hay?"

create_order - Si quiere hacer pedido:
- "quiero X unidades", "haceme el pedido", "necesito comprar"
- Especifica cantidad + producto
- "generame el pedido", "confirmar pedido"

modify_order - Si quiere cambiar pedido existente:
- "cambiar cantidad", "modificar pedido", "cancelar"
- Se refiere a pedidos ya hechos

sales_advice - Si pide consejos/recomendaciones:
- "qué me recomendás?", "para qué sirve?", "mejor opción"
- Consultas sobre uso, calidad, aplicación

general_chat - Para saludos, charla general:
- "hola", "gracias", "cómo estás", "chau"
- Conversación social

IMPORTANTE: 
- Considera el CONTEXTO completo, no solo el último mensaje
- Si después de mostrar productos dice "quiero X cantidad" = create_order
- Si pregunta por stock específico después de ver productos = check_stock
- En "reasoning" explica claramente por qué elegiste esa intención
- Sé específico sobre qué palabras clave o contexto influyó en tu decisión

Ejemplo de respuesta:
{{
    "intent": "check_stock",
    "reasoning": "El usuario pregunta 'qué colores tenés' lo cual es una consulta específica sobre variantes disponibles en inventario. La palabra 'tenés' indica consulta de disponibilidad.",
    "confidence": 0.9
}}"""

    # ✅ NUEVO método de fallback con reasoning:
    def _analyze_intent_fallback_with_reasoning(self, message: str, conversation: Dict) -> Dict:
        """Análisis de intención con reasoning como fallback"""
        message_lower = message.lower()
        
        # Análisis contextual
        recent_messages = conversation.get('messages', [])[-3:]
        context_has_products = any('stock' in msg.get('content', '').lower() 
                                  for msg in recent_messages 
                                  if msg.get('role') == 'assistant')
        
        # Palabras clave más específicas
        stock_keywords = ['stock', 'cuanto', 'cuánto', 'tenés', 'disponible', 'colores', 'talles', 'qué hay']
        order_keywords = ['pedido', 'quiero', 'necesito', 'comprar', 'encargar', 'haceme']
        modify_keywords = ['cambiar', 'modificar', 'cancelar', 'editar']
        advice_keywords = ['recomendás', 'conviene', 'mejor', 'qué', 'para qué']
        
        # Detectar con prioridad contextual y generar reasoning
        if any(word in message_lower for word in modify_keywords) and conversation.get('recent_orders'):
            matched_words = [word for word in modify_keywords if word in message_lower]
            return {
                "intent": "modify_order",
                "reasoning": f"Palabras clave de modificación detectadas: {matched_words}. Usuario tiene pedidos recientes ({len(conversation.get('recent_orders', []))}) que puede modificar.",
                "confidence": 0.8
            }
        elif any(word in message_lower for word in order_keywords):
            matched_words = [word for word in order_keywords if word in message_lower]
            return {
                "intent": "create_order", 
                "reasoning": f"Palabras clave de pedido detectadas: {matched_words}. Indica intención de compra/crear pedido.",
                "confidence": 0.8
            }
        elif any(word in message_lower for word in stock_keywords):
            matched_words = [word for word in stock_keywords if word in message_lower]
            context_info = " Con contexto de productos mostrados." if context_has_products else ""
            return {
                "intent": "check_stock",
                "reasoning": f"Palabras clave de consulta de stock: {matched_words}.{context_info} Indica búsqueda de información de inventario.",
                "confidence": 0.8
            }
        elif any(word in message_lower for word in advice_keywords):
            matched_words = [word for word in advice_keywords if word in message_lower]
            return {
                "intent": "sales_advice",
                "reasoning": f"Palabras clave de asesoramiento: {matched_words}. Usuario busca consejos o recomendaciones comerciales.",
                "confidence": 0.7
            }
        else:
            return {
                "intent": "general_chat",
                "reasoning": f"No se detectaron palabras clave específicas en: '{message}'. Clasificado como conversación general/saludo.",
                "confidence": 0.6
            }

    async def dispatch_to_specialized_agent(self, intent: str, message: str, conversation: Dict) -> str:
        """Deriva al agente especializado según la intención"""
        
        try:
            print(f"🔀 Derivando a agente: {intent}")
            
            if intent == 'check_stock':
                # ✅ USAR STOCK AGENT REAL
                return await stock_agent.handle_stock_query(message, conversation)
                
            elif intent == 'create_order':
                # ✅ USAR ORDER AGENT REAL
                return await order_agent.handle_order_creation(message, conversation)
                
            elif intent == 'modify_order':
                # ✅ USAR MODIFY AGENT REAL
                return await modify_agent.handle_order_modification(message, conversation)
                
            elif intent == 'sales_advice':
                # ✅ USAR SALES AGENT REAL
                return await sales_agent.handle_sales_advice(message, conversation)
                
            else:  # general_chat
                return await self.handle_general_chat_temp(message, conversation)
                
        except Exception as e:
            print(f"❌ Error en dispatch: {e}")
            return "Disculpa, tuve un problema procesando tu consulta. ¿Podrías intentar de nuevo?"
    
    async def handle_general_chat_temp(self, message: str, conversation: Dict) -> str:
        """Manejo temporal de charla general"""
        current_key_num = self.current_key_index + 1
        return f"¡Hola! Soy Ventix, tu asistente de ventas textiles. ¿En qué puedo ayudarte hoy?\n\n⚙️ Sistema activo con API Key #{current_key_num}\n\n🎯 Puedo mostrarte productos, consultar stock o ayudarte a hacer pedidos."
    
    async def update_conversation(self, phone: str, user_message: str, bot_response: str, intent: str, reasoning: str = None):  # ✅ NUEVO parámetro
        """Actualiza la conversación en BD y memoria"""
        
        db = SessionLocal()
        try:
            # Buscar o crear conversación
            conversation_record = db.query(models.Conversation).filter(
                models.Conversation.user_phone == phone
            ).first()
            
            if not conversation_record:
                conversation_record = models.Conversation(
                    user_phone=phone,
                    created_at=datetime.now()
                )
                db.add(conversation_record)
                db.commit()
                db.refresh(conversation_record)
            
            # Guardar mensaje del usuario CON REASONING
            user_msg = models.Message(
                conversation_id=conversation_record.id,
                user_phone=phone,
                role='user',
                content=user_message,
                intent=intent,
                reasoning=reasoning,  # ✅ NUEVO campo (necesitarás agregarlo al modelo)
                timestamp=datetime.now()
            )
            db.add(user_msg)
            
            # Guardar respuesta del bot
            bot_msg = models.Message(
                conversation_id=conversation_record.id,
                user_phone=phone,
                role='assistant',
                content=bot_response,
                timestamp=datetime.now()
            )
            db.add(bot_msg)
            
            db.commit()
            
            # Actualizar cache en memoria
            if phone in self.memory_cache:
                self.memory_cache[phone]['messages'].extend([
                    {
                        'role': 'user',
                        'content': user_message,
                        'timestamp': datetime.now().isoformat(),
                        'intent': intent
                    },
                    {
                        'role': 'assistant', 
                        'content': bot_response,
                        'timestamp': datetime.now().isoformat(),
                        'intent': None
                    }
                ])
                self.memory_cache[phone]['last_updated'] = datetime.now()
            
            print(f"💾 Conversación actualizada para {phone}")
            
        except Exception as e:
            print(f"❌ Error actualizando conversación: {e}")
            db.rollback()
        finally:
            db.close()

# Instancia global
conversation_manager = ConversationManager()