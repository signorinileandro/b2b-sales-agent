import google.generativeai as genai
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models, crud, schemas
import json
import os
from dotenv import load_dotenv
import time
from fastapi import HTTPException
import re

# Cargar variables de entorno
load_dotenv()

class OrderAgent:
    """Agente especializado en creación y gestión de pedidos"""
    
    def __init__(self):
        # ✅ USAR EL MISMO SISTEMA DE API KEYS QUE ConversationManager
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.key_retry_delays = {}  # Para tracking de delays por key
        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Configurar Gemini con la primera key válida
        self._configure_gemini()
        
        print(f"🛒 OrderAgent inicializado con {len(self.api_keys)} API keys")
    
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
        print(f"🛒 OrderAgent configurado con API key #{self.current_key_index + 1}")
    
    def _switch_to_next_key(self):
        """Cambia a la siguiente API key disponible"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._configure_gemini()
        print(f"🛒🔄 OrderAgent cambiado a API key #{self.current_key_index + 1}")
    
    async def _make_gemini_request_with_fallback(self, prompt: str, **kwargs):
        """Hace petición a Gemini con fallback automático entre API keys"""
        
        max_retries = len(self.api_keys)  # Intentar con todas las keys
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                current_key_num = self.current_key_index + 1
                print(f"🛒🔍 OrderAgent usando API Key #{current_key_num}")
                
                # Verificar si esta key tiene delay de retry
                key_id = f"order_key_{self.current_key_index}"
                if key_id in self.key_retry_delays:
                    retry_time = self.key_retry_delays[key_id]
                    if time.time() < retry_time:
                        print(f"🛒⏰ API Key #{current_key_num} en cooldown hasta {datetime.fromtimestamp(retry_time)}")
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
                print(f"🛒❌ Error con API key #{current_key_num}: {e}")
                
                # Verificar si es error de cuota
                if "quota" in error_str or "exceeded" in error_str or "429" in error_str:
                    print(f"🛒🚫 API Key #{current_key_num} agotó su cuota")
                    
                    # Poner esta key en cooldown por 1 hora
                    self.key_retry_delays[key_id] = time.time() + 3600
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                elif "rate limit" in error_str or "rate_limit" in error_str:
                    print(f"🛒⏳ API Key #{current_key_num} tiene rate limiting")
                    
                    # Cooldown más corto para rate limiting (5 minutos)
                    self.key_retry_delays[key_id] = time.time() + 300
                    
                    # Cambiar a la siguiente key
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
                    
                else:
                    # Error no relacionado con cuota, intentar una vez más con la siguiente key
                    print(f"🛒🔄 Error general, intentando con siguiente key")
                    self._switch_to_next_key()
                    retry_count += 1
                    continue
        
        # Si llegamos aquí, todas las keys fallaron
        raise Exception(f"OrderAgent: Todas las API keys ({len(self.api_keys)}) han fallado o están en cooldown")

    async def handle_order_creation(self, message: str, conversation: Dict) -> str:
        """Maneja la creación de pedidos con análisis inteligente del mensaje"""
        
        try:
            print(f"🛒 OrderAgent procesando: {message}")
            
            # 1. Analizar qué producto y cantidad quiere el usuario
            order_analysis = await self._analyze_order_request(message, conversation)
            
            # 2. Validar que la información sea suficiente
            validation = await self._validate_order_data(order_analysis, conversation)
            
            if not validation['is_valid']:
                return validation['response']
            
            # 3. Crear el pedido en la base de datos
            order_result = await self._create_order_in_db(order_analysis, conversation['phone'])
            
            # 4. Generar respuesta natural
            response = await self._generate_order_response(order_result, order_analysis)
            
            return response
            
        except Exception as e:
            print(f"🛒❌ Error en OrderAgent: {e}")
            return "Disculpa, tuve un problema creando tu pedido. ¿Podrías intentar de nuevo especificando el producto y cantidad que necesitás?"
    
    async def _analyze_order_request(self, message: str, conversation: Dict) -> Dict:
        """Analiza el mensaje para extraer información del pedido"""
        
        # Extraer contexto de la conversación
        recent_messages = ""
        for msg in conversation.get('messages', [])[-5:]:  # Últimos 5 mensajes
            role = "Usuario" if msg['role'] == 'user' else "Bot"
            recent_messages += f"{role}: {msg['content']}\n"
        
        # Productos vistos recientemente
        recent_products = ""
        if conversation.get('recent_searches'):
            recent_products = "Productos mostrados recientemente:\n"
            for search in conversation.get('recent_searches', [])[:3]:
                recent_products += f"- {search['content'][:100]}...\n"
        
        prompt = f"""Analiza esta solicitud de pedido y extrae la información del producto y cantidad:

CONVERSACIÓN RECIENTE:
{recent_messages}

{recent_products}

MENSAJE ACTUAL: "{message}"

Tipos de producto disponibles: pantalón, camiseta, falda, sudadera, camisa
Colores disponibles: blanco, negro, azul, verde, gris, rojo, amarillo
Talles disponibles: S, M, L, XL, XXL

Responde SOLO con JSON válido:
{{
    "has_product_info": true_si_especifica_tipo_prenda,
    "has_quantity": true_si_especifica_cantidad,
    "needs_context": true_si_debe_usar_productos_del_contexto,
    "product_filters": {{
        "tipo_prenda": "pantalón|camiseta|falda|sudadera|camisa|null",
        "color": "blanco|negro|azul|verde|gris|rojo|amarillo|null",
        "talla": "S|M|L|XL|XXL|null"
    }},
    "quantity": numero_o_null,
    "urgency": "normal|urgent|flexible",
    "special_requirements": "texto_con_requisitos_especiales_o_null",
    "context_completion": {{
        "use_last_shown_product": true_si_debe_usar_ultimo_producto_mostrado,
        "use_conversation_context": true_si_necesita_contexto_general
    }}
}}

EJEMPLOS:
- "quiero 50 camisetas rojas talle M" → {{"has_product_info": true, "has_quantity": true, "product_filters": {{"tipo_prenda": "camiseta", "color": "rojo", "talla": "M"}}, "quantity": 50}}
- "necesito 100 unidades" (contexto: viendo pantalones azules L) → {{"has_quantity": true, "needs_context": true, "quantity": 100, "context_completion": {{"use_conversation_context": true}}}}
- "haceme el pedido" (contexto: viendo sudaderas negras XL) → {{"needs_context": true, "context_completion": {{"use_last_shown_product": true}}}}
- "quiero comprar para construcción, 80 unidades de lo azul en L" → {{"has_quantity": true, "product_filters": {{"color": "azul", "talla": "L"}}, "quantity": 80, "special_requirements": "para construcción"}}"""

        try:
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=300,
                )
            )
            
            # Limpiar y parsear respuesta
            response_clean = response.text.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:-3]
            elif response_clean.startswith("```"):
                response_clean = response_clean[3:-3]
            
            parsed_analysis = json.loads(response_clean)
            print(f"🛒🎯 Análisis de pedido: {parsed_analysis}")
            
            return parsed_analysis
            
        except Exception as e:
            print(f"🛒❌ Error analizando pedido: {e}")
            
            # Fallback basado en palabras clave
            message_lower = message.lower()
            
            # Detectar cantidad
            quantity = None
            import re
            quantity_match = re.search(r'\b(\d+)\b', message)
            if quantity_match:
                quantity = int(quantity_match.group(1))
            
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
            
            return {
                "has_product_info": tipo_prenda is not None,
                "has_quantity": quantity is not None,
                "needs_context": tipo_prenda is None and quantity is not None,
                "product_filters": {
                    "tipo_prenda": tipo_prenda,
                    "color": color,
                    "talla": talla
                },
                "quantity": quantity,
                "urgency": "normal",
                "special_requirements": None,
                "context_completion": {
                    "use_last_shown_product": not tipo_prenda,
                    "use_conversation_context": True
                }
            }
    
    async def _validate_order_data(self, analysis: Dict, conversation: Dict) -> Dict:
        """Valida que tengamos suficiente información para crear el pedido"""
        
        product_filters = analysis.get("product_filters", {})
        quantity = analysis.get("quantity")
        needs_context = analysis.get("needs_context", False)
        
        # ✅ COMPLETAR CON CONTEXTO SI ES NECESARIO
        if needs_context:
            # Buscar en mensajes recientes productos mencionados
            recent_messages = conversation.get('messages', [])[-10:]  # Últimos 10 mensajes
            
            for msg in reversed(recent_messages):  # Empezar por los más recientes
                if msg['role'] == 'assistant' and 'stock' in msg['content'].lower():
                    # Buscar productos en respuestas del bot
                    content = msg['content'].lower()
                    
                    # Completar tipo_prenda si falta
                    if not product_filters.get("tipo_prenda"):
                        for tipo in ["pantalón", "camiseta", "sudadera", "camisa", "falda"]:
                            if tipo in content:
                                product_filters["tipo_prenda"] = tipo
                                print(f"🛒🔄 Completado del contexto: tipo_prenda = {tipo}")
                                break
                    
                    # Completar color si falta
                    if not product_filters.get("color"):
                        for color in ["azul", "negro", "blanco", "verde", "rojo", "amarillo", "gris"]:
                            if color in content:
                                product_filters["color"] = color
                                print(f"🛒🔄 Completado del contexto: color = {color}")
                                break
                    
                    # Completar talla si falta
                    if not product_filters.get("talla"):
                        for talla in ["S", "M", "L", "XL", "XXL"]:
                            if f"talle {talla.lower()}" in content or f"talla {talla.lower()}" in content:
                                product_filters["talla"] = talla
                                print(f"🛒🔄 Completado del contexto: talla = {talla}")
                                break
                    
                    # Si completamos información, salir del loop
                    if product_filters.get("tipo_prenda"):
                        break
        
        # ✅ VALIDACIONES
        
        # 1. Validar cantidad
        if not quantity or quantity <= 0:
            return {
                "is_valid": False,
                "response": "¡Entendido! Para armar tu pedido, solo necesito saber la cantidad.\n\n" \
                          "¿Cuántas unidades querés encargar?"
            }
        
        if quantity < 50:
            return {
                "is_valid": False,
                "response": f"¡Casi! Nuestro pedido mínimo es de **50 unidades** y solicitaste {quantity}.\n\n" \
                          f"Podemos ajustar tu pedido a 50. Recordá que a mayor cantidad, ¡mejor es el precio por unidad! ¿Te parece bien?"
            }
        
        # 2. Validar tipo de prenda
        if not product_filters.get("tipo_prenda"):
            return {
                "is_valid": False,
                "response": "¡Perfecto! Ya tengo la cantidad. Ahora decime qué producto te gustaría pedir.\n\n" \
                          "Podés elegir entre:\n" \
                          "• Camisetas\n" \
                          "• Pantalones\n" \
                          "• Sudaderas\n" \
                          "• Camisas\n" \
                          "• Faldas\n\n" \
                          "¿Cuál te preparamos?"
            }
        
        # 3. Validar que el producto exista con stock suficiente
        db = SessionLocal()
        try:
            query = db.query(models.Product).filter(
                models.Product.stock >= quantity
            )
            
            # Aplicar filtros
            if product_filters.get("tipo_prenda"):
                query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
            if product_filters.get("color"):
                query = query.filter(models.Product.color.ilike(f"%{product_filters['color']}%"))
            if product_filters.get("talla"):
                query = query.filter(models.Product.talla.ilike(f"%{product_filters['talla']}%"))
            
            available_product = query.first()
            
            if not available_product:
                # Buscar productos similares para sugerir
                similar_query = db.query(models.Product).filter(models.Product.stock > 0)
                if product_filters.get("tipo_prenda"):
                    similar_query = similar_query.filter(models.Product.tipo_prenda.ilike(f"%{product_filters['tipo_prenda']}%"))
                
                similar_products = similar_query.limit(3).all()
                
                if similar_products:
                    suggestion = "No tengo stock suficiente del producto exacto que buscás, pero tengo alternativas:\n\n"
                    for p in similar_products:
                        suggestion += f"• **{p.name}** - Stock: {p.stock} unidades - ${p.precio_50_u:,.0f} c/u\n"
                    suggestion += f"\n¿Te sirve alguna de estas opciones?"
                else:
                    suggestion = f"No tengo stock suficiente de **{product_filters.get('tipo_prenda', 'ese producto')}** " \
                               f"{'en ' + product_filters.get('color', '') if product_filters.get('color') else ''} " \
                               f"{'talle ' + product_filters.get('talla', '') if product_filters.get('talla') else ''} " \
                               f"para {quantity} unidades.\n\n¿Te interesa ver otros productos disponibles?"
                
                return {
                    "is_valid": False,
                    "response": suggestion
                }
        
        except Exception as e:
            print(f"🛒❌ Error validando producto: {e}")
            return {
                "is_valid": False,
                "response": "Tuve un problema verificando el stock. ¿Podrías intentar de nuevo?"
            }
        finally:
            db.close()
        
        # Si llegamos aquí, todo está válido
        return {
            "is_valid": True,
            "product_filters": product_filters,
            "quantity": quantity,
            "available_product": {
                "id": available_product.id,
                "name": available_product.name,
                "precio_50_u": available_product.precio_50_u,
                "precio_100_u": available_product.precio_100_u,
                "precio_200_u": available_product.precio_200_u,
                "stock": available_product.stock
            }
        }
    
    async def _create_order_in_db(self, analysis: Dict, user_phone: str) -> Dict:
        """Crea el pedido en la base de datos usando el CRUD existente"""
        
        try:
            validation = analysis  # Ya viene validado
            product_info = validation["available_product"]
            quantity = validation["quantity"]
            
            # ✅ USAR EL CRUD EXISTENTE QUE MANEJA STOCK AUTOMÁTICAMENTE
            order_data = schemas.OrderCreate(
                product_id=product_info["id"],
                qty=quantity,
                buyer=f"Cliente WhatsApp {user_phone}"
            )
            
            db = SessionLocal()
            try:
                # El CRUD se encarga de verificar stock y descontarlo
                new_order = crud.create_order(db, order_data)
                
                # ✅ AGREGAR DATOS DE WHATSAPP
                new_order.user_phone = user_phone
                db.commit()
                db.refresh(new_order)
                
                print(f"🛒✅ Pedido creado: ID {new_order.id}, {quantity} unidades")
                
                # Calcular precio según cantidad
                if quantity >= 200:
                    precio_unitario = product_info["precio_200_u"]
                elif quantity >= 100:
                    precio_unitario = product_info["precio_100_u"]
                else:
                    precio_unitario = product_info["precio_50_u"]
                
                return {
                    "success": True,
                    "order": {
                        "id": new_order.id,
                        "product": {
                            "id": product_info["id"],
                            "name": product_info["name"]
                        },
                        "quantity": quantity,
                        "precio_unitario": precio_unitario,
                        "total_price": precio_unitario * quantity,
                        "stock_before": product_info["stock"],
                        "stock_after": product_info["stock"] - quantity,
                        "created_at": new_order.created_at
                    }
                }
                
            finally:
                db.close()
                
        except HTTPException as http_e:
            # Error controlado del CRUD
            print(f"🛒❌ Error HTTP creando pedido: {http_e.detail}")
            return {
                "success": False,
                "error": http_e.detail,
                "error_type": "stock_insufficient" if "stock insuficiente" in str(http_e.detail).lower() else "general"
            }
            
        except Exception as e:
            print(f"🛒❌ Error inesperado creando pedido: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": "general"
            }
    
    async def _generate_order_response(self, order_result: Dict, analysis: Dict) -> str:
        """Genera respuesta natural sobre el resultado del pedido"""
        
        if order_result.get("success"):
            order = order_result["order"]
            
            response = f"🎉 **¡PEDIDO CONFIRMADO!** 🎉\n\n"
            response += f"✅ **{order['product']['name']}**\n"
            response += f"📦 Cantidad: **{order['quantity']:,} unidades**\n"
            response += f"💰 Precio unitario: **${order['precio_unitario']:,.0f}**\n"
            response += f"💸 **Total: ${order['total_price']:,.0f}**\n"
            response += f"📋 ID de pedido: **#{order['id']}**\n\n"
            response += f"📊 Stock restante: **{order['stock_after']:,} unidades**\n\n"
            
            # ✅ INFORMACIÓN IMPORTANTE SOBRE MODIFICACIONES
            response += f"ℹ️ **Podés modificar este pedido durante los próximos 5 minutos.**\n"
            response += f"⏰ Solo decí: *'cambiar a X unidades'* o *'modificar cantidad'*\n\n"
            
            # Sugerir más productos
            response += f"¿Necesitás algo más para tu empresa? 🏢"
            
            return response
        
        else:
            error = order_result.get("error", "Error desconocido")
            error_type = order_result.get("error_type", "general")
            
            if error_type == "stock_insufficient":
                return f"❌ **Stock insuficiente**\n\n{error}\n\n" \
                       f"¿Te interesa ajustar la cantidad o ver otros productos similares?"
            else:
                return f"❌ **Error creando pedido**\n\n{error}\n\n" \
                       f"Por favor, intentá de nuevo o contactá a soporte."

    async def handle_order_modification(self, message: str, conversation: Dict) -> str:
        """Maneja la modificación de pedidos recientes (dentro de 5 minutos)"""
        
        try:
            print(f"🛒✏️ OrderAgent procesando modificación: {message}")
            
            # 1. Buscar pedido reciente modificable
            recent_order = await self._find_recent_modifiable_order(conversation['phone'])
            
            if not recent_order['found']:
                return recent_order['response']
            
            # 2. Analizar qué modificación quiere hacer
            modification = await self._analyze_modification_request(message, recent_order['order'])
            
            # 3. Ejecutar la modificación
            result = await self._execute_order_modification(recent_order['order'], modification)
            
            # 4. Generar respuesta
            response = await self._generate_modification_response(result, modification)
            
            return response
            
        except Exception as e:
            print(f"🛒✏️❌ Error en modificación de pedido: {e}")
            return "Disculpa, tuve un problema modificando tu pedido. ¿Podrías intentar de nuevo?"
    
    async def _find_recent_modifiable_order(self, user_phone: str) -> Dict:
        """Busca el pedido más reciente que se pueda modificar (dentro de 5 minutos)"""
        
        db = SessionLocal()
        try:
            # Buscar pedido más reciente del usuario
            recent_time = datetime.utcnow() - timedelta(minutes=10)  # Buscar en últimos 10 minutos
            
            recent_order = db.query(models.Order).filter(
                models.Order.user_phone == user_phone,
                models.Order.created_at >= recent_time,
                models.Order.status == "pending"
            ).order_by(models.Order.created_at.desc()).first()
            
            if not recent_order:
                return {
                    "found": False,
                    "response": "No encontré pedidos recientes tuyos para modificar.\n\n¿Querés hacer un nuevo pedido?"
                }
            
            # Verificar ventana de 5 minutos
            time_passed = datetime.utcnow() - recent_order.created_at
            minutes_passed = time_passed.total_seconds() / 60
            
            if minutes_passed > 5:
                return {
                    "found": False,
                    "response": f"Tu último pedido (#{recent_order.id}) fue hace {int(minutes_passed)} minutos.\n\n" \
                              f"❌ **Solo se puede modificar durante los primeros 5 minutos.**\n\n" \
                              f"¿Querés hacer un nuevo pedido en su lugar?"
                }
            
            # Obtener información del producto
            product = db.query(models.Product).filter(models.Product.id == recent_order.product_id).first()
            
            remaining_minutes = 5 - int(minutes_passed)
            
            return {
                "found": True,
                "order": {
                    "id": recent_order.id,
                    "product_id": recent_order.product_id,
                    "current_qty": recent_order.qty,
                    "product_name": product.name if product else "Producto",
                    "product_stock": product.stock if product else 0,
                    "created_at": recent_order.created_at,
                    "minutes_remaining": remaining_minutes
                },
                "response": f"Encontré tu pedido reciente para modificar (#{recent_order.id})"
            }
            
        except Exception as e:
            print(f"🛒✏️❌ Error buscando pedido: {e}")
            return {
                "found": False,
                "response": "Tuve un problema buscando tu pedido reciente. ¿Podrías intentar de nuevo?"
            }
        finally:
            db.close()
    
    async def _analyze_modification_request(self, message: str, order_info: Dict) -> Dict:
        """Analiza qué modificación quiere hacer el usuario"""
        
        prompt = f"""Analiza esta solicitud de modificación de pedido:

PEDIDO ACTUAL:
- ID: {order_info['id']}
- Producto: {order_info['product_name']}
- Cantidad actual: {order_info['current_qty']} unidades
- Stock disponible del producto: {order_info['product_stock']} unidades
- Tiempo restante para modificar: {order_info['minutes_remaining']} minutos

MENSAJE DEL USUARIO: "{message}"

Responde SOLO con JSON válido:
{{
    "modification_type": "change_quantity" | "cancel_order" | "add_more" | "reduce_quantity",
    "new_quantity": numero_o_null,
    "is_clear": true_si_la_instruccion_es_clara,
    "needs_confirmation": true_si_necesita_confirmacion_adicional
}}

EJEMPLOS:
- "cambiar a 80 unidades" → {{"modification_type": "change_quantity", "new_quantity": 80, "is_clear": true}}
- "quiero 20 más" → {{"modification_type": "add_more", "new_quantity": 20, "is_clear": true}}
- "reducir a la mitad" → {{"modification_type": "reduce_quantity", "new_quantity": null, "needs_confirmation": true}}
- "cancelar pedido" → {{"modification_type": "cancel_order", "is_clear": true}}"""

        try:
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=150,
                )
            )
            
            # Limpiar y parsear respuesta
            response_clean = response.text.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:-3]
            elif response_clean.startswith("```"):
                response_clean = response_clean[3:-3]
            
            parsed = json.loads(response_clean)
            print(f"🛒✏️🎯 Análisis de modificación: {parsed}")
            
            # Calcular cantidad final
            if parsed.get("modification_type") == "add_more" and parsed.get("new_quantity"):
                parsed["final_quantity"] = order_info["current_qty"] + parsed["new_quantity"]
            elif parsed.get("modification_type") == "reduce_quantity" and not parsed.get("new_quantity"):
                parsed["final_quantity"] = order_info["current_qty"] // 2  # Reducir a la mitad
            elif parsed.get("modification_type") == "change_quantity":
                parsed["final_quantity"] = parsed.get("new_quantity")
            
            return parsed
            
        except Exception as e:
            print(f"🛒✏️❌ Error analizando modificación: {e}")
            
            # Fallback simple
            import re
            message_lower = message.lower()
            
            if "cancelar" in message_lower:
                return {"modification_type": "cancel_order", "is_clear": True}
            
            # Buscar números en el mensaje
            numbers = re.findall(r'\d+', message)
            if numbers:
                new_qty = int(numbers[0])
                return {
                    "modification_type": "change_quantity",
                    "new_quantity": new_qty,
                    "final_quantity": new_qty,
                    "is_clear": True
                }
            
            return {
                "modification_type": "unclear",
                "is_clear": False,
                "needs_confirmation": True
            }
    
    async def _execute_order_modification(self, order_info: Dict, modification: Dict) -> Dict:
        """Ejecuta la modificación del pedido usando el CRUD existente"""
        
        try:
            if modification.get("modification_type") == "cancel_order":
                # Cancelar pedido y restaurar stock
                db = SessionLocal()
                try:
                    # ✅ FIX: Usar order_info en lugar de modification_data
                    result = crud.restore_stock_on_order_cancellation(db, order_info["id"])
                    
                    return {
                        "success": True,
                        "action": "cancelled", 
                        "order_id": order_info["id"],
                        "restored_quantity": order_info["current_qty"],
                        "product_name": order_info["product_name"]
                    }
                finally:
                    db.close()
            
            elif modification.get("final_quantity"):
                new_quantity = modification["final_quantity"]
                
                # ✅ USAR EL CRUD EXISTENTE QUE MANEJA STOCK
                db = SessionLocal()
                try:
                    updated_order = crud.update_order(db, order_info["id"], new_quantity)
                    
                    # Obtener producto para precio
                    product = db.query(models.Product).filter(models.Product.id == updated_order.product_id).first()
                    
                    # Calcular precio según nueva cantidad
                    if new_quantity >= 200:
                        precio_unitario = product.precio_200_u
                    elif new_quantity >= 100:
                        precio_unitario = product.precio_100_u
                    else:
                        precio_unitario = product.precio_50_u
                    
                    return {
                        "success": True,
                        "action": "modified",
                        "order_id": order_info["id"],
                        "old_quantity": order_info["current_qty"],
                        "new_quantity": new_quantity,
                        "precio_unitario": precio_unitario,
                        "new_total": precio_unitario * new_quantity,
                        "stock_after": product.stock
                    }
                    
                finally:
                    db.close()
            
            else:
                return {
                    "success": False,
                    "error": "No se pudo determinar la modificación a realizar"
                }
                
        except HTTPException as http_e:
            print(f"🛒✏️❌ Error HTTP modificando: {http_e.detail}")
            return {
                "success": False,
                "error": http_e.detail,
                "error_type": "stock_insufficient" if "stock" in str(http_e.detail).lower() else "general"
            }
            
        except Exception as e:
            print(f"🛒✏️❌ Error modificando pedido: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": "general"
            }
    
    async def _generate_modification_response(self, result: Dict, modification: Dict) -> str:
        """Genera respuesta sobre el resultado de la modificación"""
        
        if result.get("success"):
            
            if result.get("action") == "cancelled":
                return f"✅ **Pedido #{result['order_id']} CANCELADO**\n\n" \
                       f"♻️ Stock restaurado: **+{result['restored_quantity']} unidades**\n\n" \
                       f"¿Querés hacer un nuevo pedido?"
            
            elif result.get("action") == "modified":
                response = f"✅ **PEDIDO #{result['order_id']} MODIFICADO**\n\n"
                response += f"📦 Cantidad anterior: **{result['old_quantity']} unidades**\n"
                response += f"📦 Nueva cantidad: **{result['new_quantity']} unidades**\n"
                response += f"💰 Precio unitario: **${result['precio_unitario']:,.0f}**\n"
                response += f"💸 **Nuevo total: ${result['new_total']:,.0f}**\n\n"
                response += f"📊 Stock restante: **{result['stock_after']} unidades**\n\n"
                response += f"¡Cambio realizado exitosamente! 🎉"
                return response
        
        else:
            error = result.get("error", "Error desconocido")
            error_type = result.get("error_type", "general")
            
            if error_type == "stock_insufficient":
                return f"❌ **No se pudo modificar el pedido**\n\n{error}\n\n" \
                       f"¿Te interesa una cantidad menor o cancelar este pedido?"
            else:
                return f"❌ **Error modificando pedido**\n\n{error}\n\n" \
                       f"¿Querés intentar de nuevo?"

# Instancia global
order_agent = OrderAgent()