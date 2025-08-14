from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from fastapi import HTTPException
from . import models, schemas
#from .utils.notifications import notify_new_order_sync

def get_products(db: Session):
    return db.query(models.Product).all()

def create_order(db: Session, order: schemas.OrderCreate):
    # Crear la orden
    db_order = models.Order(**order.dict())
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
       
    #Notificación desactivada 
    # Obtener info del producto para la notificación
    #product = db.query(models.Product).filter(models.Product.id == order.product_id).first()
    
    # Enviar notificación a n8n
    #try:
    #    notify_new_order_sync(db_order, product)
    #except Exception as e:
    #    print(f"⚠️ Error enviando notificación (orden creada exitosamente): {e}")
    
    return db_order

def get_orders(db: Session):
    return db.query(models.Order).all()

def update_order(db: Session, order_id: int, new_qty: int):
    db_order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    if datetime.utcnow() - db_order.created_at > timedelta(minutes=5):
        raise HTTPException(status_code=403, detail="Edit 5 minutes window expired")
    db_order.qty = new_qty
    db.commit()
    db.refresh(db_order)
    return db_order
