from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v0510_fix_capacidad_dropdown_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.10"', text)

# ------------------------------------------------------------
# 2. Asegurar helper para tomar estaciones desde config
# ------------------------------------------------------------
helper = r'''
def get_device_options_from_config(devices_for_model):
    """
    Obtiene todas las estaciones configuradas para el modelo seleccionado.
    Esto evita que Capacidad muestre solo estaciones con datos recientes.
    """
    if devices_for_model is None:
        return []

    try:
        if devices_for_model.empty:
            return []
    except Exception:
        return []

    if "device_id" not in devices_for_model.columns:
        return []

    return (
        devices_for_model["device_id"]
        .dropna()
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )
'''

if "def get_device_options_from_config" not in text:
    if "\ndef normal_pdf" in text:
        text = text.replace("\ndef normal_pdf", "\n" + helper + "\n\ndef normal_pdf", 1)
    elif "\ndef render_capability_tab" in text:
        text = text.replace("\ndef render_capability_tab", "\n" + helper + "\n\ndef render_capability_tab", 1)
    else:
        print("Advertencia: no se encontró dónde insertar get_device_options_from_config.")

# ------------------------------------------------------------
# 3. Corregir llamada en main()
# Antes llamaba Capacidad solo con capability_df.
# Ahora también manda devices_for_model y selected_model.
# ------------------------------------------------------------
old_call = '''render_capability_tab(
            capability_df=capability_base_df,
            minimum_samples=30,
        )'''

new_call = '''render_capability_tab(
            capability_df=capability_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            minimum_samples=30,
        )'''

if old_call in text:
    text = text.replace(old_call, new_call, 1)
    print("Llamada vieja de Capacidad corregida.")
else:
    print("No se encontró la llamada vieja exacta. Intentando corrección por regex...")

    pattern = r'''render_capability_tab\(
\s*capability_df=capability_base_df,
\s*minimum_samples=30,
\s*\)'''

    replacement = '''render_capability_tab(
            capability_df=capability_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            minimum_samples=30,
        )'''

    text, n = re.subn(pattern, replacement, text, count=1)

    if n:
        print("Llamada de Capacidad corregida por regex.")
    else:
        print("Advertencia: no se pudo corregir automáticamente la llamada de Capacidad.")

# ------------------------------------------------------------
# 4. Asegurar que dentro de render_capability_tab use config primero
# ------------------------------------------------------------
old_line = '''    device_options = get_device_options_from_config(devices_for_model)'''

new_block = '''    device_options = get_device_options_from_config(devices_for_model)

    # La prioridad es usar config/asg_devices.csv.
    # Solo si no viene devices_for_model, usamos los datos como respaldo.
    if not device_options and clean_df is not None and not clean_df.empty and "device_id" in clean_df.columns:
        device_options = (
            clean_df["device_id"]
            .dropna()
            .astype(str)
            .str.strip()
            .drop_duplicates()
            .tolist()
        )'''

# Evitar duplicar si ya existe el comentario nuevo
if "La prioridad es usar config/asg_devices.csv" not in text:
    if old_line in text:
        text = text.replace(old_line, new_block, 1)
        print("Selector de estaciones ajustado para usar config primero.")
    else:
        print("Advertencia: no se encontró la línea device_options.")

# ------------------------------------------------------------
# 5. Validación básica
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5.10"',
    "def get_device_options_from_config",
    "devices_for_model=devices_for_model",
    "selected_model=selected_model",
    "capability_station_selector",
]

missing = [item for item in required if item not in text]

APP.write_text(text, encoding="utf-8")

if missing:
    print("Advertencia: faltan elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación OK.")

print("Parche v0.5.10 aplicado correctamente.")