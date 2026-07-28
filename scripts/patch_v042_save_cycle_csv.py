from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v042_save_cycle_csv_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = text.replace('APP_VERSION = "v0.4.1"', 'APP_VERSION = "v0.4.2"')
text = text.replace('APP_VERSION = "v0.4"', 'APP_VERSION = "v0.4.2"')

# ------------------------------------------------------------
# 2. Agregar función para guardar CSV de piezas y resumen
# ------------------------------------------------------------
save_function = r'''
def save_cycle_outputs(pieces_df, summary_df, selected_model):
    """
    Guarda automáticamente los cálculos de piezas y tiempos de ciclo.

    Importante:
    - Estos CSV son datos calculados desde el CSV limpio master.
    - No reemplazan el RAW ni el asg_clean_master.
    - Se guardan por modelo y por día.
    """
    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        safe_model = str(selected_model).replace("/", "_").replace("\\", "_").replace(" ", "_")
        date_txt = today_str()

        pieces_file = PROCESSED_DIR / f"asg_piece_cycles_{safe_model}_{date_txt}.csv"
        summary_file = PROCESSED_DIR / f"asg_cycle_summary_{safe_model}_{date_txt}.csv"

        exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if pieces_df is None:
            pieces_df = pd.DataFrame()

        if summary_df is None:
            summary_df = pd.DataFrame()

        pieces_export = pieces_df.copy()
        summary_export = summary_df.copy()

        if pieces_export.empty and len(pieces_export.columns) == 0:
            pieces_export = pd.DataFrame(
                columns=[
                    "export_timestamp_pc",
                    "export_model",
                    "running_model",
                    "device_id",
                    "station",
                    "process",
                    "screws_per_piece",
                    "piece_number",
                    "piece_start_time",
                    "piece_end_time",
                    "cycle_time_sec",
                    "tightening_time_sec",
                    "piece_result",
                ]
            )
        else:
            pieces_export.insert(0, "export_timestamp_pc", exported_at)
            pieces_export.insert(1, "export_model", selected_model)

        if summary_export.empty and len(summary_export.columns) == 0:
            summary_export = pd.DataFrame(
                columns=[
                    "export_timestamp_pc",
                    "export_model",
                    "device_id",
                    "station",
                    "process",
                    "screws_per_piece",
                    "torque_results",
                    "complete_pieces",
                    "partial_screws",
                    "avg_cycle_time_sec",
                    "min_cycle_time_sec",
                    "max_cycle_time_sec",
                    "std_cycle_time_sec",
                    "avg_tightening_time_sec",
                ]
            )
        else:
            summary_export.insert(0, "export_timestamp_pc", exported_at)
            summary_export.insert(1, "export_model", selected_model)

        pieces_export.to_csv(pieces_file, index=False, encoding="utf-8-sig")
        summary_export.to_csv(summary_file, index=False, encoding="utf-8-sig")

        return {
            "ok": True,
            "pieces_file": str(pieces_file),
            "summary_file": str(summary_file),
            "pieces_rows": len(pieces_export),
            "summary_rows": len(summary_export),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }
'''

if "def save_cycle_outputs" not in text:
    text = text.replace(
        "\n\n\ndef render_device_status",
        f"\n\n{save_function}\n\n\ndef render_device_status"
    )

# ------------------------------------------------------------
# 3. Cambiar firma de render_cycle_time_tab
# ------------------------------------------------------------
text = text.replace(
    "def render_cycle_time_tab(cycle_base_df, devices_for_model, selected_model):",
    "def render_cycle_time_tab(cycle_base_df, devices_for_model, selected_model, export_base_df=None):"
)

# ------------------------------------------------------------
# 4. Insertar guardado automático dentro de render_cycle_time_tab
# ------------------------------------------------------------
target_block = r'''    summary_df = calculate_cycle_summary(
        cycle_base_df,
        pieces_df=pieces_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )

    if summary_df.empty:
        st.warning("No se pudo calcular el resumen. Revisa que `asg_devices.csv` tenga tornillos por pieza.")
        return'''

replacement_block = r'''    summary_df = calculate_cycle_summary(
        cycle_base_df,
        pieces_df=pieces_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )

    # Guardado automático de CSV calculados.
    # Para guardar usamos export_base_df, que debe venir con todo el día del modelo.
    # La pestaña puede seguir mostrando solo la ventana visual seleccionada.
    save_source_df = export_base_df if export_base_df is not None else cycle_base_df

    export_pieces_df = calculate_piece_cycles(
        save_source_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )

    export_summary_df = calculate_cycle_summary(
        save_source_df,
        pieces_df=export_pieces_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )

    save_info = save_cycle_outputs(
        pieces_df=export_pieces_df,
        summary_df=export_summary_df,
        selected_model=selected_model,
    )

    if save_info.get("ok"):
        st.caption(
            f"CSV calculados guardados automáticamente: "
            f"{save_info.get('pieces_rows', 0)} piezas | "
            f"{save_info.get('summary_rows', 0)} estaciones."
        )

        with st.expander("Ver archivos CSV calculados"):
            st.code(
                f"""
Piezas calculadas:
{save_info.get("pieces_file", "")}

Resumen tiempos de ciclo:
{save_info.get("summary_file", "")}
                """.strip()
            )
    else:
        st.warning(f"No se pudieron guardar los CSV calculados: {save_info.get('error', '')}")

    if summary_df.empty:
        st.warning("No se pudo calcular el resumen. Revisa que `asg_devices.csv` tenga tornillos por pieza.")
        return'''

if "save_info = save_cycle_outputs" not in text:
    text = text.replace(target_block, replacement_block)

# ------------------------------------------------------------
# 5. Crear dataframe de exportación con TODO el día del modelo
# ------------------------------------------------------------
cycle_export_block = r'''    cycle_export_base_df = filter_cycle_base_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=device_options,
        last_n=0,
        time_window_minutes=None,
    )
'''

if "cycle_export_base_df = filter_cycle_base_data" not in text:
    text = text.replace(
        '''    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(''',
        f"{cycle_export_block}\n    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs("
    )

# ------------------------------------------------------------
# 6. Pasar export_base_df a la pestaña de tiempos de ciclo
# ------------------------------------------------------------
old_call = r'''        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )'''

new_call = r'''        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            export_base_df=cycle_export_base_df,
        )'''

text = text.replace(old_call, new_call)

# ------------------------------------------------------------
# 7. Agregar información en Acerca del sistema
# ------------------------------------------------------------
about_old = '''        - El tiempo de atornillado se calcula entre el primer y último tornillo de la misma pieza.'''

about_new = '''        - El tiempo de atornillado se calcula entre el primer y último tornillo de la misma pieza.
        - Los cálculos se guardan automáticamente en CSV dentro de `data/processed`.'''

text = text.replace(about_old, about_new)

# ------------------------------------------------------------
# 8. Validaciones básicas
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.4.2"',
    "def save_cycle_outputs",
    "export_base_df=None",
    "cycle_export_base_df = filter_cycle_base_data",
    "save_info = save_cycle_outputs",
    "asg_piece_cycles_",
    "asg_cycle_summary_",
]

missing = [item for item in required if item not in text]

if missing:
    print("Advertencia: faltan estos elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación del parche OK.")

APP.write_text(text, encoding="utf-8")

print("Parche v0.4.2 aplicado correctamente.")