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
from .base_agent import BaseAgent

# Cargar variables de entorno
load_dotenv()

class SalesAgent(BaseAgent):
    """Agente especializado en asesoramiento comercial y recomendaciones de venta"""
    
    def __init__(self):
        super().__init__(agent_name="SalesAgent")

        
        if not self.api_keys:
            raise ValueError("No se encontraron GOOGLE_API_KEY en variables de entorno")
        
        # Configurar Gemini con la primera key válida
        self._configure_gemini()
        
        log(f"💡 SalesAgent inicializado con {len(self.api_keys)} API keys")
        
        # Definir conocimiento de productos textiles
        self.product_knowledge = {
            "camiseta": {
                "usos": ["uniformes empresariales", "promocionales", "eventos", "dotación personal"],
                "materiales": ["algodón 100%", "poliéster", "mezcla algodón-poliéster"],
                "ventajas": ["comodidad", "durabilidad", "fácil lavado", "personalizable"],
                "sectores": ["construcción", "servicios", "retail", "hospitality"]
            },
            "pantalón": {
                "usos": ["uniformes trabajo", "dotación laboral", "seguridad industrial"],
                "materiales": ["drill", "gabardina", "denim", "poliéster"],
                "ventajas": ["resistencia", "durabilidad", "profesionalismo", "comodidad"],
                "sectores": ["construcción", "industria", "servicios", "oficina"]
            },
            "sudadera": {
                "usos": ["construcción", "trabajo exterior", "promocionales", "deportivo"],
                "materiales": ["algodón afelpado", "poliéster", "mezclas"],
                "ventajas": ["abrigo", "comodidad", "durabilidad", "versátil"],
                "sectores": ["construcción", "logística", "deportivo", "promocional"]
            },
            "camisa": {
                "usos": ["oficina", "atención cliente", "eventos", "uniformes formales"],
                "materiales": ["algodón", "poliéster", "mezclas anti-arrugas"],
                "ventajas": ["profesionalismo", "elegancia", "comodidad", "fácil planchado"],
                "sectores": ["oficina", "servicios", "hospitality", "retail"]
            },
            "falda": {
                "usos": ["uniformes femeninos", "oficina", "servicios", "hospitality"],
                "materiales": ["gabardina", "poliéster", "mezclas"],
                "ventajas": ["profesionalismo", "comodidad", "versatilidad", "elegancia"],
                "sectores": ["oficina", "servicios", "hospitality", "retail"]
            }
        }
    

    async def handle_sales_advice(self, message: str, conversation: Dict) -> str:
        """Maneja consultas de asesoramiento comercial y recomendaciones"""
        
        try:
            log(f"💡 SalesAgent procesando: {message}")
            
            # 1. Analizar qué tipo de asesoramiento necesita
            advice_type = await self._analyze_advice_request(message, conversation)
            
            # 2. Obtener información relevante del inventario
            relevant_products = await self._get_relevant_products_for_advice(advice_type, conversation)
            
            # 3. Generar recomendación personalizada
            response = await self._generate_sales_advice(message, advice_type, relevant_products, conversation)
            
            return response
            
        except Exception as e:
            log(f"💡❌ Error en SalesAgent: {e}")
            return "Disculpa, tuve un problema generando recomendaciones. ¿Podrías contarme más específicamente qué necesitás para tu empresa?"
    
    async def _analyze_advice_request(self, message: str, conversation: Dict) -> Dict:
        """Analiza qué tipo de asesoramiento comercial necesita"""
        
        # Extraer contexto de la conversación
        recent_messages = ""
        for msg in conversation.get('messages', [])[-3:]:  # Últimos 3 mensajes
            role = "Usuario" if msg['role'] == 'user' else "Bot"
            recent_messages += f"{role}: {msg['content']}\n"
        
        # Información de pedidos previos para personalización
        previous_orders = ""
        if conversation.get('recent_orders'):
            previous_orders = "Pedidos anteriores:\n"
            for order in conversation.get('recent_orders', [])[:3]:
                previous_orders += f"- Cantidad: {order['quantity']}, Status: {order['status']}\n"
        
        prompt = f"""Analiza qué tipo de asesoramiento comercial necesita este cliente B2B:

CONVERSACIÓN RECIENTE:
{recent_messages}

MENSAJE ACTUAL: "{message}"

{previous_orders}

Sectores típicos: construcción, servicios, retail, oficina, hospitality, industria
Productos disponibles: camisetas, pantalones, sudaderas, camisas, faldas

Responde SOLO con JSON válido:
{{
    "advice_type": "product_recommendation" | "sector_specific" | "quantity_advice" | "use_case_advice" | "cost_optimization" | "material_advice" | "general_business",
    "sector_context": "construcción|servicios|retail|oficina|hospitality|industria|unclear",
    "specific_products": ["lista_de_productos_mencionados"],
    "business_need": "uniformes|dotación|promocional|eventos|seguridad|unclear",
    "budget_concern": true_si_menciona_precio_o_presupuesto,
    "quantity_context": "small_batch|medium_volume|large_scale|unclear",
    "urgency": "urgent|normal|flexible",
    "personalization_hints": ["detalles_específicos_del_negocio"]
}}

EJEMPLOS:
- "qué me recomendás para mi constructora?" → {{"advice_type": "sector_specific", "sector_context": "construcción", "business_need": "dotación"}}
- "cuál es mejor para uniformes?" → {{"advice_type": "product_recommendation", "business_need": "uniformes"}}
- "necesito algo económico para 200 empleados" → {{"advice_type": "cost_optimization", "quantity_context": "large_scale", "budget_concern": true}}
- "qué tela dura más?" → {{"advice_type": "material_advice"}}"""

        try:
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,  # Algo más creativo para asesoramiento
                    max_output_tokens=250,
                )
            )
            
            # Limpiar y parsear respuesta
            response_clean = response.text.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:-3]
            elif response_clean.startswith("```"):
                response_clean = response_clean[3:-3]
            
            parsed_advice = json.loads(response_clean)
            log(f"💡🎯 Análisis de asesoramiento: {parsed_advice}")
            
            return parsed_advice
            
        except Exception as e:
            log(f"💡❌ Error analizando asesoramiento: {e}")
            
            # Fallback basado en palabras clave
            message_lower = message.lower()
            
            # Detectar sector
            sector = "unclear"
            if any(word in message_lower for word in ["construcción", "construcción", "obra", "albañil"]):
                sector = "construcción"
            elif any(word in message_lower for word in ["oficina", "empresa", "corporativo"]):
                sector = "oficina"
            elif any(word in message_lower for word in ["restaurant", "hotel", "servicio"]):
                sector = "hospitality"
            elif any(word in message_lower for word in ["tienda", "retail", "comercio"]):
                sector = "retail"
            
            # Detectar tipo de consulta
            if any(word in message_lower for word in ["recomendás", "mejor", "conviene"]):
                advice_type = "product_recommendation"
            elif any(word in message_lower for word in ["precio", "económico", "barato", "costo"]):
                advice_type = "cost_optimization"
            elif any(word in message_lower for word in ["material", "tela", "calidad", "dura"]):
                advice_type = "material_advice"
            else:
                advice_type = "general_business"
            
            return {
                "advice_type": advice_type,
                "sector_context": sector,
                "specific_products": [],
                "business_need": "unclear",
                "budget_concern": "económico" in message_lower,
                "quantity_context": "unclear",
                "urgency": "normal",
                "personalization_hints": []
            }
    
    async def _get_relevant_products_for_advice(self, advice_type: Dict, conversation: Dict) -> Dict:
        """Obtiene productos relevantes del inventario para el asesoramiento"""
        
        db = SessionLocal()
        try:
            # Base query: productos con stock > 0
            query = db.query(models.Product).filter(models.Product.stock > 0)
            
            # Filtrar según el contexto del asesoramiento
            sector = advice_type.get("sector_context", "")
            business_need = advice_type.get("business_need", "")
            
            # Si hay productos específicos mencionados, priorizarlos
            specific_products = advice_type.get("specific_products", [])
            if specific_products:
                # Buscar productos específicos mencionados
                for product_type in specific_products:
                    query = query.filter(models.Product.tipo_prenda.ilike(f"%{product_type}%"))
            
            # Limitar a productos más relevantes
            products = query.order_by(models.Product.stock.desc()).limit(15).all()
            
            # Organizar productos por categoría para el asesoramiento
            products_by_type = {}
            total_options = 0
            
            for product in products:
                tipo = product.tipo_prenda.lower()
                if tipo not in products_by_type:
                    products_by_type[tipo] = []
                
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
                
                products_by_type[tipo].append(product_data)
                total_options += 1
            
            log(f"💡📊 Productos obtenidos para asesoramiento: {total_options} opciones en {len(products_by_type)} categorías")
            
            return {
                "products_by_type": products_by_type,
                "total_products": total_options,
                "advice_context": advice_type
            }
            
        except Exception as e:
            log(f"💡❌ Error obteniendo productos para asesoramiento: {e}")
            return {
                "products_by_type": {},
                "total_products": 0,
                "advice_context": advice_type,
                "error": str(e)
            }
        finally:
            db.close()
    
    async def _generate_sales_advice(self, message: str, advice_type: Dict, products_data: Dict, conversation: Dict) -> str:
        """Genera asesoramiento comercial personalizado"""
        
        products_by_type = products_data.get("products_by_type", {})
        total_products = products_data.get("total_products", 0)
        
        # Si no hay productos disponibles
        if total_products == 0:
            return "En este momento no tengo productos disponibles para hacerte recomendaciones específicas. " \
                   "¿Hay algún producto particular que te interese que pueda conseguir?"
        
        # Obtener información específica del sector/uso
        sector_context = advice_type.get("sector_context", "unclear")
        advice_type_str = advice_type.get("advice_type", "general_business")
        budget_concern = advice_type.get("budget_concern", False)
        
        # Construir contexto de productos para Gemini
        products_context = self._build_products_context_for_gemini(products_by_type)
        
        # Construir contexto de conocimiento sectorial
        sector_knowledge = self._get_sector_knowledge(sector_context)
        
        prompt = f"""Eres un asesor comercial experto en textiles B2B. Genera una recomendación personalizada:

CONSULTA DEL CLIENTE: "{message}"

CONTEXTO DEL CLIENTE:
- Sector: {sector_context}
- Tipo de consulta: {advice_type_str}
- Preocupación por presupuesto: {budget_concern}
- Necesidad de negocio: {advice_type.get('business_need', 'unclear')}

CONOCIMIENTO SECTORIAL:
{sector_knowledge}

PRODUCTOS DISPONIBLES:
{products_context}

INSTRUCCIONES:
1. Genera una respuesta profesional y consultiva
2. Recomienda productos específicos basado en el sector/uso
3. Incluye precios y cantidades cuando sea relevante
4. Menciona ventajas específicas para su negocio
5. Sugiere combinaciones inteligentes de productos
6. Si hay preocupación por presupuesto, destaca opciones económicas
7. Incluye consejos prácticos de implementación
8. Termina con una pregunta para continuar la conversación

FORMATO DE RESPUESTA:
- Saludo consultivo
- Recomendación principal con justificación
- Opciones específicas con precios
- Ventajas para su sector
- Consejo práctico adicional
- Pregunta de seguimiento

TONO: Profesional, consultivo, orientado a soluciones empresariales"""

        try:
            response = await self._make_gemini_request_with_fallback(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,  # Creativo pero coherente
                    max_output_tokens=600,
                )
            )
            
            advice_response = response.text.strip()
            
            # Agregar emoji y estructura si es necesario
            if not advice_response.startswith("💡"):
                advice_response = f"💡 **ASESORAMIENTO COMERCIAL**\n\n{advice_response}"
            
            return advice_response
            
        except Exception as e:
            log(f"💡❌ Error generando asesoramiento: {e}")
            
            # Fallback con recomendación básica basada en el sector
            return self._generate_fallback_advice(advice_type, products_by_type, message)
    
    def _build_products_context_for_gemini(self, products_by_type: Dict) -> str:
        """Construye contexto de productos para Gemini"""
        
        context = ""
        for tipo, products in products_by_type.items():
            context += f"\n{tipo.upper()}S DISPONIBLES:\n"
            
            # Mostrar hasta 3 productos por tipo para no sobrecargar
            for product in products[:3]:
                context += f"- {product['name']} ({product['color']} - {product['talla']})\n"
                context += f"  Stock: {product['stock']} unidades\n"
                context += f"  Precios: ${product['precio_50_u']:,.0f} (50+) | ${product['precio_100_u']:,.0f} (100+) | ${product['precio_200_u']:,.0f} (200+)\n"
            
            if len(products) > 3:
                context += f"  ... y {len(products) - 3} opciones más en {tipo}s\n"
            context += "\n"
        
        return context
    
    def _get_sector_knowledge(self, sector: str) -> str:
        """Obtiene conocimiento específico del sector"""
        
        sector_info = {
            "construcción": """
CONSTRUCCIÓN:
- Priorizan durabilidad y resistencia al desgaste
- Necesitan ropa cómoda para trabajo físico intenso
- Importante: fácil lavado y secado rápido
- Colores recomendados: azul, gris, verde (ocultan manchas)
- Productos clave: sudaderas, pantalones resistentes, camisetas básicas
- Volúmenes típicos: 50-200 unidades por pedido
""",
            "oficina": """
OFICINA/CORPORATIVO:
- Imagen profesional es prioridad
- Comodidad para trabajo sedentario
- Colores corporativos: azul, blanco, gris, negro
- Productos clave: camisas, pantalones formales, faldas
- Consideran personalización con logos
- Volúmenes típicos: 20-100 unidades por pedido
""",
            "servicios": """
SERVICIOS:
- Equilibrio entre profesionalismo y practicidad
- Interacción con clientes requiere buena imagen
- Fácil mantenimiento y lavado
- Productos clave: camisetas uniformes, camisas, pantalones
- Personalización con logos empresariales
- Volúmenes típicos: 30-150 unidades por pedido
""",
            "hospitality": """
HOSPITALITY (Hoteles/Restaurantes):
- Imagen impecable y profesional
- Comodidad para largas jornadas
- Resistencia a manchas y lavados frecuentes
- Productos clave: camisas, pantalones, faldas uniformes
- Colores que representen la marca
- Volúmenes típicos: 50-300 unidades por pedido
""",
            "retail": """
RETAIL:
- Imagen de marca es crucial
- Comodidad para estar de pie muchas horas
- Necesitan proyectar confianza al cliente
- Productos clave: camisetas polo, camisas, pantalones
- Personalización con branding
- Volúmenes típicos: 25-120 unidades por pedido
""",
            "industria": """
INDUSTRIA:
- Seguridad y durabilidad son prioridad
- Resistencia a condiciones adversas
- Comodidad para trabajo físico
- Productos clave: pantalones resistentes, sudaderas, camisetas
- Colores oscuros preferidos
- Volúmenes típicos: 100-500 unidades por pedido
"""
        }
        
        return sector_info.get(sector, "GENERAL: Enfoque en calidad, durabilidad y valor por dinero.")
    
    def _generate_fallback_advice(self, advice_type: Dict, products_by_type: Dict, message: str) -> str:
        """Genera recomendación básica si falla Gemini"""
        
        sector = advice_type.get("sector_context", "unclear")
        advice_type_str = advice_type.get("advice_type", "general_business")
        
        response = "💡 **ASESORAMIENTO COMERCIAL**\n\n"
        
        if sector == "construcción":
            response += "Para tu empresa de **construcción**, recomiendo priorizan **durabilidad y comodidad**:\n\n"
            
            if "sudadera" in products_by_type:
                sudaderas = products_by_type["sudadera"][:2]
                response += "🔸 **SUDADERAS** - Ideales para trabajo exterior:\n"
                for s in sudaderas:
                    response += f"   • {s['name']} - ${s['precio_100_u']:,.0f} c/u (100+ un.)\n"
                response += "\n"
            
            if "pantalón" in products_by_type:
                pantalones = products_by_type["pantalón"][:2]
                response += "🔸 **PANTALONES** - Resistentes al desgaste:\n"
                for p in pantalones:
                    response += f"   • {p['name']} - ${p['precio_100_u']:,.0f} c/u (100+ un.)\n"
                response += "\n"
                
        elif sector == "oficina":
            response += "Para ambiente **corporativo/oficina**, recomiendo productos que proyecten **profesionalismo**:\n\n"
            
            if "camisa" in products_by_type:
                camisas = products_by_type["camisa"][:2]
                response += "🔸 **CAMISAS** - Imagen profesional:\n"
                for c in camisas:
                    response += f"   • {c['name']} - ${c['precio_50_u']:,.0f} c/u (50+ un.)\n"
                response += "\n"
                
        else:
            # Recomendación general
            response += "Basado en tu consulta, estas son mis **recomendaciones principales**:\n\n"
            
            # Mostrar productos más populares
            for tipo, products in list(products_by_type.items())[:2]:
                response += f"🔸 **{tipo.upper()}S** disponibles:\n"
                for product in products[:2]:
                    response += f"   • {product['name']} - ${product['precio_50_u']:,.0f} c/u (50+ un.)\n"
                response += "\n"
        
        response += "💰 **Ventajas de comprar por volumen:**\n"
        response += "• 50+ unidades: Precio base\n"
        response += "• 100+ unidades: Hasta 15% descuento\n"
        response += "• 200+ unidades: Hasta 25% descuento\n\n"
        
        response += "¿Te sirve esta información? ¿Hay algún producto específico que te interese más?"
        
        return response

# Instancia global
sales_agent = SalesAgent()