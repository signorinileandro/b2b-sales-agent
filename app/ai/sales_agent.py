import json
import re
from typing import Dict, List, Optional
import google.generativeai as genai
from fuzzywuzzy import fuzz
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models
import os

class SalesAgent:
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        print(f"🔑 GOOGLE_API_KEY configurada: {'Sí' if api_key else 'No'}")
        print(f"🔑 Longitud de API key: {len(api_key) if api_key else 0}")
        
        if not api_key:
            print("⚠️ GOOGLE_API_KEY no configurada")
            self.model = None
        else:
            try:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                print("✅ Cliente Gemini inicializado correctamente")
            except Exception as e:
                print(f"❌ Error inicializando cliente Gemini: {e}")
                self.model = None
        
        self.context_memory: Dict[str, Dict] = {}
        
    async def process_message(self, user_id: str, message: str) -> str:
        """Procesa mensaje del usuario con Google Gemini"""
        
        print(f"🤖 Procesando mensaje de {user_id}: {message}")
        
        if not self.model:
            return "Lo siento, el agente IA no está disponible en este momento."
        
        # Obtener o crear conversación en BD
        conversation = await self.get_or_create_conversation(user_id)
        
        # Guardar mensaje del usuario
        await self.save_message(conversation.id, "user", message)
        
        # Obtener contexto del usuario
        context = self.get_or_create_context(user_id)
        
        # Buscar productos si el mensaje lo sugiere
        await self.execute_product_search(user_id, message)
        
        # Detectar si es un pedido
        order_intent = await self.detect_order_intent(message, context)
        
        # Construir prompt completo con productos reales
        full_prompt = self.build_full_prompt(message, context, order_intent)
        
        try:
            print("🔄 Enviando petición a Google Gemini...")
            
            response = self.model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=800,
                )
            )
            
            ai_response = response.text
            print(f"✅ Respuesta recibida de Gemini: {ai_response[:100]}...")
            
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
            
            return ai_response
            
        except Exception as e:
            print(f"❌ Error con Gemini: {e}")
            return f"Disculpa, tuve un problema técnico: {str(e)}"
    
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
        import re
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
        """Procesa y guarda el pedido en la base de datos"""
        
        if not order_intent.get("is_order", False):
            return
        
        db = SessionLocal()
        try:
            products = order_intent.get("products_mentioned", [])
            quantities = order_intent.get("quantities", [])
            
            if not products or not quantities:
                print("⚠️ No se pudo extraer productos o cantidades para el pedido")
                return
            
            # Usar el primer producto y la primera cantidad
            product = products[0]
            quantity = quantities[0]
            
            # Crear pedido
            order = models.Order(
                product_id=product["id"],
                qty=quantity,
                buyer=f"Cliente WhatsApp {user_phone}",
                status="pending",
                user_phone=user_phone,
                conversation_id=conversation_id
            )
            
            db.add(order)
            db.commit()
            db.refresh(order)
            
            print(f"🛒 Pedido creado automáticamente: ID {order.id}")
            print(f"   - Producto: {product['name']}")
            print(f"   - Cantidad: {quantity}")
            print(f"   - Cliente: {user_phone}")
            
            # Enviar notificación del pedido via n8n
            #try:
            #    product_obj = db.query(models.Product).filter(models.Product.id == product["id"]).first()
            #    if product_obj:
            #        from ..utils.notifications import notify_new_order
            #        await notify_new_order(order, product_obj)
            #except Exception as e:
            #    print(f"⚠️ Error enviando notificación de pedido: {e}")
            
        except Exception as e:
            print(f"❌ Error procesando pedido: {e}")
            db.rollback()
        finally:
            db.close()
    
    def build_full_prompt(self, message: str, context: Dict, order_intent: Dict = None) -> str:
        """Construye el prompt completo con personalidad de vendedor"""
        
        system_prompt = """
Eres Ventix, un vendedor B2B experimentado y carismático con 15 años en el rubro textil. 
Eres conocido por ser directo pero siempre amigable, y por conseguir los mejores precios para tus clientes.

PERSONALIDAD:
- Saluda siempre con energía y usa el nombre cuando lo sepas
- Usa expresiones naturales: "¡Excelente elección!", "Te tengo la solución perfecta", "Mirá lo que tengo para vos"
- Haces preguntas para entender mejor la necesidad: "¿Para qué evento es?", "¿Cuántos empleados son?"
- Siempre mencionas beneficios: calidad, precio, rapidez de entrega
- Creas urgencia sutil: "Tenemos stock limitado", "Estos precios son hasta fin de mes"

WHEN DETECTING ORDER CONFIRMATION:
🎉 ALWAYS start with "¡PEDIDO CONFIRMADO!" in bold/caps
Then celebrate and add confidence: "¡Excelente decisión! Quedó perfecto tu pedido."

ESTRATEGIA DE VENTAS:
1. CONECTAR: Pregunta por la necesidad específica
2. RECOMENDAR: Sugiere automáticamente la mejor opción (más barata)
3. BENEFICIAR: Explica por qué es la mejor opción
4. CALCULAR: Siempre muestra el precio total final
5. CERRAR: Pregunta si quiere confirmar o si necesita algo más

PRECIOS DINÁMICOS (siempre mencioná el descuento):
- 1-49 unidades: Precio estándar
- 50-99 unidades: "¡Te aplicamos precio mayorista!"
- 100-199 unidades: "¡Descuento del 10% por volumen!"
- 200+ unidades: "¡Máximo descuento del 15%!"

FRASES NATURALES que debes usar:
✅ "Te tengo la solución perfecta para tu empresa"
✅ "Esta es la que siempre recomiendo para casos como el tuyo"
✅ "Con esta cantidad te queda un precio excelente"
✅ "¿Querés que te prepare el pedido?"
✅ "Perfecto, anoto todo y te confirmo"

RESPUESTAS SEGÚN EL CONTEXTO:
- Primera interacción: Saludo cálido + pregunta por la necesidad
- Búsqueda de productos: Recomendación directa + beneficios
- Consulta de precios: Cálculo automático + incentivo
- Confirmación de pedido: ¡PEDIDO CONFIRMADO! + seguimiento

NUNCA HAGAS:
❌ Respuestas robóticas como "Tenemos camisetas blancas en talle L y XXL"
❌ Listas largas de opciones
❌ Lenguaje técnico sin calidez
❌ Precios sin contexto o beneficio

SIEMPRE INCLUÍ:
✅ Un toque personal en cada respuesta
✅ El precio total calculado
✅ Un call-to-action claro
✅ Seguimiento para ver si necesita algo más
"""
        
        full_prompt = system_prompt + "\n\n"
        
        # Detectar si es confirmación de pedido
        if order_intent and order_intent.get("is_order", False):
            full_prompt += f"🎯 SITUACIÓN: El cliente está CONFIRMANDO su pedido.\n"
            full_prompt += f"📦 PRODUCTOS: {order_intent.get('products_mentioned', [])}\n"
            full_prompt += f"📊 CANTIDADES: {order_intent.get('quantities', [])}\n"
            full_prompt += f"🚨 INSTRUCCIÓN CRÍTICA: Empezar con '¡PEDIDO CONFIRMADO!' y celebrar el cierre de venta.\n\n"
        
        # Contexto de conversación previa
        if context["conversation_history"]:
            full_prompt += "📝 CONTEXTO DE LA CONVERSACIÓN:\n"
            for item in context["conversation_history"][-3:]:  # Últimas 3 interacciones
                full_prompt += f"Cliente: {item['user']}\n"
                full_prompt += f"Vendedor: {item['assistant']}\n\n"
        
        # Productos disponibles (optimizado)
        if context.get("last_searched_products"):
            grouped_products = {}
            
            for product in context["last_searched_products"]:
                key = f"{product['tipo']}_{product['color']}_{product['talla']}"
                if key not in grouped_products or product['price'] < grouped_products[key]['price']:
                    grouped_products[key] = product
            
            full_prompt += "🛍️ PRODUCTOS DISPONIBLES (MEJORES PRECIOS):\n"
            for product in list(grouped_products.values())[:3]:
                # Calcular descuentos reales
                price_base = product['price']
                price_100 = product.get('price_100', price_base * 0.9)
                price_200 = product.get('price_200', price_base * 0.85)
                
                discount_100 = int((price_base - price_100) / price_base * 100)
                discount_200 = int((price_base - price_200) / price_base * 100)
                
                full_prompt += f"• {product['name']}\n"
                full_prompt += f"  💰 Precios: ${price_base} c/u (1-49), ${price_100:.0f} c/u (50-99), ${price_200:.0f} c/u (100+)\n"
                full_prompt += f"  📦 Stock: {product['stock']} unidades disponibles\n"
                full_prompt += f"  🎯 Beneficio: {discount_200}% de descuento en compras grandes\n\n"
        
        full_prompt += f"💬 MENSAJE ACTUAL DEL CLIENTE: '{message}'\n\n"
        
        full_prompt += """
🎯 TU RESPUESTA DEBE:
1. Ser cálida y profesional como un vendedor experimentado
2. Si es confirmación de pedido: empezar con "¡PEDIDO CONFIRMADO!"
3. Incluir cálculos automáticos de precio total
4. Hacer una pregunta de seguimiento
5. Mostrar entusiasmo por ayudar

FORMATO DE EJEMPLO PARA CONFIRMACIÓN:
"¡PEDIDO CONFIRMADO! 🎉

Excelente decisión, Juan. Quedó perfecto tu pedido:
• 50 camisetas blancas talla L a $445 c/u
• Total: $22,250 (precio mayorista aplicado)

Ya está anotado y listo para procesar. ¿Necesitás algo más para tu empresa o con esto estás?"

¡Dale vida a la conversación! 🚀
"""
        
        return full_prompt
    
    async def execute_product_search(self, user_id: str, query: str):
        """Busca productos REALES en la base de datos (sin duplicados)"""
        
        db = SessionLocal()
        try:
            # Extraer tipo de prenda del mensaje
            clothing_type = self.extract_clothing_type(query)
            color = self.extract_color(query)
            size = self.extract_size(query)  # Agregar extracción de talla
            
            print(f"🔍 Búsqueda: tipo='{clothing_type}', color='{color}', talla='{size}'")
            
            # Construir query base
            products_query = db.query(models.Product)
            
            # Filtrar por tipo de prenda
            if clothing_type:
                products_query = products_query.filter(
                    models.Product.tipo_prenda.ilike(f"%{clothing_type}%")
                )
            
            # Filtrar por color si se menciona
            if color:
                products_query = products_query.filter(
                    models.Product.color.ilike(f"%{color}%")
                )
            
            # Filtrar por talla si se menciona
            if size:
                products_query = products_query.filter(
                    models.Product.talla.ilike(f"%{size}%")
                )
            
            # Solo productos con stock > 0
            products_query = products_query.filter(models.Product.stock > 0)
            
            # Ordenar por precio (más barato primero)
            products_query = products_query.order_by(models.Product.precio_50_u.asc())
            
            # Obtener productos
            products = products_query.limit(10).all()
            
            # Filtrar duplicados por tipo-color-talla, manteniendo el más barato
            unique_products = {}
            for p in products:
                key = f"{p.tipo_prenda}_{p.color}_{p.talla}"
                if key not in unique_products or p.precio_50_u < unique_products[key].precio_50_u:
                    unique_products[key] = p
            
            # Convertir a lista y limitar a 5 productos únicos
            final_products = list(unique_products.values())[:5]
            
            # Actualizar contexto con productos únicos
            context = self.get_or_create_context(user_id)
            context["last_searched_products"] = [
                {
                    "id": p.id,
                    "name": p.name,
                    "price": p.precio_50_u,
                    "price_100": p.precio_100_u,
                    "price_200": p.precio_200_u,
                    "stock": p.stock,
                    "tipo": p.tipo_prenda,
                    "color": p.color,
                    "talla": p.talla
                }
                for p in final_products
            ]
            
            print(f"🔍 Encontrados {len(final_products)} productos únicos para '{query}'")
            
        except Exception as e:
            print(f"Error en búsqueda de productos: {e}")
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
            for variant in variants:
                if variant in query_lower:
                    return clothing_type
        
        # Si menciona "ropa" genericamente, priorizar camisetas
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
            for variant in variants:
                if variant in query_lower:
                    return color
        return None
    
    def extract_size(self, query: str) -> str:
        """Extrae talla del mensaje"""
        sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL']
        
        query_upper = query.upper()
        for size in sizes:
            if f' {size} ' in f' {query_upper} ' or f'TALLA {size}' in query_upper:
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