from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..database import SessionLocal
from .. import models
import json
import os
from ..utils.logger import log
from .base_agent import BaseAgent

class StockAgent(BaseAgent):
    """Agente especializado en consultas de inventario y stock"""
    
    def __init__(self):
        super().__init__(agent_name="StockAgent")
        
        log(f"📦 StockAgent inicializado para Ollama")

    async def handle_stock_query(self, message: str, conversation: Dict) -> str:
        """Maneja consultas de stock con análisis inteligente del mensaje"""
        
        try:
            log(f"📦 StockAgent procesando: {message}")
            
            # 1. Analizar qué busca específicamente el usuario
            stock_query = await self._analyze_stock_query(message, conversation)
            
            # 2. Ejecutar búsqueda en la base de datos
            stock_data = await self._get_stock_data(stock_query)
            
            # 3. ✅ GENERAR RESPUESTA INTELIGENTE CON OLLAMA
            response = await self._generate_intelligent_stock_response(message, stock_query, stock_data, conversation)
            
            return response
            
        except Exception as e:
            log(f"📦❌ Error en StockAgent: {e}")
            return "Disculpa, tuve un problema consultando el inventario. ¿Podrías intentar de nuevo?"

    async def _analyze_stock_query(self, message: str, conversation: Dict) -> Dict:
        """Analiza el mensaje para entender qué stock consulta específicamente"""
        
        # ✅ EXTRAER CONTEXTO MÁS INTELIGENTE
        recent_context = self._extract_conversation_context(conversation)
        
        prompt = f"""Analiza esta consulta de stock usando el CONTEXTO de la conversación:

CONTEXTO RECIENTE:
{recent_context}

MENSAJE ACTUAL: "{message}"

PRODUCTOS DISPONIBLES: pantalón, camiseta, falda, sudadera, camisa
COLORES: blanco, negro, azul, verde, gris, rojo, amarillo
TALLES: S, M, L, XL, XXL

✅ USAR EL CONTEXTO:
- Si mencionó un producto antes, mantener ese foco
- Si pidió colores/talles específicos, recordar
- Si pregunta "y camisetas?", significa que ya vio otros productos

Responde SOLO con JSON:
{{
    "query_type": "specific_product" | "general_availability" | "color_options" | "size_options",
    "filters": {{
        "tipo_prenda": "valor_o_null",
        "color": "valor_o_null", 
        "talla": "valor_o_null"
    }},
    "context_continuation": true_si_es_continuación_de_búsqueda_previa,
    "question_focus": "availability" | "colors" | "sizes" | "quantities",
    "detail_level": "basic" | "detailed"
}}"""

        try:
            response = self.call_ollama([
                {"role": "system", "content": "Analizas consultas de stock usando contexto conversacional."},
                {"role": "user", "content": prompt}
            ])
            
            json_content = self._extract_json_from_response(response)
            if json_content:
                parsed_query = json.loads(json_content)
                
                # ✅ APLICAR MEJORAS CONTEXTUALES
                parsed_query = self._apply_contextual_improvements(parsed_query, conversation, message)
                
                log(f"📦🎯 Query analizada con contexto: {parsed_query}")
                return parsed_query
                
        except Exception as e:
            log(f"📦❌ Error analizando query: {e}")
            
        return self._fallback_query_analysis(message, conversation)

    def _extract_conversation_context(self, conversation: Dict) -> str:
        """Extrae contexto relevante de la conversación"""
        
        context_parts = []
        recent_messages = conversation.get('messages', [])[-6:]  # Últimos 6 mensajes
        
        for msg in recent_messages:
            role = "Usuario" if msg['role'] == 'user' else "Bot"
            content = msg['content'][:100]  # Primeros 100 caracteres
            context_parts.append(f"{role}: {content}")
        
        return "\n".join(context_parts)

    def _apply_contextual_improvements(self, parsed_query: Dict, conversation: Dict, message: str) -> Dict:
        """Aplica mejoras basadas en el contexto"""
        
        # ✅ NO USAR CONTEXTO SI EL MENSAJE ACTUAL ES ESPECÍFICO
        message_lower = message.lower()
        
        # Si el mensaje actual especifica un producto claramente, ignorar contexto
        explicit_products = ["pantalón", "pantalones", "camiseta", "camisetas", "sudadera", "chaqueta", "falda"]
        has_explicit_product = any(product in message_lower for product in explicit_products)
        
        if has_explicit_product:
            log(f"📦🎯 Mensaje específico detectado, ignorando contexto previo")
            return parsed_query
        
        # Solo aplicar contexto si es continuación Y no hay producto específico
        if parsed_query.get("context_continuation") and not parsed_query["filters"].get("tipo_prenda"):
            
            # Buscar productos mencionados recientemente por el bot
            for msg in reversed(conversation.get('messages', [])[-5:]):
                if msg['role'] == 'assistant':
                    content = msg['content'].lower()
                    
                    # Buscar tipos de prenda mencionados
                    for tipo in ["camiseta", "pantalón", "sudadera", "camisa", "falda"]:
                        if tipo in content:
                            parsed_query["filters"]["tipo_prenda"] = tipo
                            log(f"📦🔍 Contexto aplicado: tipo_prenda = {tipo}")
                            break
                    
                    if parsed_query["filters"].get("tipo_prenda"):
                        break
        
        return parsed_query

    # ✅ NUEVO MÉTODO: Respuesta inteligente generada por Ollama
    async def _generate_intelligent_stock_response(self, original_message: str, query: Dict, stock_data: Dict, conversation: Dict) -> str:
        """Genera respuesta inteligente y detallada usando Ollama"""
        
        products = stock_data.get("products", [])
        
        if not products:
            return await self._generate_no_stock_response(query)
        
        # ✅ LIMITAR PRODUCTOS SEGÚN LONGITUD ESPERADA
        max_products_to_show = 4 if len(products) > 8 else min(6, len(products))
        products_to_show = products[:max_products_to_show]
        
        # ✅ INCLUIR descripción y categoría pero compactas
        products_summary = []
        for product in products_to_show:
            # Truncar descripción si es muy larga
            description = product.get('descripcion', 'Material de alta calidad')
            if len(description) > 50:
                description = description[:47] + "..."
                
            products_summary.append({
                "name": product['name'],
                "tipo": product['tipo_prenda'],
                "color": product['color'],
                "talla": product['talla'],
                "stock": product['stock'],
                "precio_50": product['precio_50_u'],
                "precio_100": product['precio_100_u'],
                "precio_200": product['precio_200_u'],
                "descripcion": description,
                "categoria": product.get('categoria', 'General')
            })
        
        # Estadísticas compactas
        stats = {
            "total_products": len(products),
            "total_stock": sum(p['stock'] for p in products),
            "showing": len(products_to_show),
            "categories": list(set(p.get('categoria', 'General') for p in products))
        }
        
        # ✅ PROMPT MEJORADO PARA MOSTRAR TODOS LOS PRODUCTOS ENCONTRADOS
        prompt = f"""Genera UNA respuesta COMPLETA sobre inventario (MÁXIMO 3200 caracteres).

CONSULTA: "{original_message}"
PRODUCTOS ENCONTRADOS (mostrar TODOS los {stats['showing']} productos):
{json.dumps(products_summary, indent=1, ensure_ascii=False)}

INSTRUCCIONES CRÍTICAS:
- MOSTRAR TODOS LOS PRODUCTOS de la lista
- RESPONDE DIRECTAMENTE, sin tags <think> ni metadata
- MÁXIMO 3200 caracteres total
- AGRUPAR por categorías con emojis apropiados
- Incluir descripción Y precios para cada producto
- Si hay múltiples productos del mismo tipo, mostrarlos todos

FORMATO OBLIGATORIO:
🏢 *FORMAL*
• *Pantalón Verde L* - 334 unidades
  📋 Material de alta calidad
  💰 $1,017 (50+) | $639 (100+) | $1,238 (200+)

• *Pantalón Verde XL* - 151 unidades  
  📋 Diseño moderno y elegante
  💰 $603 (50+) | $799 (100+) | $367 (200+)

💡 *Mejor precio comprando +200 unidades*

¿Te interesa alguno en particular?

MOSTRAR TODOS LOS PRODUCTOS ENCONTRADOS:"""

        try:
            response = self.call_ollama([
                {"role": "system", "content": "Respondes DIRECTAMENTE sobre inventario textil B2B mostrando TODOS los productos encontrados. NO uses tags <think> ni metadata. Máximo 3200 caracteres."},
                {"role": "user", "content": prompt}
            ])
            
            # ✅ LIMPIAR TAGS Y METADATA DE OLLAMA
            clean_response = self._clean_ollama_response(response)
            
            # ✅ VALIDAR QUE NO ESTÉ VACÍA O MUY CORTA
            if len(clean_response.strip()) < 50:
                log(f"📦⚠️ Respuesta muy corta ({len(clean_response)} chars), usando fallback")
                return self._generate_category_organized_fallback(products_to_show, stats)
            
            # Si aún es muy largo, usar fallback
            if len(clean_response) > 3200:
                log(f"📦⚠️ Respuesta muy larga ({len(clean_response)} chars), usando fallback")
                return self._generate_category_organized_fallback(products_to_show, stats)
            
            # Agregar información adicional si hay más productos
            if len(products) > max_products_to_show:
                clean_response += f"\n\n📋 *+{len(products) - max_products_to_show} productos más disponibles*"
                clean_response += f"\n💬 Especifica color/talle para ver opciones exactas."
            
            log(f"📦✅ Respuesta completa generada: {len(clean_response)} caracteres")
            return clean_response
            
        except Exception as e:
            log(f"📦❌ Error generando respuesta: {e}")
            return self._generate_category_organized_fallback(products_to_show, stats)

    def _clean_ollama_response(self, response: str) -> str:
        """Limpia respuesta de Ollama removiendo metadata y tags"""
        
        if not response or len(response.strip()) < 10:
            return ""
        
        # ✅ REMOVER TAG <think>
        if "<think>" in response:
            # Buscar el final del tag think
            think_start = response.find("<think>")
            think_end = response.find("</think>")
            
            if think_end != -1:
                # Remover toda la sección <think>...</think>
                response = response[:think_start] + response[think_end + 8:]
            else:
                # Si no hay cierre, remover desde <think> hasta encontrar contenido real
                lines = response.split('\n')
                cleaned_lines = []
                skip_mode = False
                
                for line in lines:
                    if "<think>" in line:
                        skip_mode = True
                        continue
                    elif skip_mode and (line.strip().startswith("🏢") or line.strip().startswith("👕") or line.strip().startswith("🎽") or line.strip().startswith("📦")):
                        # Encontró el inicio del contenido real
                        skip_mode = False
                    
                    if not skip_mode:
                        cleaned_lines.append(line)
                
                response = '\n'.join(cleaned_lines)
        
        # ✅ REMOVER OTROS TAGS COMUNES
        unwanted_tags = [
            "<thinking>", "</thinking>",
            "<analysis>", "</analysis>", 
            "<response>", "</response>",
            "```thinking", "```",
            "```text", "```markdown"
        ]
        
        for tag in unwanted_tags:
            response = response.replace(tag, "")
        
        # ✅ PRESERVAR ESTRUCTURA PERO LIMPIAR ESPACIOS EXCESIVOS
        lines = response.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if line:  # Solo agregar líneas no vacías
                cleaned_lines.append(line)
            elif cleaned_lines and not cleaned_lines[-1]:  # Evitar líneas vacías múltiples
                continue
            else:
                cleaned_lines.append("")  # Mantener una línea vacía para estructura
        
        # Remover líneas vacías al final
        while cleaned_lines and not cleaned_lines[-1]:
            cleaned_lines.pop()
        
        return '\n'.join(cleaned_lines)

    async def _generate_enhanced_fallback_response(self, products: List[Dict], stats: Dict) -> str:
        """Respuesta de fallback mejorada con más información"""
        
        # Agrupar por categoría
        by_category = {}
        for product in products[:8]:  # Mostrar hasta 8
            category = product.get('categoria', 'General')
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(product)
        
        response = "📦 *INVENTARIO DISPONIBLE*\n\n"
        
        for category, cat_products in by_category.items():
            category_emoji = {
                'Deportivo': '🏃‍♂️',
                'Formal': '🏢', 
                'Casual': '👕',
                'General': '📋'
            }.get(category, '📋')
            
            response += f"{category_emoji} *{category.upper()}*\n"
            
            for product in cat_products:
                response += f"• *{product['name']}* - {product['stock']} unidades\n"
                response += f"  💰 ${product['precio_50_u']:,.0f} (50+) | ${product['precio_100_u']:,.0f} (100+) | ${product['precio_200_u']:,.0f} (200+)\n"
                if product.get('descripcion'):
                    response += f"  📋 {product['descripcion']}\n"
                response += "\n"
        
        # Estadísticas finales
        response += f"📊 *RESUMEN*\n"
        response += f"• Stock total: *{stats['total_stock']:,} unidades*\n"
        response += f"• Categorías: {', '.join(stats['categories'])}\n"
        response += f"• Colores: {', '.join(stats['colors'])}\n\n"
        response += f"💡 *CONSEJO:* Mayor cantidad = mejor precio por unidad\n\n"
        response += f"¿Qué categoría te interesa para tu empresa?"
        
        return response

    async def _generate_no_stock_response(self, query: Dict) -> str:
        """Respuesta cuando no hay stock disponible"""
        filters = query.get("filters", {})
        
        suggestions = []
        if filters.get("color"):
            suggestions.append(f"otros colores en {filters.get('tipo_prenda', 'productos')}")
        if filters.get("talla"):
            suggestions.append(f"otros talles disponibles")
        if not suggestions:
            suggestions.append("nuestro catálogo completo")
        
        return f"❌ No tengo stock disponible con esos filtros específicos.\n\n" \
               f"¿Te interesa ver {' o '.join(suggestions)}?\n\n" \
               f"💬 Escribí *'todo el catálogo'* para ver todas las opciones."

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

    async def _get_stock_data(self, query: Dict) -> Dict:
        """Obtiene datos de stock de la base de datos según los filtros"""
        
        db = SessionLocal()
        try:
            # Construcción de query base
            db_query = db.query(models.Product).filter(models.Product.stock > 0)
            
            # Aplicar filtros
            filters = query.get("filters", {})
            
            if filters.get("tipo_prenda"):
                db_query = db_query.filter(models.Product.tipo_prenda.ilike(f"%{filters['tipo_prenda']}%"))
                
            if filters.get("color"):
                db_query = db_query.filter(models.Product.color.ilike(f"%{filters['color']}%"))
                
            if filters.get("talla"):
                db_query = db_query.filter(models.Product.talla.ilike(f"%{filters['talla']}%"))
            
            # Ordenar por stock descendente
            db_query = db_query.order_by(models.Product.stock.desc())
            
            # Ejecutar query
            products_db = db_query.all()
            
            log(f"📦🔍 Encontrados {len(products_db)} productos")
            
            # Convertir a diccionarios
            products = []
            for product in products_db:
                product_dict = {
                    "id": product.id,
                    "name": product.name,
                    "tipo_prenda": product.tipo_prenda,
                    "color": product.color,
                    "talla": product.talla,
                    "stock": product.stock,
                    "precio_50_u": product.precio_50_u,
                    "precio_100_u": product.precio_100_u,
                    "precio_200_u": product.precio_200_u,
                    "descripcion": getattr(product, 'descripcion', 'Material de alta calidad'),
                    "categoria": getattr(product, 'categoria', 'General'),
                    "created_at": product.created_at.isoformat() if product.created_at else None
                }
                products.append(product_dict)
            
            return {
                "products": products,
                "total_found": len(products),
                "filters_applied": filters,
                "query_type": query.get("query_type", "general")
            }
            
        except Exception as e:
            log(f"📦❌ Error obteniendo datos de stock: {e}")
            return {
                "products": [],
                "total_found": 0,
                "error": str(e)
            }
        finally:
            db.close()

    def _group_products_by_category(self, products: List[Dict]) -> Dict:
        """Agrupa productos por categoría"""
        
        grouped = {}
        for product in products:
            category = product.get('categoria', 'General')
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(product)
        
        return grouped

    def _calculate_savings_percentage(self, price_50: float, price_200: float) -> int:
        """Calcula el porcentaje de ahorro comprando en volumen"""
        if price_50 <= 0:
            return 0
        return int(((price_50 - price_200) / price_50) * 100)

    def _get_category_emoji(self, category: str) -> str:
        """Obtiene emoji apropiado para cada categoría"""
        emoji_map = {
            'Deportivo': '🏃‍♂️',
            'Formal': '🏢', 
            'Casual': '👕',
            'General': '📋'
        }
        return emoji_map.get(category, '📋')

    def _generate_ultra_compact_fallback(self, products: List[Dict]) -> str:
        """Fallback ultra-compacto para emergencias"""
        
        if not products:
            return "❌ No hay stock disponible.\n¿Qué otro producto te interesa?"
        
        response = f"📦 **{products[0]['tipo_prenda'].upper()}S DISPONIBLES**\n\n"
        
        for product in products[:4]:  # Solo 4 productos máximo
            response += f"• {product['name']} - {product['stock']} unidades - ${product['precio_50_u']:,.0f}\n"
        
        if len(products) > 4:
            response += f"\n... y {len(products) - 4} más disponibles.\n"
        
        response += f"\n¿Cuál te interesa?"
        
        return response

    def _generate_category_organized_fallback(self, products: List[Dict], stats: Dict) -> str:
        """Fallback organizado por categorías con descripciones"""
        
        if not products:
            return "❌ No hay stock disponible.\n¿Qué otro producto te interesa?"
        
        # Agrupar por categoría
        by_category = self._group_products_by_category(products)
        
        response = f"📦 *{products[0]['tipo_prenda'].upper()}S DISPONIBLES*\n\n"
        
        for category, cat_products in by_category.items():
            # Emoji por categoría
            category_emoji = self._get_category_emoji(category)
            response += f"{category_emoji} *{category.upper()}*\n"
            
            for product in cat_products[:3]:  # Max 3 por categoría para evitar límite
                response += f"• *{product['name']}* - {product['stock']} unidades\n"
                
                # Descripción truncada
                desc = product.get('descripcion', 'Material de alta calidad')
                if len(desc) > 40:
                    desc = desc[:37] + "..."
                response += f"  📋 {desc}\n"
                
                # Precios compactos
                response += f"  💰 ${product['precio_50_u']:,.0f} (50+) | ${product['precio_100_u']:,.0f} (100+) | ${product['precio_200_u']:,.0f} (200+)\n\n"
        
        # Resumen final
        if stats['total_products'] > len(products):
            response += f"📋 *+{stats['total_products'] - len(products)} más disponibles*\n"
        
        response += f"💡 *Mejor precio comprando +200 unidades*\n"
        response += f"¿Te interesa alguno en particular?"
        
        return response

    def _fallback_query_analysis(self, message: str, conversation: Dict) -> Dict:
        """Análisis de fallback más inteligente"""
        message_lower = message.lower()
        
        tipo_prenda = None
        if any(word in message_lower for word in ["pantalón", "pantalones"]):
            tipo_prenda = "pantalón"
        elif any(word in message_lower for word in ["camiseta", "camisetas", "camisa", "camisas"]):
            tipo_prenda = "camiseta"  
        elif any(word in message_lower for word in ["sudadera", "sudaderas", "buzo", "buzos"]):
            tipo_prenda = "sudadera"
        elif any(word in message_lower for word in ["chaqueta", "chaquetas", "campera"]):
            tipo_prenda = "chaqueta"
        elif any(word in message_lower for word in ["falda", "faldas"]):
            tipo_prenda = "falda"
        
        color = None
        if "amarillo" in message_lower:
            color = "amarillo"
        elif "verde" in message_lower:
            color = "verde"  
        elif "azul" in message_lower:
            color = "azul"
        elif "rojo" in message_lower:
            color = "rojo"
        elif "negro" in message_lower:
            color = "negro"
        elif "blanco" in message_lower:
            color = "blanco"
        elif "gris" in message_lower:
            color = "gris"
        
        talla = None
        if " s " in message_lower or message_lower.endswith(" s"):
            talla = "S"
        elif " m " in message_lower or message_lower.endswith(" m"):
            talla = "M" 
        elif " l " in message_lower or message_lower.endswith(" l") or "talle l" in message_lower:
            talla = "L"
        elif "xl" in message_lower:
            if "xxl" in message_lower:
                talla = "XXL"
            else:
                talla = "XL"
        
        log(f"📦🎯 Fallback detectó - Tipo: {tipo_prenda}, Color: {color}, Talla: {talla}")
        
        return {
            "query_type": "specific_product" if tipo_prenda or color else "general_availability",
            "filters": {"tipo_prenda": tipo_prenda, "color": color, "talla": talla},  # ✅ Incluir talla
            "question_focus": "availability",
            "context_needed": False,
            "detail_level": "basic"
        }

# Instancia global
stock_agent = StockAgent()