from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v055_streamlit_width_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# Actualizar versión
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.5"', text)

# Reemplazo recomendado por Streamlit
text = text.replace("use_container_width=True", 'width="stretch"')
text = text.replace("use_container_width=False", 'width="content"')

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.5 aplicado correctamente.")
print("Se reemplazó use_container_width por width.")
