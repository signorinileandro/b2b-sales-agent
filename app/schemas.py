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
    # ✅ AGREGAR CAMPOS FALTANTES
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
        populate_by_name = True

class Product(ProductResponse):
    pass

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
    
    @property
    def precio_unitario_minimo(self) -> float:
        """Precio más bajo disponible (200+ unidades)"""
        return min(self.precio_50_u, self.precio_100_u, self.precio_200_u)
    
    @property
    def descuento_volumen_max(self) -> int:
        """Descuento máximo por volumen (%)"""
        return int(((self.precio_50_u - self.precio_unitario_minimo) / self.precio_50_u) * 100)
    
    @property
    def stock_status(self) -> str:
        """Estado del stock para mensajes del AI"""
        if self.stock < 50:
            return "limited"  # ⚠️ Stock limitado
        elif self.stock < 150:
            return "moderate"  # 📦 Stock moderado
        else:
            return "excellent"  # ✅ Excelente disponibilidad
    
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
