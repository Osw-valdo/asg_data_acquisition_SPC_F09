from pathlib import Path
from datetime import datetime
import json
import time
import math
import sys
import asyncio

# Corrección para Windows + Python 3.13 + Streamlit.
# Evita errores tipo:
# _ProactorBasePipeTransport._call_connection_lost()
# ConnectionResetError: [WinError 10054]
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import pandas as pd
import plotly.graph_objects as go
import streamlit as st



from streamlit_autorefresh import st_autorefresh

from src.asg_config import get_enabled_devices, get_devices_by_model


PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

STATUS_FILE = LOG_DIR / "device_status.json"
SESSION_FILE = LOG_DIR / "production_session.json"
APP_VERSION = "v0.5.8"


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


def fmt_seconds(value):
    try:
        if pd.isna(value):
            return "—"

        value = float(value)

        if value < 60:
            return f"{value:.1f} s"

        minutes = value / 60

        if minutes < 60:
            return f"{minutes:.1f} min"

        hours = minutes / 60
        return f"{hours:.2f} h"

    except Exception:
        return "—"


def get_screws_column(selected_model):
    if selected_model == "T1XX-1":
        return "screws_per_piece_t1xx1"

    if selected_model == "T1XX-2":
        return "screws_per_piece_t1xx2"

    return "screws_per_piece"


def get_screws_from_config(config, selected_model):
    screws_col = get_screws_column(selected_model)

    value = config.get(screws_col, None)

    try:
        if pd.isna(value):
            return None

        value = int(float(value))

        if value <= 0:
            return None

        return value

    except Exception:
        return None



def get_time_window_minutes(option):
    mapping = {
        "Últimos 15 min": 15,
        "Últimos 30 min": 30,
        "Últimos 60 min": 60,
        "Todo el día": None,
    }

    return mapping.get(option, 30)


@st.cache_data(ttl=1)
def load_status():
    if not STATUS_FILE.exists():
        return {}

    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_data(ttl=1)
def load_clean_data():
    today_file = PROCESSED_DIR / f"asg_clean_master_{today_str()}.csv"

    if not today_file.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(today_file)
    except Exception:
        return pd.DataFrame()

    if "timestamp_asg" in df.columns:
        df["timestamp_asg"] = pd.to_datetime(df["timestamp_asg"], errors="coerce")

    if "pc_timestamp" in df.columns:
        df["pc_timestamp"] = pd.to_datetime(df["pc_timestamp"], errors="coerce")

    # Para graficar usamos la hora de la PC.
    # La hora del ASG se conserva solo como trazabilidad.
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

    if "tightening_status" in df.columns:
        df["result_label"] = df["tightening_status"].replace({"NOK": "NG"})
    else:
        df["result_label"] = ""

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



@st.cache_data(ttl=1)
def load_session_state():
    if not SESSION_FILE.exists():
        return {"models": {}}

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if "models" not in payload:
            payload["models"] = {}

        return payload

    except Exception:
        return {"models": {}}


def get_active_session_info(session_payload, selected_model):
    if not session_payload:
        return {}

    return session_payload.get("models", {}).get(selected_model, {}) or {}


def apply_count_scope(df, selected_model, count_scope, session_payload):
    """
    Controla si el dashboard muestra:
    - Sesión actual: datos desde que inició el conteo actual del modelo.
    - Todo el día: todos los datos del CSV diario.

    Esto permite iniciar nuevo conteo sin borrar archivos anteriores.
    """
    if df.empty:
        return df

    if count_scope != "Sesión actual":
        return df

    session_info = get_active_session_info(session_payload, selected_model)
    session_start = session_info.get("production_session_started_at", "")

    if not session_start:
        return df

    filtered = df.copy()

    time_col = "plot_timestamp" if "plot_timestamp" in filtered.columns else "pc_timestamp"

    if time_col not in filtered.columns:
        return filtered

    filtered[time_col] = pd.to_datetime(filtered[time_col], errors="coerce")
    start_dt = pd.to_datetime(session_start, errors="coerce")

    if pd.isna(start_dt):
        return filtered

    filtered = filtered[filtered[time_col] >= start_dt]

    return filtered


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


def filter_data(
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


def calculate_piece_cycles(df, devices_for_model, selected_model):
    if df.empty or devices_for_model is None or devices_for_model.empty:
        return pd.DataFrame()

    if "device_id" not in df.columns or "plot_timestamp" not in df.columns:
        return pd.DataFrame()

    config_lookup = {}

    for _, row in devices_for_model.iterrows():
        config_lookup[row["device_id"]] = row.to_dict()

    rows = []

    work_df = df.copy()
    work_df = work_df.dropna(subset=["plot_timestamp"])
    work_df = work_df.sort_values(["device_id", "plot_timestamp"])

    for device_id, station_df in work_df.groupby("device_id"):
        config = config_lookup.get(device_id, {})
        screws_per_piece = get_screws_from_config(config, selected_model)

        if not screws_per_piece:
            continue

        station_df = station_df.sort_values("plot_timestamp").reset_index(drop=True)

        station_df["screw_sequence_in_station"] = station_df.index + 1
        station_df["piece_number"] = (
            (station_df["screw_sequence_in_station"] - 1) // screws_per_piece
        ) + 1
        station_df["screw_number_in_piece"] = (
            (station_df["screw_sequence_in_station"] - 1) % screws_per_piece
        ) + 1

        for piece_number, piece_df in station_df.groupby("piece_number"):
            if len(piece_df) < screws_per_piece:
                continue

            piece_df = piece_df.sort_values("plot_timestamp")

            piece_start = piece_df["plot_timestamp"].iloc[0]
            piece_end = piece_df["plot_timestamp"].iloc[-1]
            tightening_time_sec = (piece_end - piece_start).total_seconds()

            result_values = piece_df.get("result_label", pd.Series(dtype=str)).astype(str).tolist()
            piece_result = "NG" if "NG" in result_values else "OK"

            task_values = sorted(
                {
                    str(x).strip()
                    for x in piece_df.get("task_label", pd.Series(dtype=str)).tolist()
                    if str(x).strip() and str(x).strip().lower() != "nan"
                }
            )

            rows.append(
                {
                    "running_model": selected_model,
                    "device_id": device_id,
                    "station": piece_df["station"].iloc[-1] if "station" in piece_df.columns else config.get("station", ""),
                    "module_name": piece_df["module_name"].iloc[-1] if "module_name" in piece_df.columns else config.get("module_name", ""),
                    "process": piece_df["process"].iloc[-1] if "process" in piece_df.columns else config.get("process", ""),
                    "station_type": piece_df["station_type"].iloc[-1] if "station_type" in piece_df.columns else config.get("station_type", ""),
                    "screws_per_piece": screws_per_piece,
                    "piece_number": int(piece_number),
                    "piece_start_time": piece_start,
                    "piece_end_time": piece_end,
                    "tightening_time_sec": tightening_time_sec,
                    "piece_result": piece_result,
                    "tasks_in_piece": " | ".join(task_values),
                    "first_tightening_id": piece_df["tightening_id"].iloc[0] if "tightening_id" in piece_df.columns else "",
                    "last_tightening_id": piece_df["tightening_id"].iloc[-1] if "tightening_id" in piece_df.columns else "",
                    "torque_avg": piece_df["torque_actual"].mean() if "torque_actual" in piece_df.columns else None,
                    "torque_min_actual": piece_df["torque_actual"].min() if "torque_actual" in piece_df.columns else None,
                    "torque_max_actual": piece_df["torque_actual"].max() if "torque_actual" in piece_df.columns else None,
                    "angle_avg": piece_df["angle_actual"].mean() if "angle_actual" in piece_df.columns else None,
                    "screws_ok": int((piece_df["result_label"] == "OK").sum()) if "result_label" in piece_df.columns else None,
                    "screws_ng": int((piece_df["result_label"] == "NG").sum()) if "result_label" in piece_df.columns else None,
                }
            )

    pieces_df = pd.DataFrame(rows)

    if pieces_df.empty:
        return pieces_df

    pieces_df = pieces_df.sort_values(["device_id", "piece_start_time"])

    pieces_df["cycle_time_sec"] = (
        pieces_df
        .groupby("device_id")["piece_start_time"]
        .diff()
        .dt.total_seconds()
    )

    return pieces_df


def calculate_cycle_summary(df, pieces_df, devices_for_model, selected_model):
    if devices_for_model is None or devices_for_model.empty:
        return pd.DataFrame()

    rows = []

    for _, config_row in devices_for_model.iterrows():
        config = config_row.to_dict()
        device_id = config.get("device_id", "")
        screws_per_piece = get_screws_from_config(config, selected_model)

        if not screws_per_piece:
            screws_per_piece = 0

        station_df = pd.DataFrame()

        if not df.empty and "device_id" in df.columns:
            station_df = df[df["device_id"] == device_id].copy()

        total_screws = len(station_df)

        if screws_per_piece > 0:
            complete_pieces = total_screws // screws_per_piece
            partial_screws = total_screws % screws_per_piece
        else:
            complete_pieces = 0
            partial_screws = 0

        station_pieces = pd.DataFrame()

        if pieces_df is not None and not pieces_df.empty:
            station_pieces = pieces_df[pieces_df["device_id"] == device_id].copy()

        cycle_values = pd.Series(dtype=float)
        tightening_values = pd.Series(dtype=float)

        if not station_pieces.empty:
            cycle_values = pd.to_numeric(station_pieces["cycle_time_sec"], errors="coerce").dropna()
            cycle_values = cycle_values[cycle_values > 0]

            tightening_values = pd.to_numeric(station_pieces["tightening_time_sec"], errors="coerce").dropna()
            tightening_values = tightening_values[tightening_values >= 0]

        rows.append(
            {
                "device_id": device_id,
                "station": config.get("station", ""),
                "process": config.get("process", ""),
                "module_name": config.get("module_name", ""),
                "screws_per_piece": screws_per_piece,
                "torque_results": total_screws,
                "complete_pieces": complete_pieces,
                "partial_screws": partial_screws,
                "avg_cycle_time_sec": cycle_values.mean() if not cycle_values.empty else None,
                "min_cycle_time_sec": cycle_values.min() if not cycle_values.empty else None,
                "max_cycle_time_sec": cycle_values.max() if not cycle_values.empty else None,
                "std_cycle_time_sec": cycle_values.std() if len(cycle_values) > 1 else None,
                "avg_tightening_time_sec": tightening_values.mean() if not tightening_values.empty else None,
            }
        )

    summary_df = pd.DataFrame(rows)

    if summary_df.empty:
        return summary_df

    for col in [
        "avg_cycle_time_sec",
        "min_cycle_time_sec",
        "max_cycle_time_sec",
        "std_cycle_time_sec",
        "avg_tightening_time_sec",
    ]:
        summary_df[f"{col}_fmt"] = summary_df[col].apply(fmt_seconds)

    return summary_df


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
        width="stretch",
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
        tickformat="%H:%M",
        nticks=6,
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
        tickformat="%H:%M",
        nticks=6,
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
                screws_per_piece = get_screws_from_config(config, config.get("running_model", ""))

                if not screws_per_piece:
                    screws_per_piece = get_screws_from_config(config, "T1XX-1") or get_screws_from_config(config, "T1XX-2")

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
                            width="stretch",
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
                                width="stretch",
                                key=f"angle_grid_{device_id}",
                            )


def render_cycle_time_tab(cycle_base_df, devices_for_model, selected_model, export_base_df=None):
    st.subheader("Tiempos de ciclo y piezas producidas")

    if cycle_base_df.empty:
        st.info("Todavía no hay datos para calcular piezas o tiempos de ciclo.")
        return

    pieces_df = calculate_piece_cycles(
        cycle_base_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )

    summary_df = calculate_cycle_summary(
        cycle_base_df,
        pieces_df=pieces_df,
        devices_for_model=devices_for_model,
        selected_model=selected_model,
    )


    if summary_df.empty:
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

    valid_cycle_values = pd.Series(dtype=float)

    if not pieces_df.empty and "cycle_time_sec" in pieces_df.columns:
        valid_cycle_values = pd.to_numeric(pieces_df["cycle_time_sec"], errors="coerce").dropna()
        valid_cycle_values = valid_cycle_values[valid_cycle_values > 0]

    valid_tightening_values = pd.Series(dtype=float)

    if not pieces_df.empty and "tightening_time_sec" in pieces_df.columns:
        valid_tightening_values = pd.to_numeric(pieces_df["tightening_time_sec"], errors="coerce").dropna()
        valid_tightening_values = valid_tightening_values[valid_tightening_values >= 0]

    total_torque_results = int(summary_df["torque_results"].sum())
    total_complete_pieces = int(summary_df["complete_pieces"].sum())
    total_partial_screws = int(summary_df["partial_screws"].sum())

    avg_cycle = valid_cycle_values.mean() if not valid_cycle_values.empty else None
    avg_tightening = valid_tightening_values.mean() if not valid_tightening_values.empty else None

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Resultados torque", total_torque_results)
    c2.metric("Piezas completas", total_complete_pieces)
    c3.metric("Tornillos en proceso", total_partial_screws)
    c4.metric("Tiempo ciclo promedio", fmt_seconds(avg_cycle))

    c5, c6, c7, c8 = st.columns(4)

    c5.metric("Tiempo atornillado prom.", fmt_seconds(avg_tightening))
    c6.metric("Mín. ciclo", fmt_seconds(valid_cycle_values.min() if not valid_cycle_values.empty else None))
    c7.metric("Máx. ciclo", fmt_seconds(valid_cycle_values.max() if not valid_cycle_values.empty else None))
    c8.metric("Std ciclo", fmt_seconds(valid_cycle_values.std() if len(valid_cycle_values) > 1 else None))

    st.divider()

    st.markdown("### Piezas completas por estación")

    bar_fig = go.Figure()

    bar_fig.add_trace(
        go.Bar(
            x=summary_df["device_id"],
            y=summary_df["complete_pieces"],
            text=summary_df["complete_pieces"],
            textposition="auto",
            customdata=safe_customdata(
                summary_df,
                [
                    "station",
                    "process",
                    "screws_per_piece",
                    "torque_results",
                    "partial_screws",
                ],
            ),
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Estación: %{customdata[0]}<br>"
                "Proceso: %{customdata[1]}<br>"
                "Tornillos por pieza: %{customdata[2]}<br>"
                "Resultados torque: %{customdata[3]}<br>"
                "Piezas completas: %{y}<br>"
                "Tornillos en proceso: %{customdata[4]}<br>"
                "<extra></extra>"
            ),
        )
    )

    bar_fig.update_layout(
        xaxis_title="Estación",
        yaxis_title="Piezas completas",
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
    )

    st.plotly_chart(
        bar_fig,
        width="stretch",
        key="cycle_pieces_bar",
    )

    st.divider()

    st.markdown("### Histograma de tiempos de ciclo")

    if pieces_df.empty or valid_cycle_values.empty:
        st.info("Aún no hay suficientes piezas completas para calcular tiempo de ciclo.")
    else:
        station_options = ["Todas"] + summary_df["device_id"].tolist()

        selected_hist_station = st.selectbox(
            "Estación para histograma",
            station_options,
            index=0,
        )

        hist_df = pieces_df.copy()

        if selected_hist_station != "Todas":
            hist_df = hist_df[hist_df["device_id"] == selected_hist_station]

        hist_df["cycle_time_sec"] = pd.to_numeric(hist_df["cycle_time_sec"], errors="coerce")
        hist_df = hist_df.dropna(subset=["cycle_time_sec"])
        hist_df = hist_df[hist_df["cycle_time_sec"] > 0]

        if hist_df.empty:
            st.info("No hay tiempos de ciclo válidos para esta estación.")
        else:
            hist_fig = go.Figure()

            hist_fig.add_trace(
                go.Histogram(
                    x=hist_df["cycle_time_sec"],
                    nbinsx=20,
                    name="Tiempo de ciclo",
                    hovertemplate=(
                        "Tiempo de ciclo: %{x:.1f} s<br>"
                        "Frecuencia: %{y}<br>"
                        "<extra></extra>"
                    ),
                )
            )

            title = "Distribución de tiempos de ciclo"

            if selected_hist_station != "Todas":
                title = f"Distribución de tiempos de ciclo | {selected_hist_station}"

            hist_fig.update_layout(
                title=title,
                xaxis_title="Tiempo de ciclo (s)",
                yaxis_title="Frecuencia",
                height=420,
                bargap=0.05,
                margin=dict(l=10, r=10, t=55, b=10),
            )

            st.plotly_chart(
                hist_fig,
                width="stretch",
                key="cycle_time_histogram",
            )

    st.divider()

    st.markdown("### Resumen por estación")

    summary_display_cols = [
        "device_id",
        "process",
        "station",
        "screws_per_piece",
        "torque_results",
        "complete_pieces",
        "partial_screws",
        "avg_cycle_time_sec_fmt",
        "min_cycle_time_sec_fmt",
        "max_cycle_time_sec_fmt",
        "std_cycle_time_sec_fmt",
        "avg_tightening_time_sec_fmt",
    ]

    summary_available_cols = [col for col in summary_display_cols if col in summary_df.columns]

    st.dataframe(
        summary_df[summary_available_cols],
        width="stretch",
        hide_index=True,
    )

    st.markdown("### Piezas calculadas")

    if pieces_df.empty:
        st.info("Aún no hay piezas completas calculadas.")
    else:
        pieces_display = pieces_df.copy()

        pieces_display["cycle_time_fmt"] = pieces_display["cycle_time_sec"].apply(fmt_seconds)
        pieces_display["tightening_time_fmt"] = pieces_display["tightening_time_sec"].apply(fmt_seconds)

        pieces_cols = [
            "running_model",
            "device_id",
            "process",
            "station",
            "screws_per_piece",
            "piece_number",
            "piece_start_time",
            "piece_end_time",
            "cycle_time_fmt",
            "tightening_time_fmt",
            "piece_result",
            "tasks_in_piece",
            "torque_avg",
            "torque_min_actual",
            "torque_max_actual",
            "screws_ok",
            "screws_ng",
        ]

        pieces_available_cols = [col for col in pieces_cols if col in pieces_display.columns]

        pieces_display = pieces_display.sort_values(
            ["device_id", "piece_start_time"],
            ascending=[True, False],
        )

        st.dataframe(
            pieces_display[pieces_available_cols].head(500),
            width="stretch",
            hide_index=True,
        )



def calculate_torque_capability(df, minimum_samples=30):
    """
    Calcula Cp y Cpk por estación, Task/PSet y límites de torque.

    Importante:
    - Usa torque_actual como variable de proceso.
    - No mezcla grupos con PSet o límites distintos.
    - La desviación estándar es muestral (ddof=1).
    """
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()

    required_cols = [
        "device_id",
        "torque_actual",
        "torque_min",
        "torque_max",
    ]

    missing = [col for col in required_cols if col not in df.columns]

    if missing:
        return pd.DataFrame(), pd.DataFrame()

    work_df = df.copy()

    optional_defaults = {
        "running_model": "",
        "station": "",
        "process": "",
        "task_label": "",
        "parameter_set_id": None,
        "torque_target": None,
        "torque_unit": "",
    }

    for col, default_value in optional_defaults.items():
        if col not in work_df.columns:
            work_df[col] = default_value

    numeric_cols = [
        "torque_actual",
        "torque_min",
        "torque_target",
        "torque_max",
        "parameter_set_id",
    ]

    for col in numeric_cols:
        work_df[col] = pd.to_numeric(work_df[col], errors="coerce")

    work_df = work_df.dropna(
        subset=[
            "device_id",
            "torque_actual",
            "torque_min",
            "torque_max",
        ]
    )

    work_df = work_df[
        work_df["torque_max"] > work_df["torque_min"]
    ].copy()

    if work_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    work_df["task_label"] = (
        work_df["task_label"]
        .fillna("")
        .astype(str)
        .replace({"nan": "", "None": ""})
    )

    def format_pset(value):
        try:
            if pd.isna(value):
                return "Sin PSet"
            return f"PSet {int(float(value)):03d}"
        except Exception:
            return f"PSet {value}"

    work_df["pset_label"] = work_df["parameter_set_id"].apply(format_pset)

    work_df["capability_group"] = work_df.apply(
        lambda row: (
            f"{row.get('device_id', '')} | "
            f"{row.get('task_label', '') or row.get('pset_label', '')} | "
            f"LSL {fmt_value(row.get('torque_min'), 3)} | "
            f"USL {fmt_value(row.get('torque_max'), 3)}"
        ),
        axis=1,
    )

    group_cols = [
        "running_model",
        "device_id",
        "station",
        "process",
        "task_label",
        "pset_label",
        "parameter_set_id",
        "torque_min",
        "torque_target",
        "torque_max",
        "torque_unit",
        "capability_group",
    ]

    rows = []

    for group_values, group_df in work_df.groupby(
        group_cols,
        dropna=False,
        sort=True,
    ):
        group_info = dict(zip(group_cols, group_values))

        values = pd.to_numeric(
            group_df["torque_actual"],
            errors="coerce",
        ).dropna()

        sample_count = len(values)

        if sample_count == 0:
            continue

        mean_value = values.mean()
        std_value = values.std(ddof=1) if sample_count > 1 else None

        lsl = float(group_info["torque_min"])
        usl = float(group_info["torque_max"])

        cp = None
        cpl = None
        cpu = None
        cpk = None

        if (
            std_value is not None
            and pd.notna(std_value)
            and float(std_value) > 0
        ):
            cp = (usl - lsl) / (6 * std_value)
            cpl = (mean_value - lsl) / (3 * std_value)
            cpu = (usl - mean_value) / (3 * std_value)
            cpk = min(cpl, cpu)

        out_of_spec = int(
            ((values < lsl) | (values > usl)).sum()
        )

        out_of_spec_pct = (
            out_of_spec / sample_count * 100
            if sample_count
            else 0
        )

        if sample_count < minimum_samples:
            classification = "Muestra insuficiente"
        elif cpk is None or pd.isna(cpk):
            classification = "No calculable"
        elif cpk >= 1.33:
            classification = "Capaz"
        elif cpk >= 1.00:
            classification = "Marginal"
        else:
            classification = "No capaz"

        rows.append(
            {
                **group_info,
                "samples": sample_count,
                "mean": mean_value,
                "std": std_value,
                "min_actual": values.min(),
                "max_actual": values.max(),
                "cp": cp,
                "cpl": cpl,
                "cpu": cpu,
                "cpk": cpk,
                "out_of_spec": out_of_spec,
                "out_of_spec_pct": out_of_spec_pct,
                "classification": classification,
            }
        )

    summary_df = pd.DataFrame(rows)

    if summary_df.empty:
        return summary_df, work_df

    summary_df = summary_df.sort_values(
        ["device_id", "parameter_set_id", "torque_min", "torque_max"],
        na_position="last",
    )

    return summary_df, work_df



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

        st.plotly_chart(fig_part, width="stretch", key="msa_measurement_by_part")

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

        st.plotly_chart(fig_operator, width="stretch", key="msa_measurement_by_operator")

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

        st.plotly_chart(fig_r, width="stretch", key="msa_r_chart_operator")

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

        st.plotly_chart(fig_xbar, width="stretch", key="msa_xbar_chart_operator")

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

        st.plotly_chart(fig_interaction, width="stretch", key="msa_interaction_chart")

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

        st.plotly_chart(fig_components, width="stretch", key="msa_components_variation")

    st.markdown("### Datos del estudio MSA")

    st.dataframe(
        msa_df,
        width="stretch",
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

        st.plotly_chart(hist_fig, width="stretch", key="msa_live_histogram")

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

        st.plotly_chart(box_fig, width="stretch", key="msa_live_boxplot")

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

        st.plotly_chart(trend_fig, width="stretch", key="msa_live_trend")


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

    table_df = df[available_cols].tail(200)

    if "plot_timestamp" in table_df.columns:
        table_df = table_df.sort_values("plot_timestamp", ascending=False)
    else:
        table_df = table_df.sort_index(ascending=False)

    st.dataframe(
        table_df,
        width="stretch",
        hide_index=True,
    )


def render_config_tab():
    st.subheader("Equipos configurados")

    try:
        config_df = get_enabled_devices()
        st.dataframe(config_df, width="stretch", hide_index=True)
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

        **Cálculo de piezas y tiempos de ciclo:**

        - Cada estación tiene configurada su cantidad de tornillos por pieza.
        - Las piezas completas se calculan agrupando resultados de torque.
        - El tiempo de ciclo se calcula entre el inicio de una pieza y el inicio de la siguiente.
        - El tiempo de atornillado se calcula entre el primer y último tornillo de la misma pieza.
        - Los cálculos se pueden guardar en CSV desde la pestaña Tiempos de ciclo.

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
        - Pestaña MSA base
        - Capacidad con campana normal
        - Pestaña de capacidad Cp/Cpk por estación y PSet
        - Auto-refresh no bloqueante
        - Sesiones de conteo de producción por modelo
        - Guardado manual optimizado de tiempos de ciclo
        - Tabla de datos en vivo
        - Renderizado por vista activa para evitar recálculo innecesario
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
        st.header("Vista")

        selected_page = st.radio(
            "Selecciona vista",
            [
                "Vista general",
                "Dashboard por estación",
                "Tiempos de ciclo",
                "Capacidad",
                "MSA",
                "Tabla de datos",
                "Configuración",
                "Acerca del sistema",
            ],
            index=0,
        )

        st.divider()

        st.header("Controles")

        selected_model = st.radio(
            "Modelo de corrida",
            ["T1XX-1", "T1XX-2"],
            index=0,
        )

        count_scope = st.radio(
            "Conteo de piezas",
            ["Sesión actual", "Todo el día"],
            index=0,
        )

        st.caption(
            "La sesión actual se define al iniciar el colector."
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
        refresh_seconds = st.slider("Refresh segundos", 1, 30, 1)

    status_payload = load_status()
    status_df = status_to_dataframe(status_payload)

    clean_df = load_clean_data()

    session_payload = load_session_state()
    active_session_info = get_active_session_info(session_payload, selected_model)

    clean_df_for_view = apply_count_scope(
        df=clean_df,
        selected_model=selected_model,
        count_scope=count_scope,
        session_payload=session_payload,
    )

    if count_scope == "Sesión actual":
        session_id = active_session_info.get("production_session_id", "")
        session_start = active_session_info.get("production_session_started_at", "")

        if session_id:
            st.caption(f"Conteo activo: {session_id} | Inicio: {session_start}")
        else:
            st.caption("No hay sesión activa registrada. Inicia el colector para crear una sesión.")

    filtered_df = filter_data(
        clean_df_for_view,
        selected_model=selected_model,
        selected_devices=selected_devices,
        selected_result=selected_result,
        last_n=last_n,
        time_window_minutes=time_window_minutes,
    )

    live_pages = [
        "Vista general",
        "Dashboard por estación",
        "Tabla de datos",
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
        )

    elif selected_page == "Capacidad":
        capability_base_df = filter_data(
            clean_df_for_view,
            selected_model=selected_model,
            selected_devices=selected_devices,
            selected_result="Todos",
            last_n=last_n,
            time_window_minutes=time_window_minutes,
        )

        render_capability_tab(
            capability_df=capability_base_df,
            minimum_samples=30,
        )

    elif selected_page == "MSA":
        render_msa_tab(
            clean_df=clean_df_for_view,
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
            "El auto-refresh solo queda activo en Vista general, Dashboard por estación y Tabla de datos."
        )


if __name__ == "__main__":
    main()