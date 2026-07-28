from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v051_lazy_pages_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.1"', text)

# ------------------------------------------------------------
# 2. Agregar navegación por vista en sidebar
# ------------------------------------------------------------
old_sidebar_header = '''    with st.sidebar:
        st.header("Controles")

        selected_model = st.radio('''

new_sidebar_header = '''    with st.sidebar:
        st.header("Vista")

        selected_page = st.radio(
            "Selecciona vista",
            [
                "Vista general",
                "Dashboard por estación",
                "Tiempos de ciclo",
                "MSA",
                "Tabla de datos",
                "Configuración",
                "Acerca del sistema",
            ],
            index=0,
        )

        st.divider()

        st.header("Controles")

        selected_model = st.radio('''

if "selected_page = st.radio" not in text:
    text = text.replace(old_sidebar_header, new_sidebar_header)

# ------------------------------------------------------------
# 3. Reemplazar sistema de tabs por renderizado condicional
# ------------------------------------------------------------
new_render_block = r'''    live_pages = [
        "Vista general",
        "Dashboard por estación",
    ]

    if selected_page == "Vista general":
        render_device_status(status_df)
        st.divider()
        render_summary_metrics(filtered_df)

    elif selected_page == "Dashboard por estación":
        render_station_dashboard_grid(
            filtered_df,
            selected_devices=selected_devices,
            devices_for_model=devices_for_model,
            show_markers=show_markers,
            show_limits=show_limits,
            auto_scale=auto_scale,
            columns_per_row=station_chart_columns,
            show_angle_chart=show_angle_station_grid,
            chart_height=station_chart_height,
        )

    elif selected_page == "Tiempos de ciclo":
        cycle_base_df = filter_cycle_base_data(
            clean_df,
            selected_model=selected_model,
            selected_devices=selected_devices,
            last_n=last_n,
            time_window_minutes=time_window_minutes,
        )

        cycle_export_base_df = filter_cycle_base_data(
            clean_df,
            selected_model=selected_model,
            selected_devices=device_options,
            last_n=0,
            time_window_minutes=None,
        )

        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            export_base_df=cycle_export_base_df,
        )

    elif selected_page == "MSA":
        render_msa_tab(
            clean_df=clean_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )

    elif selected_page == "Tabla de datos":
        render_results_table(filtered_df)

    elif selected_page == "Configuración":
        render_config_tab()

    elif selected_page == "Acerca del sistema":
        render_about_tab()

    if auto_refresh and selected_page in live_pages:
        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="asg_dashboard_autorefresh",
        )

    elif auto_refresh and selected_page not in live_pages:
        st.caption(
            "Auto-refresh pausado en esta vista para evitar el efecto nublado. "
            "El auto-refresh solo queda activo en Vista general y Dashboard por estación."
        )'''

pattern = r'''    cycle_base_df = filter_cycle_base_data\(.*?    if auto_refresh:
        st_autorefresh\(
            interval=refresh_seconds \* 1000,
            key="asg_dashboard_autorefresh",
        \)'''

if re.search(pattern, text, flags=re.DOTALL):
    text = re.sub(pattern, new_render_block, text, flags=re.DOTALL)
else:
    pattern_tabs_only = r'''    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st\.tabs\(.*?    if auto_refresh:
        st_autorefresh\(
            interval=refresh_seconds \* 1000,
            key="asg_dashboard_autorefresh",
        \)'''
    text = re.sub(pattern_tabs_only, new_render_block, text, flags=re.DOTALL)

# ------------------------------------------------------------
# 4. Por si todavía existe refresh viejo con sleep, eliminarlo
# ------------------------------------------------------------
old_sleep_refresh = '''    if auto_refresh:
        time.sleep(refresh_seconds)
        st.rerun()'''

text = text.replace(old_sleep_refresh, '''    if auto_refresh and selected_page in live_pages:
        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="asg_dashboard_autorefresh",
        )''')

# ------------------------------------------------------------
# 5. Nota en Acerca del sistema
# ------------------------------------------------------------
about_old = '''        - Auto-refresh no bloqueante'''

about_new = '''        - Auto-refresh no bloqueante
        - Renderizado por vista activa para evitar recálculo innecesario'''

text = text.replace(about_old, about_new)

# ------------------------------------------------------------
# 6. Validación
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5.1"',
    "selected_page = st.radio",
    "live_pages = [",
    'selected_page == "MSA"',
    "auto_refresh and selected_page in live_pages",
    "Auto-refresh pausado en esta vista",
]

missing = [item for item in required if item not in text]

if missing:
    print("Advertencia: faltan estos elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación del parche OK.")

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.1 aplicado correctamente.")