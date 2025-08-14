import os
import sys
from sqlalchemy import create_engine, text, inspect  # Agregar inspect aquí
from sqlalchemy.orm import sessionmaker
from ..database import Base, engine, SessionLocal
from .. import models
from .import_from_excel import import_products_from_excel

def init_database():
    """Inicializa la base de datos desde cero"""
    
    print("🗄️ Iniciando configuración de base de datos...")
    
    try:
        # 1. Eliminar todas las tablas existentes
        print("🧹 Eliminando tablas existentes...")
        Base.metadata.drop_all(bind=engine)
        print("✅ Tablas eliminadas correctamente")
        
        # 2. Crear todas las tablas nuevas
        print("🏗️ Creando nuevas tablas...")
        Base.metadata.create_all(bind=engine)
        print("✅ Tablas creadas correctamente:")
        
        # Listar tablas creadas - CORREGIR EL INSPECTOR
        try:
            inspector = inspect(engine)  # Usar función inspect, no método
            table_names = inspector.get_table_names()
            for table in table_names:
                print(f"   - {table}")
        except Exception as e:
            print(f"   - (No se pudieron listar tablas: {e})")
        
        # 3. Importar productos desde Excel
        print("📊 Importando productos desde Excel...")
        products_imported = import_products_from_excel()
        print(f"✅ {products_imported} productos importados correctamente")
        
        # 4. Crear datos de ejemplo para conversaciones (opcional)
        create_sample_data()
        
        print("🎉 Base de datos inicializada correctamente!")
        return True
        
    except Exception as e:
        print(f"❌ Error inicializando base de datos: {e}")
        print(f"🔍 Detalles del error: {type(e).__name__}: {str(e)}")
        return False

def create_sample_data():
    """Crea datos de ejemplo para testing"""
    
    db = SessionLocal()
    try:
        # Verificar si ya existen conversaciones
        existing_conversations = db.query(models.Conversation).count()
        if existing_conversations > 0:
            print("ℹ️ Ya existen conversaciones, saltando datos de ejemplo")
            return
        
        print("📝 Creando datos de ejemplo...")
        
        # Conversación de ejemplo
        sample_conversation = models.Conversation(
            user_phone="541155744089",
            user_name="Cliente Ejemplo",
            status="active"
        )
        db.add(sample_conversation)
        db.commit()
        db.refresh(sample_conversation)
        
        # Mensaje de ejemplo
        sample_message = models.ConversationMessage(
            conversation_id=sample_conversation.id,
            message_type="user",
            content="Hola, estoy interesado en camisetas para mi empresa",
            intent_detected="search"
        )
        db.add(sample_message)
        
        # Respuesta de ejemplo
        sample_response = models.ConversationMessage(
            conversation_id=sample_conversation.id,
            message_type="assistant",
            content="¡Hola! Te ayudo con camisetas para tu empresa. Tenemos varios modelos disponibles.",
            intent_detected="response"
        )
        db.add(sample_response)
        
        db.commit()
        print("✅ Datos de ejemplo creados")
        
    except Exception as e:
        print(f"⚠️ Error creando datos de ejemplo: {e}")
        db.rollback()
    finally:
        db.close()

def reset_database():
    """Resetea completamente la base de datos - útil para development"""
    
    print("🔄 RESETEO COMPLETO DE BASE DE DATOS...")
    
    try:
        # Cerrar todas las conexiones activas
        engine.dispose()
        
        # Recrear engine
        from ..database import engine as new_engine
        
        # Eliminar y recrear todo
        with new_engine.connect() as connection:
            with connection.begin():
                # Eliminar todas las tablas
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
                connection.execute(text("GRANT ALL ON SCHEMA public TO postgres"))
                connection.execute(text("GRANT ALL ON SCHEMA public TO public"))
        
        print("✅ Schema resetado completamente")
        
        # Reinicializar
        return init_database()
        
    except Exception as e:
        print(f"❌ Error reseteando base de datos: {e}")
        return False

if __name__ == "__main__":
    # Permitir ejecutar directamente
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        success = reset_database()
    else:
        success = init_database()
    
    if not success:
        sys.exit(1)