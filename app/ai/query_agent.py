import os
import json
import google.generativeai as genai
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models, crud, schemas
from datetime import datetime, timedelta
from sqlalchemy import or_

class QueryAgent:
    """Agente especializado en consultas y operaciones de base de datos"""
    
    def __init__(self):
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.model = None
        self._setup_current_key()
    
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
                print(f"🔑 Query Agent usando API Key #{self.current_key_index + 1}")
                return True
            except:
                return False
        return False
    
    def _try_next_key(self):
        self.current_key_index += 1
        return self._setup_current_key()

    async def extract_structured_intent(self, user_message: str, conversation_context: Dict) -> Dict:
        """Extrae intención estructurada del mensaje usando prompt específico"""
        
        print(f"🎯 ANALIZANDO MENSAJE: '{user_message}'")
        
        # ✅ DETECCIÓN MEJORADA DE CONFIRM_ORDER ANTES DE GEMINI
        user_lower = user_message.lower()
        
        # Palabras clave para CONFIRM_ORDER
        confirm_keywords = [
            "pedido", "encargar", "quiero", "necesito", "generame", 
            "haceme", "confirmar", "solicitar", "pedir", "generar"
        ]
        
        quantity_indicators = [
            "50", "100", "200", "80", "unidades", "cantidad"
        ]
        
        has_confirm_keyword = any(word in user_lower for word in confirm_keywords)
        has_quantity = any(word in user_lower for word in quantity_indicators)
        
        print(f"🔍 Análisis CONFIRM_ORDER:")
        print(f"  - Tiene palabra de confirmación: {has_confirm_keyword}")
        print(f"  - Tiene cantidad: {has_quantity}")
        
        if has_confirm_keyword and (has_quantity or any(word.isdigit() for word in user_message.split())):
            print("🎯 DETECTADO CONFIRM_ORDER directo!")
            
            # Extraer cantidad
            quantity = 50  # Default
            for word in user_message.split():
                if word.isdigit():
                    quantity = int(word)
                    break
            
            # Detectar producto específico
            filters = {"tipo_prenda": None, "color": None, "talla": None}
            
            # Tipo de prenda
            if any(word in user_lower for word in ["pantalon", "pantalones"]):
                filters["tipo_prenda"] = "pantalón"
            elif any(word in user_lower for word in ["camiseta", "camisetas", "remera", "remeras"]):
                filters["tipo_prenda"] = "camiseta"
            elif any(word in user_lower for word in ["sudadera", "sudaderas", "buzo", "buzos"]):
                filters["tipo_prenda"] = "sudadera"
            elif any(word in user_lower for word in ["camisa", "camisas"]):
                filters["tipo_prenda"] = "camisa"
            elif any(word in user_lower for word in ["falda", "faldas"]):
                filters["tipo_prenda"] = "falda"
            
            # Color
            for color in ["verde", "azul", "negro", "blanco", "rojo", "amarillo", "gris"]:
                if color in user_lower:
                    filters["color"] = color
                    break
            
            # Talla
            for talla in ["S", "M", "L", "XL", "XXL"]:
                if f"talle {talla.lower()}" in user_lower or f"talla {talla.lower()}" in user_lower or f" {talla.lower()}" in user_lower:
                    filters["talla"] = talla
                    break
            
            print(f"✅ CONFIRM_ORDER detectado: quantity={quantity}, filters={filters}")
            
            return {
                "intent_type": "confirm_order",
                "confidence": 0.95,
                "extracted_data": {
                    "product_filters": filters,
                    "quantity": quantity,
                    "action_keywords": ["pedido", "confirmar"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
        # ✅ MAPEO ACTUALIZADO CON PRODUCTOS REALES DE LA BD
        user_message_mapped = user_message.lower()
        
        # Mapear términos que el cliente usa vs lo que hay REALMENTE en la DB
        mappings = {
            # CHAQUETAS Y ABRIGOS → SUDADERA (más similar)
            "chaquetas": "sudadera",
            "chaqueta": "sudadera", 
            "camperas": "sudadera",
            "campera": "sudadera",
            "abrigos": "sudadera",
            "abrigo": "sudadera",
            "jackets": "sudadera",
            "jacket": "sudadera",
            "buzos": "sudadera",
            "buzo": "sudadera",
            
            # REMERAS Y PLAYERAS → CAMISETA
            "remeras": "camiseta",
            "remera": "camiseta",
            "playeras": "camiseta",
            "playera": "camiseta",
            "polos": "camiseta",
            "polo": "camiseta",
            "poleras": "camiseta",
            
            # CAMISAS (ya existe, pero por si hay variaciones)
            "shirts": "camisa",
            "shirt": "camisa",
            
            # PANTALONES (ya existe, pero por si hay variaciones)
            "pantalones": "pantalón",
            "jeans": "pantalón",
            "jean": "pantalón",
            
            # FALDAS (ya existe, pero por si hay variaciones)
            "faldas": "falda",
            "polleras": "falda",
            "pollera": "falda"
        }
        
        original_term = None
        mapped_term = None
        
        for original, mapped in mappings.items():
            if original in user_message_mapped:
                original_term = original
                mapped_term = mapped
                user_message_mapped = user_message_mapped.replace(original, mapped)
                print(f"🔄 Mapeo aplicado: '{original}' → '{mapped}'")
                break

        extraction_prompt = f"""
Eres un asistente especializado en extraer intenciones de mensajes de clientes B2B de textiles.

CONTEXTO DE LA CONVERSACIÓN:
- Productos mencionados anteriormente: {conversation_context.get('last_searched_products', [])}
- Última consulta: "{conversation_context.get('last_search_query', '')}"
- Historial: {conversation_context.get('conversation_history', [])}

MENSAJE ORIGINAL DEL CLIENTE: "{user_message}"
MENSAJE PROCESADO: "{user_message_mapped}"

IMPORTANTE: Los productos disponibles son EXACTAMENTE:
- TIPO_PRENDA: "pantalón", "camiseta", "falda", "sudadera", "camisa"
- COLOR: "blanco", "negro", "azul", "verde", "gris", "rojo", "amarillo"
- TALLA: "S", "M", "L", "XL", "XXL"

PALABRAS CLAVE CRÍTICAS PARA CONFIRM_ORDER:
- "pedido", "encargar", "quiero", "necesito", "generame", "haceme", "confirmar", "solicitar", "pedir", "generar"
- CUALQUIER número como "50", "100", "200" junto con "unidades"

Si el mensaje contiene CUALQUIERA de estas palabras + cantidad/número, es CONFIRM_ORDER, NO search_products.

EJEMPLOS ESPECÍFICOS DE CONFIRM_ORDER:
- "generame un pedido por 50 pantalones gris en talla L" → {{"intent_type": "confirm_order", "quantity": 50, "product_filters": {{"tipo_prenda": "pantalón", "color": "gris", "talla": "L"}}}}
- "quiero encargar 50 pantalones gris en talla L" → {{"intent_type": "confirm_order", "quantity": 50, "product_filters": {{"tipo_prenda": "pantalón", "color": "gris", "talla": "L"}}}}
- "haceme el pedido por 80 camisetas azules" → {{"intent_type": "confirm_order", "quantity": 80, "product_filters": {{"tipo_prenda": "camiseta", "color": "azul"}}}}

Analiza el mensaje y responde SOLAMENTE con JSON válido:

{{
    "intent_type": "search_products" | "confirm_order" | "edit_order" | "ask_stock" | "general_question",
    "confidence": 0.0-1.0,
    "extracted_data": {{
        "product_filters": {{
            "tipo_prenda": "pantalón|camiseta|falda|sudadera|camisa|null",
            "color": "blanco|negro|azul|verde|gris|rojo|amarillo|null", 
            "talla": "S|M|L|XL|XXL|null"
        }},
        "quantity": number_or_null,
        "action_keywords": ["palabras", "clave"],
        "is_continuation": true_si_continua_conversacion_previa,
        "specific_request": "descripción_específica",
        "original_term": "{original_term}",
        "mapped_term": "{mapped_term}"
    }}
}}

Responde SOLO con el JSON, sin explicaciones adicionales.
"""

        try:
            response = await self._make_gemini_request(extraction_prompt)
            if response:
                # Limpiar respuesta y extraer JSON
                response_clean = response.strip()
                if response_clean.startswith("```json"):
                    response_clean = response_clean[7:-3]
                elif response_clean.startswith("```"):
                    response_clean = response_clean[3:-3]
                
                parsed_intent = json.loads(response_clean)
                
                # ✅ AGREGAR INFO DE MAPEO AL RESULTADO
                if original_term and mapped_term:
                    parsed_intent["extracted_data"]["original_term"] = original_term
                    parsed_intent["extracted_data"]["mapped_term"] = mapped_term
                
                print(f"✅ Gemini result: {parsed_intent['intent_type']} (confidence: {parsed_intent['confidence']})")
                
                return parsed_intent
                
        except Exception as e:
            print(f"❌ Error extrayendo intención: {e}")
        
        # ✅ FALLBACK MEJORADO
        print("🔄 Usando fallback detection...")
        
        # Fallback para confirm_order si Gemini falla
        if has_confirm_keyword and has_quantity:
            print("✅ Fallback CONFIRM_ORDER activado")
            
            quantity = 50
            for word in user_message.split():
                if word.isdigit():
                    quantity = int(word)
                    break
            
            filters = {"tipo_prenda": None, "color": None, "talla": None}
            
            if "pantalon" in user_lower:
                filters["tipo_prenda"] = "pantalón"
            if "gris" in user_lower:
                filters["color"] = "gris"
            if "talla l" in user_lower or " l " in user_lower:
                filters["talla"] = "L"
            
            return {
                "intent_type": "confirm_order",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": filters,
                    "quantity": quantity,
                    "action_keywords": ["pedido", "fallback"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
        # Otros fallbacks...
        if any(word in user_lower for word in ["pantalón", "pantalones"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "pantalón", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["pantalón"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
        return {
            "intent_type": "general_question",
            "confidence": 0.3,
            "extracted_data": {
                "product_filters": {"tipo_prenda": None, "color": None, "talla": None},
                "quantity": None,
                "action_keywords": [],
                "is_continuation": False,
                "specific_request": user_message
            }
        }
    
    async def execute_database_operation(self, intent: Dict, user_phone: str, conversation_id: int = None) -> Dict:
        """Ejecuta operación en base de datos según la intención"""
        
        intent_type = intent.get("intent_type")
        extracted_data = intent.get("extracted_data", {})
        
        print(f"🗄️ Ejecutando operación: {intent_type}")
        
        if intent_type == "search_products":
            return await self._search_products(extracted_data)
        
        elif intent_type == "confirm_order":
            print(f"🛒 EJECUTANDO CREATE_ORDER con datos: {extracted_data}")
            return await self._create_order(extracted_data, user_phone, conversation_id)
        
        elif intent_type == "edit_order":
            return await self._edit_recent_order(extracted_data, user_phone)
        
        elif intent_type == "ask_stock":
            return await self._check_stock(extracted_data)
        
        else:
            return {"operation": "none", "success": True, "data": None}
    
    async def _search_products(self, filters: Dict) -> Dict:
        """Busca productos con filtros específicos (mejorada con fallback y búsqueda flexible)"""
        
        db = SessionLocal()
        try:
            product_filters = filters.get("product_filters", {})
            
            # ✅ NORMALIZAR FILTROS: convertir "null", "None", "" en None real
            for key in ["tipo_prenda", "color", "talla"]:
                val = product_filters.get(key)
                if isinstance(val, str):
                    val_clean = val.strip().lower()
                    if val_clean in ["null", "none", ""]:
                        product_filters[key] = None
                        print(f"🔄 Filtro normalizado: {key} = '{val}' → None")
            
            print(f"🔍 Filtros después de normalización: {product_filters}")
            
            # Base query: solo productos con stock
            query = db.query(models.Product).filter(models.Product.stock > 0)
            
            tipo = product_filters.get("tipo_prenda")
            color = product_filters.get("color")
            talla = product_filters.get("talla")

            # ✅ APLICAR FILTROS SOLO SI NO SON None
            if tipo:
                tipo_lower = tipo.lower()
                query = query.filter(
                    models.Product.tipo_prenda.ilike(f"%{tipo_lower}%")
                )
                print(f"🔍 Filtro aplicado: tipo_prenda ILIKE '%{tipo_lower}%'")
            
            if color:
                query = query.filter(models.Product.color.ilike(f"%{color}%"))
                print(f"🔍 Filtro aplicado: color ILIKE '%{color}%'")
            
            if talla:
                query = query.filter(models.Product.talla.ilike(f"%{talla}%"))
                print(f"🔍 Filtro aplicado: talla ILIKE '%{talla}%'")
            
            # Primer intento
            products = query.limit(10).all()
            print(f"🔍 Productos encontrados en primer intento: {len(products)}")

            # Formateo de productos
            formatted_products = []
            for product in products:
                product_data = {
                    "id": product.id,
                    "name": product.name,
                    "tipo_prenda": product.tipo_prenda,
                    "color": product.color,
                    "talla": product.talla,
                    "precio_50_u": product.precio_50_u,
                    "precio_100_u": product.precio_100_u,
                    "precio_200_u": product.precio_200_u,
                    "stock": product.stock,
                    "descripcion": product.descripcion or 'Material resistente y de calidad premium para uso profesional',
                    "categoria": product.categoria or 'Textil Profesional'
                }
                formatted_products.append(product_data)

            print(f"✅ Búsqueda ejecutada: {len(formatted_products)} productos encontrados")
            
            return {
                "operation": "search_products",
                "success": True,
                "data": {
                    "products": formatted_products,
                    "filters_applied": product_filters,
                    "total_found": len(formatted_products)
                }
            }
            
        except Exception as e:
            print(f"❌ Error en búsqueda: {e}")
            return {
                "operation": "search_products", 
                "success": False, 
                "error": str(e)
            }
        finally:
            db.close()
    
    async def _create_order(self, data: Dict, user_phone: str, conversation_id: int = None) -> Dict:
        """Crea pedido con descuento automático de stock"""
        
        db = SessionLocal()
        try:
            quantity = data.get("quantity", 50)
            product_filters = data.get("product_filters", {})
            
            print(f"🛒 Creando pedido: quantity={quantity}, filters={product_filters}")
            
            # ✅ NORMALIZAR FILTROS IGUAL QUE EN _search_products
            for key in ["tipo_prenda", "color", "talla"]:
                val = product_filters.get(key)
                if isinstance(val, str):
                    val_clean = val.strip().lower()
                    if val_clean in ["null", "none", ""]:
                        product_filters[key] = None
                        print(f"🔄 Filtro normalizado en create_order: {key} = '{val}' → None")
        
            # ✅ BASE QUERY: solo productos con stock suficiente
            query = db.query(models.Product).filter(models.Product.stock >= quantity)
            
            # ✅ APLICAR FILTROS SOLO SI NO SON None
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
                print(f"🔍 Filtro pedido: tipo_prenda ILIKE '%{product_filters['tipo_prenda']}%'")
            
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
                print(f"🔍 Filtro pedido: color ILIKE '%{product_filters['color']}%'")
            
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
                print(f"🔍 Filtro pedido: talla ILIKE '%{product_filters['talla']}%'")
            
            # Buscar primer producto que coincida
            product = query.first()
            
            if not product:
                print("❌ No se encontró producto para el pedido")
                
                # DEBUG: Ver qué productos hay disponibles
                available = db.query(models.Product).filter(models.Product.stock >= quantity).limit(3).all()
                print(f"📊 Productos disponibles con stock >= {quantity}:")
                for p in available:
                    print(f"  - {p.name} | {p.tipo_prenda} | {p.color} | {p.talla} | Stock: {p.stock}")
                
                return {
                    "operation": "create_order",
                    "success": False,
                    "error": f"No hay producto disponible que coincida con los filtros y tenga stock suficiente (necesario: {quantity})"
                }
            
            print(f"✅ Producto encontrado para pedido: {product.name} (Stock: {product.stock})")
            
            # Crear pedido usando el CRUD existente
            order_data = schemas.OrderCreate(
                product_id=product.id,
                qty=quantity,
                buyer=f"Cliente WhatsApp {user_phone}"
            )
            
            new_order = crud.create_order(db, order_data)
            
            # ✅ AGREGAR DATOS DE WHATSAPP
            new_order.user_phone = user_phone
            if conversation_id:
                new_order.conversation_id = conversation_id
            db.commit()
            db.refresh(new_order)
            
            print(f"🛒 Pedido creado EXITOSAMENTE: ID {new_order.id}, {quantity} unidades")
            
            return {
                "operation": "create_order",
                "success": True,
                "data": {
                    "order_id": new_order.id,
                    "product": {
                        "id": product.id,
                        "name": product.name,
                        "tipo_prenda": product.tipo_prenda,
                        "color": product.color,
                        "talla": product.talla,
                        "precio_unitario": product.precio_50_u
                    },
                    "quantity": quantity,
                    "total_price": product.precio_50_u * quantity,
                    "stock_remaining": product.stock - quantity
                }
            }
            
        except Exception as e:
            db.rollback()
            print(f"❌ Error creando pedido: {e}")
            import traceback
            traceback.print_exc()
            return {
                "operation": "create_order",
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
    # Otros métodos sin cambios...
    async def _edit_recent_order(self, data: Dict, user_phone: str) -> Dict:
        """Edita pedido reciente si está dentro de los 5 minutos"""
        
        db = SessionLocal()
        try:
            # Buscar pedido más reciente
            recent_time = datetime.utcnow() - timedelta(minutes=10)
            recent_order = db.query(models.Order).filter(
                models.Order.user_phone == user_phone,
                models.Order.created_at >= recent_time,
                models.Order.status == "pending"
            ).order_by(models.Order.created_at.desc()).first()
            
            if not recent_order:
                return {
                    "operation": "edit_order",
                    "success": False,
                    "error": "No hay pedidos recientes para editar"
                }
            
            # Verificar ventana de 5 minutos
            if datetime.utcnow() - recent_order.created_at > timedelta(minutes=5):
                minutes_passed = int((datetime.utcnow() - recent_order.created_at).total_seconds() / 60)
                return {
                    "operation": "edit_order",
                    "success": False,
                    "error": f"Ya pasaron {minutes_passed} minutos. Solo se puede editar en los primeros 5 minutos."
                }
            
            new_quantity = data.get("quantity")
            if not new_quantity:
                return {
                    "operation": "edit_order",
                    "success": False,
                    "error": "No se especificó la nueva cantidad"
                }
            
            # Usar CRUD existente que maneja stock
            old_quantity = recent_order.qty
            updated_order = crud.update_order(db, recent_order.id, new_quantity)
            
            # Obtener producto para calcular nuevo precio
            product = db.query(models.Product).filter(models.Product.id == recent_order.product_id).first()
            
            print(f"✏️ Pedido editado: {old_quantity} → {new_quantity} unidades")
            
            return {
                "operation": "edit_order",
                "success": True,
                "data": {
                    "order_id": recent_order.id,
                    "old_quantity": old_quantity,
                    "new_quantity": new_quantity,
                    "product_name": product.name if product else "Producto",
                    "new_total_price": (product.precio_50_u * new_quantity) if product else 0
                }
            }
            
        except Exception as e:
            print(f"❌ Error editando pedido: {e}")
            return {
                "operation": "edit_order",
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
    async def _check_stock(self, data: Dict) -> Dict:
        """Verifica stock disponible"""
        
        db = SessionLocal()
        try:
            product_filters = data.get("product_filters", {})
            
            # ✅ NORMALIZAR FILTROS
            for key in ["tipo_prenda", "color", "talla"]:
                val = product_filters.get(key)
                if isinstance(val, str):
                    val_clean = val.strip().lower()
                    if val_clean in ["null", "none", ""]:
                        product_filters[key] = None
        
            # ✅ BASE QUERY
            query = db.query(models.Product).filter(models.Product.stock > 0)
            
            # ✅ APLICAR FILTROS SOLO SI NO SON None
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
            
            products = query.all()
            
            stock_info = []
            total_stock = 0
            
            for product in products:
                stock_info.append({
                    "id": product.id,
                    "name": product.name,
                    "stock": product.stock,
                    "precio_50_u": product.precio_50_u,
                    "tipo_prenda": product.tipo_prenda,  
                    "color": product.color,              
                    "talla": product.talla,
                    "descripcion": product.descripcion or 'Material resistente y de calidad premium para uso profesional',
                    "categoria": product.categoria or 'Textil Profesional'
                })
                total_stock += product.stock
            
            return {
                "operation": "check_stock",
                "success": True,
                "data": {
                    "products": stock_info,
                    "total_stock": total_stock,
                    "products_available": len(stock_info)
                }
            }
            
        except Exception as e:
            return {
                "operation": "check_stock",
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
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
                        temperature=0.1,  # Más determinístico para extracciones
                        max_output_tokens=500,
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

# Instancia global
query_agent = QueryAgent()