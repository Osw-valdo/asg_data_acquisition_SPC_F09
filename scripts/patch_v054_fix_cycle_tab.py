from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v054_fix_cycle_tab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.4"', text)

# ------------------------------------------------------------
# 2. Eliminar guardado automático pesado dentro de Tiempos de ciclo
# ------------------------------------------------------------
auto_save_pattern = r'''
    # Guardado automático de CSV calculados\.
    # Para guardar usamos export_base_df, que debe venir con todo el día del modelo\.
    # La pestaña puede seguir mostrando solo la ventana visual seleccionada\.
    save_source_df = export_base_df if export_base_df is not None else cycle_base_df

    export_pieces_df = calculate_piece_cycles\(
        save_source_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    \)

    export_summary_df = calculate_cycle_summary\(
        save_source_df,
        pieces_df=export_pieces_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    \)

    save_info = save_cycle_outputs\(
        pieces_df=export_pieces_df,
        summary_df=export_summary_df,
        selected_model=selected_model,
    \)

    if save_info.get\("ok"\):
        st.caption\(
            f"CSV calculados guardados automáticamente: "
            f"\{save_info.get\('pieces_rows', 0\)\} piezas \| "
            f"\{save_info.get\('summary_rows', 0\)\} estaciones."
        \)

        with st.expander\("Ver archivos CSV calculados"\):
            st.code\(
                f"""
Piezas calculadas:
\{save_info.get\("pieces_file", ""\)\}

Resumen tiempos de ciclo:
\{save_info.get\("summary_file", ""\)\}
                """.strip\(\)
            \)
    else:
        st.warning\(f"No se pudieron guardar los CSV calculados: \{save_info.get\('error', ''\)\}"\)
'''

text = re.sub(auto_save_pattern, "\n", text, flags=re.DOTALL)

# ------------------------------------------------------------
# 3. Agregar botón manual para guardar CSV después de validar summary_df
# ------------------------------------------------------------
target = '''    if summary_df.empty:
        st.warning("No se pudo calcular el resumen. Revisa que `asg_devices.csv` tenga tornillos por pieza.")
        return

    valid_cycle_values = pd.Series(dtype=float)'''

replacement = '''    if summary_df.empty:
        st.warning("No se pudo calcular el resumen. Revisa que `asg_devices.csv` tenga tornillos por pieza.")
        return

    with st.expander("Guardar CSV calculados", expanded=False):
        st.caption(
            "Para mantener rápida esta vista, el CSV de tiempos de ciclo se guarda bajo demanda."
        )

        if st.button("Guardar CSV de tiempos de ciclo", key="save_cycle_outputs_button"):
            save_info = save_cycle_outputs(
                pieces_df=pieces_df,
                summary_df=summary_df,
                selected_model=selected_model,
            )

            if save_info.get("ok"):
                st.success(
                    f"CSV guardados correctamente: "
                    f"{save_info.get('pieces_rows', 0)} piezas | "
                    f"{save_info.get('summary_rows', 0)} estaciones."
                )

                st.code(
                    f"""
Piezas calculadas:
{save_info.get("pieces_file", "")}

Resumen tiempos de ciclo:
{save_info.get("summary_file", "")}
                    """.strip()
                )
            else:
                st.error(f"No se pudieron guardar los CSV: {save_info.get('error', '')}")

    valid_cycle_values = pd.Series(dtype=float)'''

if "save_cycle_outputs_button" not in text:
    text = text.replace(target, replacement)

# ------------------------------------------------------------
# 4. Quitar cálculo extra de exportación de todo el día dentro de la vista
# ------------------------------------------------------------
export_block_pattern = r'''
        cycle_export_base_df = filter_cycle_base_data\(
            clean_df_for_view,
            selected_model=selected_model,
            selected_devices=device_options,
            last_n=0,
            time_window_minutes=None,
        \)
'''

text = re.sub(export_block_pattern, "\n", text, flags=re.DOTALL)

# ------------------------------------------------------------
# 5. Quitar argumento export_base_df en llamada
# ------------------------------------------------------------
text = text.replace(
    '''        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            export_base_df=cycle_export_base_df,
        )''',
    '''        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )'''
)

# ------------------------------------------------------------
# 6. Actualizar notas de Acerca del sistema
# ------------------------------------------------------------
text = text.replace(
    "- Los cálculos se guardan automáticamente en CSV dentro de `data/processed`.",
    "- Los cálculos se pueden guardar en CSV desde la pestaña Tiempos de ciclo."
)

text = text.replace(
    "- Sesiones de conteo de producción por modelo",
    "- Sesiones de conteo de producción por modelo\n        - Guardado manual optimizado de tiempos de ciclo"
)

# ------------------------------------------------------------
# 7. Validaciones
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5.4"',
    "save_cycle_outputs_button",
    "Guardar CSV de tiempos de ciclo",
]

missing = [item for item in required if item not in text]

bad_items = [
    "CSV calculados guardados automáticamente",
    "cycle_export_base_df=cycle_export_base_df",
]

still_present = [item for item in bad_items if item in text]

if missing:
    print("Advertencia: faltan elementos:")
    for item in missing:
        print(f"- Falta: {item}")
else:
    print("Validación básica OK.")

if still_present:
    print("Advertencia: todavía aparecen elementos pesados:")
    for item in still_present:
        print(f"- Revisar: {item}")

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.4 aplicado correctamente.")