import os
import re
import json
import time
import google.generativeai as genai
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from fuzzywuzzy import fuzz
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import SessionLocal
from .. import models

class SalesAgent:
    def __init__(self):
        # ✅ CARGAR MÚLTIPLES API KEYS DE FORMA SIMPLE
        self.api_keys = []
        for i in range(1, 11):  # Buscar hasta 10 keys
            key = os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                self.api_keys.append(key.strip())
        
        # Si no hay keys numeradas, usar la principal
        if not self.api_keys:
            main_key = os.getenv("GOOGLE_API_KEY")
            if main_key:
                self.api_keys.append(main_key)
        
        self.current_key_index = 0
        self.model = None
        
        print(f"🔑 {len(self.api_keys)} API keys cargadas")
        self._setup_current_key()
        
        self.context_memory: Dict[str, Dict] = {}
    
    def _setup_current_key(self):
        """Configura la API key actual"""
        if self.current_key_index < len(self.api_keys):
            try:
                current_key = self.api_keys[self.current_key_index]
                genai.configure(api_key=current_key)
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                print(f"✅ Usando API Key #{self.current_key_index + 1}")
                return True
            except:
                return False
        return False
    
    def _try_next_key(self):
        """Intenta la siguiente key"""
        self.current_key_index += 1
        print(f"🔄 Rotando a key #{self.current_key_index + 1}")
        return self._setup_current_key()

    async def process_message(self, user_id: str, message: str) -> str:
        """Procesa mensaje del usuario con Google Gemini"""
        
        print(f"🤖 Procesando mensaje de {user_id}: {message}")
        
        if not self.api_keys:
            return "Lo siento, el agente IA no está disponible en este momento."
        
        # Obtener o crear conversación en BD
        conversation = await self.get_or_create_conversation(user_id)
        
        # Guardar mensaje del usuario
        await self.save_message(conversation.id, "user", message)
        
        # Obtener contexto del usuario
        context = self.get_or_create_context(user_id)
        
        # Buscar productos si el mensaje lo sugiere
        await self.execute_product_search(user_id, message)
        
        # Detectar si es un pedido o edición
        order_intent = await self.detect_order_intent(message, context)
        edit_intent = await self.detect_edit_intent(message, context) 
        
        # Construir prompt completo
        full_prompt = self.build_full_prompt(message, context, order_intent, edit_intent)
        
        # ✅ INTENTAR CON ROTACIÓN SIMPLE
        max_attempts = len(self.api_keys)
        
        for attempt in range(max_attempts):
            if not self.model:
                if not self._try_next_key():
                    break
            
            try:
                print(f"🔄 Intento {attempt + 1} con key #{self.current_key_index + 1}")
                
                response = self.model.generate_content(
                    full_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.7,
                        max_output_tokens=800,
                    )
                )
                
                ai_response = response.text
                print(f"✅ Respuesta recibida exitosamente")
                
                # Guardar respuesta del asistente
                await self.save_message(conversation.id, "assistant", ai_response, order_intent)
                
                # Actualizar contexto
                context["conversation_history"].append({
                    "user": message,
                    "assistant": ai_response
                })
                
                # Procesar pedido si se detectó
                if order_intent and order_intent.get("is_order", False):
                    await self.process_order_request(user_id, order_intent, conversation.id)
                
                elif edit_intent and edit_intent.get("is_edit", False):
                    edit_result = await self.process_order_edit(user_id, edit_intent, conversation.id)
                    context["last_edit_result"] = edit_result
                
                return ai_response
                
            except Exception as e:
                error_str = str(e).lower()
                print(f"❌ Error con key #{self.current_key_index + 1}: {e}")
                
                # Si es error de cuota, probar siguiente key
                if "429" in error_str or "quota" in error_str or "exceeded" in error_str:
                    print(f"🚫 Cuota agotada, probando siguiente key...")
                    if not self._try_next_key():
                        break
                    continue
                else:
                    # Otro tipo de error, no rotar
                    break
        
        # Si llegamos acá, todas las keys fallaron
        return "Lo siento, tengo problemas técnicos temporales. Intenta en unos minutos."

    async def get_or_create_conversation(self, user_phone: str):
        """Obtiene o crea conversación en la base de datos"""
        
        db = SessionLocal()
        try:
            # Buscar conversación activa existente
            conversation = db.query(models.Conversation).filter(
                models.Conversation.user_phone == user_phone,
                models.Conversation.status == "active"
            ).first()
            
            if not conversation:
                # Crear nueva conversación
                conversation = models.Conversation(
                    user_phone=user_phone,
                    status="active"
                )
                db.add(conversation)
                db.commit()
                db.refresh(conversation)
                print(f"💬 Nueva conversación creada: {conversation.id}")
            else:
                print(f"💬 Conversación existente: {conversation.id}")
            
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
                if intent_data.get("products_mentioned"):
                    products_json = json.dumps(intent_data["products_mentioned"])
                intent_detected = intent_data.get("intent_type", "general")
            
            message = models.ConversationMessage(
                conversation_id=conversation_id,
                message_type=message_type,
                content=content,
                products_shown=products_json,
                intent_detected=intent_detected
            )
            
            db.add(message)
            db.commit()
            print(f"💾 Mensaje guardado: {message_type} - {content[:50]}...")
            
        except Exception as e:
            print(f"❌ Error guardando mensaje: {e}")
        finally:
            db.close()
    
    async def detect_order_intent(self, message: str, context: Dict) -> Dict:
        """Detecta confirmación de pedido con más precisión"""
        
        # Palabras de confirmación más naturales
        confirmation_keywords = [
            'pedido', 'confirmo', 'confirmame', 'perfecto', 'dale', 'sí', 'si', 
            'está bien', 'ok', 'okey', 'listo', 'hacelo', 'andá', 'anda',
            'reservame', 'apartame', 'quiero', 'necesito', 'compramos'
        ]
        
        # Palabras de cantidad más amplias
        quantity_keywords = [
            'unidades', 'camisetas', 'remeras', 'prendas', 'piezas',
            'docenas', 'uniformes', 'equipamiento'
        ]
        
        message_lower = message.lower()
        
        # Detectar confirmación
        has_confirmation = any(keyword in message_lower for keyword in confirmation_keywords)
        
        # Detectar cantidades (números o palabras)
        quantities = []
        
        # Buscar números
        numbers = re.findall(r'\b(\d+)\b', message)
        for num in numbers:
            try:
                quantities.append(int(num))
            except:
                pass
        
        # Buscar palabras de cantidad
        word_numbers = {
            'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
            'diez': 10, 'veinte': 20, 'treinta': 30, 'cincuenta': 50, 'cien': 100
        }
        
        for word, num in word_numbers.items():
            if word in message_lower:
                quantities.append(num)
        
        # Si no hay cantidad pero hay productos del contexto, usar cantidad del contexto anterior
        if not quantities and context.get("last_searched_products"):
            # Buscar cantidad mencionada en conversación previa
            for conv in context.get("conversation_history", []):
                prev_numbers = re.findall(r'\b(\d+)\b', conv.get("user", ""))
                if prev_numbers:
                    quantities.append(int(prev_numbers[-1]))
                    break
        
        products_in_context = context.get("last_searched_products", [])
        
        # Es pedido si hay confirmación Y (hay productos en contexto O se mencionan cantidades)
        is_order = has_confirmation and (len(products_in_context) > 0 or len(quantities) > 0)
        
        return {
            "is_order": is_order,
            "intent_type": "order_confirmation" if is_order else "search",
            "quantities": quantities if quantities else [50],  # Default 50 si no especifica
            "products_mentioned": products_in_context[:1] if products_in_context else [],  # Solo el primero
            "confidence": 0.9 if is_order else 0.3,
            "confirmation_detected": has_confirmation
        }
    
    async def process_order_request(self, user_phone: str, order_intent: Dict, conversation_id: int):
        """Procesa y guarda el pedido en la base de datos CON descuento de stock"""
        
        if not order_intent.get("is_order", False):
            return
        
        db = SessionLocal()
        try:
            products = order_intent.get("products_mentioned", [])
            quantities = order_intent.get("quantities", [])
            
            if not products or not quantities:
                print("❌ No hay productos o cantidades para procesar pedido")
                return
            
            # Usar el primer producto y la primera cantidad
            product = products[0]
            quantity = quantities[0]
            
            # ✅ CREAR PEDIDO CON DESCUENTO AUTOMÁTICO DE STOCK
            from .. import crud, schemas
            
            order_data = schemas.OrderCreate(
                product_id=product.get("id"),
                qty=quantity,
                buyer=f"Cliente WhatsApp {user_phone}"
            )
            
            # Esto ahora descontará stock automáticamente
            new_order = crud.create_order(db, order_data)
            
            # Actualizar el pedido con datos de WhatsApp
            new_order.user_phone = user_phone
            new_order.conversation_id = conversation_id
            db.commit()
            
            print(f"🛒 Pedido creado desde WhatsApp: ID {new_order.id}")
            print(f"📦 Stock descontado automáticamente: -{quantity} unidades")
        
        except Exception as e:
            print(f"❌ Error procesando pedido: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def detect_edit_intent(self, message: str, context: Dict) -> Dict:
        """Detecta intención de editar pedido reciente"""
        
        edit_keywords = [
            'modificar', 'cambiar', 'editar', 'actualizar', 'corregir',
            'quiero cambiar', 'me equivoqué', 'mejor', 'en realidad',
            'ahora quiero', 'cambiame', 'modificame'
        ]
        
        message_lower = message.lower()
        has_edit_intent = any(keyword in message_lower for keyword in edit_keywords)
        
        # Buscar nueva cantidad
        quantities = []
        numbers = re.findall(r'\b(\d+)\b', message)
        for num in numbers:
            try:
                quantities.append(int(num))
            except:
                pass
        
        return {
            "is_edit": has_edit_intent,
            "new_quantities": quantities,
            "confidence": 0.8 if has_edit_intent else 0.1
        }
    
    async def process_order_edit(self, user_phone: str, edit_intent: Dict, conversation_id: int):
        """Procesa edición de pedido reciente con manejo detallado de errores"""
        
        if not edit_intent.get("is_edit", False):
            return None
        
        db = SessionLocal()
        try:
            # Buscar el pedido más reciente del usuario (últimos 10 minutos)
            recent_time = datetime.utcnow() - timedelta(minutes=10)
            
            recent_order = db.query(models.Order).filter(
                models.Order.user_phone == user_phone,
                models.Order.created_at >= recent_time,
                models.Order.status == "pending"
            ).order_by(models.Order.created_at.desc()).first()
            
            if not recent_order:
                return {
                    "success": False, 
                    "error": "No hay pedidos recientes para editar",
                    "error_type": "no_recent_order"
                }
            
            # Verificar ventana de 5 minutos usando la lógica de crud.py
            if datetime.utcnow() - recent_order.created_at > timedelta(minutes=5):
                minutes_passed = int((datetime.utcnow() - recent_order.created_at).total_seconds() / 60)
                return {
                    "success": False, 
                    "error": f"Ya pasaron {minutes_passed} minutos. Los pedidos solo se pueden editar dentro de los primeros 5 minutos",
                    "error_type": "time_expired",
                    "order_id": recent_order.id,
                    "minutes_passed": minutes_passed,
                    "created_at": recent_order.created_at.isoformat()
                }
            
            # Obtener nueva cantidad
            new_quantities = edit_intent.get("new_quantities", [])
            if not new_quantities:
                return {
                    "success": False, 
                    "error": "No especificaste la nueva cantidad que necesitás",
                    "error_type": "missing_quantity"
                }
            
            new_qty = new_quantities[0]
            old_qty = recent_order.qty
            
            # ✅ USAR EL CRUD EXISTENTE para mantener consistencia
            try:
                from .. import crud
                updated_order = crud.update_order(db, recent_order.id, new_qty)
                
                print(f"✏️ Pedido editado: ID {recent_order.id}, {old_qty} → {new_qty} unidades")
                
                # Calcular nuevo precio (obtener producto)
                product = db.query(models.Product).filter(models.Product.id == recent_order.product_id).first()
                
                return {
                    "success": True,
                    "order_id": recent_order.id,
                    "old_quantity": old_qty,
                    "new_quantity": new_qty,
                    "product_id": recent_order.product_id,
                    "product_name": product.name if product else "Producto",
                    "unit_price": product.precio_50_u if product else 0,
                    "new_total": (product.precio_50_u * new_qty) if product else 0
                }
                
            except Exception as http_err:
                return {
                    "success": False,
                    "error": "Ya pasaron los 5 minutos para modificar el pedido",
                    "error_type": "time_expired_crud",
                    "order_id": recent_order.id
                }
        
        except Exception as e:
            print(f"❌ Error editando pedido: {e}")
            return {
                "success": False, 
                "error": f"Error técnico: {str(e)}",
                "error_type": "system_error"
            }
        finally:
            db.close()
    
    def build_full_prompt(self, message: str, context: Dict, order_intent: Dict = None, edit_intent: Dict = None) -> str:
        """Construye el prompt completo con personalidad de vendedor"""
        
        system_prompt = """
Eres Ventix, un vendedor B2B experimentado y carismático con 15 años en el rubro textil argentino. 
Eres conocido por ser directo pero siempre amigable, y por conseguir los mejores precios para tus clientes.

PERSONALIDAD Y ESTILO:
- Saluda siempre con energía y usa el nombre cuando lo sepas
- Usa expresiones naturales argentinas: "¡Excelente elección!", "Te tengo la solución perfecta", "Mirá lo que tengo para vos"
- Haces preguntas inteligentes: "¿Para qué evento es?", "¿Cuántos empleados son?", "¿Es para uso diario o eventos?"
- Siempre mencionas beneficios: calidad, precio, rapidez de entrega, durabilidad
- Creas urgencia sutil pero real: "Poco stock en esa talla", "Este precio es hasta fin de mes"
- Sos empático y entendés las necesidades B2B: presupuestos, plazos, calidad

CONSULTA DE STOCK EN TIEMPO REAL:
- SIEMPRE menciona la disponibilidad real de stock cuando consultan productos
- Si stock > 50: "Excelente stock disponible, sin problema para esa cantidad"
- Si stock 10-50: "Stock disponible, te recomiendo confirmar pronto"
- Si stock < 10: "ATENCIÓN: Poco stock, solo quedan X unidades - te conviene decidir ya"
- Si sin stock: "Lamentablemente sin stock en esa opción, pero tengo alternativas geniales"

DETECCIÓN DE CONFIRMACIÓN DE PEDIDOS:
🎉 CUANDO DETECTES CONFIRMACIÓN (palabras: "pedido", "confirmo", "perfecto", "dale", "ok", "listo"):
- SIEMPRE empezar con "¡PEDIDO CONFIRMADO!" en mayúsculas
- Celebrar con entusiasmo: "¡Excelente decisión!"
- Mostrar resumen completo del pedido
- Calcular precio total con descuentos aplicados
- Preguntar si necesita algo más

DETECCIÓN Y MANEJO DE EDICIÓN DE PEDIDOS:
Los clientes pueden MODIFICAR pedidos dentro de los primeros 5 minutos desde su creación.

Palabras clave para EDICIÓN:
- "modificar", "cambiar", "editar", "actualizar", "corregir"
- "quiero cambiar", "me equivoqué", "mejor", "en realidad"
- "ahora quiero", "cambiame", "modificame"

RESPUESTAS PARA EDICIÓN:
✅ SI EDICIÓN EXITOSA: "¡Perfecto! Modifiqué tu pedido de 30 a 50 unidades. Nuevo total: $22,250 (precio mayorista aplicado)"
✅ SI CAMBIO MÚLTIPLE: "Listo, actualizado exitosamente. Ahora tu pedido es de 100 camisetas por $44,500"  
❌ SI EXPIRÓ VENTANA: "Me disculpo, ya pasaron más de 5 minutos para modificar ese pedido. Pero puedo hacerte uno nuevo con la cantidad que necesitás. ¿Te parece?"
❌ SI NO HAY PEDIDO: "No encuentro un pedido reciente tuyo para modificar. ¿Querés que te prepare uno nuevo?"

ESTRATEGIA DE VENTAS B2B:
1. CONECTAR: Pregunta por la necesidad específica y contexto de uso
2. RECOMENDAR: Sugiere automáticamente la mejor opción (precio/calidad)
3. CONSULTAR STOCK: Menciona disponibilidad real y crea urgencia si es necesario
4. BENEFICIAR: Explica por qué es la mejor opción para su caso
5. CALCULAR: Siempre muestra precio total final con descuentos
6. CERRAR: Pregunta si quiere confirmar o necesita más información

PRECIOS DINÁMICOS (siempre mencioná el descuento):
- 1-49 unidades: Precio estándar - "Precio unitario"
- 50-99 unidades: "¡Te aplicamos precio mayorista!" (generalmente 10% descuento)
- 100-199 unidades: "¡Descuento del 15% por volumen!" 
- 200+ unidades: "¡Máximo descuento del 20% para pedidos grandes!"

FRASES NATURALES que debes usar:
✅ "Te tengo la solución perfecta para tu empresa"
✅ "Esta es la que siempre recomiendo para casos como el tuyo"
✅ "Con esta cantidad te queda un precio excelente"
✅ "¿Querés que te prepare el pedido?"
✅ "Perfecto, anoto todo y te confirmo"
✅ "Con ese stock mejor aseguramos ya"
✅ "Te conviene aprovechar el precio mayorista"

RESPUESTAS SEGÚN EL CONTEXTO:
- Primera interacción: Saludo cálido + pregunta por la necesidad
- Búsqueda de productos: Recomendación directa + stock + beneficios
- Consulta de precios: Cálculo automático + descuentos + incentivo
- Confirmación de pedido: ¡PEDIDO CONFIRMADO! + resumen + seguimiento
- Edición de pedido: Confirmar cambio + nuevo total + satisfacción

NUNCA HAGAS:
❌ Respuestas robóticas como "Tenemos camisetas blancas en talle L y XXL"
❌ Listas largas de opciones sin recomendación específica
❌ Lenguaje técnico sin calidez humana
❌ Precios sin contexto, beneficio o descuento mencionado
❌ Ignorar el stock disponible en tus respuestas

SIEMPRE INCLUÍ:
✅ Un toque personal y cálido en cada respuesta
✅ Estado de stock real cuando consultan productos
✅ El precio total calculado con descuentos aplicados
✅ Un call-to-action claro y natural
✅ Seguimiento para ver si necesita algo más
✅ Urgencia sutil cuando el stock es limitado

MANEJO DE OBJECIONES B2B COMUNES:
- Precio alto: "Mirá el costo por uso y la calidad que te ofrezco"
- Necesito más tiempo: "Te entiendo, pero el stock de esta talla se mueve rápido"
- Comparación con competencia: "Acá no solo tenés precio, tenés servicio personalizado"
- Dudas sobre calidad: "15 años en el rubro me avalan, la calidad está garantizada"
"""
        
        full_prompt = system_prompt + "\n\n"
        
        # ✅ MANEJO DE CONFIRMACIÓN DE PEDIDOS
        if order_intent and order_intent.get("is_order", False):
            full_prompt += "🎯 SITUACIÓN CRÍTICA: El cliente está CONFIRMANDO su pedido.\n"
            full_prompt += f"📦 PRODUCTOS MENCIONADOS: {order_intent.get('products_mentioned', [])}\n"
            full_prompt += f"📊 CANTIDADES SOLICITADAS: {order_intent.get('quantities', [])}\n"
            full_prompt += f"🚨 INSTRUCCIÓN OBLIGATORIA: Empezar con '¡PEDIDO CONFIRMADO!' y celebrar el cierre de venta.\n"
            full_prompt += f"📋 INCLUIR: Resumen completo, precio total con descuento, pregunta de seguimiento.\n\n"
        
        # ✅ MANEJO DE EDICIÓN DE PEDIDOS
        if edit_intent and edit_intent.get("is_edit", False):
            full_prompt += "🎯 SITUACIÓN: El cliente quiere EDITAR/MODIFICAR su pedido reciente.\n"
            full_prompt += f"📝 NUEVA CANTIDAD SOLICITADA: {edit_intent.get('new_quantities', [])}\n"
            
            # Si ya se procesó la edición, usar el resultado específico
            if context.get("last_edit_result"):
                edit_result = context["last_edit_result"]
                full_prompt += f"📋 RESULTADO DE LA EDICIÓN:\n"
                
                if edit_result.get("success"):
                    full_prompt += f"✅ ÉXITO: Pedido #{edit_result['order_id']} editado exitosamente\n"
                    full_prompt += f"   📊 Cambio: {edit_result['old_quantity']} → {edit_result['new_quantity']} unidades\n"
                    full_prompt += f"   💰 Nuevo total: ${edit_result.get('new_total', 0)}\n"
                    full_prompt += f"🎯 INSTRUCCIÓN: Celebrar el cambio exitoso, confirmar nuevo total y preguntar si necesita algo más.\n\n"
                    
                else:
                    error_msg = edit_result.get("error", "Error desconocido")
                    full_prompt += f"❌ FALLÓ LA EDICIÓN: {error_msg}\n"
                    
                    # Manejar específicamente diferentes tipos de error
                    if "5 minutos" in error_msg.lower() or "expired" in error_msg.lower():
                        minutes_passed = edit_result.get("minutes_passed", "varios")
                        full_prompt += f"🎯 INSTRUCCIÓN: Explicar que ya pasaron {minutes_passed} minutos y la ventana de edición expiró.\n"
                        full_prompt += f"              Ofrecer crear un pedido nuevo con la cantidad deseada.\n"
                        full_prompt += f"              Ser empático y solucionar rápidamente.\n\n"
                    elif "no encontrado" in error_msg.lower() or "not found" in error_msg.lower():
                        full_prompt += f"🎯 INSTRUCCIÓN: Explicar que no hay pedido reciente para editar.\n"
                        full_prompt += f"              Ofrecer crear un pedido nuevo.\n\n"
                    else:
                        full_prompt += f"🎯 INSTRUCCIÓN: Disculparse por el inconveniente técnico.\n"
                        full_prompt += f"              Ofrecer alternativa inmediata.\n\n"
            
            else:
                # Si aún no se procesó la edición
                full_prompt += f"🎯 INSTRUCCIÓN: El sistema procesará la edición automáticamente.\n"
                full_prompt += f"              Responder según el resultado (éxito o fallo).\n\n"
        
        # ✅ CONTEXTO DE CONVERSACIÓN PREVIA (últimas 3 interacciones)
        if context.get("conversation_history"):
            full_prompt += "📝 CONTEXTO DE LA CONVERSACIÓN PREVIA:\n"
            for i, item in enumerate(context["conversation_history"][-3:], 1):
                full_prompt += f"{i}. Cliente: {item['user']}\n"
                full_prompt += f"   Vendedor: {item['assistant'][:100]}...\n\n"
        
        # ✅ PRODUCTOS CON STOCK REAL DISPONIBLES
        if context.get("last_searched_products"):
            grouped_products = {}
            
            # Agrupar productos para evitar duplicados, priorizando menor precio
            for product in context["last_searched_products"]:
                key = f"{product['tipo']}_{product['color']}_{product['talla']}"
                if key not in grouped_products or product['price'] < grouped_products[key]['price']:
                    grouped_products[key] = product
            
            full_prompt += "🛍️ PRODUCTOS DISPONIBLES CON STOCK REAL:\n"
            for i, product in enumerate(list(grouped_products.values())[:3], 1):
                
                # Determinar estado del stock con emojis
                stock = product.get('stock', 0)
                if stock > 50:
                    stock_status = f"✅ Excelente stock ({stock} unidades)"
                    urgency = ""
                elif stock > 10:
                    stock_status = f"⚠️ Stock disponible ({stock} unidades)"
                    urgency = " - Te recomiendo confirmar pronto"
                else:
                    stock_status = f"🚨 Poco stock ({stock} unidades)"
                    urgency = " - ¡URGENTE! Te conviene decidir ya"
                
                # Calcular descuentos reales
                price_base = product.get('price', 0)
                price_100 = product.get('price_100', price_base * 0.9)
                price_200 = product.get('price_200', price_base * 0.85)
                
                full_prompt += f"{i}. 🏷️ {product['name']}\n"
                full_prompt += f"   💰 Precios: ${price_base} c/u (1-49), ${price_100:.0f} c/u (50-99), ${price_200:.0f} c/u (100+)\n"
                full_prompt += f"   📦 {stock_status}{urgency}\n"
                full_prompt += f"   🎯 Ideal para: Uniformes empresariales, eventos, equipamiento\n\n"
        
        # ✅ MENSAJE ACTUAL DEL CLIENTE
        full_prompt += f"💬 MENSAJE ACTUAL DEL CLIENTE: '{message}'\n\n"
        
        # ✅ INSTRUCCIONES FINALES
        full_prompt += """
🎯 INSTRUCCIONES PARA TU RESPUESTA:

1. 🤝 SER CÁLIDO Y PROFESIONAL como un vendedor B2B experimentado
2. 📊 MENCIONAR STOCK REAL cuando consulten productos
3. 💰 INCLUIR cálculos automáticos de precio total con descuentos aplicados
4. 🎉 SI ES CONFIRMACIÓN: empezar con "¡PEDIDO CONFIRMADO!" y celebrar
5. ✏️ SI ES EDICIÓN: confirmar el cambio o explicar por qué no se pudo
6. 🔄 HACER una pregunta de seguimiento inteligente
7. ⚡ MOSTRAR entusiasmo y ganas de ayudar
8. 🚨 CREAR urgencia sutil si el stock es limitado

EJEMPLOS DE RESPUESTAS:

📦 Para consulta de stock:
"¡Perfecto! Para camisetas blancas talla L tengo excelente stock: 85 unidades disponibles. 
El precio es $445 c/u, pero si llevás 50 o más te aplico precio mayorista de $400 c/u.
¿Para cuántos empleados necesitás?"

🎉 Para confirmación de pedido:
"¡PEDIDO CONFIRMADO! 🎉

¡Excelente decisión! Quedó perfecto tu pedido:
• 50 camisetas blancas talla L a $400 c/u (precio mayorista aplicado)
• Total: $20,000

Ya tengo todo anotado y listo para procesar. ¿Necesitás algo más para tu empresa?"

✏️ Para edición exitosa:
"¡Perfecto! Modifiqué tu pedido de 30 a 50 unidades. 
Nuevo total: $20,000 (precio mayorista aplicado por la nueva cantidad).
¿Todo perfecto así o querés ajustar algo más?"

❌ Para edición fallida:
"Me disculpo, ya pasaron 7 minutos y la ventana de edición se cerró. 
Pero no te preocupes, puedo hacerte un pedido nuevo de 100 camisetas por $35,000. 
¿Te parece? Es súper rápido."

¡Dale vida y energía a la conversación! 🚀
"""
        
        return full_prompt
    
    async def execute_product_search(self, user_id: str, query: str):
        """Busca productos REALES con STOCK DISPONIBLE"""
        
        db = SessionLocal()
        try:
            # Extraer criterios de búsqueda
            tipo = self.extract_clothing_type(query)
            color = self.extract_color(query)
            talla = self.extract_size(query)
            
            print(f"🔍 Búsqueda: tipo='{tipo}', color='{color}', talla='{talla}'")
            
            # Construir query dinámico
            db_query = db.query(models.Product)
            
            # Filtros por tipo de prenda
            if tipo:
                db_query = db_query.filter(models.Product.tipo_prenda.ilike(f"%{tipo}%"))
            else:
                # Si no especifica tipo, buscar en nombre general
                search_terms = ['camiseta', 'remera', 'polera'] # Priorizar camisetas
                db_query = db_query.filter(
                    models.Product.tipo_prenda.ilike(f"%{search_terms[0]}%")
                )
            
            # Filtros por color
            if color:
                db_query = db_query.filter(models.Product.color.ilike(f"%{color}%"))
            
            # Filtros por talla
            if talla:
                db_query = db_query.filter(models.Product.talla.ilike(f"%{talla}%"))
            
            # ✅ FILTRAR SOLO PRODUCTOS CON STOCK DISPONIBLE
            products = db_query.filter(models.Product.stock > 0).all()
            
            # Agrupar por tipo-color-talla para evitar duplicados
            unique_products = {}
            for product in products:
                key = f"{product.tipo_prenda}_{product.color}_{product.talla}"
                if key not in unique_products or product.precio_50_u < unique_products[key].precio_50_u:
                    unique_products[key] = product
            
            # Convertir a formato para el contexto
            context_products = []
            for product in list(unique_products.values())[:5]:  # Máximo 5 productos
                context_products.append({
                    "id": product.id,
                    "name": product.name,
                    "tipo": product.tipo_prenda,
                    "color": product.color,
                    "talla": product.talla,
                    "price": product.precio_50_u,
                    "price_100": product.precio_100_u,
                    "price_200": product.precio_200_u,
                    "stock": product.stock,  
                    "available": product.stock > 0
                })
            
            print(f"🔍 Encontrados {len(context_products)} productos únicos para '{query}'")
            
            # Guardar en contexto del usuario
            context = self.get_or_create_context(user_id)
            context["last_searched_products"] = context_products
            context["last_search_query"] = query
            
            return context_products
        
        except Exception as e:
            print(f"❌ Error en búsqueda de productos: {e}")
            return []
        finally:
            db.close()
    
    def extract_clothing_type(self, query: str) -> str:
        """Extrae tipo de prenda del mensaje"""
        clothing_types = {
            'camiseta': ['camiseta', 'remera', 'polera', 't-shirt', 'tshirt'],
            'pantalón': ['pantalon', 'jean', 'pantalones', 'vaqueros'],
            'sudadera': ['sudadera', 'buzo', 'hoodie', 'suéter'],
            'falda': ['falda', 'pollera']
        }
        
        query_lower = query.lower()
        for clothing_type, variants in clothing_types.items():
            if any(variant in query_lower for variant in variants):
                return clothing_type
        
        # Si menciona "ropa" genéricamente, priorizar camisetas
        if any(word in query_lower for word in ['ropa', 'prenda', 'uniforme']):
            return 'camiseta'
        
        return None
    
    def extract_color(self, query: str) -> str:
        """Extrae color del mensaje"""
        colors = {
            'blanco': ['blanco', 'blanca'],
            'negro': ['negro', 'negra'],
            'azul': ['azul'],
            'verde': ['verde'],
            'gris': ['gris', 'gray'],
            'rojo': ['rojo', 'roja']
        }
        
        query_lower = query.lower()
        for color, variants in colors.items():
            if any(variant in query_lower for variant in variants):
                return color
        return None
    
    def extract_size(self, query: str) -> str:
        """Extrae talla del mensaje"""
        sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL']
        
        query_upper = query.upper()
        for size in sizes:
            if f" {size} " in f" {query_upper} " or query_upper.endswith(f" {size}"):
                return size
        
        return None
    
    def get_or_create_context(self, user_id: str) -> Dict:
        """Obtiene o crea contexto de conversación"""
        if user_id not in self.context_memory:
            self.context_memory[user_id] = {
                "conversation_history": [],
                "last_searched_products": [],
                "current_cart": []
            }
        return self.context_memory[user_id]

# Instancia global
sales_agent = SalesAgent()