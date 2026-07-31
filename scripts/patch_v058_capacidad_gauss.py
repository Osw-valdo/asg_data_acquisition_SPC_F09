from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v058_capacidad_gauss_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Actualizar versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.8"', text)

# ------------------------------------------------------------
# 2. Agregar import math
# ------------------------------------------------------------
if "import math" not in text:
    if "import time" in text:
        text = text.replace("import time", "import time\nimport math")
    else:
        text = text.replace("import json", "import json\nimport math")

# ------------------------------------------------------------
# 3. Funciones nuevas / reemplazo de Capacidad
# ------------------------------------------------------------
capability_functions = r'''
def get_device_options_from_config(devices_for_model):
    """
    Obtiene todas las estaciones configuradas para el modelo seleccionado.
    No depende de que la estación tenga datos recientes.
    """
    if devices_for_model is None or devices_for_model.empty:
        return []

    if "device_id" not in devices_for_model.columns:
        return []

    return (
        devices_for_model["device_id"]
        .dropna()
        .astype(str)
        .str.strip()
        .tolist()
    )


def normal_pdf(x, mu, sigma):
    if sigma is None or sigma <= 0:
        return 0

    return (1 / (sigma * math.sqrt(2 * math.pi))) * math.exp(
        -0.5 * ((x - mu) / sigma) ** 2
    )


def get_last_numeric_value(df, col):
    if col not in df.columns:
        return None

    values = pd.to_numeric(df[col], errors="coerce").dropna()

    if values.empty:
        return None

    return float(values.iloc[-1])


def get_capability_columns(variable_col):
    if variable_col == "torque_actual":
        return {
            "label": "Torque",
            "lsl": "torque_min",
            "target": "torque_target",
            "usl": "torque_max",
            "unit": "cN·m",
        }

    if variable_col == "angle_actual":
        return {
            "label": "Ángulo",
            "lsl": "angle_min",
            "target": "angle_target",
            "usl": "angle_max",
            "unit": "deg",
        }

    return {
        "label": variable_col,
        "lsl": "",
        "target": "",
        "usl": "",
        "unit": "",
    }


def calculate_cp_cpk(values, lsl, usl):
    values = pd.to_numeric(values, errors="coerce").dropna()

    if values.empty:
        return None, None, None, None

    mean = float(values.mean())
    std = float(values.std())

    if std <= 0 or lsl is None or usl is None:
        return mean, std, None, None

    cp = (usl - lsl) / (6 * std)
    cpu = (usl - mean) / (3 * std)
    cpl = (mean - lsl) / (3 * std)
    cpk = min(cpu, cpl)

    return mean, std, cp, cpk


def format_capability_value(value, decimals=3):
    try:
        if value is None or pd.isna(value):
            return "—"

        return f"{float(value):.{decimals}f}"
    except Exception:
        return "—"


def get_capability_status(cpk):
    if cpk is None or pd.isna(cpk):
        return "Sin evaluar"

    if cpk >= 1.33:
        return "Aceptable"

    if cpk >= 1.00:
        return "Marginal"

    return "No aceptable"


def make_capability_gauss_chart(
    station_df,
    variable_col,
    selected_station,
    variable_label,
    unit,
    lsl,
    target,
    usl,
):
    fig = go.Figure()

    values = pd.to_numeric(station_df[variable_col], errors="coerce").dropna()

    if values.empty:
        return fig

    mean = float(values.mean())
    std = float(values.std())

    fig.add_trace(
        go.Histogram(
            x=values,
            histnorm="probability density",
            nbinsx=25,
            name="Datos reales",
            opacity=0.65,
            hovertemplate=(
                f"{variable_label}: " + "%{x}<br>"
                "Densidad: %{y}<br>"
                "<extra></extra>"
            ),
        )
    )

    # Rango visual considerando datos y límites
    limits = [v for v in [lsl, target, usl, mean] if v is not None and not pd.isna(v)]

    x_min = min([values.min()] + limits)
    x_max = max([values.max()] + limits)

    if x_max == x_min:
        x_min -= 1
        x_max += 1

    padding = (x_max - x_min) * 0.15
    x_min -= padding
    x_max += padding

    # Campana de Gauss
    if std > 0:
        x_values = [
            x_min + (x_max - x_min) * i / 250
            for i in range(251)
        ]

        y_values = [
            normal_pdf(x, mean, std)
            for x in x_values
        ]

        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines",
                name="Campana normal",
                line=dict(width=3),
                hovertemplate=(
                    f"{variable_label}: " + "%{x:.3f}<br>"
                    "Densidad normal: %{y:.5f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    # Líneas de especificación
    if lsl is not None:
        fig.add_vline(
            x=lsl,
            line_dash="dash",
            line_width=2,
            annotation_text="LSL",
            annotation_position="top left",
        )

    if target is not None:
        fig.add_vline(
            x=target,
            line_dash="dot",
            line_width=2,
            annotation_text="Target",
            annotation_position="top",
        )

    if usl is not None:
        fig.add_vline(
            x=usl,
            line_dash="dash",
            line_width=2,
            annotation_text="USL",
            annotation_position="top right",
        )

    fig.add_vline(
        x=mean,
        line_dash="solid",
        line_width=2,
        annotation_text="Media",
        annotation_position="bottom",
    )

    fig.update_layout(
        title=f"Distribución seleccionada | {selected_station} | {variable_label}",
        xaxis_title=f"{variable_label} ({unit})" if unit else variable_label,
        yaxis_title="Densidad",
        bargap=0.05,
        height=480,
        margin=dict(l=10, r=10, t=60, b=10),
        hovermode="x unified",
        legend_title="Serie",
    )

    return fig


def render_capability_tab(clean_df, devices_for_model, selected_model):
    st.subheader("Capacidad de proceso")

    st.caption(
        "Distribución con histograma real, campana normal ajustada, límites de especificación y Cp/Cpk."
    )

    device_options = get_device_options_from_config(devices_for_model)

    if not device_options:
        st.warning("No hay estaciones configuradas para el modelo seleccionado.")
        return

    if clean_df.empty:
        st.info("Todavía no hay datos limpios para calcular capacidad.")
        return

    df = clean_df.copy()

    if "running_model" in df.columns:
        df = df[df["running_model"] == selected_model]

    c1, c2, c3 = st.columns(3)

    with c1:
        selected_station = st.selectbox(
            "Estación",
            device_options,
            key="capability_station_selector",
        )

    with c2:
        variable_col = st.selectbox(
            "Variable",
            ["torque_actual", "angle_actual"],
            format_func=lambda x: "Torque actual" if x == "torque_actual" else "Ángulo actual",
            key="capability_variable_selector",
        )

    station_df = df[df["device_id"] == selected_station].copy()

    task_options = ["Todas"]

    if "task_label" in station_df.columns:
        task_options += sorted(
            [
                str(x).strip()
                for x in station_df["task_label"].dropna().unique().tolist()
                if str(x).strip() and str(x).strip().lower() != "nan"
            ]
        )

    with c3:
        selected_task = st.selectbox(
            "Task / PSet",
            task_options,
            key="capability_task_selector",
        )

    if selected_task != "Todas" and "task_label" in station_df.columns:
        station_df = station_df[station_df["task_label"] == selected_task]

    if station_df.empty:
        st.warning(
            "La estación está configurada, pero no tiene datos en la sesión o ventana actual."
        )
        return

    if variable_col not in station_df.columns:
        st.warning(f"No existe la columna {variable_col}.")
        return

    variable_cfg = get_capability_columns(variable_col)

    variable_label = variable_cfg["label"]
    lsl_col = variable_cfg["lsl"]
    target_col = variable_cfg["target"]
    usl_col = variable_cfg["usl"]
    unit = variable_cfg["unit"]

    values = pd.to_numeric(station_df[variable_col], errors="coerce").dropna()

    if values.empty:
        st.warning("No hay valores numéricos válidos para esta selección.")
        return

    lsl = get_last_numeric_value(station_df, lsl_col)
    target = get_last_numeric_value(station_df, target_col)
    usl = get_last_numeric_value(station_df, usl_col)

    mean, std, cp, cpk = calculate_cp_cpk(values, lsl, usl)

    out_spec = 0

    if lsl is not None and usl is not None:
        out_spec = int(((values < lsl) | (values > usl)).sum())

    out_spec_pct = (out_spec / len(values) * 100) if len(values) else 0

    status = get_capability_status(cpk)

    m1, m2, m3, m4, m5, m6 = st.columns(6)

    m1.metric("N", len(values))
    m2.metric("Media", format_capability_value(mean, 3))
    m3.metric("Std", format_capability_value(std, 4))
    m4.metric("Cp", format_capability_value(cp, 3))
    m5.metric("Cpk", format_capability_value(cpk, 3))
    m6.metric("Estatus", status)

    s1, s2, s3, s4 = st.columns(4)

    s1.metric("LSL", format_capability_value(lsl, 3))
    s2.metric("Target", format_capability_value(target, 3))
    s3.metric("USL", format_capability_value(usl, 3))
    s4.metric("% fuera spec", f"{out_spec_pct:.2f}%")

    if lsl is None or usl is None:
        st.warning(
            "No se encontraron límites LSL/USL para esta variable. "
            "La campana se mostrará, pero Cp/Cpk no se puede evaluar formalmente."
        )

    fig = make_capability_gauss_chart(
        station_df=station_df,
        variable_col=variable_col,
        selected_station=selected_station,
        variable_label=variable_label,
        unit=unit,
        lsl=lsl,
        target=target,
        usl=usl,
    )

    st.plotly_chart(
        fig,
        width="stretch",
        key="capability_gauss_chart",
    )

    st.markdown("### Interpretación")

    if cpk is None:
        st.info(
            "Se requiere desviación estándar válida y límites de especificación para calcular Cp/Cpk."
        )
    elif cpk >= 1.33:
        st.success(
            "El proceso se observa capaz para esta selección. "
            "Cpk es mayor o igual a 1.33."
        )
    elif cpk >= 1.00:
        st.warning(
            "El proceso se observa marginal. "
            "Conviene revisar centrado, variación o cantidad de muestras."
        )
    else:
        st.error(
            "El proceso no se observa capaz para esta selección. "
            "Cpk es menor a 1.00."
        )

    st.caption(
        "Nota: la campana normal es una referencia visual. "
        "Cp/Cpk asumen que la distribución se comporta razonablemente normal."
    )

    st.markdown("### Datos usados para capacidad")

    display_cols = [
        "plot_timestamp",
        "pc_timestamp",
        "timestamp_asg",
        "running_model",
        "device_id",
        "station",
        "process",
        "task_label",
        variable_col,
        lsl_col,
        target_col,
        usl_col,
        "result_label",
        "tightening_id",
    ]

    available_cols = [
        col for col in display_cols
        if col and col in station_df.columns
    ]

    table_df = station_df[available_cols].copy()

    if "plot_timestamp" in table_df.columns:
        table_df = table_df.sort_values("plot_timestamp", ascending=False)

    st.dataframe(
        table_df.head(300),
        width="stretch",
        hide_index=True,
    )
'''

# Reemplazar render_capability_tab existente si ya existe
pattern = r'\ndef render_capability_tab\(.*?\n\ndef render_'

if re.search(pattern, text, flags=re.DOTALL):
    text = re.sub(
        pattern,
        "\n" + capability_functions + "\n\ndef render_",
        text,
        flags=re.DOTALL,
        count=1,
    )
else:
    # Insertar antes de MSA si existe, si no antes de results table
    if "\ndef render_msa_tab" in text:
        text = text.replace(
            "\ndef render_msa_tab",
            "\n" + capability_functions + "\n\ndef render_msa_tab",
            1,
        )
    else:
        text = text.replace(
            "\ndef render_results_table",
            "\n" + capability_functions + "\n\ndef render_results_table",
            1,
        )

# ------------------------------------------------------------
# 4. Agregar vista Capacidad al selector lateral si no existe
# ------------------------------------------------------------
if '"Capacidad"' not in text:
    text = text.replace(
        '''"Tiempos de ciclo",
                "MSA",''',
        '''"Tiempos de ciclo",
                "Capacidad",
                "MSA",'''
    )

    text = text.replace(
        '''"Tiempos de ciclo",
            "MSA",''',
        '''"Tiempos de ciclo",
            "Capacidad",
            "MSA",'''
    )

# ------------------------------------------------------------
# 5. Agregar rama de renderizado para Capacidad
# ------------------------------------------------------------
if 'selected_page == "Capacidad"' not in text:
    text = text.replace(
        '''    elif selected_page == "MSA":
        render_msa_tab(''',
        '''    elif selected_page == "Capacidad":
        render_capability_tab(
            clean_df=clean_df_for_view if "clean_df_for_view" in locals() else clean_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )

    elif selected_page == "MSA":
        render_msa_tab('''
    )

# ------------------------------------------------------------
# 6. Nota en Acerca del sistema
# ------------------------------------------------------------
if "- Capacidad con campana normal" not in text:
    text = text.replace(
        "- Pestaña MSA base",
        "- Pestaña MSA base\n        - Capacidad con campana normal"
    )

# ------------------------------------------------------------
# 7. Validaciones
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5.8"',
    "def render_capability_tab",
    "def make_capability_gauss_chart",
    "def get_device_options_from_config",
    '"Capacidad"',
    'selected_page == "Capacidad"',
    "Campana normal",
    "Cp",
    "Cpk",
]

missing = [item for item in required if item not in text]

if missing:
    print("Advertencia: faltan elementos:")
    for item in missing:
        print(f"- {item}")
else:
    print("Validación del parche OK.")

APP.write_text(text, encoding="utf-8")

print("Parche v0.5.8 aplicado correctamente.")