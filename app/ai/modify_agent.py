from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models, crud, schemas
import json
import os
from fastapi import HTTPException
from ..utils.logger import log
from .base_agent import BaseAgent
import pytz

class ModifyAgent(BaseAgent):
    """Agente especializado en modificación y gestión de pedidos existentes"""
    
    def __init__(self):
        super().__init__(agent_name="ModifyAgent")
        log(f"✏️ ModifyAgent inicializado para Ollama")

    async def handle_order_modification(self, message: str, conversation: Dict) -> str:
        """Maneja modificaciones de pedidos con análisis inteligente"""
        
        try:
            log(f"✏️ ModifyAgent procesando: {message}")
            
            # 1. Identificar qué pedido quiere modificar
            order_identification = await self._identify_target_order(message, conversation)
            
            if not order_identification['found']:
                return order_identification['response']
            
            # 2. Analizar qué tipo de modificación quiere hacer
            modification_analysis = await self._analyze_modification_type(message, order_identification)
            
            # 3. Validar que la modificación sea posible
            validation = await self._validate_modification(modification_analysis, order_identification)
            
            if not validation['is_valid']:
                return validation['response']
            
            # 4. Ejecutar la modificación con gestión de stock
            execution_result = await self._execute_modification_with_stock_management(
                validation['modification_data'], 
                order_identification['order']
            )
            
            # 5. Generar respuesta natural
            response = await self._generate_modification_response(execution_result, modification_analysis)
            
            return response
            
        except Exception as e:
            log(f"✏️❌ Error en ModifyAgent: {e}")
            return "Disculpa, tuve un problema modificando tu pedido. ¿Podrías especificar qué pedido querés cambiar y cómo?"
    
    async def _identify_target_order(self, message: str, conversation: Dict) -> Dict:
        """Identifica qué pedido específico quiere modificar"""
        
        # Buscar pedidos recientes del usuario
        db = SessionLocal()
        try:
            # ✅ ARREGLAR TIMEZONE - usar timezone-aware datetime
            utc = pytz.UTC
            recent_time = datetime.now(utc) - timedelta(days=30)
            
            user_orders = db.query(models.Order).filter(
                models.Order.user_phone == conversation['phone'],
                models.Order.created_at >= recent_time
            ).order_by(models.Order.created_at.desc()).limit(10).all()
            
            if not user_orders:
                return {
                    "found": False,
                    "response": "No encontré pedidos tuyos para modificar.\n\n¿Querés hacer un nuevo pedido?"
                }
            
            # Extraer información de pedidos para análisis
            orders_info = []
            for order in user_orders:
                product = db.query(models.Product).filter(models.Product.id == order.product_id).first()
                
                # ✅ ARREGLAR CÁLCULO DE TIEMPO - manejar timezone correctly
                if order.created_at.tzinfo is None:
                    # Si created_at no tiene timezone, asumimos UTC
                    order_time = utc.localize(order.created_at)
                else:
                    order_time = order.created_at
                
                now = datetime.now(utc)
                time_passed = now - order_time
                minutes_passed = time_passed.total_seconds() / 60
                can_modify = minutes_passed <= 5 and order.status == "pending"
                
                orders_info.append({
                    "id": order.id,
                    "product_name": product.name if product else "Producto",
                    "quantity": order.qty,
                    "status": order.status,
                    "created_at": order.created_at.isoformat(),
                    "minutes_ago": int(minutes_passed),
                    "can_modify": can_modify,
                    "product_id": order.product_id,
                    "buyer": order.buyer
                })
            
            # ✅ USAR OLLAMA EN LUGAR DE GEMINI
            prompt = f"""Identifica qué pedido quiere modificar el usuario:

MENSAJE DEL USUARIO: "{message}"

PEDIDOS DISPONIBLES:
{json.dumps(orders_info, indent=2)}

REGLAS:
- Solo se pueden modificar pedidos "pending" de los últimos 5 minutos
- Si menciona un ID específico (#123), usar ese
- Si dice "último pedido" o "pedido reciente", usar el más reciente modificable
- Si no especifica, sugerir opciones

Responde SOLO con JSON válido:
{{
    "target_found": true_si_identificas_pedido_específico,
    "target_order_id": numero_o_null,
    "requires_clarification": true_si_necesita_aclaración,
    "suggested_orders": [lista_de_ids_sugeridos],
    "reasoning": "explicación_breve"
}}"""

            try:
                response = self.call_ollama([
                    {"role": "system", "content": "Eres un asistente para modificación de pedidos textiles B2B."},
                    {"role": "user", "content": prompt}
                ])
                                
                # ✅ MEJORAR PARSING JSON
                json_content = self._extract_json_from_response(response)
                if json_content:
                    analysis = json.loads(json_content)
                    log(f"✏️🎯 Identificación de pedido: {analysis}")
                    
                    # Procesar resultado
                    if analysis.get("target_found") and analysis.get("target_order_id"):
                        target_order_id = analysis["target_order_id"]
                        target_order = next((o for o in orders_info if o["id"] == target_order_id), None)
                        
                        if target_order:
                            if not target_order["can_modify"]:
                                return {
                                    "found": False,
                                    "response": f"❌ **El pedido #{target_order_id} no se puede modificar**\n\n" \
                                              f"📅 Fue creado hace {target_order['minutes_ago']} minutos\n" \
                                              f"⏰ Solo se puede modificar durante los primeros 5 minutos\n\n" \
                                              f"¿Querés hacer un nuevo pedido en su lugar?"
                                }
                            
                            return {
                                "found": True,
                                "order": target_order,
                                "response": f"Pedido #{target_order_id} identificado para modificar"
                            }
                    
                    elif analysis.get("requires_clarification"):
                        # Mostrar pedidos disponibles para modificar
                        modifiable_orders = [o for o in orders_info if o["can_modify"]]
                        
                        if not modifiable_orders:
                            return {
                                "found": False,
                                "response": "❌ **No tenés pedidos que se puedan modificar actualmente**\n\n" \
                                          "Solo se pueden modificar pedidos dentro de los primeros 5 minutos.\n\n" \
                                          "¿Querés hacer un nuevo pedido?"
                            }
                        
                        response_text = "¿Cuál de estos pedidos querés modificar?\n\n"
                        
                        for order in modifiable_orders:
                            response_text += f"**#{order['id']}** - {order['product_name']}\n"
                            response_text += f"    📦 Cantidad: {order['quantity']} unidades\n"
                            response_text += f"    ⏰ Creado hace {order['minutes_ago']} minutos\n\n"
                        
                        response_text += "Decí el número de pedido que querés cambiar."
                        
                        return {
                            "found": False,
                            "response": response_text,
                            "available_orders": modifiable_orders
                        }
                
            except Exception as e:
                log(f"✏️❌ Error en análisis Ollama: {e}")
                # Fallback: usar el pedido más reciente modificable
                modifiable_orders = [o for o in orders_info if o["can_modify"]]
                
                if modifiable_orders:
                    most_recent = modifiable_orders[0]  # Ya están ordenados por fecha desc
                    return {
                        "found": True,
                        "order": most_recent,
                        "response": f"Usando tu pedido más reciente #{most_recent['id']}"
                    }
                else:
                    return {
                        "found": False,
                        "response": "No tenés pedidos que se puedan modificar en este momento.\n\n¿Querés hacer un nuevo pedido?"
                    }
                    
        except Exception as e:
            log(f"✏️❌ Error identificando pedido: {e}")
            return {
                "found": False,
                "response": "Tuve un problema accediendo a tus pedidos. ¿Podrías intentar de nuevo?"
            }
        finally:
            db.close()

    def _extract_json_from_response(self, response_text: str) -> str:
        """Extrae JSON de la respuesta de Ollama"""
        
        # CASO 1: Respuesta directa es JSON
        if response_text.strip().startswith('{') and response_text.strip().endswith('}'):
            return response_text.strip()
        
        # CASO 2: JSON dentro de markdown
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            if end != -1:
                return response_text[start:end].strip()
        
        # CASO 3: JSON después de texto explicativo
        first_brace = response_text.find('{')
        last_brace = response_text.rfind('}')
        
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_candidate = response_text[first_brace:last_brace + 1]
            
            # Verificar que sea JSON válido básico
            if json_candidate.count('{') == json_candidate.count('}'):
                return json_candidate
        
        return None
    
    async def _analyze_modification_type(self, message: str, order_identification: Dict) -> Dict:
        """Analiza qué tipo de modificación quiere hacer"""
        
        order_info = order_identification["order"]
        
        prompt = f"""Analiza qué modificación quiere hacer el usuario:

PEDIDO ACTUAL:
- ID: #{order_info['id']}
- Producto: {order_info['product_name']}
- Cantidad actual: {order_info['quantity']} unidades
- Estado: {order_info['status']}

MENSAJE DEL USUARIO: "{message}"

Responde SOLO con JSON válido:
{{
    "modification_type": "change_quantity" | "cancel_order" | "add_more" | "reduce_quantity" | "unclear",
    "new_quantity": numero_específico_o_null,
    "quantity_change": numero_para_sumar_o_restar_o_null,
    "is_clear": true_si_la_instrucción_es_clara,
    "confirmation_needed": true_si_necesita_confirmación,
    "extracted_keywords": ["palabras_clave_importantes"]
}}

EJEMPLOS:
- "cambiar a 100 unidades" → {{"modification_type": "change_quantity", "new_quantity": 100, "is_clear": true}}
- "quiero 30 más" → {{"modification_type": "add_more", "quantity_change": 30, "is_clear": true}}
- "reducir 20" → {{"modification_type": "reduce_quantity", "quantity_change": -20, "is_clear": true}}
- "cancelar pedido" → {{"modification_type": "cancel_order", "is_clear": true}}
- "cambiar cantidad" → {{"modification_type": "unclear", "confirmation_needed": true}}"""

        try:
            response = self.call_ollama([
                {"role": "system", "content": "Eres un dispatcher inteligente para un sistema de ventasB2B textil."},
                {"role": "user", "content": prompt}
            ])
            
            # Limpiar y parsear respuesta
            response_clean = self._extract_json_from_response(response)
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:-3]
            elif response_clean.startswith("```"):
                response_clean = response_clean[3:-3]
            
            analysis = json.loads(response_clean)
            log(f"✏️🎯 Análisis de modificación: {analysis}")
            
            # Calcular cantidad final
            current_qty = order_info["quantity"]
            
            if analysis.get("modification_type") == "change_quantity":
                analysis["final_quantity"] = analysis.get("new_quantity")
                
            elif analysis.get("modification_type") == "add_more":
                if analysis.get("quantity_change"):
                    analysis["final_quantity"] = current_qty + analysis["quantity_change"]
                elif analysis.get("new_quantity"):
                    analysis["final_quantity"] = current_qty + analysis["new_quantity"]
                    
            elif analysis.get("modification_type") == "reduce_quantity":
                if analysis.get("quantity_change"):
                    analysis["final_quantity"] = current_qty + analysis["quantity_change"]  # quantity_change ya es negativo
                elif analysis.get("new_quantity"):
                    analysis["final_quantity"] = current_qty - analysis["new_quantity"]
                    
            return analysis
            
        except Exception as e:
            log(f"✏️❌ Error analizando modificación: {e}")
            
            # Fallback basado en palabras clave
            import re
            message_lower = message.lower()
            
            if "cancelar" in message_lower:
                return {"modification_type": "cancel_order", "is_clear": True}
            
            # Buscar números
            numbers = re.findall(r'\d+', message)
            if numbers:
                new_qty = int(numbers[0])
                
                if "más" in message_lower or "agregar" in message_lower:
                    return {
                        "modification_type": "add_more",
                        "quantity_change": new_qty,
                        "final_quantity": order_info["quantity"] + new_qty,
                        "is_clear": True
                    }
                elif "menos" in message_lower or "reducir" in message_lower:
                    return {
                        "modification_type": "reduce_quantity",
                        "quantity_change": -new_qty,
                        "final_quantity": order_info["quantity"] - new_qty,
                        "is_clear": True
                    }
                else:
                    return {
                        "modification_type": "change_quantity",
                        "new_quantity": new_qty,
                        "final_quantity": new_qty,
                        "is_clear": True
                    }
            
            return {
                "modification_type": "unclear",
                "is_clear": False,
                "confirmation_needed": True
            }
    
    async def _validate_modification(self, modification: Dict, order_identification: Dict) -> Dict:
        """Valida que la modificación sea posible"""
        
        order_info = order_identification["order"]
        modification_type = modification.get("modification_type")
        
        # 1. Validar claridad de instrucción
        if not modification.get("is_clear") or modification_type == "unclear":
            return {
                "is_valid": False,
                "response": f"No entendí bien qué querés cambiar del pedido #{order_info['id']}.\n\n" \
                          f"📦 **Pedido actual:** {order_info['product_name']} - {order_info['quantity']} unidades\n\n" \
                          f"Podés decir:\n" \
                          f"• *'Cambiar a 80 unidades'*\n" \
                          f"• *'Agregar 20 más'*\n" \
                          f"• *'Reducir 10 unidades'*\n" \
                          f"• *'Cancelar pedido'*\n\n" \
                          f"¿Qué querés hacer exactamente?"
            }
        
        # 2. Si es cancelación, está ok
        if modification_type == "cancel_order":
            return {
                "is_valid": True,
                "modification_data": {
                    "type": "cancel",
                    "order_id": order_info["id"]
                }
            }
        
        # 3. Validar nueva cantidad
        final_quantity = modification.get("final_quantity")
        
        if not final_quantity or final_quantity <= 0:
            return {
                "is_valid": False,
                "response": f"❌ La cantidad debe ser mayor a 0.\n\n" \
                          f"📦 **Cantidad actual:** {order_info['quantity']} unidades\n\n" \
                          f"¿Cuántas unidades querés en total?"
            }
        
        if final_quantity < 50:
            return {
                "is_valid": False,
                "response": f"❌ **Pedido mínimo: 50 unidades**\n\n" \
                          f"📦 Cantidad solicitada: {final_quantity} unidades\n" \
                          f"📦 Cantidad actual: {order_info['quantity']} unidades\n\n" \
                          f"¿Querés ajustar a 50 unidades o cancelar el pedido?"
            }
        
        # 4. Validar stock disponible
        db = SessionLocal()
        try:
            product = db.query(models.Product).filter(
                models.Product.id == order_info["product_id"]
            ).first()
            
            if not product:
                return {
                    "is_valid": False,
                    "response": "❌ No pude encontrar el producto del pedido. Contactá a soporte."
                }
            
            # Calcular stock necesario considerando el cambio
            current_qty = order_info["quantity"]
            quantity_difference = final_quantity - current_qty
            
            # Si va a necesitar más stock del que actualmente reservó
            if quantity_difference > 0:
                available_stock = product.stock
                
                if available_stock < quantity_difference:
                    return {
                        "is_valid": False,
                        "response": f"❌ **Stock insuficiente**\n\n" \
                                  f"📦 Cantidad actual del pedido: {current_qty} unidades\n" \
                                  f"📦 Cantidad solicitada: {final_quantity} unidades\n" \
                                  f"📦 Stock disponible adicional: {available_stock} unidades\n" \
                                  f"📦 Necesitás: {quantity_difference} unidades más\n\n" \
                                  f"**Máximo posible:** {current_qty + available_stock} unidades\n\n" \
                                  f"¿Querés ajustar la cantidad?"
                    }
            
            # Calcular precio según nueva cantidad
            if final_quantity >= 200:
                precio_unitario = product.precio_200_u
            elif final_quantity >= 100:
                precio_unitario = product.precio_100_u
            else:
                precio_unitario = product.precio_50_u
            
            return {
                "is_valid": True,
                "modification_data": {
                    "type": "quantity_change",
                    "order_id": order_info["id"],
                    "current_quantity": current_qty,
                    "new_quantity": final_quantity,
                    "quantity_difference": quantity_difference,
                    "product_id": order_info["product_id"],
                    "product_name": order_info["product_name"],
                    "precio_unitario": precio_unitario,
                    "new_total": precio_unitario * final_quantity,
                    "stock_after_change": product.stock - quantity_difference
                }
            }
            
        except Exception as e:
            log(f"✏️❌ Error validando stock: {e}")
            return {
                "is_valid": False,
                "response": "Tuve un problema verificando el stock. ¿Podrías intentar de nuevo?"
            }
        finally:
            db.close()
    
    async def _execute_modification_with_stock_management(self, modification_data: Dict, order_info: Dict) -> Dict:
        """Ejecuta la modificación usando el CRUD arreglado"""
        
        try:
            if modification_data["type"] == "cancel":
                # ✅ USAR CRUD PARA CANCELAR
                order_update = schemas.OrderUpdate(status="cancelled")
                
                db = SessionLocal()
                try:
                    # Restaurar stock manualmente antes de cancelar
                    order = db.query(models.Order).filter(models.Order.id == modification_data["order_id"]).first()
                    product = db.query(models.Product).filter(models.Product.id == order.product_id).first()
                    
                    if order and product:
                        product.stock += order.qty
                        order.status = "cancelled"
                        db.commit()
                        
                        log(f"✏️✅ Pedido #{modification_data['order_id']} cancelado")
                        return {
                            "success": True,
                            "action": "cancelled",
                            "order_id": modification_data["order_id"],
                            "restored_quantity": order.qty,
                            "product_name": order_info["product_name"]
                        }
                finally:
                    db.close()
                    
            elif modification_data["type"] == "quantity_change":
                # ✅ USAR CRUD PARA CAMBIAR CANTIDAD
                order_update = schemas.OrderUpdate(qty=modification_data["new_quantity"])
                
                db = SessionLocal()
                try:
                    updated_order = crud.update_order(db, modification_data["order_id"], order_update)
                    
                    log(f"✏️✅ Pedido #{modification_data['order_id']} actualizado con CRUD")
                    
                    return {
                        "success": True,
                        "action": "quantity_changed",
                        "order_id": modification_data["order_id"],
                        "old_quantity": modification_data["current_quantity"],
                        "new_quantity": modification_data["new_quantity"],
                        "quantity_difference": modification_data["quantity_difference"],
                        "product_name": modification_data["product_name"],
                        "precio_unitario": modification_data["precio_unitario"],
                        "new_total": modification_data["new_total"],
                        "stock_after": modification_data["stock_after_change"]
                    }
                    
                except HTTPException as http_e:
                    log(f"✏️❌ Error CRUD: {http_e.detail}")
                    return {
                        "success": False,
                        "error": http_e.detail,
                        "error_type": "crud_error"
                    }
                finally:
                    db.close()
            
            else:
                return {
                    "success": False,
                    "error": f"Tipo de modificación no soportado: {modification_data['type']}"
                }
                
        except Exception as e:
            log(f"✏️❌ Error ejecutando modificación: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": "general"
            }

# Instancia global
modify_agent = ModifyAgent()