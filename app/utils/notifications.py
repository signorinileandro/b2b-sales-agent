import httpx
import asyncio
import os
from .. import models

async def notify_new_order(order: models.Order, product: models.Product = None):
    """Envía notificación a n8n cuando se crea una nueva orden desde WhatsApp"""
    
    N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
    
    if not N8N_WEBHOOK_URL:
        print("⚠️ N8N_WEBHOOK_URL no configurado, saltando notificación")
        return
    
    try:
        # Preparar datos para enviar al webhook
        webhook_data = {
            "order_id": order.id,
            "product_id": order.product_id,
            "qty": order.qty,
            "buyer": order.buyer,
            "status": order.status,
            "created_at": order.created_at.isoformat(),
            "source": "whatsapp_ai"  # Identificar que viene del agente IA
        }
        
        # Agregar info del producto si está disponible
        if product:
            webhook_data.update({
                "product_name": product.name,
                "product_price": product.precio_50_u,
                "tipo_prenda": product.tipo_prenda,
                "color": product.color,
                "talla": product.talla,
            })
        
        print(f"📤 Enviando notificación de pedido WhatsApp a n8n: {webhook_data}")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                N8N_WEBHOOK_URL,
                json=webhook_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                print("✅ Notificación de pedido WhatsApp enviada exitosamente a n8n")
            else:
                print(f"⚠️ Error al enviar notificación: {response.status_code} - {response.text}")
                
    except Exception as e:
        print(f"❌ Error enviando notificación a n8n: {e}")

def notify_new_order_sync(order: models.Order, product: models.Product = None):
    """Versión sincrónica para llamar desde endpoints sync"""
    try:
        asyncio.run(notify_new_order(order, product))
    except Exception as e:
        print(f"❌ Error en notificación sincrónica: {e}")