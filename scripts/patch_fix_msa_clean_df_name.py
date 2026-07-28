from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_fix_msa_clean_df_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# Corregir SOLO dentro de render_msa_tab()
pattern = r"(def render_msa_tab\(.*?\n)(.*?)(\n\ndef render_results_table)"

match = re.search(pattern, text, flags=re.DOTALL)

if not match:
    print("No se encontró render_msa_tab(). No se aplicó cambio.")
else:
    start = match.group(1)
    body = match.group(2)
    end = match.group(3)

    body = body.replace(
        "clean_df=clean_df_for_view,",
        "clean_df=clean_df,"
    )

    text = text[:match.start()] + start + body + end + text[match.end():]

    APP.write_text(text, encoding="utf-8")
    print("Corrección aplicada: render_msa_tab ahora usa clean_df interno.")