from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v041_time_window_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = text.replace('APP_VERSION = "v0.4"', 'APP_VERSION = "v0.4.1"')

# ------------------------------------------------------------
# 2. Agregar función para ventana de tiempo
# ------------------------------------------------------------
helper = '''
def get_time_window_minutes(option):
    mapping = {
        "Últimos 15 min": 15,
        "Últimos 30 min": 30,
        "Últimos 60 min": 60,
        "Todo el día": None,
    }

    return mapping.get(option, 30)
'''

if "def get_time_window_minutes" not in text:
    text = text.replace(
        "\n\n@st.cache_data(ttl=2)\ndef load_status():",
        f"\n\n{helper}\n\n@st.cache_data(ttl=2)\ndef load_status():"
    )

# ------------------------------------------------------------
# 3. Reemplazar filter_data y filter_cycle_base_data
# ------------------------------------------------------------
new_filter_block = r'''def filter_data(
    df,
    selected_model,
    selected_devices,
    selected_result,
    last_n,
    time_window_minutes=None,
):
    if df.empty:
        return df

    filtered = df.copy()

    if "running_model" in filtered.columns:
        filtered = filtered[filtered["running_model"] == selected_model]

    if selected_devices and "device_id" in filtered.columns:
        filtered = filtered[filtered["device_id"].isin(selected_devices)]

    if selected_result != "Todos" and "result_label" in filtered.columns:
        filtered = filtered[filtered["result_label"] == selected_result]

    sort_col = "plot_timestamp" if "plot_timestamp" in filtered.columns else "pc_timestamp"

    if sort_col in filtered.columns:
        filtered[sort_col] = pd.to_datetime(filtered[sort_col], errors="coerce")
        filtered = filtered.sort_values(sort_col)

    # Ventana de tiempo para visualización en vivo.
    # Se usa como referencia el último dato recibido, no la hora actual del sistema.
    if time_window_minutes is not None and sort_col in filtered.columns:
        reference_time = filtered[sort_col].max()

        if pd.notna(reference_time):
            start_time = reference_time - pd.Timedelta(minutes=time_window_minutes)
            filtered = filtered[filtered[sort_col] >= start_time]

    # Tomar últimos N resultados por estación, no globalmente.
    if last_n > 0 and "device_id" in filtered.columns:
        filtered = (
            filtered
            .groupby("device_id", group_keys=False)
            .tail(last_n)
        )
    elif last_n > 0:
        filtered = filtered.tail(last_n)

    if sort_col in filtered.columns:
        filtered = filtered.sort_values(sort_col)

    return filtered


def filter_cycle_base_data(
    df,
    selected_model,
    selected_devices,
    last_n,
    time_window_minutes=None,
):
    # Para tiempos de ciclo usamos OK + NG.
    # No aplicamos filtro de resultado, porque una pieza NG también consume ciclo.
    return filter_data(
        df=df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        selected_result="Todos",
        last_n=last_n,
        time_window_minutes=time_window_minutes,
    )


def calculate_piece_cycles'''

text = re.sub(
    r"def filter_data\(df, selected_model, selected_devices, selected_result, last_n\):.*?\n\ndef calculate_piece_cycles",
    new_filter_block,
    text,
    flags=re.DOTALL,
)

# ------------------------------------------------------------
# 4. Cambiar eje X: quitar dtick fijo y usar nticks=6
# ------------------------------------------------------------
text = text.replace(
    '''fig.update_xaxes(
        tickformat="%H:%M",
        dtick=60 * 1000,
    )''',
    '''fig.update_xaxes(
        tickformat="%H:%M",
        nticks=6,
    )'''
)

# ------------------------------------------------------------
# 5. Agregar control de ventana de tiempo en sidebar
# ------------------------------------------------------------
sidebar_old = '''        last_n = st.slider(
            "Últimos N resultados por estación",
            min_value=10,
            max_value=1000,
            value=200,
            step=10,
        )

        st.divider()'''

sidebar_new = '''        last_n = st.slider(
            "Últimos N resultados por estación",
            min_value=10,
            max_value=1000,
            value=200,
            step=10,
        )

        time_window_option = st.selectbox(
            "Ventana de tiempo en vivo",
            [
                "Últimos 15 min",
                "Últimos 30 min",
                "Últimos 60 min",
                "Todo el día",
            ],
            index=1,
        )

        time_window_minutes = get_time_window_minutes(time_window_option)

        st.divider()'''

if "time_window_option = st.selectbox" not in text:
    text = text.replace(sidebar_old, sidebar_new)

# ------------------------------------------------------------
# 6. Pasar time_window_minutes a filtered_df
# ------------------------------------------------------------
filtered_call_old = '''    filtered_df = filter_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        selected_result=selected_result,
        last_n=last_n,
    )'''

filtered_call_new = '''    filtered_df = filter_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        selected_result=selected_result,
        last_n=last_n,
        time_window_minutes=time_window_minutes,
    )'''

text = text.replace(filtered_call_old, filtered_call_new)

# ------------------------------------------------------------
# 7. Pasar time_window_minutes a cycle_base_df
# ------------------------------------------------------------
cycle_call_old = '''    cycle_base_df = filter_cycle_base_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        last_n=last_n,
    )'''

cycle_call_new = '''    cycle_base_df = filter_cycle_base_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        last_n=last_n,
        time_window_minutes=time_window_minutes,
    )'''

text = text.replace(cycle_call_old, cycle_call_new)

# ------------------------------------------------------------
# 8. Validaciones básicas
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.4.1"',
    "def get_time_window_minutes",
    "time_window_option = st.selectbox",
    "time_window_minutes=time_window_minutes",
    "nticks=6",
]

missing = [item for item in required if item not in text]

if missing:
    print("Advertencia: faltan estos elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación del parche OK.")

APP.write_text(text, encoding="utf-8")

print("Parche v0.4.1 aplicado correctamente.")