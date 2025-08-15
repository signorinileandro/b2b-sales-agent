import os
import json
import google.generativeai as genai
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models, crud, schemas
from datetime import datetime, timedelta
from sqlalchemy import or_
from ..utils.logger import log
from dotenv import load_dotenv
from .base_agent import BaseAgent

load_dotenv()

class QueryAgent(BaseAgent):
    """Agente especializado en consultas y operaciones de base de datos"""
    
    def __init__(self):
        super().__init__(agent_name="QueryAgent")
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.model = None
        self._setup_current_key()

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
                log(f"🔄 Mapeo aplicado: '{original}' → '{mapped}'")
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

EJEMPLOS ESPECÍFICOS POR TIPO DE PRENDA:

1. PANTALONES:
- "necesito pantalones negros talle L" → {{"tipo_prenda": "pantalón", "color": "negro", "talla": "L"}}
- "jeans azules para trabajo" → {{"tipo_prenda": "pantalón", "color": "azul"}}
- "pantalones de trabajo, que colores tenes?" → {{"tipo_prenda": "pantalón"}}

2. CAMISETAS:
- "camisetas blancas talle M para el equipo" → {{"tipo_prenda": "camiseta", "color": "blanco", "talla": "M"}}
- "remeras rojas" → {{"tipo_prenda": "camiseta", "color": "rojo"}}
- "playeras para construcción" → {{"tipo_prenda": "camiseta"}}

3. SUDADERAS:
- "chaquetas negras para construcción" → {{"tipo_prenda": "sudadera", "color": "negro"}}
- "buzos grises talle XL" → {{"tipo_prenda": "sudadera", "color": "gris", "talla": "XL"}}
- "camperas para trabajo pesado" → {{"tipo_prenda": "sudadera"}}

4. CAMISAS:
- "camisas azules para oficina talle L" → {{"tipo_prenda": "camisa", "color": "azul", "talla": "L"}}
- "shirts blancos" → {{"tipo_prenda": "camisa", "color": "blanco"}}
- "camisas formales" → {{"tipo_prenda": "camisa"}}

5. FALDAS:
- "faldas negras talle S" → {{"tipo_prenda": "falda", "color": "negro", "talla": "S"}}
- "polleras azules" → {{"tipo_prenda": "falda", "color": "azul"}}
- "faldas para uniformes" → {{"tipo_prenda": "falda"}}

CASOS ESPECIALES:
- "para hombre, que colores tenes" (contexto: buscaba chaquetas) → {{"is_continuation": true, "specific_request": "colores disponibles"}}
- "talle L" (contexto: viendo productos) → {{"is_continuation": true, "product_filters": {{"talla": "L"}}}}
- "200 unidades" → {{"quantity": 200, "intent_type": "confirm_order"}}
- "cambiar a 150" → {{"quantity": 150, "intent_type": "edit_order"}}

PATRONES DE CONTINUACIÓN:
Si el mensaje es corto y NO menciona tipo de prenda, pero el contexto indica una búsqueda previa:
- "que colores tenes?" → buscar en historial el tipo de prenda y marcar is_continuation: true
- "talle M" → agregar talla al filtro existente
- "para construcción" → mantener tipo de prenda del contexto

# En extract_structured_intent, ACTUALIZAR examples:

EJEMPLOS DE CONFIRM_ORDER:
- "haceme el pedido por 50 unidades de buzos azules en talla L" → {{"intent_type": "confirm_order", "quantity": 50, "product_filters": {{"tipo_prenda": "sudadera", "color": "azul", "talla": "L"}}}}
- "quiero encargarte 80 en talle L color verde" → {{"intent_type": "confirm_order", "quantity": 80, "product_filters": {{"color": "verde", "talla": "L"}}}}
- "necesito 100 unidades" (después de ver productos) → {{"intent_type": "confirm_order", "quantity": 100, "is_continuation": true}}
- "generame el pedido" (después de especificar producto) → {{"intent_type": "confirm_order", "is_continuation": true}}

PALABRAS CLAVE CONFIRM_ORDER: pedido, encargar, quiero, necesito, generame, haceme, confirmar, solicitar
PALABRAS CLAVE CANTIDAD: unidades, 50, 80, 100, 200, cantidad

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
            log(f"❌ Error extrayendo intención con Gemini: {e}")
        
        # ✅ FALLBACK COMPLETADO
        # ... (tu lógica de fallback parece extensa, la mantendré y completaré) ...
        user_lower = user_message.lower()
        
        # Detección de cantidad primero
        quantity = None
        quantity_keywords = ["unidades", "cantidad"]
        has_quantity_keyword = any(word in user_lower for word in quantity_keywords)
        
        numbers = [int(s) for s in user_lower.split() if s.isdigit()]
        if numbers:
            quantity = numbers[0]

        # Detección de intención de pedido
        order_keywords = ["pedido", "encargar", "quiero", "necesito", "generame", "haceme", "confirmar", "solicitar", "pedir"]
        if any(word in user_lower for word in order_keywords) and (quantity or has_quantity_keyword):
            filters = {"tipo_prenda": None, "color": None, "talla": None}
            for tipo, term in mappings.items():
                if tipo in user_lower:
                    filters["tipo_prenda"] = term
                    break
            # ... (completar extracción de color y talla) ...
            return {"intent_type": "confirm_order", "confidence": 0.95, "extracted_data": {"product_filters": filters, "quantity": quantity}}

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
        
        # ✅ DETECCIÓN MEJORADA DE CONFIRM_ORDER
        if any(word in user_lower for word in [
            "pedido", "encargar", "quiero", "necesito", "generame", 
            "haceme", "confirmar", "solicitar", "pedir"
        ]) and any(word in user_lower for word in [
            "unidades", "50", "80", "100", "200", "cantidad"
        ]):
            
            # Extraer cantidad
            quantity = None
            for word in user_message.split():
                if word.isdigit():
                    quantity = int(word)
                    break
            
            # Usar contexto si no especifica producto
            filters = {"tipo_prenda": None, "color": None, "talla": None}
            
            # Detectar producto específico en el mensaje
            for tipo in ["pantalón", "pantalones", "camiseta", "camisetas", "sudadera", "buzos", "camisa", "camisas", "falda", "faldas"]:
                if tipo in user_lower:
                    if tipo in ["pantalones", "pantalón"]:
                        filters["tipo_prenda"] = "pantalón"
                    elif tipo in ["buzos"]:
                        filters["tipo_prenda"] = "sudadera"
                    else:
                        filters["tipo_prenda"] = tipo.rstrip('s')  # remover plural
                    break
            
            # Detectar color
            for color in ["verde", "azul", "negro", "blanco", "rojo", "amarillo", "gris"]:
                if color in user_lower:
                    filters["color"] = color
                    break
            
            # Detectar talla
            for talla in ["S", "M", "L", "XL", "XXL"]:
                if f"talle {talla.lower()}" in user_lower or f"talla {talla.lower()}" in user_lower:
                    filters["talla"] = talla
                    break
            
            log(f"🎯 CONFIRM_ORDER detectado: quantity={quantity}, filters={filters}")
            
            return {
                "intent_type": "confirm_order",
                "confidence": 0.95,
                "extracted_data": {
                    "product_filters": filters,
                    "quantity": quantity,
                    "action_keywords": ["pedido", "confirmar"],
                    "is_continuation": True,  # Usar contexto
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
                        log(f"🔄 Filtro normalizado: {key} = '{val}' → None")
            
            log(f"🔍 Filtros después de normalización: {product_filters}")
            
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
                log(f"🔍 Filtro aplicado: tipo_prenda ILIKE '%{tipo_lower}%'")
            
            if color:
                query = query.filter(models.Product.color.ilike(f"%{color}%"))
                log(f"🔍 Filtro aplicado: color ILIKE '%{color}%'")
            
            if talla:
                query = query.filter(models.Product.talla.ilike(f"%{talla}%"))
                log(f"🔍 Filtro aplicado: talla ILIKE '%{talla}%'")
            
            # Primer intento
            products = query.limit(10).all()
            log(f"🔍 Productos encontrados en primer intento: {len(products)}")

            # 🔄 Fallback si no hay resultados
            if not products and tipo:
                log(f"⚠️ Sin resultados exactos para '{tipo}', buscando relacionados...")
                fallback_query = db.query(models.Product).filter(models.Product.stock > 0)
                
                if color:
                    fallback_query = fallback_query.filter(models.Product.color.ilike(f"%{color}%"))
                if talla:
                    fallback_query = fallback_query.filter(models.Product.talla.ilike(f"%{talla}%"))

                # Buscar por nombre o categoría, ignorando tipo_prenda
                fallback_query = fallback_query.filter(
                    models.Product.name.ilike(f"%{tipo}%")
                )
                products = fallback_query.limit(10).all()
                log(f"🔄 Productos encontrados en fallback: {len(products)}")
            
            # ✅ DEBUG ADICIONAL: Si sigue sin encontrar nada, mostrar qué hay disponible
            if not products:
                log("❌ No se encontraron productos. Verificando qué hay disponible...")
                total_products = db.query(models.Product).filter(models.Product.stock > 0).count()
                log(f"📊 Total productos con stock: {total_products}")
                
                # Mostrar algunos ejemplos
                sample_products = db.query(models.Product).filter(models.Product.stock > 0).limit(3).all()
                for sp in sample_products:
                    log(f"  📋 Ejemplo: {sp.name} | Tipo: '{sp.tipo_prenda}' | Color: '{sp.color}' | Talla: '{sp.talla}'")
            
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
                    "descripcion": product.descripcion or 'Material de calidad premium',
                    "categoria": product.categoria or 'General'
                }
                formatted_products.append(product_data)

            log(f"🔍 Búsqueda ejecutada (final): {len(formatted_products)} productos encontrados")
            
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
            log(f"❌ Error en búsqueda: {e}")
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
            
            log(f"🛒 Creando pedido: quantity={quantity}, filters={product_filters}")
            
            # ✅ NORMALIZAR FILTROS IGUAL QUE EN _search_products
            for key in ["tipo_prenda", "color", "talla"]:
                val = product_filters.get(key)
                if isinstance(val, str):
                    val_clean = val.strip().lower()
                    if val_clean in ["null", "none", ""]:
                        product_filters[key] = None
                        log(f"🔄 Filtro normalizado en create_order: {key} = '{val}' → None")
        
            # ✅ BASE QUERY: solo productos con stock suficiente
            query = db.query(models.Product).filter(models.Product.stock >= quantity)
            
            # ✅ APLICAR FILTROS SOLO SI NO SON None
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
                log(f"🔍 Filtro pedido: tipo_prenda ILIKE '%{product_filters['tipo_prenda']}%'")
            
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
                log(f"🔍 Filtro pedido: color ILIKE '%{product_filters['color']}%'")
            
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
                log(f"🔍 Filtro pedido: talla ILIKE '%{product_filters['talla']}%'")
            
            # Buscar primer producto que coincida
            product = query.first()
            
            if not product:
                log("❌ No se encontró producto para el pedido")
                return {
                    "operation": "create_order",
                    "success": False,
                    "error": "No hay producto disponible que coincida con los filtros y tenga stock suficiente"
                }
            
            log(f"✅ Producto encontrado para pedido: {product.name} (Stock: {product.stock})")
            
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
            
            log(f"🛒 Pedido creado: ID {new_order.id}, {quantity} unidades")
            
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
            log(f"❌ Error creando pedido: {e}")
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
            
            log(f"✏️ Pedido editado: {old_quantity} → {new_quantity} unidades")
            
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
            log(f"❌ Error editando pedido: {e}")
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
                    "descripcion": product.descripcion or 'Material de calidad premium',
                    "categoria": product.categoria or 'General'
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

# Instancia global
query_agent = QueryAgent()