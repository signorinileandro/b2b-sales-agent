import os
import json
import google.generativeai as genai
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from ..database import SessionLocal
from .. import models
from .query_agent import query_agent  

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
           
            if intent['intent_type'] == 'confirm_order':
                db_result = await query_agent.execute_database_operation(intent, user_id, conversation.id)
            else:
                db_result = await query_agent.execute_database_operation(intent, user_id)
            
            print(f"🗄️ Operación DB: {db_result.get('operation', 'none')} - Éxito: {db_result.get('success', False)}")
            
            # ✅ AGREGAR DEBUG MÁS DETALLADO
            if db_result and db_result.get('success') and db_result.get('data'):
                products = db_result['data'].get('products', [])
                print(f"📊 Productos encontrados en BD: {len(products)}")
                if products:
                    print(f"🔍 Primer producto: {products[0]}")
                else:
                    print(f"⚠️ Lista de productos vacía")
            
            # Actualizar contexto con resultados
            if db_result.get('success') and db_result.get('data'):
                if 'products' in db_result['data']:
                    context['last_searched_products'] = db_result['data']['products']
                if intent['intent_type'] == 'confirm_order':
                    context['last_order_created'] = db_result['data']
                if intent['intent_type'] == 'edit_order':
                    context['last_order_edited'] = db_result['data']
        
        # DEBUG - Mostrar resultado de DB si existe
        if db_result:
            print(f"🔍 DEBUG - DB Result SUCCESS: {db_result.get('success')}")
            print(f"🔍 DEBUG - DB Result OPERATION: {db_result.get('operation')}")
            if db_result.get('data'):
                data_summary = {k: len(v) if isinstance(v, list) else str(v)[:50] for k, v in db_result['data'].items()}
                print(f"🔍 DEBUG - DB Result DATA: {data_summary}")
        else:
            print(f"🔍 DEBUG - No DB result for intent: {intent['intent_type']}")
        
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
        
        # ✅ VERIFICAR SI HAY DATOS REALES DE LA BD
        if db_result and db_result.get("success") and db_result.get("data"):
            data = db_result["data"]
            operation = db_result["operation"]
            
            # ✅ RESPUESTA BASADA EN DATOS REALES
            if operation == "search_products" and data.get("products"):
                products = data["products"]
                if len(products) == 0:
                    return "Lo siento, en este momento no tengo productos que coincidan con tu búsqueda. ¿Te interesa algún otro tipo de prenda?"
                
                # Generar respuesta con productos REALES
                response = "¡Perfecto! Te cuento qué tengo disponible:\n\n"
                
                for i, product in enumerate(products[:5], 1):  # Mostrar hasta 5 productos
                    response += f"{i}. **{product['name']}**\n"
                    response += f"   - Color: {product['color']}\n"
                    response += f"   - Talla: {product['talla']}\n" 
                    response += f"   - Stock disponible: {product['stock']} unidades\n"
                    response += f"   - Precio: ${product['precio_50_u']:,} c/u (50+ unidades)\n\n"
                
                response += "¿Alguno te interesa? ¿Para cuántas personas necesitás?"
                return response
            
            elif operation == "check_stock" and data.get("products"):
                products = data["products"]
                if len(products) == 0:
                    return "En este momento no tengo pantalones en stock, pero estoy esperando mercadería nueva. ¿Te interesa que te avise cuando llegue?"
                
                # Agrupar por talla y color
                talleres_disponibles = set()
                colores_disponibles = set()
                total_stock = 0
                
                response = "📋 **PANTALONES DISPONIBLES:**\n\n"
                
                for product in products:
                    talleres_disponibles.add(product.get('talla', 'N/A'))
                    colores_disponibles.add(product.get('color', 'N/A'))
                    total_stock += product.get('stock', 0)
                    
                    response += f"• {product['name']}\n"
                    response += f"  Color: {product.get('color', 'N/A')} | Talla: {product.get('talla', 'N/A')}\n"
                    response += f"  Stock: {product['stock']} unidades | ${product['precio_50_u']:,} c/u\n\n"
                
                response += f"📊 **RESUMEN:**\n"
                response += f"• Talles disponibles: {', '.join(sorted(talleres_disponibles))}\n"
                response += f"• Colores disponibles: {', '.join(sorted(colores_disponibles))}\n"
                response += f"• Stock total: {total_stock} unidades\n\n"
                response += "¿Qué talle y color te interesa?"
                
                return response
            
            elif operation == "create_order" and data.get("order_id"):
                order = data
                return f"¡PEDIDO CONFIRMADO! 🎉\n\n" \
                       f"✅ **{order['product']['name']}**\n" \
                       f"📦 Cantidad: {order['quantity']} unidades\n" \
                       f"💰 Total: ${order['total_price']:,}\n" \
                       f"📋 ID de pedido: {order['order_id']}\n\n" \
                       f"Stock restante: {order['stock_remaining']} unidades\n\n" \
                       f"¿Necesitás algo más para tu empresa?"
            
            elif operation == "edit_order" and data.get("order_id"):
                return f"✅ **PEDIDO ACTUALIZADO**\n\n" \
                       f"📋 Pedido #{data['order_id']}\n" \
                       f"📦 Cantidad anterior: {data['old_quantity']}\n" \
                       f"📦 Nueva cantidad: {data['new_quantity']}\n" \
                       f"💰 Nuevo total: ${data['new_total_price']:,}\n\n" \
                       f"¡Cambio realizado exitosamente!"
        
        # ✅ SI HAY ERROR EN LA BD, USAR LA INFO DEL ERROR
        elif db_result and not db_result.get("success"):
            error_msg = db_result.get("error", "Error desconocido")
            
            if "no hay producto" in error_msg.lower():
                return "No encontré productos que coincidan con lo que buscás. ¿Podrías ser más específico sobre el tipo, color o talla que necesitás?"
            elif "no hay pedidos recientes" in error_msg.lower():
                return "No encontré pedidos recientes tuyos para modificar. ¿Querés hacer un nuevo pedido?"
            elif "ya pasaron" in error_msg.lower():
                return f"Lo siento, {error_msg}. ¿Querés hacer un nuevo pedido en su lugar?"
            else:
                return f"Tuve un problemita técnico: {error_msg}. ¿Podés intentar de nuevo?"
        
        # ✅ FALLBACK: USAR GEMINI SOLO PARA CONVERSACIÓN GENERAL
        prompt = f"""
Eres Ventix, un vendedor B2B argentino de textiles con 15 años de experiencia.

El cliente dice: "{user_message}"

IMPORTANTE: NO INVENTES productos, precios ni stock. Solo responde de forma conversacional y ofrece ayuda.

Si pregunta por productos específicos, dile que vas a consultarlo en el sistema.
Si es un saludo o pregunta general, responde de forma amigable y ofrece ayuda.

Mantente en personaje de vendedor experimentado argentino.
"""
        
        try:
            response = await self._make_gemini_request(prompt)
            if response:
                return response
                
        except Exception as e:
            print(f"❌ Error generando respuesta: {e}")
        
        # Fallback final
        return "¡Hóla! Soy Ventix, especialista en textiles. ¿En qué te puedo ayudar hoy?"
    
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