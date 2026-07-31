from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v059_fix_capability_signature_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.9"', text)

# ------------------------------------------------------------
# 2. Hacer render_capability_tab compatible con llamada vieja y nueva
# ------------------------------------------------------------
old_signature = '''def render_capability_tab(clean_df, devices_for_model, selected_model):'''

new_signature = '''def render_capability_tab(
    clean_df=None,
    devices_for_model=None,
    selected_model=None,
    capability_df=None,
    minimum_samples=30,
    **kwargs,
):'''

if old_signature in text:
    text = text.replace(old_signature, new_signature)

# ------------------------------------------------------------
# 3. Insertar normalización al inicio de render_capability_tab
# ------------------------------------------------------------
old_start = '''def render_capability_tab(
    clean_df=None,
    devices_for_model=None,
    selected_model=None,
    capability_df=None,
    minimum_samples=30,
    **kwargs,
):
    st.subheader("Capacidad de proceso")'''

new_start = '''def render_capability_tab(
    clean_df=None,
    devices_for_model=None,
    selected_model=None,
    capability_df=None,
    minimum_samples=30,
    **kwargs,
):
    # Compatibilidad:
    # Algunas versiones anteriores llamaban esta función con capability_df.
    # La versión nueva usa clean_df.
    if clean_df is None and capability_df is not None:
        clean_df = capability_df

    if clean_df is None:
        clean_df = pd.DataFrame()

    if selected_model is None:
        selected_model = "T1XX-1"

    st.subheader("Capacidad de proceso")'''

if old_start in text:
    text = text.replace(old_start, new_start)

# ------------------------------------------------------------
# 4. Si devices_for_model viene vacío, evitar que truene
# ------------------------------------------------------------
old_device_line = '''    device_options = get_device_options_from_config(devices_for_model)'''

new_device_line = '''    device_options = get_device_options_from_config(devices_for_model)

    # Si esta función fue llamada desde una versión anterior sin devices_for_model,
    # usamos las estaciones presentes en los datos como respaldo.
    if not device_options and clean_df is not None and not clean_df.empty and "device_id" in clean_df.columns:
        device_options = (
            clean_df["device_id"]
            .dropna()
            .astype(str)
            .str.strip()
            .drop_duplicates()
            .tolist()
        )'''

text = text.replace(old_device_line, new_device_line)

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.9 aplicado correctamente.")