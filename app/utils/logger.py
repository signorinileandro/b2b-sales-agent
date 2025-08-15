import sys
from datetime import datetime

def log(message: str):
    """
    Función de logging personalizada que fuerza el flush para ser visible en Render.
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", flush=True)