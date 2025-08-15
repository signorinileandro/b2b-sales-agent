from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class ProductBase(BaseModel):
    name: str
    tipo_prenda: str
    color: str
    talla: str
    precio_50_u: float
    precio_100_u: float
    precio_200_u: float
    stock: int
    descripcion: Optional[str] = None
    categoria: Optional[str] = None

class ProductResponse(BaseModel):
    id: int
    name: str
    tipo_prenda: str
    color: str
    talla: str
    precio_50_u: float
    precio_100_u: float
    precio_200_u: float
    stock: int
    created_at: datetime
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    
    class Config:
        from_attributes = True

class Product(ProductResponse):
    pass

# ✅ SCHEMA SIMPLIFICADO PARA EL AI
class ProductAIResponse(BaseModel):
    """Schema optimizado para respuestas del AI Agent"""
    id: int
    name: str
    tipo_prenda: str
    color: str
    talla: str
    precio_50_u: float
    precio_100_u: float
    precio_200_u: float
    stock: int
    descripcion: Optional[str] = "Material de calidad premium"
    categoria: Optional[str] = "General"
    
    class Config:
        from_attributes = True

class OrderCreate(BaseModel):
    """Schema para crear pedidos"""
    product_id: int
    qty: int
    buyer: str

class OrderUpdate(BaseModel):
    """Schema para actualizar pedidos"""
    qty: int

class OrderBase(BaseModel):
    product_id: int
    qty: int
    buyer: str

class Order(BaseModel):
    id: int
    product_id: int
    qty: int
    buyer: str
    status: str
    created_at: datetime
    user_phone: Optional[str] = None
    conversation_id: Optional[int] = None
    
    class Config:
        from_attributes = True

class OrderWithProductResponse(Order):
    """Schema de pedido con información completa del producto"""
    product: Optional[ProductAIResponse] = None

class ConversationMessageResponse(BaseModel):
    id: int
    message_type: str
    content: str
    created_at: datetime
    intent_detected: Optional[str] = None
    
    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    id: int
    user_phone: str
    user_name: Optional[str] = None
    status: str
    created_at: datetime
    messages: list[ConversationMessageResponse] = []
    
    class Config:
        from_attributes = True

# ✅ SCHEMAS PARA RESPUESTAS DEL AI AGENT
class AIProductSearchResponse(BaseModel):
    """Schema para respuestas de búsqueda del AI"""
    products: list[ProductAIResponse]
    filters_applied: dict
    total_found: int
    total_stock: int

class AIStockCheckResponse(BaseModel):
    """Schema para consultas de stock del AI"""
    products: list[ProductAIResponse]
    total_stock: int
    products_available: int
    summary: dict
