import os
import time
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from .database import Base, engine, SessionLocal
from . import crud, schemas, models
from .ai.conversation_manager import conversation_manager
from .utils.logger import log  # ✅ IMPORTAR
import httpx
from typing import Set

# Modificar solo el lifespan para producción

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Maneja el ciclo de vida de la aplicación - RENDER + SUPABASE"""
    
    log("🚀 Iniciando aplicación en Render...") # ✅ USAR LOG
    
    try:
        # Crear tablas en Supabase
        Base.metadata.create_all(bind=engine)
        log("✅ Tablas verificadas en Supabase") # ✅ USAR LOG
        
        # Verificar si necesita importar productos
        db = SessionLocal()
        product_count = db.query(models.Product).count()
        
        if product_count == 0:
            log("📊 Importando productos desde Excel...") # ✅ USAR LOG
            from .utils.import_from_excel import import_products_from_excel
            imported = import_products_from_excel("DB.xlsx")
            log(f"✅ {imported} productos importados!") # ✅ USAR LOG
        else:
            log(f"📦 Ya hay {product_count} productos en BD") # ✅ USAR LOG
            
        db.close()
        
    except Exception as e:
        log(f"❌ Error en inicialización: {e}") # ✅ USAR LOG
    
    yield
    
    log("🛑 Aplicación cerrada") # ✅ USAR LOG

app = FastAPI(title="B2B Sales Agent", lifespan=lifespan)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/products", response_model=list[schemas.Product])
def list_products(db: Session = Depends(get_db)):
    return crud.get_products(db)

@app.get("/orders", response_model=list[schemas.Order])
def list_orders(db: Session = Depends(get_db)):
    return crud.get_orders(db)

@app.post("/orders", response_model=schemas.Order)
def create_order(order: schemas.OrderCreate, db: Session = Depends(get_db)):  # ✅ Usar OrderCreate
    return crud.create_order(db, order)

@app.patch("/orders/{order_id}", response_model=schemas.Order)
def update_order(order_id: int, update: schemas.OrderUpdate, db: Session = Depends(get_db)):  # ✅ Usar OrderUpdate
    return crud.update_order(db, order_id, update.qty)

@app.get("/")
def read_root():
    return {"message": "B2B Sales Agent API"}

@app.post("/api/chat")
async def chat_with_agent(
    request: dict,  # {"user_id": "123", "message": "Hola"}
    db: Session = Depends(get_db)
):
    """Chat inteligente con el agente de ventas IA"""
    
    try:
        user_id = request.get("user_id", "anonymous")
        message = request.get("message", "")
        
        if not message.strip():
            return {"error": "Mensaje vacío"}
        
        # ✅ USAR CONVERSATION_MANAGER
        ai_response = await conversation_manager.process_message(user_id, message)
        
        return {
            "user_id": user_id,
            "user_message": message,
            "ai_response": ai_response,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "ai_response": "Lo siento, tuve un problema técnico. ¿Podrías intentar de nuevo?"
        }

@app.get("/api/inventory/search")
async def smart_inventory_search(
    query: str = Query(..., description="Consulta de búsqueda"),
    db: Session = Depends(get_db)
):
    """Búsqueda inteligente de inventario"""
    
    # Búsqueda básica por ahora
    products = db.query(models.Product).filter(
        models.Product.name.ilike(f"%{query}%")
    ).limit(10).all()
    
    return {
        "query": query,
        "found": len(products),
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "tipo_prenda": p.tipo_prenda,
                "color": p.color,
                "talla": p.talla,
                "precio_50_u": p.precio_50_u,
                "stock": p.stock
            }
            for p in products
        ]
    }

# En main.py, AGREGAR al inicio:
processed_messages: Set[str] = set()

# REEMPLAZAR webhook_whatsapp:
@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """Webhook para recibir mensajes de WhatsApp"""
    try:
        data = await request.json()
        log(f"📱 Webhook WhatsApp recibido: {data}")  # ✅ USAR LOG
        
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    
                    if "messages" in value and value.get("messages"):
                        for message in value["messages"]:
                            message_id = message.get("id")
                            message_type = message.get("type")
                            from_number = message.get("from")
                            
                            # ✅ DEDUPLICACIÓN
                            if message_id in processed_messages:
                                log(f"⏭️ Mensaje {message_id} ya procesado")  # ✅ USAR LOG
                                continue
                            
                            processed_messages.add(message_id)
                            
                            # Limpiar cache
                            if len(processed_messages) > 1000:
                                processed_messages.clear()
                            
                            if message_type == "text" and from_number:
                                text_body = message.get("text", {}).get("body", "")
                                log(f"📨 Mensaje de {from_number}: {text_body}")  # ✅ USAR LOG
                                
                                normalized_number = normalize_phone_number(from_number)
                                
                                try:
                                    # ✅ USAR CONVERSATION_MANAGER
                                    ai_response = await conversation_manager.process_message(normalized_number, text_body)
                                    await send_ai_response_via_n8n(normalized_number, ai_response)
                                except Exception as e:
                                    log(f"❌ Error procesando: {e}")  # ✅ USAR LOG
        
        return {"status": "ok"}
    except Exception as e:
        log(f"❌ Error webhook: {e}")  # ✅ USAR LOG
        return {"status": "error"}

@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    """Verificación del webhook de WhatsApp por Meta"""
    
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token") 
    challenge = request.query_params.get("hub.challenge")
    
    VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
    
    log(f"🔍 Verificación webhook: mode={mode}, token={token}, challenge={challenge}")  # ✅ USAR LOG
    log(f"🔑 Token esperado: {VERIFY_TOKEN}")  # ✅ USAR LOG
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        log("✅ Webhook verificado correctamente")  # ✅ USAR LOG
        return int(challenge)
    else:
        log("❌ Token de verificación incorrecto")  # ✅ USAR LOG
        raise HTTPException(status_code=403, detail="Forbidden")

async def process_incoming_whatsapp_message(message: dict):
    """Procesa mensaje entrante de WhatsApp"""
    
    user_phone = message["from"]
    message_text = message.get("text", {}).get("body", "")
    message_type = message["type"]
    
    log(f"📨 Mensaje de {user_phone}: {message_text}")  # ✅ USAR LOG
    
    if message_type == "text" and message_text:
        # ✅ USAR CONVERSATION_MANAGER
        ai_response = await conversation_manager.process_message(user_phone, message_text)
        
        # Enviar respuesta vía n8n
        await send_ai_response_via_n8n(user_phone, ai_response)

async def send_ai_response_via_n8n(phone: str, ai_message: str):
    """Envía respuesta del AI vía n8n"""
    
    normalized_phone = normalize_phone_number(phone)
    
    webhook_data = {
        "phone": normalized_phone,  # Usar número normalizado
        "ai_message": ai_message,
        "access_token": os.getenv("ACCESS_TOKEN")
    }
    
    webhook_url = os.getenv("N8N_AI_RESPONSE_URL", "http://n8n:5678/webhook-test/whatsapp-ai-response")
    
    log(f"📞 Número original: {phone}")  # ✅ USAR LOG
    log(f"📞 Número normalizado: {normalized_phone}")  # ✅ USAR LOG
    log(f"🔍 Enviando respuesta IA a: {webhook_url}")  # ✅ USAR LOG
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=webhook_data,
                timeout=10.0
            )
            log(f"✅ Respuesta AI enviada vía n8n: {response.status_code}")  # ✅ USAR LOG
            
            if response.status_code != 200:
                log(f"❌ Error response: {response.status_code} - {response.text}")  # ✅ USAR LOG
                
    except Exception as e:
        log(f"❌ Error enviando respuesta AI: {type(e).__name__}: {e}")  # ✅ USAR LOG

def normalize_phone_number(phone: str) -> str:
    """Normaliza números de teléfono argentinos para WhatsApp"""
    
    # Remover espacios, guiones y signos +
    clean_phone = phone.replace("+", "").replace("-", "").replace(" ", "")
    
    log(f"🔍 Normalizando: {phone} → {clean_phone}")  # ✅ USAR LOG
    
    # NÚMEROS ARGENTINOS - Formato correcto WhatsApp: 541155744089 (13 dígitos)
    
    # Caso 1: WhatsApp envía 549111155744089 → corregir a 541155744089
    if clean_phone.startswith("5491") and len(clean_phone) == 13:
        # Solo remover el "9" del medio: 5491155744089 → 541155744089
        normalized = "541" + clean_phone[4:]  # "54" + "1" + resto
        log(f"🇦🇷 Número argentino normalizado (remover 9): {clean_phone} → {normalized}")  # ✅ USAR LOG
        return normalized
    
    # Caso 2: Formato con doble 9 y 1: 54911155744089 → 541155744089  
    if clean_phone.startswith("54911") and len(clean_phone) == 14:
        # Remover "911" y reemplazar por "1": 54911155744089 → 541155744089
        normalized = "541" + clean_phone[5:]
        log(f"🇦🇷 Número argentino normalizado (remover 911): {clean_phone} → {normalized}")  # ✅ USAR LOG
        return normalized
    
    # Caso 3: Ya está en formato correcto 541155744089
    if clean_phone.startswith("541") and len(clean_phone) == 13:
        log(f"🇦🇷 Número argentino correcto: {clean_phone}")  # ✅ USAR LOG
        return clean_phone
    
    # Caso 4: Formato local 1155744089 → agregar código país
    if clean_phone.startswith("11") and len(clean_phone) == 10:
        normalized = "541" + clean_phone
        log(f"🇦🇷 Número local argentino: {clean_phone} → {normalized}")  # ✅ USAR LOG
        return normalized
    
    # Caso 5: Otros formatos internacionales
    log(f"🌐 Número internacional sin cambios: {clean_phone}")  # ✅ USAR LOG
    return clean_phone

@app.get("/api/products/by-user/{user_id}")
async def get_user_products(user_id: str):
    """Obtiene productos que el usuario vio recientemente"""
    
    # ✅ USAR CONVERSATION_MANAGER
    context = conversation_manager.memory_cache.get(user_id, {})
    products = context.get("last_searched_products", [])
    
    return {
        "user_id": user_id,
        "products_found": len(products),
        "products": products
    }

@app.get("/api/conversations/{user_phone}")
async def get_user_conversation(user_phone: str, db: Session = Depends(get_db)):
    """Obtiene conversación completa de un usuario"""
    
    conversation = db.query(models.Conversation).filter(
        models.Conversation.user_phone == user_phone
    ).first()
    
    if not conversation:
        return {"error": "No conversation found"}
    
    messages = db.query(models.ConversationMessage).filter(
        models.ConversationMessage.conversation_id == conversation.id
    ).order_by(models.ConversationMessage.created_at).all()
    
    orders = db.query(models.Order).filter(
        models.Order.conversation_id == conversation.id
    ).all()
    
    return {
        "conversation_id": conversation.id,
        "user_phone": conversation.user_phone,
        "created_at": conversation.created_at,
        "message_count": len(messages),
        "order_count": len(orders),
        "messages": [
            {
                "type": m.message_type,
                "content": m.content,
                "created_at": m.created_at,
                "intent": m.intent_detected
            }
            for m in messages
        ],
        "orders": [
            {
                "id": o.id,
                "product_id": o.product_id,
                "qty": o.qty,
                "status": o.status,
                "created_at": o.created_at
            }
            for o in orders
        ]
    }

@app.get("/api/orders/recent")
async def get_recent_orders(db: Session = Depends(get_db)):
    """Obtiene pedidos recientes con datos de conversación"""
    
    orders = db.query(models.Order).filter(
        models.Order.user_phone.isnot(None)  # Solo pedidos de WhatsApp
    ).order_by(models.Order.created_at.desc()).limit(20).all()
    
    return [
        {
            "id": o.id,
            "product_id": o.product_id,
            "qty": o.qty,
            "buyer": o.buyer,
            "status": o.status,
            "user_phone": o.user_phone,
            "created_at": o.created_at,
            "from_whatsapp": True
        }
        for o in orders
    ]
