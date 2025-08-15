import os
import json
import google.generativeai as genai
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models, crud, schemas
from datetime import datetime, timedelta

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
                print(f"🔍 Query Agent usando API Key #{self.current_key_index + 1}")
                return True
            except:
                return False
        return False
    
    def _try_next_key(self):
        self.current_key_index += 1
        return self._setup_current_key()

    async def extract_structured_intent(self, user_message: str, conversation_context: Dict) -> Dict:
        """Extrae intención estructurada del mensaje usando prompt específico"""
        
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
- TALLA: "XS", "S", "M", "L", "XL", "XXL", "XXXL"

MAPEOS AUTOMÁTICOS APLICADOS:
- chaquetas/camperas/abrigos → sudadera
- remeras/playeras/polos → camiseta  
- jeans → pantalón
- polleras → falda

Analiza el mensaje y responde SOLAMENTE con JSON válido:

{{
    "intent_type": "search_products" | "confirm_order" | "edit_order" | "ask_stock" | "general_question",
    "confidence": 0.0-1.0,
    "extracted_data": {{
        "product_filters": {{
            "tipo_prenda": "pantalón|camiseta|falda|sudadera|camisa|null",
            "color": "blanco|negro|azul|verde|gris|rojo|amarillo|null", 
            "talla": "XS|S|M|L|XL|XXL|XXXL|null"
        }},
        "quantity": number_or_null,
        "action_keywords": ["palabras", "clave"],
        "is_continuation": true_si_continua_conversacion_previa,
        "specific_request": "descripción_específica",
        "original_term": "{original_term}",
        "mapped_term": "{mapped_term}"
    }}
}}

EJEMPLOS DE MAPEO:
- "chaquetas negras para construcción" → {{"tipo_prenda": "sudadera", "color": "negro"}}
- "remeras blancas talle L" → {{"tipo_prenda": "camiseta", "color": "blanco", "talla": "L"}}
- "jeans azules" → {{"tipo_prenda": "pantalón", "color": "azul"}}

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
                
                return parsed_intent
                
        except Exception as e:
            print(f"❌ Error extrayendo intención: {e}")
        
        # ✅ FALLBACK MEJORADO CON TODOS LOS MAPEOS
        user_lower = user_message.lower()
        
        # Chaquetas/camperas/abrigos → sudadera
        if any(word in user_lower for word in ["chaqueta", "campera", "abrigo", "jacket", "buzo"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.8,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "sudadera", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["chaqueta", "mapped"],
                    "is_continuation": False,
                    "specific_request": user_message,
                    "original_term": "chaquetas",
                    "mapped_term": "sudadera"
                }
            }
        
        # Remeras/playeras → camiseta
        if any(word in user_lower for word in ["remera", "playera", "polo", "polera"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.8,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "camiseta", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["remera", "mapped"],
                    "is_continuation": False,
                    "specific_request": user_message,
                    "original_term": "remeras",
                    "mapped_term": "camiseta"
                }
            }
        
        # Jeans → pantalón
        if any(word in user_lower for word in ["jean", "jeans"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.8,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "pantalón", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["jean", "mapped"],
                    "is_continuation": False,
                    "specific_request": user_message,
                    "original_term": "jeans",
                    "mapped_term": "pantalón"
                }
            }
        
        # Búsquedas directas de productos existentes
        if any(word in user_lower for word in ["camiseta", "camisetas"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "camiseta", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["camiseta"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
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
        
        if any(word in user_lower for word in ["camisa", "camisas"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "camisa", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["camisa"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
        if any(word in user_lower for word in ["falda", "faldas", "pollera", "polleras"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "falda", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["falda"],
                    "is_continuation": False,
                    "specific_request": user_message
                }
            }
        
        if any(word in user_lower for word in ["sudadera", "sudaderas"]):
            return {
                "intent_type": "search_products",
                "confidence": 0.9,
                "extracted_data": {
                    "product_filters": {"tipo_prenda": "sudadera", "color": None, "talla": None},
                    "quantity": None,
                    "action_keywords": ["sudadera"],
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
        
        if intent_type == "search_products":
            return await self._search_products(extracted_data)
        
        elif intent_type == "confirm_order":
            return await self._create_order(extracted_data, user_phone, conversation_id)
        
        elif intent_type == "edit_order":
            return await self._edit_recent_order(extracted_data, user_phone)
        
        elif intent_type == "ask_stock":
            return await self._check_stock(extracted_data)
        
        else:
            return {"operation": "none", "success": True, "data": None}
    
    async def _search_products(self, filters: Dict) -> Dict:
        """Busca productos con filtros específicos"""
        
        db = SessionLocal()
        try:
            product_filters = filters.get("product_filters", {})
            
            # ✅ USAR CAMPO CORRECTO: stock
            query = db.query(models.Product).filter(models.Product.stock > 0)
            
            # Aplicar filtros
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
            
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
            
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
            
            # Obtener resultados
            products = query.limit(10).all()
            
            # Formatear para respuesta usando schema
            formatted_products = []
            for product in products:
                # ✅ USAR SCHEMA PARA VALIDACIÓN Y FORMATEO
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
                    "descripcion": product.descripcion or 'Material de calidad premium',
                    "categoria": product.categoria or 'General'
                }
                
                # Validar con schema
                try:
                    validated_product = schemas.ProductAIResponse(**product_data)
                    formatted_products.append(validated_product.dict())
                except Exception as e:
                    print(f"⚠️ Error validando producto {product.id}: {e}")
                    # Fallback sin validación
                    formatted_products.append(product_data)
            
            print(f"🔍 Búsqueda ejecutada: {len(formatted_products)} productos encontrados")
            
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
            
            # ✅ USAR CAMPO CORRECTO: stock
            query = db.query(models.Product).filter(models.Product.stock >= quantity)
            
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
            
            product = query.first()
            
            if not product:
                return {
                    "operation": "create_order",
                    "success": False,
                    "error": "No hay producto disponible que coincida con los filtros"
                }
            
            # Crear pedido usando el CRUD existente
            order_data = schemas.OrderCreate(
                product_id=product.id,
                qty=quantity,
                buyer=f"Cliente WhatsApp {user_phone}"
            )
            
            new_order = crud.create_order(db, order_data)
            
            new_order.user_phone = user_phone
            if conversation_id:
                new_order.conversation_id = conversation_id
            db.commit()
            db.refresh(new_order)
            
            print(f"🛒 Pedido creado: ID {new_order.id}, {quantity} unidades")
            
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
                    "stock_remaining": product.stock - quantity  # ✅ CORREGIDO
                }
            }
            
        except Exception as e:
            db.rollback()
            print(f"❌ Error creando pedido: {e}")
            return {
                "operation": "create_order",
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
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
            
            # ✅ USAR CAMPO CORRECTO: stock
            query = db.query(models.Product).filter(models.Product.stock > 0)
            
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
                    "descripcion": product.descripcion if product.descripcion else 'Material de calidad premium',
                    "categoria": product.categoria if product.categoria else 'General'
                })
                total_stock += product.stock  # ✅ CORREGIDO
            
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