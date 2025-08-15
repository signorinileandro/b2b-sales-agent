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
            
            # 2. Analizar intención con contexto completo (con fallback de API keys)
            intent = await self.analyze_intent_with_context(message, conversation)
            
            # 3. Derivar al agente especializado
            response = await self.dispatch_to_specialized_agent(intent, message, conversation)
            
            # 4. Actualizar conversación
            await self.update_conversation(phone, message, response, intent)
            
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
    
    async def analyze_intent_with_context(self, message: str, conversation: Dict) -> str:
        """Analiza intención del usuario con contexto completo"""
        
        try:
            # Crear prompt con contexto completo
            prompt = self.create_intent_analysis_prompt(message, conversation)
            
            # ✅ USAR SISTEMA DE FALLBACK DE API KEYS
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=50,
                )
            )
            
            intent = response.text.strip().lower()
            
            # ✅ MEJORAR validación de intenciones
            valid_intents = ['check_stock', 'create_order', 'modify_order', 'sales_advice', 'general_chat']
            
            if intent not in valid_intents:
                # Usar análisis más sofisticado
                intent = self._analyze_intent_fallback(message, conversation)
            
            print(f"🎯 Intención detectada: {intent} para mensaje: '{message}'")
            return intent
            
        except Exception as e:
            print(f"❌ Error analizando intención: {e}")
            return self._analyze_intent_fallback(message, conversation)

    def _analyze_intent_fallback(self, message: str, conversation: Dict) -> str:
        """Análisis de intención más robusto como fallback"""
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
        
        # Detectar con prioridad contextual
        if any(word in message_lower for word in modify_keywords) and conversation.get('recent_orders'):
            return 'modify_order'
        elif any(word in message_lower for word in order_keywords):
            return 'create_order'
        elif any(word in message_lower for word in stock_keywords):
            return 'check_stock'
        elif any(word in message_lower for word in advice_keywords):
            return 'sales_advice'
        else:
            return 'general_chat'
    
    def create_intent_analysis_prompt(self, message: str, conversation: Dict) -> str:
        """Crea prompt para análisis de intención"""
        
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

Analiza la intención y responde SOLO con UNA de estas opciones:

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

Respuesta (solo la intención):"""

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
    
    async def update_conversation(self, phone: str, user_message: str, bot_response: str, intent: str):
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
            
            # Guardar mensaje del usuario
            user_msg = models.Message(
                conversation_id=conversation_record.id,
                user_phone=phone,
                role='user',
                content=user_message,
                intent=intent,
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