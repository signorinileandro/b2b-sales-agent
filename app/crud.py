from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
import pytz
from fastapi import HTTPException
from . import models, schemas
#from .utils.notifications import notify_new_order_sync

def get_products(db: Session):
    return db.query(models.Product).all()

def create_order(db: Session, order: schemas.OrderCreate):
    """Crear pedido con descuento automático de stock"""
    
    # 1. Verificar que el producto existe
    product = db.query(models.Product).filter(models.Product.id == order.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # 2. Verificar stock disponible
    if product.stock < order.qty:
        raise HTTPException(
            status_code=400, 
            detail=f"Stock insuficiente. Disponible: {product.stock}, Solicitado: {order.qty}"
        )
    
    # 3. Crear el pedido
    db_order = models.Order(**order.dict())
    db.add(db_order)
    
    # 4. ✅ DESCONTAR STOCK AUTOMÁTICAMENTE
    product.stock = product.stock - order.qty
    print(f"📦 Stock actualizado para producto {product.id}: {product.stock + order.qty} → {product.stock}")
    
    # 5. Guardar cambios
    db.commit()
    db.refresh(db_order)
    db.refresh(product)
    
    print(f"✅ Pedido creado: {order.qty} unidades del producto {product.name}")
    print(f"📊 Stock restante: {product.stock} unidades")
    
    return db_order

def get_orders(db: Session):
    return db.query(models.Order).all()


def update_order(db: Session, order_id: int, order_update: schemas.OrderUpdate):
    """Actualiza un pedido existente"""
    
    # ✅ OBTENER PEDIDO
    db_order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    
    # ✅ VERIFICAR TIEMPO LÍMITE PARA MODIFICAR (5 minutos) - ARREGLAR TIMEZONE
    utc = pytz.UTC
    
    # Manejar timezone del created_at
    if db_order.created_at.tzinfo is None:
        # Si created_at no tiene timezone, asumimos UTC
        order_time = utc.localize(db_order.created_at)
    else:
        order_time = db_order.created_at
    
    now = datetime.now(utc)
    time_passed = now - order_time  # ✅ AHORA AMBAS SON TIMEZONE-AWARE
    
    if time_passed.total_seconds() > 300:  # 5 minutos
        raise HTTPException(
            status_code=400, 
            detail=f"No se puede modificar. Han pasado {int(time_passed.total_seconds() / 60)} minutos desde la creación"
        )
    
    # ✅ MANEJAR STOCK SEGÚN EL CAMBIO DE CANTIDAD
    if hasattr(order_update, 'qty') and order_update.qty is not None:
        old_qty = db_order.qty
        new_qty = order_update.qty
        qty_difference = new_qty - old_qty
        
        # Obtener producto para gestionar stock
        product = db.query(models.Product).filter(models.Product.id == db_order.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        
        # Si aumenta cantidad, verificar stock
        if qty_difference > 0:
            if product.stock < qty_difference:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Stock insuficiente. Disponible: {product.stock}, necesario: {qty_difference}"
                )
            # Reducir stock disponible
            product.stock -= qty_difference
            
        # Si reduce cantidad, restaurar stock
        elif qty_difference < 0:
            product.stock += abs(qty_difference)
    
    # ✅ ACTUALIZAR CAMPOS DEL PEDIDO
    update_data = order_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_order, field, value)
    
    # ✅ ACTUALIZAR TIMESTAMP
    db_order.updated_at = datetime.now(utc)
    
    db.commit()
    db.refresh(db_order)
    
    return db_order

def get_products_with_stock(db: Session):
    """Obtener solo productos con stock disponible"""
    return db.query(models.Product).filter(models.Product.stock > 0).all()

def check_low_stock_products(db: Session, threshold: int = 10):
    """Obtener productos con stock bajo"""
    return db.query(models.Product).filter(
        models.Product.stock <= threshold,
        models.Product.stock > 0
    ).all()

def restore_stock_on_order_cancellation(db: Session, order_id: int):
    """Restaurar stock cuando se cancela un pedido"""
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    product = db.query(models.Product).filter(models.Product.id == order.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Restaurar stock
    product.stock += order.qty
    
    # Marcar pedido como cancelado
    order.status = "cancelled"
    
    db.commit()
    
    print(f"♻️ Stock restaurado: +{order.qty} unidades para producto {product.name}")
    print(f"📊 Nuevo stock: {product.stock} unidades")
    
    return order
