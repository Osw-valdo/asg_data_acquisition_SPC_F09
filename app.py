from pathlib import Path
from datetime import datetime
import json
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.asg_config import get_enabled_devices, get_devices_by_model


PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

STATUS_FILE = LOG_DIR / "device_status.json"
APP_VERSION = "v0.3.1"


st.set_page_config(
    page_title="ASG SPC F09",
    page_icon="🔩",
    layout="wide",
)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def fmt_value(value, decimals=2):
    try:
        if pd.isna(value):
            return ""
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


@st.cache_data(ttl=2)
def load_status():
    if not STATUS_FILE.exists():
        return {}

    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_data(ttl=2)
def load_clean_data():
    today_file = PROCESSED_DIR / f"asg_clean_master_{today_str()}.csv"

    if not today_file.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(today_file)
    except Exception:
        return pd.DataFrame()

    # Convertimos ambos tiempos, pero para graficar usaremos pc_timestamp.
    # timestamp_asg se conserva como trazabilidad.
    if "timestamp_asg" in df.columns:
        df["timestamp_asg"] = pd.to_datetime(df["timestamp_asg"], errors="coerce")

    if "pc_timestamp" in df.columns:
        df["pc_timestamp"] = pd.to_datetime(df["pc_timestamp"], errors="coerce")

    # Timestamp oficial para gráficas:
    # 1) pc_timestamp como principal
    # 2) timestamp_asg solo como respaldo si pc_timestamp viene vacío
    if "pc_timestamp" in df.columns:
        df["plot_timestamp"] = df["pc_timestamp"]
    elif "timestamp_asg" in df.columns:
        df["plot_timestamp"] = df["timestamp_asg"]
    else:
        df["plot_timestamp"] = pd.NaT

    if "timestamp_asg" in df.columns:
        df["plot_timestamp"] = df["plot_timestamp"].fillna(df["timestamp_asg"])

    numeric_cols = [
        "torque_min",
        "torque_max",
        "torque_target",
        "torque_actual",
        "angle_min",
        "angle_max",
        "angle_target",
        "angle_actual",
        "parameter_set_id",
        "batch_counter",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Columna visual:
    # El ASG puede mandar NOK, pero en piso lo mostraremos como NG.
    if "tightening_status" in df.columns:
        df["result_label"] = df["tightening_status"].replace({"NOK": "NG"})
    else:
        df["result_label"] = ""

    # Columna visual de tarea:
    # Se forma usando job_id y parameter_set_id.
    def build_task_label(row):
        job = str(row.get("job_id", "")).strip()
        pset = row.get("parameter_set_id", "")

        if job.lower() in ["nan", "none"]:
            job = ""

        try:
            if pd.notna(pset):
                pset = str(int(float(pset))).zfill(3)
            else:
                pset = ""
        except Exception:
            pset = str(pset).strip()

        parts = []

        if job:
            parts.append(f"Job {job}")

        if pset:
            parts.append(f"PSet {pset}")

        return " | ".join(parts)

    df["task_label"] = df.apply(build_task_label, axis=1)

    return df


def status_to_dataframe(status_payload):
    devices = status_payload.get("devices", {})

    if not devices:
        return pd.DataFrame()

    rows = []

    for _, info in devices.items():
        rows.append(info)

    return pd.DataFrame(rows)


def safe_customdata(df, columns):
    temp = df.copy()

    for col in columns:
        if col not in temp.columns:
            temp[col] = ""

    return temp[columns].fillna("").values


def filter_data(df, selected_model, selected_devices, selected_result, last_n):
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
        filtered = filtered.sort_values(sort_col)

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


def render_device_status(status_df):
    st.subheader("Estado de conexión de estaciones")

    if status_df.empty:
        st.warning("Todavía no existe `logs/device_status.json`. Ejecuta primero el colector.")
        return

    connected_count = int(status_df["connected"].sum()) if "connected" in status_df.columns else 0
    total_count = len(status_df)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Equipos monitoreados", total_count)
    col2.metric("Conectados", connected_count)
    col3.metric("Desconectados", total_count - connected_count)

    total_results = 0

    if "total_results" in status_df.columns:
        total_results = pd.to_numeric(status_df["total_results"], errors="coerce").fillna(0).sum()

    col4.metric("Resultados recibidos", int(total_results))

    display_cols = [
        "device_id",
        "station",
        "module_name",
        "ip",
        "running_model",
        "process",
        "station_type",
        "connected",
        "state",
        "last_mid",
        "last_result_pc_timestamp",
        "total_results",
        "last_error",
    ]

    available_cols = [col for col in display_cols if col in status_df.columns]

    st.dataframe(
        status_df[available_cols],
        use_container_width=True,
        hide_index=True,
    )


def render_summary_metrics(df):
    st.subheader("Resumen de resultados")

    if df.empty:
        st.info("Todavía no hay resultados cocinados para mostrar.")
        return

    total = len(df)

    ok_count = 0
    ng_count = 0

    if "result_label" in df.columns:
        ok_count = int((df["result_label"] == "OK").sum())
        ng_count = int((df["result_label"] == "NG").sum())

    ok_pct = (ok_count / total * 100) if total else 0

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Resultados filtrados", total)
    col2.metric("OK", ok_count)
    col3.metric("NG", ng_count)
    col4.metric("% OK", f"{ok_pct:.1f}%")

    last_row = df.tail(1)

    if not last_row.empty:
        row = last_row.iloc[0]

        st.markdown("### Último resultado")

        c1, c2, c3, c4, c5, c6 = st.columns(6)

        c1.metric("ASG", row.get("device_id", ""))
        c2.metric("Proceso", row.get("process", ""))
        c3.metric("Torque", fmt_value(row.get("torque_actual", ""), 2))
        c4.metric("Ángulo", fmt_value(row.get("angle_actual", ""), 0))
        c5.metric("Resultado", row.get("result_label", ""))
        c6.metric("Task", row.get("task_label", ""))


def make_station_torque_chart(
    df,
    device_id,
    show_markers=True,
    show_limits=True,
    auto_scale=True,
    chart_height=330,
):
    fig = go.Figure()

    if df.empty or "device_id" not in df.columns:
        return fig

    station_df = df[df["device_id"] == device_id].copy()

    if station_df.empty:
        return fig

    x_col = "plot_timestamp" if "plot_timestamp" in station_df.columns else "pc_timestamp"
    mode = "lines+markers" if show_markers else "lines"

    process = station_df["process"].iloc[-1] if "process" in station_df.columns else ""

    fig.add_trace(
        go.Scatter(
            x=station_df[x_col],
            y=station_df["torque_actual"],
            mode=mode,
            name="Torque actual",
            customdata=safe_customdata(
                station_df,
                [
                    "result_label",
                    "torque_status",
                    "angle_status",
                    "tightening_id",
                    "parameter_set_id",
                    "task_label",
                    "timestamp_asg",
                    "pc_timestamp",
                ],
            ),
            hovertemplate=(
                "<b>Torque actual</b><br>"
                "Hora PC: %{customdata[7]}<br>"
                "Hora ASG: %{customdata[6]}<br>"
                "Torque: %{y}<br>"
                "Resultado: %{customdata[0]}<br>"
                "Torque status: %{customdata[1]}<br>"
                "Angle status: %{customdata[2]}<br>"
                "Tightening ID: %{customdata[3]}<br>"
                "Parameter Set: %{customdata[4]}<br>"
                "Task: %{customdata[5]}<br>"
                "<extra></extra>"
            ),
        )
    )

    if show_limits:
        for col, label in [
            ("torque_min", "Torque min"),
            ("torque_target", "Torque target"),
            ("torque_max", "Torque max"),
        ]:
            if col in station_df.columns and station_df[col].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=station_df[x_col],
                        y=station_df[col],
                        mode="lines",
                        name=label,
                        line=dict(dash="dash"),
                    )
                )

    fig.update_layout(
        title=f"{device_id} | {process} | Torque",
        xaxis_title="Tiempo PC",
        yaxis_title="Torque",
        hovermode="x unified",
        legend_title="Serie",
        height=chart_height,
        margin=dict(l=10, r=10, t=55, b=10),
    )

    fig.update_xaxes(
        tickformat="%H:%M<br>%b %d, %Y",
    )

    if auto_scale:
        values = []

        for col in ["torque_actual", "torque_min", "torque_target", "torque_max"]:
            if col in station_df.columns:
                values.extend(station_df[col].dropna().tolist())

        if values:
            y_min = min(values)
            y_max = max(values)
            padding = max((y_max - y_min) * 0.20, 1)
            fig.update_yaxes(range=[y_min - padding, y_max + padding])

    return fig


def make_station_angle_chart(
    df,
    device_id,
    show_markers=True,
    auto_scale=True,
    chart_height=300,
):
    fig = go.Figure()

    if df.empty or "device_id" not in df.columns:
        return fig

    station_df = df[df["device_id"] == device_id].copy()

    if station_df.empty:
        return fig

    x_col = "plot_timestamp" if "plot_timestamp" in station_df.columns else "pc_timestamp"
    mode = "lines+markers" if show_markers else "lines"

    process = station_df["process"].iloc[-1] if "process" in station_df.columns else ""

    fig.add_trace(
        go.Scatter(
            x=station_df[x_col],
            y=station_df["angle_actual"],
            mode=mode,
            name="Ángulo actual",
            customdata=safe_customdata(
                station_df,
                [
                    "result_label",
                    "angle_status",
                    "tightening_id",
                    "parameter_set_id",
                    "task_label",
                    "timestamp_asg",
                    "pc_timestamp",
                ],
            ),
            hovertemplate=(
                "<b>Ángulo actual</b><br>"
                "Hora PC: %{customdata[6]}<br>"
                "Hora ASG: %{customdata[5]}<br>"
                "Ángulo: %{y}<br>"
                "Resultado: %{customdata[0]}<br>"
                "Angle status: %{customdata[1]}<br>"
                "Tightening ID: %{customdata[2]}<br>"
                "Parameter Set: %{customdata[3]}<br>"
                "Task: %{customdata[4]}<br>"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=f"{device_id} | {process} | Ángulo",
        xaxis_title="Tiempo PC",
        yaxis_title="Ángulo",
        hovermode="x unified",
        legend_title="Serie",
        height=chart_height,
        margin=dict(l=10, r=10, t=55, b=10),
    )

    fig.update_xaxes(
        tickformat="%H:%M<br>%b %d, %Y",
    )

    if auto_scale and "angle_actual" in station_df.columns:
        values = station_df["angle_actual"].dropna().tolist()

        if values:
            y_min = min(values)
            y_max = max(values)
            padding = max((y_max - y_min) * 0.20, 5)
            fig.update_yaxes(range=[y_min - padding, y_max + padding])

    return fig


def render_station_dashboard_grid(
    df,
    selected_devices,
    devices_for_model,
    show_markers=True,
    show_limits=True,
    auto_scale=True,
    columns_per_row=2,
    show_angle_chart=False,
    chart_height=330,
):
    st.subheader("Dashboard visual por estación")

    if not selected_devices:
        st.info("Selecciona al menos una estación en el panel lateral.")
        return

    columns_per_row = max(1, min(columns_per_row, 4))

    config_lookup = {}

    if devices_for_model is not None and not devices_for_model.empty:
        for _, row in devices_for_model.iterrows():
            config_lookup[row["device_id"]] = row.to_dict()

    for i in range(0, len(selected_devices), columns_per_row):
        row_devices = selected_devices[i:i + columns_per_row]
        cols = st.columns(columns_per_row)

        for col, device_id in zip(cols, row_devices):
            with col:
                station_df = pd.DataFrame()

                if not df.empty and "device_id" in df.columns:
                    station_df = df[df["device_id"] == device_id].copy()

                config = config_lookup.get(device_id, {})

                if not station_df.empty:
                    latest = station_df.tail(1).iloc[0]

                    station_name = latest.get("station", config.get("station", ""))
                    module_name = latest.get("module_name", config.get("module_name", ""))
                    process = latest.get("process", config.get("process", ""))
                    torque_actual = fmt_value(latest.get("torque_actual", ""), 2)
                    angle_actual = fmt_value(latest.get("angle_actual", ""), 0)
                    result = latest.get("result_label", "")
                    task_label = latest.get("task_label", "")
                    torque_unit = latest.get("torque_unit", config.get("torque_unit", ""))
                    results_count = len(station_df)
                else:
                    station_name = config.get("station", "")
                    module_name = config.get("module_name", "")
                    process = config.get("process", "")
                    torque_actual = "Sin datos"
                    angle_actual = "Sin datos"
                    result = "Sin datos"
                    task_label = "Sin datos"
                    torque_unit = config.get("torque_unit", "")
                    results_count = 0

                with st.container(border=True):
                    st.markdown(f"### {device_id} | {process}")
                    st.caption(f"{station_name} | Módulo: {module_name}")

                    m1, m2, m3, m4 = st.columns(4)

                    m1.metric("Torque", f"{torque_actual} {torque_unit}")
                    m2.metric("Ángulo", angle_actual)
                    m3.metric("Resultado", result)
                    m4.metric("Ciclos", results_count)

                    st.caption(f"Task actual: {task_label}")

                    if station_df.empty:
                        st.info("Sin resultados para esta estación en la selección actual.")
                    else:
                        torque_fig = make_station_torque_chart(
                            df,
                            device_id=device_id,
                            show_markers=show_markers,
                            show_limits=show_limits,
                            auto_scale=auto_scale,
                            chart_height=chart_height,
                        )

                        st.plotly_chart(
                            torque_fig,
                            use_container_width=True,
                            key=f"torque_grid_{device_id}",
                        )

                        if show_angle_chart:
                            angle_fig = make_station_angle_chart(
                                df,
                                device_id=device_id,
                                show_markers=show_markers,
                                auto_scale=auto_scale,
                                chart_height=chart_height,
                            )

                            st.plotly_chart(
                                angle_fig,
                                use_container_width=True,
                                key=f"angle_grid_{device_id}",
                            )


def render_results_table(df):
    st.subheader("Últimos resultados")

    if df.empty:
        st.info("No hay datos para la selección actual.")
        return

    display_cols = [
        "plot_timestamp",
        "timestamp_asg",
        "pc_timestamp",
        "running_model",
        "device_id",
        "station",
        "module_name",
        "process",
        "station_type",
        "job_id",
        "parameter_set_id",
        "task_label",
        "torque_min",
        "torque_target",
        "torque_max",
        "torque_actual",
        "angle_min",
        "angle_target",
        "angle_max",
        "angle_actual",
        "tightening_status",
        "result_label",
        "torque_status",
        "angle_status",
        "tightening_id",
    ]

    available_cols = [col for col in display_cols if col in df.columns]

    table_df = df[available_cols].tail(200).sort_index(ascending=False)

    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
    )


def render_config_tab():
    st.subheader("Equipos configurados")

    try:
        config_df = get_enabled_devices()
        st.dataframe(config_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"No se pudo leer config/asg_devices.csv: {e}")

    st.subheader("Archivos usados")

    st.code(
        f"""
Status JSON:
{STATUS_FILE}

CSV master de hoy:
{PROCESSED_DIR / f"asg_clean_master_{today_str()}.csv"}

Carpeta de datos:
{DATA_DIR}
        """.strip()
    )


def render_about_tab():
    st.subheader("Acerca del sistema")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Proyecto", "ASG SPC F09")
    c2.metric("Versión", APP_VERSION)
    c3.metric("Línea", "F09")
    c4.metric("Autor", "Ing. Oswaldo Cantú Casillas")

    st.markdown(
        """
        Este sistema permite adquirir, procesar, visualizar y almacenar datos de torque y ángulo
        desde módulos ASG-NW2500 de la línea F09.

        **Objetivo principal:** generar trazabilidad automática de resultados de atornillado
        por estación, proceso, modelo de corrida e IP.

        **Criterio de tiempo usado en gráficas:**

        - La gráfica usa **pc_timestamp** como eje de tiempo principal.
        - La hora interna del ASG se conserva como **timestamp_asg** en tabla y hover.
        - Esto evita diferencias cuando una estación ASG tiene una hora interna distinta.

        **Modelos soportados:**

        - T1XX-1
        - T1XX-2

        **Salidas disponibles:**

        - CSV RAW
        - CSV procesado
        - SQLite master
        - SQLite por estación
        - Logs de conexión
        - Dashboard Streamlit
        """
    )

    st.markdown(
        """
        **Autor:** Ing. Oswaldo Cantú Casillas  
        **Área:** Ingeniería / Soporte a producción  
        **Línea:** F09
        """
    )

    st.info(
        "Proyecto desarrollado como herramienta interna para soporte de producción, "
        "calidad, mantenimiento e ingeniería."
    )


def main():
    st.title("🔩 ASG Data Acquisition SPC F09")
    st.caption("Dashboard multi-IP para ASG-NW2500 / Open Protocol")

    with st.sidebar:
        st.header("Controles")

        selected_model = st.radio(
            "Modelo de corrida",
            ["T1XX-1", "T1XX-2"],
            index=0,
        )

        try:
            devices_for_model = get_devices_by_model(selected_model)
        except Exception as e:
            st.error(f"Error leyendo configuración: {e}")
            devices_for_model = pd.DataFrame()

        device_options = []

        if not devices_for_model.empty:
            device_options = devices_for_model["device_id"].tolist()

        selected_devices = st.multiselect(
            "ASG / estaciones",
            device_options,
            default=device_options,
        )

        selected_result = st.selectbox(
            "Resultado",
            ["Todos", "OK", "NG"],
        )

        last_n = st.slider(
            "Últimos N resultados por estación",
            min_value=10,
            max_value=1000,
            value=200,
            step=10,
        )

        st.divider()
        st.markdown("### Gráficas")

        show_markers = st.checkbox("Mostrar marcadores", value=True)
        show_limits = st.checkbox("Mostrar límites torque", value=True)
        auto_scale = st.checkbox("Auto escala", value=True)

        station_chart_columns = st.slider(
            "Columnas dashboard estaciones",
            min_value=1,
            max_value=4,
            value=2,
            step=1,
        )

        station_chart_height = st.slider(
            "Altura gráfica estación",
            min_value=250,
            max_value=600,
            value=330,
            step=50,
        )

        show_angle_station_grid = st.checkbox(
            "Mostrar gráfica de ángulo por estación",
            value=False,
        )

        st.divider()

        auto_refresh = st.checkbox("Auto refresh", value=True)
        refresh_seconds = st.slider("Refresh segundos", 2, 30, 5)

    status_payload = load_status()
    status_df = status_to_dataframe(status_payload)

    clean_df = load_clean_data()

    filtered_df = filter_data(
        clean_df,
        selected_model=selected_model,
        selected_devices=selected_devices,
        selected_result=selected_result,
        last_n=last_n,
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Vista general",
            "Dashboard por estación",
            "Tabla de datos",
            "Configuración",
            "Acerca del sistema",
        ]
    )

    with tab1:
        render_device_status(status_df)
        st.divider()
        render_summary_metrics(filtered_df)

    with tab2:
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

    with tab3:
        render_results_table(filtered_df)

    with tab4:
        render_config_tab()

    with tab5:
        render_about_tab()

    if auto_refresh:
        time.sleep(refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    main()