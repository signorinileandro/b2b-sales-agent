from typing import Dict
from datetime import datetime
from .base_agent import BaseAgent
from ..utils.logger import log

class GeneralChatAgent(BaseAgent):
    """Agente para conversación general, saludos y presentación"""
    
    def __init__(self):
        super().__init__(agent_name="GeneralChatAgent")
        log(f"💬 GeneralChatAgent inicializado para Ollama")

    async def handle_general_chat(self, message: str, conversation: Dict) -> str:
        """Maneja conversación general con respuestas contextuales"""
        
        try:
            log(f"💬 GeneralChatAgent procesando: {message}")
            
            # Analizar tipo de conversación general
            chat_analysis = await self._analyze_general_message(message, conversation)
            
            # Generar respuesta apropiada
            response = await self._generate_contextual_response(message, chat_analysis, conversation)
            
            return response
            
        except Exception as e:
            log(f"💬❌ Error en GeneralChatAgent: {e}")
            return "¡Hola! Soy Ventix, tu asistente de ventas textiles. ¿En qué puedo ayudarte hoy?"
    
    async def _analyze_general_message(self, message: str, conversation: Dict) -> Dict:
        """Analiza el tipo de mensaje general"""
        
        # Contexto de mensajes previos
        recent_messages = ""
        for msg in conversation.get('messages', [])[-3:]:
            role = "Usuario" if msg['role'] == 'user' else "Ventix"
            recent_messages += f"{role}: {msg['content'][:100]}...\n"
        
        # Información del historial
        has_orders = len(conversation.get('recent_orders', [])) > 0
        
        prompt = f"""Analiza este mensaje de conversación general y determina cómo responder:

CONVERSACIÓN PREVIA:
{recent_messages}

MENSAJE ACTUAL: "{message}"

CONTEXTO DEL CLIENTE:
- Ha hecho pedidos antes: {has_orders}
- Mensajes previos: {len(conversation.get('messages', []))}

Responde SOLO con JSON válido:
{{
    "message_type": "greeting" | "who_are_you" | "thanks" | "goodbye" | "small_talk" | "confused" | "repeat_question",
    "user_mood": "friendly" | "business" | "curious" | "impatient" | "neutral",
    "needs_introduction": true_si_parece_primera_vez,
    "should_offer_help": true_si_debe_ofrecer_ayuda_especifica,
    "context_hints": ["información_relevante_del_contexto"]
}}

EJEMPLOS:
- "hola" → {{"message_type": "greeting", "needs_introduction": true, "should_offer_help": true}}
- "quien eres" → {{"message_type": "who_are_you", "needs_introduction": true}}
- "gracias" → {{"message_type": "thanks", "should_offer_help": false}}
- "no entiendo" → {{"message_type": "confused", "should_offer_help": true}}
- "como estas" → {{"message_type": "small_talk", "user_mood": "friendly"}}"""

        try:
            response_text = self.call_ollama([
                {"role": "system", "content": "Eres un asistente de análisis conversacional."},
                {"role": "user", "content": prompt}
            ])
            
            json_content = self._extract_json_from_response(response_text)
            if json_content:
                import json
                analysis = json.loads(json_content)
                log(f"💬🎯 Análisis general: {analysis}")
                return analysis
                
        except Exception as e:
            log(f"💬❌ Error analizando mensaje general: {e}")
        
        # Fallback simple
        message_lower = message.lower()
        
        if any(word in message_lower for word in ["hola", "buenos", "buenas", "hi", "hello"]):
            return {"message_type": "greeting", "needs_introduction": True, "should_offer_help": True}
        elif any(word in message_lower for word in ["quien", "qué eres", "who", "what are you"]):
            return {"message_type": "who_are_you", "needs_introduction": True}
        elif any(word in message_lower for word in ["gracias", "thanks", "thank"]):
            return {"message_type": "thanks", "should_offer_help": False}
        elif any(word in message_lower for word in ["chau", "adiós", "bye", "goodbye"]):
            return {"message_type": "goodbye", "should_offer_help": False}
        else:
            return {"message_type": "small_talk", "user_mood": "neutral", "should_offer_help": True}
    
    async def _generate_contextual_response(self, message: str, analysis: Dict, conversation: Dict) -> str:
        """Genera respuesta contextual basada en el análisis"""
        
        message_type = analysis.get("message_type", "greeting")
        needs_introduction = analysis.get("needs_introduction", False)
        should_offer_help = analysis.get("should_offer_help", True)
        user_mood = analysis.get("user_mood", "neutral")
        
        # Información del cliente para personalización
        has_orders = len(conversation.get('recent_orders', [])) > 0
        message_count = len(conversation.get('messages', []))
        
        # Construir respuesta base
        if message_type == "greeting":
            if message_count <= 2:  # Primera interacción
                response = "¡Hola! 👋 Soy **Ventix**, tu asistente de ventas textiles B2B.\n\n"
            else:
                response = "¡Hola de nuevo! 😊\n\n"
                
        elif message_type == "who_are_you":
            response = "Soy **Ventix** 🤖, tu asistente especializado en **ventas textiles B2B**.\n\n"
            response += "Estoy aquí para ayudarte con:\n"
            response += "• 📋 **Consultas de inventario** - stock, colores, talles\n"
            response += "• 🛒 **Pedidos empresariales** - desde 50 unidades\n"
            response += "• ✏️ **Modificar pedidos** - cambios dentro de 5 minutos\n"
            response += "• 💡 **Asesoramiento comercial** - recomendaciones por sector\n\n"
            
        elif message_type == "thanks":
            responses = [
                "¡De nada! 😊 ¿Hay algo más en lo que pueda ayudarte?",
                "¡Un placer ayudarte! ¿Necesitás algo más para tu empresa?",
                "¡Perfecto! Si necesitás más productos, acá estoy. 👍"
            ]
            return responses[hash(message) % len(responses)]
            
        elif message_type == "goodbye":
            if has_orders:
                return "¡Hasta luego! Gracias por confiar en nosotros. 🙌\n\nRecordá que podés volver cuando necesites más productos para tu empresa."
            else:
                return "¡Hasta luego! Fue un gusto conocerte. 👋\n\nCuando necesites textiles para tu empresa, acá estaré para ayudarte."
                
        elif message_type == "confused":
            response = "Sin problema, te explico mejor. 😊\n\n"
            response += "Soy tu asistente para **compras textiles empresariales**. Podés preguntarme:\n\n"
            response += "🔍 *'¿Qué productos tenés?'*\n"
            response += "📦 *'Cuánto stock hay de camisetas azules?'*\n"
            response += "🛒 *'Quiero 100 pantalones negros talle L'*\n"
            response += "💡 *'Qué me recomendás para construcción?'*\n\n"
            return response
            
        else:  # small_talk o otros
            if user_mood == "friendly":
                response = "¡Todo bien por aquí! 😊 Trabajando para ayudar empresas como la tuya.\n\n"
            else:
                response = "¡Perfecto! Estoy aquí para ayudarte con tus necesidades textiles.\n\n"
        
        # Agregar información específica según contexto
        if has_orders:
            response += "Veo que ya compraste con nosotros antes. ¡Genial! 🎉\n\n"
        
        # Agregar oferta de ayuda si corresponde
        if should_offer_help:
            if has_orders:
                response += "¿Necesitás reabastecer stock o algo nuevo para tu empresa?"
            else:
                response += "¿En qué puedo ayudarte hoy?\n\n"
                response += "🎯 Puedo mostrarte nuestro catálogo, consultar stock o ayudarte con un pedido."
        
        return response

# Instancia global
general_chat_agent = GeneralChatAgent()