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
from ..utils.logger import log
from .base_agent import BaseAgent


# Cargar variables de entorno
load_dotenv()

class ConversationManager(BaseAgent):
    def __init__(self):
        super().__init__(agent_name="ConversationManager")
        self.memory_cache = {}  # Cache en memoria por número de teléfono
        
        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Configurar Gemini con la primera key válida
        self._configure_gemini()

        log(f"🔑 ConversationManager inicializado con {len(self.api_keys)} API keys")


    async def process_message(self, phone: str, message: str) -> str:
        """Punto de entrada principal para procesar mensajes"""
        try:
            log(f"🤖 ConversationManager procesando: {phone} - {message}")
            
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
            log(f"❌ Error en ConversationManager: {e}")
            return "Disculpa, tuve un problema técnico. ¿Podrías repetir tu consulta?"
    
    async def get_full_conversation(self, phone: str) -> Dict:
        """Obtiene conversación completa de BD + memoria"""
        
        # Verificar cache en memoria primero
        if phone in self.memory_cache:
            cached_conversation = self.memory_cache[phone]
            # Si el cache es reciente (menos de 10 minutos), usarlo
            if (datetime.now() - cached_conversation.get('last_updated', datetime.now())).seconds < 600:
                log(f"💾 Usando conversación en memoria para {phone}")
                return cached_conversation
        
        # Obtener de base de datos
        db = SessionLocal()
        try:
            # Buscar conversación existente
            conversation_record = db.query(models.Conversation).filter(
                models.Conversation.user_phone == phone
            ).order_by(models.Conversation.created_at.desc()).first()
            
            # Buscar mensajes recientes (últimos 50)
            
            recent_messages = db.query(models.ConversationMessage).filter(
                models.ConversationMessage.user_phone == phone
            ).order_by(models.ConversationMessage.timestamp.desc()).limit(50).all()
            
            # Buscar productos vistos recientemente (última hora)
            one_hour_ago = datetime.now() - timedelta(hours=1)
            
            recent_searches = db.query(models.ConversationMessage).filter(
                models.ConversationMessage.user_phone == phone,
                models.ConversationMessage.timestamp >= one_hour_ago,
                models.ConversationMessage.role == 'assistant'
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
            
            log(f"📚 Conversación cargada para {phone}: {len(conversation['messages'])} mensajes, {len(recent_orders)} pedidos")
            
            return conversation
            
        except Exception as e:
            log(f"❌ Error obteniendo conversación: {e}")
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
            
            log(f"🎯 Intención detectada: {intent} (confianza: {confidence:.1f})")
            log(f"🧠 Reasoning: {reasoning}")
            
            return result
            
        except Exception as e:
            log(f"❌ Error analizando intención: {e}")
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
- Stock, inventario, cantidades, colores, talles, tipos de productos.
- "¿qué tenés?", "cuánto stock?", "qué colores hay?".
- ✅ **IMPORTANTE: Si dice "quiero comprar [producto]" SIN cantidad, es check_stock para iniciar la venta.**

create_order - Si quiere hacer pedido:
- "quiero X unidades", "necesito 50 de...", "haceme el pedido por 80".
- ✅ **IMPORTANTE: Debe especificar una CANTIDAD NUMÉRICA para ser create_order.**

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
- La diferencia clave entre check_stock y create_order es la **presencia de una cantidad**.
- "Quiero pantalones" -> check_stock.
- "Quiero 50 pantalones" -> create_order.
- En "reasoning" explica claramente por qué elegiste esa intención.
- Sé específico sobre qué palabras clave o contexto influyó en tu decisión.

Ejemplo de respuesta:
{{
    "intent": "check_stock",
    "reasoning": "El usuario dice 'quiero comprar pantalones'. Aunque usa 'comprar', no especifica cantidad, por lo que la intención es iniciar una consulta de venta, que corresponde a check_stock.",
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
        stock_keywords = ['stock', 'cuanto', 'cuánto', 'tenés', 'disponible', 'colores', 'talles', 'qué hay', 'mostrar', 'ver']
        order_keywords = ['pedido', 'quiero', 'necesito', 'comprar', 'encargar', 'haceme']
        modify_keywords = ['cambiar', 'modificar', 'cancelar', 'editar']
        advice_keywords = ['recomendás', 'conviene', 'mejor', 'qué', 'para qué']
        
        # ✅ LÓGICA MEJORADA: create_order solo si hay número
        has_number = any(char.isdigit() for char in message)

        # Detectar con prioridad contextual y generar reasoning
        if any(word in message_lower for word in modify_keywords) and conversation.get('recent_orders'):
            matched_words = [word for word in modify_keywords if word in message_lower]
            return {
                "intent": "modify_order",
                "reasoning": f"Palabras clave de modificación detectadas: {matched_words}. Usuario tiene pedidos recientes ({len(conversation.get('recent_orders', []))}) que puede modificar.",
                "confidence": 0.8
            }
        elif any(word in message_lower for word in order_keywords) and has_number: # ✅ AÑADIR CONDICIÓN
            matched_words = [word for word in order_keywords if word in message_lower]
            return {
                "intent": "create_order", 
                "reasoning": f"Palabras clave de pedido ({matched_words}) y una cantidad numérica detectadas. Indica intención de compra/crear pedido.",
                "confidence": 0.9
            }
        elif any(word in message_lower for word in stock_keywords) or (any(word in message_lower for word in order_keywords) and not has_number): # ✅ AÑADIR LÓGICA
            matched_words = [word for word in stock_keywords if word in message_lower]
            context_info = " Con contexto de productos mostrados." if context_has_products else ""
            return {
                "intent": "check_stock",
                "reasoning": f"Palabras clave de consulta de stock ({matched_words}) o intención de compra sin cantidad. Indica búsqueda de información de inventario.{context_info}",
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
            log(f"🔀 Derivando a agente: {intent}")
            
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
            log(f"❌ Error en dispatch: {e}")
            return "Disculpa, tuve un problema procesando tu consulta. ¿Podrías intentar de nuevo?"
    
    async def handle_general_chat_temp(self, message: str, conversation: Dict) -> str:
        """Manejo temporal de charla general"""
        # ✅ ELIMINAR LA INFO DE LA API KEY
        return f"¡Hola! Soy Ventix, tu asistente de ventas textiles. ¿En qué puedo ayudarte hoy?\n\n🎯 Puedo mostrarte nuestro catálogo, consultar stock o ayudarte a hacer un pedido."
    
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
            
            user_msg = models.ConversationMessage(
                conversation_id=conversation_record.id,
                user_phone=phone,
                role='user',
                content=user_message,
                intent=intent,
                reasoning=reasoning,
                timestamp=datetime.now()
            )
            db.add(user_msg)
            
            # Guardar respuesta del bot
            
            bot_msg = models.ConversationMessage(
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
            
            log(f"💾 Conversación actualizada para {phone}")
            
        except Exception as e:
            log(f"❌ Error actualizando conversación: {e}")
            db.rollback()
        finally:
            db.close()

# Instancia global
conversation_manager = ConversationManager()