# B2B Sales Agent API

API para gestión de pedidos y productos tipo B2B, parte del desafío técnico **My Upgrade**.

## 🚀 Tecnologías

- Python 3.11
- FastAPI
- SQLAlchemy
- PostgreSQL
- Docker / Docker Compose
- Pandas (importación de Excel)
- n8n (mensaje de confirmación de pedido)

## 📦 Instalación local

```bash
git clone https://github.com/signorinileandro/b2b-sales-agent.git
cd b2b-sales-agent
cp .env.example .env
docker compose up --build
```

## 📄 Importar datos desde DB.xlsx

```bash
docker compose exec api python app/utils/import_excel.py
```

## 📚 Endpoints

- `GET /products` → Lista de productos
- `GET /orders` → Lista de pedidos
- `POST /orders` → Crear pedido
- `PATCH /orders/{id}` → Editar pedido (máx 5 minutos después de creado)

## 🔍 Probar API

Abrir documentación interactiva:

- Swagger UI → `http://localhost:8000/docs`
- ReDoc → `http://localhost:8000/redoc`

## 🧪 Lógica especial

- Los pedidos solo se pueden modificar durante los **primeros 5 minutos** desde su creación.
- Si se intenta editar luego de ese tiempo, la API devuelve **403 Edit window expired**.

## 🛠 Variables de entorno (.env)

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=b2b_db
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/b2b_db
```

## 🐳 Servicios Docker

- **db** → PostgreSQL 15
- **api** → FastAPI con endpoints REST

## 📞 Integración con WhatsApp vía n8n

Para integrar con WhatsApp sin costo:

- Usar **WhatsApp Cloud API** en modo sandbox (Meta Developer), IMPORTANTE: modificar parametros en n8n, APP_ID, y token
- n8n corre en Docker y recibe un webhook desde FastAPI al crear pedidos.
- En modo prueba, se pueden enviar mensajes gratis a números verificados en la consola de Meta.
