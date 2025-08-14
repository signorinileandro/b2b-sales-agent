from sqlalchemy.orm import Session
from datetime import datetime, timedelta
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


def update_order(db: Session, order_id: int, new_qty: int):
    """Actualizar pedido manejando stock correctamente"""
    
    db_order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Verificar ventana de tiempo
    if datetime.utcnow() - db_order.created_at > timedelta(minutes=5):
        raise HTTPException(status_code=403, detail="Edit window expired")
    
    # Obtener producto
    product = db.query(models.Product).filter(models.Product.id == db_order.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    old_qty = db_order.qty
    
    # ✅ RESTAURAR stock anterior
    product.stock += old_qty
    
    # ✅ VERIFICAR stock para nueva cantidad
    if product.stock < new_qty:
        # Volver al estado anterior
        product.stock -= old_qty
        raise HTTPException(
            status_code=400, 
            detail=f"Stock insuficiente. Disponible: {product.stock}, Solicitado: {new_qty}"
        )
    
    # ✅ DESCONTAR nuevo stock
    product.stock -= new_qty
    
    # ✅ ACTUALIZAR pedido
    db_order.qty = new_qty
    
    db.commit()
    db.refresh(db_order)
    db.refresh(product)
    
    print(f"✏️ Pedido {order_id}: {old_qty} → {new_qty} unidades")
    print(f"📦 Stock actualizado: {product.stock} unidades")
    
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
