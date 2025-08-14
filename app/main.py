import os
import time
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from .database import Base, engine, SessionLocal
from . import crud, schemas, models
from .ai.sales_agent import sales_agent
import httpx

# Modificar solo el lifespan para producción

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Maneja el ciclo de vida de la aplicación - RENDER + SUPABASE"""
    
    print("🚀 Iniciando aplicación en Render...")
    
    try:
        # Crear tablas en Supabase
        Base.metadata.create_all(bind=engine)
        print("✅ Tablas verificadas en Supabase")
        
        # Verificar si necesita importar productos
        db = SessionLocal()
        product_count = db.query(models.Product).count()
        
        if product_count == 0:
            print("📊 Importando productos desde Excel...")
            from .utils.import_from_excel import import_products_from_excel
            imported = import_products_from_excel("DB.xlsx")
            print(f"✅ {imported} productos importados!")
        else:
            print(f"📦 Ya hay {product_count} productos en BD")
            
        db.close()
        
    except Exception as e:
        print(f"❌ Error en inicialización: {e}")
    
    yield
    
    print("🛑 Aplicación cerrada")

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
        
        # Procesar mensaje con IA
        ai_response = await sales_agent.process_message(user_id, message)
        
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
                "stock": p.cantidad_disponible
            }
            for p in products
        ]
    }

# Webhook para recibir mensajes de WhatsApp
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Webhook para recibir mensajes de WhatsApp"""
    try:
        data = await request.json()
        print(f"📱 Webhook WhatsApp recibido: {data}")
        
        # Verificar si es un mensaje
        if "entry" in data:
            for entry in data["entry"]:
                if "changes" in entry:
                    for change in entry["changes"]:
                        if "value" in change and "messages" in change["value"]:
                            for message in change["value"]["messages"]:
                                await process_incoming_whatsapp_message(message)
        
        return {"status": "success"}
        
    except Exception as e:
        print(f"❌ Error procesando webhook WhatsApp: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    """Verificación del webhook de WhatsApp por Meta"""
    
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token") 
    challenge = request.query_params.get("hub.challenge")
    
    VERIFY_TOKEN = "mi_token_secreto_123"
    
    print(f"🔍 Verificación webhook: mode={mode}, token={token}, challenge={challenge}")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verificado correctamente")
        return int(challenge)
    else:
        print("❌ Token de verificación incorrecto")
        raise HTTPException(status_code=403, detail="Forbidden")

async def process_incoming_whatsapp_message(message: dict):
    """Procesa mensaje entrante de WhatsApp"""
    
    user_phone = message["from"]
    message_text = message.get("text", {}).get("body", "")
    message_type = message["type"]
    
    print(f"📨 Mensaje de {user_phone}: {message_text}")
    
    if message_type == "text" and message_text:
        # Procesar con el agente de IA
        ai_response = await sales_agent.process_message(user_phone, message_text)
        
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
    
    print(f"📞 Número original: {phone}")
    print(f"📞 Número normalizado: {normalized_phone}")
    print(f"🔍 Enviando respuesta IA a: {webhook_url}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=webhook_data,
                timeout=10.0
            )
            print(f"✅ Respuesta AI enviada vía n8n: {response.status_code}")
            
            if response.status_code != 200:
                print(f"❌ Error response: {response.status_code} - {response.text}")
                
    except Exception as e:
        print(f"❌ Error enviando respuesta AI: {type(e).__name__}: {e}")

def normalize_phone_number(phone: str) -> str:
    """Normaliza números de teléfono argentinos para WhatsApp"""
    
    # Remover espacios, guiones y signos +
    clean_phone = phone.replace("+", "").replace("-", "").replace(" ", "")
    
    print(f"🔍 Normalizando: {phone} → {clean_phone}")
    
    # Si es número argentino que empieza con 5491 (formato incorrecto de WhatsApp)
    if clean_phone.startswith("5491"):
        # Convertir 5491155744089 → 541155744089
        normalized = "54" + clean_phone[4:]  # Remover "91" del medio
        print(f"🇦🇷 Número argentino normalizado: {clean_phone} → {normalized}")
        return normalized
    
    # Si empieza con 54911 (otro formato incorrecto)
    if clean_phone.startswith("54911"):
        # Convertir 54911155744089 → 541155744089  
        normalized = "541" + clean_phone[5:]  # Remover "911" y poner "1"
        print(f"🇦🇷 Número argentino 911 normalizado: {clean_phone} → {normalized}")
        return normalized
    
    # Para otros países o formatos correctos, devolver como está
    return clean_phone

@app.get("/api/products/by-user/{user_id}")
async def get_user_products(user_id: str):
    """Obtiene productos que el usuario vio recientemente"""
    
    context = sales_agent.context_memory.get(user_id, {})
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
