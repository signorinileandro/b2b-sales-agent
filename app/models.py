from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .database import Base

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    tipo_prenda = Column(String)
    color = Column(String)
    talla = Column(String)
    precio_50_u = Column(Float)
    precio_100_u = Column(Float)  
    precio_200_u = Column(Float)
    stock = Column(Integer)  # Campo principal para stock
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    descripcion = Column(Text, nullable=True)
    categoria = Column(String, nullable=True)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    qty = Column(Integer)
    buyer = Column(String)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Columnas para conversaciones de WhatsApp
    user_phone = Column(String, nullable=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    
    # Relaciones
    product = relationship("Product")
    conversation = relationship("Conversation", back_populates="orders")

class Conversation(Base):
    """Historial de conversaciones con el agente IA"""
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_phone = Column(String, index=True)
    user_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    status = Column(String, default="active")  # active, completed, abandoned
    
    # Relaciones
    messages = relationship("ConversationMessage", back_populates="conversation", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="conversation")

class ConversationMessage(Base):
    """Mensajes individuales de la conversación"""
    __tablename__ = "conversation_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"))
    message_type = Column(String)  # "user" o "assistant"
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Datos adicionales
    products_shown = Column(Text, nullable=True)  # JSON con productos mostrados
    intent_detected = Column(String, nullable=True)
    
    # Relación
    conversation = relationship("Conversation", back_populates="messages")
