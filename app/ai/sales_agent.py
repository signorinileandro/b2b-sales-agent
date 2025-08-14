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
        """Detecta si el mensaje contiene intención de pedido"""
        
        order_keywords = [
            'quiero comprar', 'necesito', 'me gustaría', 'quisiera',
            'confirmo', 'pedido', 'orden', 'solicito', 'encargar'
        ]
        
        quantity_patterns = [
            r'(\d+)\s*(unidad|camiseta|pantalon|sudadera|falda)',
            r'(un|una|dos|tres|cuatro|cinco|diez|veinte)',
            r'\b(\d+)\b'
        ]
        
        message_lower = message.lower()
        
        # Detectar palabras clave de pedido
        has_order_intent = any(keyword in message_lower for keyword in order_keywords)
        
        # Extraer cantidad
        quantities = []
        for pattern in quantity_patterns:
            matches = re.findall(pattern, message_lower)
            for match in matches:
                try:
                    if match.isdigit():
                        quantities.append(int(match))
                    elif match in {'un', 'una', 'uno'}: quantities.append(1)
                    elif match in {'dos'}: quantities.append(2)
                    elif match in {'tres'}: quantities.append(3)
                    elif match in {'cinco'}: quantities.append(5)
                    elif match in {'diez'}: quantities.append(10)
                except: pass
        
        # Productos del contexto
        products_in_context = context.get("last_searched_products", [])
        
        return {
            "is_order": has_order_intent and len(quantities) > 0 and len(products_in_context) > 0,
            "intent_type": "order" if has_order_intent else "search",
            "quantities": quantities,
            "products_mentioned": products_in_context[:3] if products_in_context else [],
            "confidence": 0.8 if (has_order_intent and quantities) else 0.3
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
        """Construye el prompt completo con productos REALES"""
        
        system_prompt = """
Eres un agente de ventas B2B experto y práctico. Tu objetivo es facilitar las compras, no complicarlas.

REGLAS IMPORTANTES:
1. Si hay múltiples productos similares (mismo tipo, color, talla), SIEMPRE recomienda automáticamente el más barato
2. No ofrezcas opciones innecesarias - el cliente busca eficiencia
3. Cuando detectes una búsqueda específica, muestra máximo 3 productos más relevantes
4. Para pedidos con cantidades específicas, calcula el precio total automáticamente

PRECIOS POR VOLUMEN:
- 1-49 unidades: Precio base (precio_50_u)
- 50-99 unidades: Precio base (precio_50_u)
- 100-199 unidades: precio_100_u (mejor precio)
- 200+ unidades: precio_200_u (mejor precio)

FORMATO DE RESPUESTAS EFICIENTES:
✅ "Tenemos camisetas blancas L a $445 c/u (la más económica). Para 50 unidades: $22,250 total"
❌ "Tenemos opción 1 y opción 2, ¿cuál prefiere?"

CUANDO HAY PRODUCTOS SIMILARES:
- Agrupa por tipo y características
- Muestra solo el más barato de cada grupo
- Menciona si hay opciones premium solo si el cliente pregunta

DETECCIÓN DE PEDIDOS:
- Si menciona cantidad específica + producto específico → confirma pedido inmediatamente
- Calcula precio con descuentos automáticamente
- Resume: "Confirmo pedido: X unidades de Y a $Z c/u = $TOTAL"

PERSONALIDAD:
- Directo y eficiente
- Recomienda lo mejor para el cliente (precio/calidad)
- No pierdas tiempo en opciones obvias
"""
        
        full_prompt = system_prompt + "\n\n"
        
        # Agregar contexto de intención de pedido
        if order_intent and order_intent.get("is_order", False):
            full_prompt += f"🛒 PEDIDO DETECTADO:\n"
            full_prompt += f"- Productos mencionados: {len(order_intent.get('products_mentioned', []))}\n"
            full_prompt += f"- Cantidades detectadas: {order_intent.get('quantities', [])}\n"
            full_prompt += f"INSTRUCCIÓN: Procesa este pedido inmediatamente con el producto más barato disponible.\n\n"
        
        # Agregar historial reciente
        if context["conversation_history"]:
            full_prompt += "CONVERSACIÓN PREVIA:\n"
            for item in context["conversation_history"][-2:]:
                full_prompt += f"Cliente: {item['user']}\n"
                full_prompt += f"Asistente: {item['assistant']}\n\n"
        
        # Agregar productos REALES encontrados (OPTIMIZADO - evitar duplicados)
        if context.get("last_searched_products"):
            # Agrupar productos similares y mostrar solo el más barato
            grouped_products = {}
            
            for product in context["last_searched_products"]:
                key = f"{product['tipo']}_{product['color']}_{product['talla']}"
                
                if key not in grouped_products or product['price'] < grouped_products[key]['price']:
                    grouped_products[key] = product
            
            full_prompt += "PRODUCTOS DISPONIBLES (MEJORES PRECIOS):\n"
            for product in list(grouped_products.values())[:3]:  # Máximo 3 productos
                # Calcular precio con descuentos
                price_50 = product['price']
                price_100 = price_50 * 0.9  # 10% descuento estimado
                price_200 = price_50 * 0.85  # 15% descuento estimado
                
                full_prompt += f"- {product['name']} | ${price_50} c/u (1-99 u), ${price_100:.0f} c/u (100-199 u), ${price_200:.0f} c/u (200+ u) | Stock: {product['stock']}\n"
            full_prompt += "\n"
        
        full_prompt += f"MENSAJE ACTUAL: {message}\n\n"
        full_prompt += "RESPONDE: Sé directo, recomienda la mejor opción automáticamente, calcula precios totales cuando sea necesario."
        
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