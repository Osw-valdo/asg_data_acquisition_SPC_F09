from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v052_live_table_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.2"', text)

# ------------------------------------------------------------
# 2. Bajar cache de lectura de 2 s a 1 s
# ------------------------------------------------------------
text = text.replace("@st.cache_data(ttl=2)", "@st.cache_data(ttl=1)")

# ------------------------------------------------------------
# 3. Permitir refresh cada 1 segundo
# ------------------------------------------------------------
text = text.replace(
    'refresh_seconds = st.slider("Refresh segundos", 2, 30, 5)',
    'refresh_seconds = st.slider("Refresh segundos", 1, 30, 1)'
)

# Por si el código quedó en varias líneas
text = text.replace(
    '''refresh_seconds = st.slider("Refresh segundos", 2, 30, 5)''',
    '''refresh_seconds = st.slider("Refresh segundos", 1, 30, 1)'''
)

# ------------------------------------------------------------
# 4. Agregar Tabla de datos a páginas live
# ------------------------------------------------------------
old_live_pages = '''    live_pages = [
        "Vista general",
        "Dashboard por estación",
    ]'''

new_live_pages = '''    live_pages = [
        "Vista general",
        "Dashboard por estación",
        "Tabla de datos",
    ]'''

text = text.replace(old_live_pages, new_live_pages)

# ------------------------------------------------------------
# 5. Actualizar mensaje de refresh pausado
# ------------------------------------------------------------
text = text.replace(
    "El auto-refresh solo queda activo en Vista general y Dashboard por estación.",
    "El auto-refresh solo queda activo en Vista general, Dashboard por estación y Tabla de datos."
)

# ------------------------------------------------------------
# 6. Actualizar Acerca del sistema
# ------------------------------------------------------------
text = text.replace(
    "- Auto-refresh no bloqueante",
    "- Auto-refresh no bloqueante\n        - Tabla de datos en vivo"
)

# ------------------------------------------------------------
# 7. Validación
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5.2"',
    "@st.cache_data(ttl=1)",
    '"Tabla de datos",',
    "st_autorefresh(",
]

missing = [item for item in required if item not in text]

if missing:
    print("Advertencia: faltan estos elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación del parche OK.")

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.2 aplicado correctamente.")