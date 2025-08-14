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
            
            # ✅ AGREGAR DESCRIPCIÓN Y CATEGORÍA SI NO EXISTEN
            print("🔧 Verificando si faltan descripción y categoría...")
            productos_sin_descripcion = db.query(models.Product).filter(
                models.Product.descripcion.is_(None)
            ).all()
            
            if len(productos_sin_descripcion) > 0:
                print(f"📝 Actualizando {len(productos_sin_descripcion)} productos con descripción/categoría...")
                
                # Crear mapeo ID → datos del Excel
                excel_data = {}
                for _, row in df.iterrows():
                    product_name = f"{row['TIPO_PRENDA']} {row['COLOR']} - {row['TALLA']}"
                    excel_data[product_name] = {
                        'descripcion': row.get('DESCRIPCIÓN', 'Material de calidad premium'),
                        'categoria': row.get('CATEGORÍA', 'General')
                    }
                
                # Actualizar productos existentes
                updated_count = 0
                for producto in productos_sin_descripcion:
                    if producto.name in excel_data:
                        producto.descripcion = excel_data[producto.name]['descripcion']
                        producto.categoria = excel_data[producto.name]['categoria']
                        updated_count += 1
                
                db.commit()
                print(f"✅ Actualizados {updated_count} productos con descripción y categoría")
            
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
                stock=int(row['CANTIDAD_DISPONIBLE']),
                descripcion=row.get('DESCRIPCIÓN', 'Material de calidad premium'),
                categoria=row.get('CATEGORÍA', 'General')
            )
            db.add(product)
            count += 1
        
        # Confirmar los cambios
        db.commit()
        print(f"✅ Importados {count} productos exitosamente")
        
        # Mostrar estadísticas
        total_products = db.query(models.Product).count()
        print(f"📦 Total de productos en la base de datos: {total_products}")
        
        # Mostrar algunos productos importados con descripción
        recent_products = db.query(models.Product).limit(3).all()
        print("\n🛍️  Algunos productos importados:")
        for product in recent_products:
            print(f"  - {product.tipo_prenda} {product.color} - {product.talla}")
            print(f"    💰 ${product.precio_50_u} | 📦 Stock: {product.stock}")
            print(f"    📂 {product.categoria} | 📝 {product.descripcion}")
            print()
        
        return count
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error importando datos: {e}")
        print(f"🔍 Columnas disponibles en Excel: {list(df.columns) if 'df' in locals() else 'No se pudo leer'}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    import_excel()
