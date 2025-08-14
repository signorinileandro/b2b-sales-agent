import pandas as pd
from sqlalchemy.orm import sessionmaker
from ..config import DATABASE_URL
from ..database import SessionLocal
from .. import models

def import_products_from_excel(path="DB.xlsx"):
    """Importa productos desde Excel - función principal para init_database"""
    return import_excel(path)

def import_excel(path="DB.xlsx"):
    """Función original de importación"""
    db = SessionLocal()
    try:
        # Leer el Excel
        df = pd.read_excel(path)
        print(f"📊 Leyendo {len(df)} registros del archivo Excel")
        print(f"📋 Columnas: {list(df.columns)}")
        
        # Verificar si ya hay productos
        existing_count = db.query(models.Product).count()
        if existing_count > 0:
            print(f"📦 Ya existen {existing_count} productos en la base de datos")
            return existing_count
        
        count = 0
        # Mapear directamente las columnas del Excel
        for _, row in df.iterrows():
            # Crear nombre descriptivo para compatibilidad con la API
            product_name = f"{row['TIPO_PRENDA']} {row['COLOR']} - {row['TALLA']}"
            
            product = models.Product(
                # Campos exactos del Excel - verificar que coincidan con el modelo
                name=product_name,
                tipo_prenda=row['TIPO_PRENDA'],
                color=row['COLOR'],
                talla=row['TALLA'],
                precio_50_u=float(row['PRECIO_50_U']),
                precio_100_u=float(row['PRECIO_100_U']),
                precio_200_u=float(row['PRECIO_200_U']),
                stock=int(row['CANTIDAD_DISPONIBLE'])  # Usar stock en lugar de cantidad_disponible
            )
            db.add(product)
            count += 1
        
        # Confirmar los cambios
        db.commit()
        print(f"✅ Importados {count} productos exitosamente")
        
        # Mostrar estadísticas
        total_products = db.query(models.Product).count()
        print(f"📦 Total de productos en la base de datos: {total_products}")
        
        # Mostrar algunos productos importados
        recent_products = db.query(models.Product).limit(5).all()
        print("\n🛍️  Algunos productos importados:")
        for product in recent_products:
            print(f"  - {product.tipo_prenda} {product.color} - {product.talla}: ${product.precio_50_u} (Stock: {product.stock})")
        
        return count
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error importando datos: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    import_excel()
