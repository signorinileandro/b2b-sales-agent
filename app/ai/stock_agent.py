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
from ..utils.logger import log

# Cargar variables de entorno
load_dotenv()

class StockAgent:
    """Agente especializado en consultas de inventario y stock"""
    
    def __init__(self):
        # ✅ USAR EL MISMO SISTEMA DE API KEYS QUE ConversationManager
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.key_retry_delays = {}  # Para tracking de delays por key
        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Configurar Gemini con la primera key válida
        self._configure_gemini()
        
        log(f"📦 StockAgent inicializado con {len(self.api_keys)} API keys")
    
    def _load_api_keys(self) -> List[str]:
        """Carga todas las API keys disponibles desde el .env"""
        api_keys = []
        
        # Buscar todas las keys que sigan el patrón GOOGLE_API_KEY_X
        for i in range(1, 10):  # Buscar hasta GOOGLE_API_KEY_9
            key = os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                api_keys.append(key)
        
        # También buscar la key genérica por compatibilidad
        generic_key = os.getenv("GEMINI_API_KEY")
        if generic_key and generic_key not in api_keys:
            api_keys.append(generic_key)
        
        return api_keys
    
    def _configure_gemini(self):
        """Configura Gemini con la API key actual"""
        current_key = self.api_keys[self.current_key_index]
        genai.configure(api_key=current_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        log(f"📦 StockAgent configurado con API key #{self.current_key_index + 1}")
    
    def _switch_to_next_key(self):
        """Cambia a la siguiente API key disponible"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._configure_gemini()
        log(f"📦🔄 StockAgent cambiado a API key #{self.current_key_index + 1}")
    
    async def _make_gemini_request_with_fallback(self, prompt: str, **kwargs):
        """Hace petición a Gemini con fallback automático entre API keys"""
        
        max_retries = len(self.api_keys)  # Intentar con todas las keys
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                current_key_num = self.current_key_index + 1
                log(f"📦🔍 StockAgent usando API Key #{current_key_num}")
                
                # Verificar si esta key tiene delay de retry
                key_id = f"stock_key_{self.current_key_index}"
                if key_id in self.key_retry_delays:
                    retry_time = self.key_retry_delays[key_id]
                    if time.time() < retry_time:
                        log(f"📦⏰ API Key #{current_key_num} en cooldown hasta {datetime.fromtimestamp(retry_time)}")
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
                log(f"📦❌ Error con API key #{current_key_num}: {e}")
                
                # Verificar si es error de cuota
                if "quota" in error_str or "exceeded" in error_str or "429" in error_str:
                    log(f"📦🚫 API Key #{current_key_num} agotó su cuota")
                    
                    # Poner esta key en cooldown por 1 hora
                    self.key_retry_delays[key_id] = time.time() + 3600
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                elif "rate limit" in error_str or "rate_limit" in error_str:
                    log(f"📦⏳ API Key #{current_key_num} tiene rate limiting")
                    
                    # Cooldown más corto para rate limiting (5 minutos)
                    self.key_retry_delays[key_id] = time.time() + 300
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                else:
                    # Error no relacionado con cuota, intentar una vez más con la siguiente key
                    log(f"📦🔄 Error general, intentando con siguiente key")
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
        
        # Si llegamos aquí, todas las keys fallaron
        raise Exception(f"StockAgent: Todas las API keys ({len(self.api_keys)}) han fallado o están en cooldown")

    async def handle_stock_query(self, message: str, conversation: Dict) -> str:
        """Maneja consultas de stock con análisis inteligente del mensaje"""
        
        try:
            log(f"📦 StockAgent procesando: {message}")
            
            # 1. Analizar qué busca específicamente el usuario
            stock_query = await self._analyze_stock_query(message, conversation)
            
            # 2. Ejecutar búsqueda en la base de datos
            stock_data = await self._get_stock_data(stock_query)
            
            # 3. Generar respuesta natural
            response = await self._generate_stock_response(message, stock_query, stock_data, conversation)
            
            return response
            
        except Exception as e:
            log(f"📦❌ Error en StockAgent: {e}")
            return "Disculpa, tuve un problema consultando el inventario. ¿Podrías intentar de nuevo?"
    
    async def _analyze_stock_query(self, message: str, conversation: Dict) -> Dict:
        """Analiza el mensaje para entender qué stock consulta específicamente"""
        
        # Extraer contexto de la conversación
        recent_messages = ""
        for msg in conversation.get('messages', [])[-3:]:  # Últimos 3 mensajes
            role = "Usuario" if msg['role'] == 'user' else "Bot"
            recent_messages += f"{role}: {msg['content']}\n"
        
        prompt = f"""Analiza esta consulta de stock y extrae la información específica:

CONVERSACIÓN RECIENTE:
{recent_messages}

MENSAJE ACTUAL: "{message}"

Tipos de producto disponibles: pantalón, camiseta, falda, sudadera, camisa
Colores disponibles: blanco, negro, azul, verde, gris, rojo, amarillo
Talles disponibles: S, M, L, XL, XXL

Responde SOLO con JSON válido:
{{
    "query_type": "specific_product" | "general_availability" | "color_options" | "size_options" | "quantity_check",
    "filters": {{
        "tipo_prenda": "pantalón|camiseta|falda|sudadera|camisa|null",
        "color": "blanco|negro|azul|verde|gris|rojo|amarillo|null",
        "talla": "S|M|L|XL|XXL|null"
    }},
    "question_focus": "availability" | "colors" | "sizes" | "quantities" | "prices",
    "context_needed": true_si_debe_usar_productos_del_contexto
}}

EJEMPLOS:
- "¿cuánto stock queda de buzo azul talle L?" → {{"query_type": "quantity_check", "filters": {{"tipo_prenda": "sudadera", "color": "azul", "talla": "L"}}, "question_focus": "quantities"}}
- "¿qué colores tenés en pantalones?" → {{"query_type": "color_options", "filters": {{"tipo_prenda": "pantalón"}}, "question_focus": "colors"}}
- "que talles hay?" (contexto: viendo camisetas azules) → {{"query_type": "size_options", "context_needed": true, "question_focus": "sizes"}}"""

        try:
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=200,
                )
            )
            
            # Limpiar y parsear respuesta
            response_clean = response.text.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:-3]
            elif response_clean.startswith("```"):
                response_clean = response_clean[3:-3]
            
            parsed_query = json.loads(response_clean)
            log(f"📦🎯 Query analizada: {parsed_query}")
            
            return parsed_query
            
        except Exception as e:
            log(f"📦❌ Error analizando query: {e}")
            
            # Fallback basado en palabras clave
            message_lower = message.lower()
            
            # Detectar tipo de prenda
            tipo_prenda = None
            for tipo in ["pantalón", "pantalones", "camiseta", "camisetas", "sudadera", "buzos", "camisa", "camisas", "falda", "faldas"]:
                if tipo in message_lower:
                    if tipo in ["pantalones", "pantalón"]:
                        tipo_prenda = "pantalón"
                    elif tipo in ["buzos"]:
                        tipo_prenda = "sudadera"
                    else:
                        tipo_prenda = tipo.rstrip('s')
                    break
            
            # Detectar color
            color = None
            for c in ["azul", "negro", "blanco", "verde", "rojo", "amarillo", "gris"]:
                if c in message_lower:
                    color = c
                    break
            
            # Detectar talla
            talla = None
            for t in ["S", "M", "L", "XL", "XXL"]:
                if f"talle {t.lower()}" in message_lower or f"talla {t.lower()}" in message_lower:
                    talla = t
                    break
            
            # Determinar tipo de consulta
            if any(word in message_lower for word in ["cuanto", "cuánto", "stock", "quedan", "disponible"]):
                query_type = "quantity_check"
                focus = "quantities"
            elif any(word in message_lower for word in ["colores", "color", "qué color"]):
                query_type = "color_options"
                focus = "colors"
            elif any(word in message_lower for word in ["talles", "tallas", "talle", "tamaños"]):
                query_type = "size_options"
                focus = "sizes"
            else:
                query_type = "general_availability"
                focus = "availability"
            
            return {
                "query_type": query_type,
                "filters": {
                    "tipo_prenda": tipo_prenda,
                    "color": color,
                    "talla": talla
                },
                "question_focus": focus,
                "context_needed": False
            }
    
    async def _get_stock_data(self, query: Dict) -> Dict:
        """Obtiene datos de stock de la base de datos según la consulta"""
        
        db = SessionLocal()
        try:
            filters = query.get("filters", {})
            
            # Base query: productos con stock > 0
            db_query = db.query(models.Product).filter(models.Product.stock > 0)
            
            # Aplicar filtros
            if filters.get("tipo_prenda"):
                db_query = db_query.filter(models.Product.tipo_prenda.ilike(f"%{filters['tipo_prenda']}%"))
                
            if filters.get("color"):
                db_query = db_query.filter(models.Product.color.ilike(f"%{filters['color']}%"))
                
            if filters.get("talla"):
                db_query = db_query.filter(models.Product.talla.ilike(f"%{filters['talla']}%"))
            
            products = db_query.all()
            
            # Procesar datos según tipo de consulta
            stock_data = {
                "products": [],
                "summary": {},
                "total_stock": 0,
                "unique_colors": set(),
                "unique_sizes": set(),
                "unique_types": set(),
                "query_type": query.get("query_type", "general")
            }
            
            for product in products:
                product_data = {
                    "id": product.id,
                    "name": product.name,
                    "tipo_prenda": product.tipo_prenda,
                    "color": product.color,
                    "talla": product.talla,
                    "stock": product.stock,
                    "precio_50_u": product.precio_50_u,
                    "precio_100_u": product.precio_100_u,
                    "precio_200_u": product.precio_200_u,
                    "descripcion": product.descripcion or "Material de calidad premium",
                    "categoria": product.categoria or "General"
                }
                
                stock_data["products"].append(product_data)
                stock_data["total_stock"] += product.stock
                stock_data["unique_colors"].add(product.color.lower())
                stock_data["unique_sizes"].add(product.talla)
                stock_data["unique_types"].add(product.tipo_prenda)
            
            # Convertir sets a listas ordenadas para JSON
            stock_data["unique_colors"] = sorted(list(stock_data["unique_colors"]))
            stock_data["unique_sizes"] = sorted(list(stock_data["unique_sizes"]))
            stock_data["unique_types"] = sorted(list(stock_data["unique_types"]))
            
            log(f"📦📊 Stock data obtenida: {len(products)} productos, stock total: {stock_data['total_stock']}")
            
            return stock_data
            
        except Exception as e:
            log(f"📦❌ Error obteniendo stock data: {e}")
            return {
                "products": [],
                "summary": {},
                "total_stock": 0,
                "unique_colors": [],
                "unique_sizes": [],
                "unique_types": [],
                "error": str(e)
            }
        finally:
            db.close()
    
    async def _generate_stock_response(self, original_message: str, query: Dict, stock_data: Dict, conversation: Dict) -> str:
        """Genera respuesta natural sobre el stock basada en la consulta específica"""
        
        products = stock_data.get("products", [])
        query_type = query.get("query_type", "general")
        question_focus = query.get("question_focus", "availability")
        filters = query.get("filters", {})
        
        # Si no hay productos
        if not products:
            tipo_buscado = filters.get("tipo_prenda", "productos")
            color_buscado = filters.get("color", "")
            talla_buscada = filters.get("talla", "")
            
            filtros_str = []
            if color_buscado:
                filtros_str.append(f"color {color_buscado}")
            if talla_buscada:
                filtros_str.append(f"talle {talla_buscada}")
            
            if filtros_str:
                return f"No tengo **{tipo_buscado}** disponibles en {' y '.join(filtros_str)} en este momento.\n\n" \
                       f"¿Te interesa ver otras opciones similares o diferentes colores/talles?"
            else:
                return f"No tengo **{tipo_buscado}** disponibles en este momento.\n\n" \
                       f"¿Te sirve algún otro tipo de producto?"
        
        # Respuestas específicas según el tipo de consulta
        if query_type == "quantity_check":
            # Consulta específica de cantidad
            if len(products) == 1:
                product = products[0]
                return f"📦 **Stock disponible:** {product['stock']} unidades de **{product['name']}**\n\n" \
                       f"💰 Precio: **${product['precio_50_u']:,.0f}** (50+ un.) | **${product['precio_100_u']:,.0f}** (100+ un.)\n\n" \
                       f"¿Cuántas unidades necesitás?"
            else:
                # Múltiples productos que coinciden
                response = f"📦 **Stock disponible para tu consulta:**\n\n"
                for i, product in enumerate(products[:5], 1):
                    response += f"**{i}.** {product['name']} - **{product['stock']} unidades**\n"
                    response += f"    💰 ${product['precio_50_u']:,.0f} (50+ un.)\n\n"
                
                total_stock = sum(p['stock'] for p in products)
                response += f"📊 **Total disponible:** {total_stock:,} unidades\n\n¿Cuál te interesa específicamente?"
                return response
        
        elif query_type == "color_options":
            # Consulta sobre colores disponibles
            tipo_prenda = filters.get("tipo_prenda", "productos")
            colors = stock_data["unique_colors"]
            
            response = f"🎨 **Colores disponibles en {tipo_prenda}s:**\n\n"
            
            # Mostrar colores con stock
            color_stock = {}
            for product in products:
                color = product['color'].lower()
                if color not in color_stock:
                    color_stock[color] = 0
                color_stock[color] += product['stock']
            
            for color in sorted(color_stock.keys()):
                response += f"• **{color.title()}** - {color_stock[color]:,} unidades\n"
            
            response += f"\n📊 **Total:** {stock_data['total_stock']:,} unidades en {len(colors)} colores\n\n"
            response += f"¿Qué color te interesa más?"
            
            return response
        
        elif query_type == "size_options":
            # Consulta sobre talles disponibles
            tipo_prenda = filters.get("tipo_prenda", "productos")
            color = filters.get("color", "")
            
            header = f"📏 **Talles disponibles"
            if tipo_prenda:
                header += f" en {tipo_prenda}s"
            if color:
                header += f" {color}s"
            header += ":**\n\n"
            
            response = header
            
            # Mostrar talles con stock
            size_stock = {}
            for product in products:
                talla = product['talla']
                if talla not in size_stock:
                    size_stock[talla] = 0
                size_stock[talla] += product['stock']
            
            # Ordenar talles lógicamente
            talle_order = ['S', 'M', 'L', 'XL', 'XXL']
            sorted_talles = sorted(size_stock.keys(), key=lambda x: talle_order.index(x) if x in talle_order else 999)
            
            for talla in sorted_talles:
                response += f"• **Talle {talla}** - {size_stock[talla]:,} unidades\n"
            
            response += f"\n📊 **Total:** {stock_data['total_stock']:,} unidades en {len(sorted_talles)} talles\n\n"
            response += f"¿Qué talle necesitás?"
            
            return response
        
        else:
            # Consulta general de disponibilidad
            response = f"📋 **INVENTARIO DISPONIBLE:**\n\n"
            
            # Mostrar productos destacados (primeros 4)
            for i, product in enumerate(products[:4], 1):
                response += f"**{i}.** {product['name']}\n"
                response += f"    📦 Stock: **{product['stock']} unidades**\n"
                response += f"    💰 Desde: **${product['precio_50_u']:,.0f}** (50+ un.)\n\n"
            
            # Resumen
            response += f"📊 **RESUMEN GENERAL:**\n"
            response += f"• **Tipos:** {', '.join(stock_data['unique_types'])}\n"
            response += f"• **Colores:** {', '.join([c.title() for c in stock_data['unique_colors']])}\n"
            response += f"• **Talles:** {', '.join(stock_data['unique_sizes'])}\n"
            response += f"• **Stock total:** {stock_data['total_stock']:,} unidades\n"
            response += f"• **Productos diferentes:** {len(products)}\n\n"
            response += f"¿Hay algún producto específico que te interese?"
            
            return response

# Instancia global
stock_agent = StockAgent()