import os
import httpx
from typing import Dict, Optional
from .logger import log

class WhatsAppClient:
    """Cliente para enviar mensajes directamente a WhatsApp Business API"""
    
    def __init__(self):
        self.access_token = os.getenv("ACCESS_TOKEN")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        self.api_url = os.getenv("WHATSAPP_API_URL", "https://graph.facebook.com/v18.0")
        
        if not self.access_token or not self.phone_number_id:
            raise ValueError("ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID son requeridos")
    
    async def send_message(self, to: str, message: str, message_type: str = "text") -> Dict:
        """Envía mensaje de texto a WhatsApp"""
        
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": message_type,
            message_type: {
                "body": message
            }
        }
        
        log(f"📤 Enviando mensaje WhatsApp a {to}")
        log(f"🔗 URL: {url}")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    timeout=30.0
                )
                
                response_data = response.json()
                
                if response.status_code == 200:
                    message_id = response_data.get("messages", [{}])[0].get("id", "unknown")
                    log(f"✅ Mensaje enviado exitosamente. ID: {message_id}")
                    return {
                        "success": True,
                        "message_id": message_id,
                        "response": response_data
                    }
                else:
                    log(f"❌ Error enviando mensaje: {response.status_code} - {response_data}")
                    return {
                        "success": False,
                        "error": response_data,
                        "status_code": response.status_code
                    }
                    
        except Exception as e:
            log(f"❌ Excepción enviando mensaje WhatsApp: {e}")
            return {
                "success": False,
                "error": str(e),
                "exception": True
            }
    
    async def send_template_message(self, to: str, template_name: str, language_code: str = "es", components: Optional[list] = None) -> Dict:
        """Envía mensaje con template de WhatsApp"""
        
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code
                }
            }
        }
        
        if components:
            payload["template"]["components"] = components
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    timeout=30.0
                )
                
                response_data = response.json()
                
                if response.status_code == 200:
                    message_id = response_data.get("messages", [{}])[0].get("id", "unknown")
                    log(f"✅ Template enviado exitosamente. ID: {message_id}")
                    return {
                        "success": True,
                        "message_id": message_id,
                        "response": response_data
                    }
                else:
                    log(f"❌ Error enviando template: {response.status_code} - {response_data}")
                    return {
                        "success": False,
                        "error": response_data,
                        "status_code": response.status_code
                    }
                    
        except Exception as e:
            log(f"❌ Excepción enviando template WhatsApp: {e}")
            return {
                "success": False,
                "error": str(e),
                "exception": True
            }
    
    async def mark_as_read(self, message_id: str) -> Dict:
        """Marca mensaje como leído"""
        
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    log(f"✅ Mensaje {message_id} marcado como leído")
                    return {"success": True}
                else:
                    log(f"❌ Error marcando como leído: {response.status_code}")
                    return {"success": False, "error": response.json()}
                    
        except Exception as e:
            log(f"❌ Error marcando como leído: {e}")
            return {"success": False, "error": str(e)}

# Instancia global
whatsapp_client = WhatsAppClient()