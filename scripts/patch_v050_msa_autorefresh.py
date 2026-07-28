from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

backup = ROOT / f"app_backup_before_v050_msa_autorefresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

text = APP.read_text(encoding="utf-8")
backup.write_text(text, encoding="utf-8")

print(f"Backup creado: {backup}")

# ------------------------------------------------------------
# 1. Versión
# ------------------------------------------------------------
text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5"', text)

# ------------------------------------------------------------
# 2. Importar st_autorefresh
# ------------------------------------------------------------
if "from streamlit_autorefresh import st_autorefresh" not in text:
    text = text.replace(
        "import streamlit as st",
        "import streamlit as st\nfrom streamlit_autorefresh import st_autorefresh"
    )

# ------------------------------------------------------------
# 3. Funciones MSA base
# ------------------------------------------------------------
msa_functions = r'''
def normalize_msa_columns(df):
    """
    Normaliza columnas de un estudio MSA cargado por CSV.

    Columnas esperadas:
    - part_id
    - operator
    - trial
    - measurement
    """
    df = df.copy()

    rename_map = {}

    for col in df.columns:
        c = str(col).strip().lower()

        if c in ["part", "part_id", "pieza", "pieza_id"]:
            rename_map[col] = "part_id"
        elif c in ["operator", "operador"]:
            rename_map[col] = "operator"
        elif c in ["trial", "repetition", "rep", "repeticion", "repetición"]:
            rename_map[col] = "trial"
        elif c in ["measurement", "medicion", "medición", "value", "valor", "torque"]:
            rename_map[col] = "measurement"
        elif c in ["station", "estacion", "estación", "device_id", "asg"]:
            rename_map[col] = "station"

    df = df.rename(columns=rename_map)

    required = ["part_id", "operator", "trial", "measurement"]

    missing = [col for col in required if col not in df.columns]

    if missing:
        return df, missing

    df["measurement"] = pd.to_numeric(df["measurement"], errors="coerce")
    df = df.dropna(subset=["measurement"])

    df["part_id"] = df["part_id"].astype(str)
    df["operator"] = df["operator"].astype(str)
    df["trial"] = df["trial"].astype(str)

    return df, []


def render_msa_uploaded_study(msa_df):
    st.markdown("### Reporte MSA desde archivo cargado")

    msa_df, missing = normalize_msa_columns(msa_df)

    if missing:
        st.warning(
            "El archivo cargado no tiene las columnas mínimas requeridas: "
            + ", ".join(missing)
        )

        st.markdown("Formato esperado:")

        st.code(
            """
station,part_id,operator,trial,measurement
ASG_01,1,A,1,95.2
ASG_01,1,A,2,95.1
ASG_01,1,B,1,95.4
ASG_01,1,B,2,95.3
            """.strip()
        )
        return

    if msa_df.empty:
        st.info("El archivo MSA no contiene mediciones válidas.")
        return

    measurements = msa_df["measurement"]

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric("Mediciones", len(msa_df))
    c2.metric("Partes", msa_df["part_id"].nunique())
    c3.metric("Operadores", msa_df["operator"].nunique())
    c4.metric("Promedio", fmt_value(measurements.mean(), 3))
    c5.metric("Std", fmt_value(measurements.std(), 3))

    st.info(
        "Esta pestaña ya genera el layout base tipo MSA. "
        "El cálculo Gage R&R formal por ANOVA lo agregaremos en la siguiente etapa."
    )

    col1, col2 = st.columns(2)

    with col1:
        part_mean = (
            msa_df
            .groupby("part_id", as_index=False)["measurement"]
            .mean()
            .sort_values("part_id")
        )

        fig_part = go.Figure()

        fig_part.add_trace(
            go.Scatter(
                x=part_mean["part_id"],
                y=part_mean["measurement"],
                mode="lines+markers",
                name="Promedio por parte",
            )
        )

        fig_part.update_layout(
            title="Measurement by Part",
            xaxis_title="Part",
            yaxis_title="Measurement",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_part, use_container_width=True, key="msa_measurement_by_part")

    with col2:
        fig_operator = go.Figure()

        for operator, op_df in msa_df.groupby("operator"):
            fig_operator.add_trace(
                go.Box(
                    y=op_df["measurement"],
                    name=str(operator),
                    boxmean=True,
                )
            )

        fig_operator.update_layout(
            title="Measurement by Operator",
            xaxis_title="Operator",
            yaxis_title="Measurement",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_operator, use_container_width=True, key="msa_measurement_by_operator")

    col3, col4 = st.columns(2)

    with col3:
        ranges = (
            msa_df
            .groupby(["operator", "part_id"], as_index=False)["measurement"]
            .agg(lambda x: x.max() - x.min())
            .rename(columns={"measurement": "sample_range"})
        )

        fig_r = go.Figure()

        for operator, op_df in ranges.groupby("operator"):
            fig_r.add_trace(
                go.Scatter(
                    x=op_df["part_id"],
                    y=op_df["sample_range"],
                    mode="lines+markers",
                    name=str(operator),
                )
            )

        fig_r.update_layout(
            title="R Chart by Operator",
            xaxis_title="Part",
            yaxis_title="Sample Range",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_r, use_container_width=True, key="msa_r_chart_operator")

    with col4:
        xbar = (
            msa_df
            .groupby(["operator", "part_id"], as_index=False)["measurement"]
            .mean()
            .rename(columns={"measurement": "sample_mean"})
        )

        fig_xbar = go.Figure()

        for operator, op_df in xbar.groupby("operator"):
            fig_xbar.add_trace(
                go.Scatter(
                    x=op_df["part_id"],
                    y=op_df["sample_mean"],
                    mode="lines+markers",
                    name=str(operator),
                )
            )

        fig_xbar.update_layout(
            title="Xbar Chart by Operator",
            xaxis_title="Part",
            yaxis_title="Sample Mean",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_xbar, use_container_width=True, key="msa_xbar_chart_operator")

    col5, col6 = st.columns(2)

    with col5:
        interaction = (
            msa_df
            .groupby(["operator", "part_id"], as_index=False)["measurement"]
            .mean()
            .rename(columns={"measurement": "average"})
        )

        fig_interaction = go.Figure()

        for operator, op_df in interaction.groupby("operator"):
            fig_interaction.add_trace(
                go.Scatter(
                    x=op_df["part_id"],
                    y=op_df["average"],
                    mode="lines+markers",
                    name=str(operator),
                )
            )

        fig_interaction.update_layout(
            title="Part * Operator Interaction",
            xaxis_title="Part",
            yaxis_title="Average",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_interaction, use_container_width=True, key="msa_interaction_chart")

    with col6:
        # Aproximación visual inicial de componentes.
        # No reemplaza el cálculo formal Gage R&R.
        total_var = measurements.var()

        repeat_var = (
            msa_df
            .groupby(["operator", "part_id"])["measurement"]
            .var()
            .mean()
        )

        operator_var = (
            msa_df
            .groupby("operator")["measurement"]
            .mean()
            .var()
        )

        part_var = (
            msa_df
            .groupby("part_id")["measurement"]
            .mean()
            .var()
        )

        comp_df = pd.DataFrame(
            {
                "component": ["Repeat", "Reprod", "Part-to-Part"],
                "variance": [
                    repeat_var if pd.notna(repeat_var) else 0,
                    operator_var if pd.notna(operator_var) else 0,
                    part_var if pd.notna(part_var) else 0,
                ],
            }
        )

        comp_total = comp_df["variance"].sum()

        if comp_total > 0:
            comp_df["percent"] = comp_df["variance"] / comp_total * 100
        else:
            comp_df["percent"] = 0

        fig_components = go.Figure()

        fig_components.add_trace(
            go.Bar(
                x=comp_df["component"],
                y=comp_df["percent"],
                text=comp_df["percent"].round(1).astype(str) + "%",
                textposition="auto",
                name="% Contribution",
            )
        )

        fig_components.update_layout(
            title="Components of Variation",
            xaxis_title="Component",
            yaxis_title="Percent",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(fig_components, use_container_width=True, key="msa_components_variation")

    st.markdown("### Datos del estudio MSA")

    st.dataframe(
        msa_df,
        use_container_width=True,
        hide_index=True,
    )


def render_msa_live_exploratory(clean_df, devices_for_model, selected_model):
    st.markdown("### MSA exploratorio con datos de producción")

    st.caption(
        "Este apartado usa la data real del ASG como análisis exploratorio. "
        "No sustituye un Gage R&R formal con partes, operadores y repeticiones controladas."
    )

    if clean_df.empty:
        st.info("Todavía no hay datos limpios para análisis exploratorio.")
        return

    df = clean_df.copy()

    if "running_model" in df.columns:
        df = df[df["running_model"] == selected_model]

    if df.empty:
        st.info("No hay datos para el modelo seleccionado.")
        return

    station_options = sorted(df["device_id"].dropna().unique().tolist()) if "device_id" in df.columns else []

    if not station_options:
        st.info("No hay estaciones disponibles.")
        return

    c1, c2, c3 = st.columns(3)

    with c1:
        selected_station = st.selectbox(
            "Estación",
            station_options,
            key="msa_live_station",
        )

    with c2:
        measurement_col = st.selectbox(
            "Variable",
            ["torque_actual", "angle_actual"],
            key="msa_live_measurement",
        )

    station_df = df[df["device_id"] == selected_station].copy()

    task_options = ["Todas"]

    if "task_label" in station_df.columns:
        task_options += sorted(
            [
                str(x)
                for x in station_df["task_label"].dropna().unique().tolist()
                if str(x).strip()
            ]
        )

    with c3:
        selected_task = st.selectbox(
            "Task / PSet",
            task_options,
            key="msa_live_task",
        )

    if selected_task != "Todas" and "task_label" in station_df.columns:
        station_df = station_df[station_df["task_label"] == selected_task]

    if measurement_col not in station_df.columns:
        st.warning(f"No existe la columna {measurement_col}.")
        return

    station_df[measurement_col] = pd.to_numeric(station_df[measurement_col], errors="coerce")
    station_df = station_df.dropna(subset=[measurement_col])

    if station_df.empty:
        st.info("No hay datos válidos para la selección actual.")
        return

    values = station_df[measurement_col]

    ng_count = int((station_df["result_label"] == "NG").sum()) if "result_label" in station_df.columns else 0

    m1, m2, m3, m4, m5 = st.columns(5)

    m1.metric("Muestras", len(station_df))
    m2.metric("Promedio", fmt_value(values.mean(), 3))
    m3.metric("Std", fmt_value(values.std(), 3))
    m4.metric("Rango", fmt_value(values.max() - values.min(), 3))
    m5.metric("NG", ng_count)

    col1, col2 = st.columns(2)

    with col1:
        hist_fig = go.Figure()

        hist_fig.add_trace(
            go.Histogram(
                x=values,
                nbinsx=20,
                name=measurement_col,
                hovertemplate=(
                    "Valor: %{x}<br>"
                    "Frecuencia: %{y}<br>"
                    "<extra></extra>"
                ),
            )
        )

        hist_fig.update_layout(
            title=f"Distribución | {selected_station}",
            xaxis_title=measurement_col,
            yaxis_title="Frecuencia",
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(hist_fig, use_container_width=True, key="msa_live_histogram")

    with col2:
        box_fig = go.Figure()

        group_col = "task_label" if "task_label" in station_df.columns else "device_id"

        for group, gdf in station_df.groupby(group_col):
            box_fig.add_trace(
                go.Box(
                    y=gdf[measurement_col],
                    name=str(group),
                    boxmean=True,
                )
            )

        box_fig.update_layout(
            title=f"Variación por Task / PSet | {selected_station}",
            xaxis_title="Task / PSet",
            yaxis_title=measurement_col,
            height=330,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        st.plotly_chart(box_fig, use_container_width=True, key="msa_live_boxplot")

    trend_fig = go.Figure()

    x_col = "plot_timestamp" if "plot_timestamp" in station_df.columns else "pc_timestamp"

    if x_col in station_df.columns:
        station_df = station_df.sort_values(x_col)

        trend_fig.add_trace(
            go.Scatter(
                x=station_df[x_col],
                y=station_df[measurement_col],
                mode="lines+markers",
                name=measurement_col,
            )
        )

        trend_fig.update_layout(
            title=f"Tendencia | {selected_station}",
            xaxis_title="Tiempo PC",
            yaxis_title=measurement_col,
            height=360,
            margin=dict(l=10, r=10, t=55, b=10),
        )

        trend_fig.update_xaxes(
            tickformat="%H:%M",
            nticks=8,
        )

        st.plotly_chart(trend_fig, use_container_width=True, key="msa_live_trend")


def render_msa_tab(clean_df, devices_for_model, selected_model):
    st.subheader("MSA")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Gage name", "ASG-NW2500")
    c2.metric("Modelo", selected_model)
    c3.metric("Versión", APP_VERSION)
    c4.metric("Reportado por", "Ing. Oswaldo Cantú")

    st.markdown(
        """
        Esta pestaña queda preparada para dos usos:

        1. **MSA exploratorio:** usa los datos reales de producción.
        2. **MSA formal:** permite cargar un CSV con partes, operadores y repeticiones.
        """
    )

    uploaded = st.file_uploader(
        "Cargar CSV de estudio MSA formal",
        type=["csv"],
        key="msa_csv_uploader",
    )

    if uploaded is not None:
        try:
            msa_df = pd.read_csv(uploaded)
            render_msa_uploaded_study(msa_df)
        except Exception as e:
            st.error(f"No se pudo leer el archivo MSA: {e}")
    else:
        render_msa_live_exploratory(
            clean_df=clean_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )
'''

if "def render_msa_tab" not in text:
    text = text.replace(
        "\n\n\ndef render_results_table",
        f"\n\n{msa_functions}\n\n\ndef render_results_table"
    )

# ------------------------------------------------------------
# 4. Agregar pestaña MSA
# ------------------------------------------------------------
text = text.replace(
    "tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(",
    "tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs("
)

text = text.replace(
    '''            "Tiempos de ciclo",
            "Tabla de datos",''',
    '''            "Tiempos de ciclo",
            "MSA",
            "Tabla de datos",'''
)

# ------------------------------------------------------------
# 5. Reacomodar llamadas de tabs
# ------------------------------------------------------------
old_tabs_block = r'''    with tab3:
        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            export_base_df=cycle_export_base_df,
        )

    with tab4:
        render_results_table(filtered_df)

    with tab5:
        render_config_tab()

    with tab6:
        render_about_tab()'''

new_tabs_block = r'''    with tab3:
        render_cycle_time_tab(
            cycle_base_df=cycle_base_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
            export_base_df=cycle_export_base_df,
        )

    with tab4:
        render_msa_tab(
            clean_df=clean_df,
            devices_for_model=devices_for_model,
            selected_model=selected_model,
        )

    with tab5:
        render_results_table(filtered_df)

    with tab6:
        render_config_tab()

    with tab7:
        render_about_tab()'''

text = text.replace(old_tabs_block, new_tabs_block)

# ------------------------------------------------------------
# 6. Cambiar refresh bloqueante por st_autorefresh
# ------------------------------------------------------------
old_refresh = r'''    if auto_refresh:
        time.sleep(refresh_seconds)
        st.rerun()'''

new_refresh = r'''    if auto_refresh:
        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="asg_dashboard_autorefresh",
        )'''

text = text.replace(old_refresh, new_refresh)

# ------------------------------------------------------------
# 7. Acerca del sistema: agregar nota MSA y refresh
# ------------------------------------------------------------
about_old = '''        - Logs de conexión
        - Dashboard Streamlit'''

about_new = '''        - Logs de conexión
        - Dashboard Streamlit
        - Pestaña MSA base
        - Auto-refresh no bloqueante'''

text = text.replace(about_old, about_new)

# ------------------------------------------------------------
# 8. Validaciones
# ------------------------------------------------------------
required = [
    'APP_VERSION = "v0.5"',
    "from streamlit_autorefresh import st_autorefresh",
    "def render_msa_tab",
    "def render_msa_uploaded_study",
    "def render_msa_live_exploratory",
    '"MSA"',
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

print("Parche v0.5 aplicado correctamente.")