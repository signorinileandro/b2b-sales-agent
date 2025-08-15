# B2B Sales Agent - AI WhatsApp Assistant

**Agente de ventas inteligente B2B** con integración completa a WhatsApp Business API, desarrollado como parte del desafío técnico **My Upgrade**.

## Características principales

- **🤖 IA Conversacional**: Múltiples agentes especializados con Ollama
- **📱 WhatsApp Business API**: Integración nativa con webhook automático
- **📦 Gestión de inventario**: Consultas inteligentes con filtros por categoría, color y talla
- **🛒 Pedidos automatizados**: Creación y modificación de pedidos vía chat
- **💬 Conversaciones contextuales**: Memoria de conversación y seguimiento de estado
- **🔄 Sistema de agentes**: Router inteligente que deriva consultas al agente apropiado

## 🧠 Arquitectura de Agentes IA

### **Conversation Manager**

- Analiza intención del usuario (check_stock, create_order, modify_order, general_chat)
- Mantiene contexto conversacional con memoria persistente
- Router inteligente que deriva a agentes especializados

### **Stock Agent**

- Consultas de inventario con filtros avanzados
- Análisis contextual de productos (tipo, color, talla)
- Respuestas organizadas por categorías con descripciones y precios
- Detección de variaciones (pantalón/pantalones, camiseta/camisetas)

### **Order Agent**

- Creación de pedidos con validación de stock
- Cálculo automático de precios por volumen (50+, 100+, 200+)
- Generación de resúmenes detallados de pedido

### **Modify Agent**

- Modificación de pedidos existentes (primeros 5 minutos)
- Gestión de cambios de cantidad y productos
- Validación de ventana temporal de edición

### **Sales Agent**

- Asesoría comercial y recomendaciones
- Información sobre descuentos y promociones
- Seguimiento de leads y oportunidades

### **General Chat Agent**

- Conversación general y soporte
- Presentación de la empresa y servicios
- Manejo de consultas no comerciales

## Stack Tecnológico

- **Backend**: Python 3.11 + FastAPI
- **Base de datos**: PostgreSQL + SQLAlchemy ORM
- **IA**: Ollama LLM
- **WhatsApp**: Meta Business API + Webhook
- **Contenedores**: Docker + Docker Compose
- **Importación**: Pandas (Excel → BD)

## 📦 Instalación y configuración

### 1. Clonar repositorio

```bash
git clone https://github.com/signorinileandro/b2b-sales-agent.git
cd b2b-sales-agent
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Editar `.env` con tus credenciales:

```env
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=b2b_db
DATABASE_URL=postgresql://postgres:postgres@db:5432/b2b_db

# WhatsApp Business API
WHATSAPP_PHONE_NUMBER_ID=tu_phone_number_id
WHATSAPP_ACCESS_TOKEN=tu_access_token
WHATSAPP_VERIFY_TOKEN=tu_verify_token
WEBHOOK_URL=https://tu-dominio.com/webhook/whatsapp

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=
```

### 3. Levantar servicios

```bash
docker compose up --build
```

### 4. Importar productos de prueba

```bash
docker compose exec api python app/utils/import_excel.py
```

## 📱 Configuración WhatsApp Business API

### 1. Meta Developer Console

1. Crear app en [developers.facebook.com](https://developers.facebook.com)
2. Agregar producto "WhatsApp Business API"
3. Configurar webhook: `https://tu-dominio.com/webhook/whatsapp`
4. Obtener Phone Number ID y Access Token

### 2. Webhook de verificación

El endpoint `/webhook/whatsapp` maneja automáticamente:

- ✅ Verificación inicial de Meta
- 📨 Recepción de mensajes
- ✅ Marcado de mensajes como leídos
- 📤 Envío de respuestas

### 3. Números de prueba

En modo sandbox, agregar números verificados en Meta Developer Console.

## 🤖 Configuración Ollama

### Instalación Ollama

```bash
# Linux/Mac
curl -fsSL https://ollama.ai/install.sh | sh

# Windows - descargar desde ollama.ai
```

### Modelos requeridos

```bash
ollama pull qwen3:8b
```

### Verificar servicio

```bash
curl http://localhost:11434/api/tags
```

## 📚 API Endpoints

### **Productos y Stock**

```http
GET /products              # Lista todos los productos
GET /products/{id}         # Producto específico
GET /stock                 # Estado de inventario
```

### **Pedidos**

```http
GET /orders                # Lista pedidos
POST /orders               # Crear pedido
GET /orders/{id}           # Pedido específico
PATCH /orders/{id}         # Modificar (5 min window)
```

### **WhatsApp Webhook**

```http
GET /webhook/whatsapp      # Verificación Meta
POST /webhook/whatsapp     # Recibir mensajes
```

### **Conversaciones**

```http
GET /conversations         # Lista conversaciones
GET /conversations/{phone} # Conversación específica
```

## 💬 Ejemplos de uso por WhatsApp

### **Consulta de stock**

```
Usuario: "Hola, tienen camisetas rojas en talla M?"

Bot: 👕 *CAMISETAS DISPONIBLES*

🏢 *FORMAL*
• *Camiseta Roja M* - 85 unidades
  📋 Material de alta calidad
  💰 $800 (50+) | $650 (100+) | $500 (200+)

💡 *Mejor precio comprando +200 unidades*
¿Te interesa alguna en particular?
```

### **Crear pedido**

```
Usuario: "Quiero 100 camisetas rojas talla M"

Bot: 🛒 *PEDIDO CREADO* #ORD-001

📦 *PRODUCTOS*
• Camiseta Roja M → 100 unidades × $650 = $65,000

💰 *TOTAL: $65,000*
🎯 Precio por volumen (100+ unidades)

¿Confirmas el pedido?
```

### **Modificar pedido**

```
Usuario: "Cambiar el pedido anterior a 150 unidades"

Bot: ✏️ *PEDIDO MODIFICADO* #ORD-001

🔄 *CAMBIOS APLICADOS*
• Cantidad: 100 → 150 unidades
• Precio unitario: $650 → $500 (descuento 200+)
• Total: $65,000 → $75,000

✅ Pedido actualizado correctamente
```

## 🎯 Funcionalidades avanzadas

### **Detección contextual**

- Entiende variaciones: "pantalón/pantalones", "camiseta/camisetas"
- Memoria conversacional: "y en azul?" recordará el contexto anterior
- Filtros inteligentes: tipo + color + talla simultáneos

### **Precios por volumen**

- 50-99 unidades: Precio base
- 100-199 unidades: Descuento nivel 1
- 200+ unidades: Máximo descuento

### **Validaciones automáticas**

- Stock disponible en tiempo real
- Ventana de edición de 5 minutos para pedidos
- Formato de productos y cantidades

### **Respuestas organizadas**

- Agrupación por categorías (Formal, Casual, Deportivo)
- Límite de caracteres WhatsApp (4096)
- Fallbacks automáticos si Ollama falla

## 🔧 Desarrollo y debug

### Logs en tiempo real

```bash
docker compose logs -f api
```

### Acceder a contenedor

```bash
docker compose exec api bash
```

### Base de datos

```bash
docker compose exec db psql -U postgres -d b2b_db
```

### Testing endpoints

```bash
# Swagger UI
http://localhost:8000/docs

# ReDoc
http://localhost:8000/redoc
```

## 🚀 Deploy en producción

### 1. Variables de producción

```env
DATABASE_URL=postgresql://user:pass@production-db:5432/db
WHATSAPP_ACCESS_TOKEN=token_de_produccion
WEBHOOK_URL=https://tu-dominio-productivo.com/webhook/whatsapp
```

### 2. Configurar webhook en Meta

```bash
curl -X POST "https://graph.facebook.com/v18.0/{phone-number-id}/webhooks" \
  -H "Authorization: Bearer {access-token}" \
  -d "webhook_url=https://tu-dominio.com/webhook/whatsapp"
```

### 3. SSL/HTTPS requerido

Meta requiere HTTPS para webhooks en producción.

## 📊 Monitoreo

### Métricas disponibles

- Conversaciones activas por usuario
- Intenciones detectadas y derivaciones
- Tiempo de respuesta de agentes IA
- Errores de Ollama vs Gemini fallback
- Productos más consultados

### Health checks

```http
GET /health     # Estado general de la API
GET /ai/health  # Estado de servicios IA (Ollama/Gemini)
```

## 🛠 Troubleshooting

### Ollama no responde

```bash
# Verificar servicio
curl http://localhost:11434/api/tags

# Reiniciar Ollama
sudo systemctl restart ollama
```

### WhatsApp webhook falla

1. Verificar HTTPS activo
2. Validar tokens en Meta Developer
3. Comprobar logs: `docker compose logs api`

**Desarrollado por**: Leandro Signorini
