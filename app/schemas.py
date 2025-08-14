from pydantic import BaseModel, Field
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

class ProductResponse(BaseModel):
    id: int
    name: str
    tipo_prenda: str
    color: str
    talla: str
    price: float = Field(alias="precio_50_u")  # Mapear precio_50_u a price
    precio_50_u: float
    precio_100_u: float
    precio_200_u: float
    stock: int
    created_at: datetime
    
    class Config:
        from_attributes = True
        populate_by_name = True

class Product(ProductResponse):
    pass

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
