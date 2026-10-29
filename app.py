from pathlib import Path
from datetime import datetime, timedelta
import base64
import html
import json
from types import SimpleNamespace
import os
import io
import threading
import time
import math
import urllib.error
import urllib.parse
import urllib.request
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




from src.asg_config import get_enabled_devices, get_devices_by_model
from src import auth


PROJECT_ROOT = Path(__file__).resolve().parent
GENERIC_LOGO_PATH = PROJECT_ROOT / "assets" / "brand" / "logo_f09.png"
COMPANY_LOGO_PATH = PROJECT_ROOT / "assets" / "brand" / "logo_nsna.png"
LOGO_PATH = (
    COMPANY_LOGO_PATH
    if COMPANY_LOGO_PATH.exists()
    else GENERIC_LOGO_PATH
)

DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
PLC_PROCESSED_DIR = DATA_DIR / "plc_processed"
LOG_DIR = PROJECT_ROOT / "logs"

STATUS_FILE = LOG_DIR / "device_status.json"
PLC_STATUS_FILE = LOG_DIR / "plc_ftp_status.json"
SESSION_FILE = LOG_DIR / "production_session.json"
LAUNCHER_SELECTION_FILE = LOG_DIR / "launcher_selection.json"
APP_VERSION = "v0.6.3"
MIN_CAPABILITY_SAMPLES = 30
MIN_CAPABILITY_STD = 0.001


st.set_page_config(
    page_title="ASG SPC F09",
    page_icon=":material/analytics:",
    layout="wide",
)
st.logo(LOGO_PATH, size="medium", icon_image=LOGO_PATH)


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
def load_plc_status():
    if not PLC_STATUS_FILE.exists():
        return {}

    try:
        with open(PLC_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_resource
def _clean_state():
    return {
        "path": None,
        "offset": 0,
        "columns": None,
        "df": pd.DataFrame(),
        "lock": threading.Lock(),
    }


def _process_clean_chunk(df):
    """Convierte tipos y deriva columnas; trabaja solo sobre las filas nuevas."""
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

    df["task_label"] = df.apply(build_task_label, axis=1) if len(df) else ""

    return df


def load_clean_data():
    """Lee el CSV del día de forma incremental: solo procesa los bytes agregados desde la última lectura."""
    today_file = PROCESSED_DIR / f"asg_clean_master_{today_str()}.csv"

    state = _clean_state()

    with state["lock"]:
        try:
            size = today_file.stat().st_size
        except OSError:
            state.update(path=None, offset=0, columns=None, df=pd.DataFrame())
            return pd.DataFrame()

        # Archivo nuevo, reescrito o truncado: se recarga completo.
        if state["path"] != str(today_file) or size < state["offset"]:
            state.update(path=str(today_file), offset=0, columns=None, df=pd.DataFrame())

        if size == state["offset"]:
            return state["df"].copy()

        try:
            with open(today_file, "rb") as handle:
                handle.seek(state["offset"])
                data = handle.read(size - state["offset"])
        except OSError:
            return state["df"].copy()

        # Solo se consumen líneas completas; la última, si está a medias, se lee la próxima vez.
        last_newline = data.rfind(b"\n")
        if last_newline == -1:
            return state["df"].copy()
        data = data[: last_newline + 1]
        consumed = len(data)

        text = data.decode("utf-8-sig" if state["offset"] == 0 else "utf-8", errors="replace")
        try:
            if state["columns"] is None:
                chunk = pd.read_csv(io.StringIO(text))
                state["columns"] = list(chunk.columns)
            else:
                chunk = pd.read_csv(io.StringIO(text), header=None, names=state["columns"])
        except Exception:
            state.update(path=None, offset=0, columns=None, df=pd.DataFrame())
            return pd.DataFrame()

        chunk = _process_clean_chunk(chunk)
        frames = [f for f in (state["df"], chunk) if not f.empty]
        state["df"] = pd.concat(frames, ignore_index=True) if frames else chunk
        state["offset"] += consumed
        return state["df"].copy()



@st.cache_data(ttl=2)
def load_plc_traceability_data():
    suffix = today_str()
    files = {
        "pieces": PLC_PROCESSED_DIR / f"plc_pieces_master_{suffix}.csv",
        "attempts": PLC_PROCESSED_DIR / f"plc_piece_attempts_master_{suffix}.csv",
        "tightenings": PLC_PROCESSED_DIR / f"plc_tightenings_master_{suffix}.csv",
    }

    loaded = {}

    for key, file_path in files.items():
        if not file_path.exists():
            loaded[key] = pd.DataFrame()
            continue

        try:
            loaded[key] = pd.read_csv(file_path)
        except Exception:
            loaded[key] = pd.DataFrame()

    for df in loaded.values():
        if "plc_timestamp" in df.columns:
            df["plc_timestamp"] = pd.to_datetime(
                df["plc_timestamp"],
                errors="coerce",
            )

    numeric_columns = {
        "declared_screws",
        "captured_screws",
        "ng_tightenings",
        "attempt_number",
        "attempts_for_qr_station",
        "is_repeated_qr",
        "is_final_attempt",
        "screw_position",
        "torque_actual",
        "torque_min",
        "torque_max",
        "torque_target",
        "angle_actual",
        "angle_min",
        "angle_max",
        "angle_target",
    }

    for df in loaded.values():
        for column in numeric_columns:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

    return loaded["pieces"], loaded["attempts"], loaded["tightenings"]



@st.cache_data(ttl=2)
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


def load_launcher_selection():
    """
    Lee la selección realizada desde asg_launcher.py.

    Archivo esperado:
    logs/launcher_selection.json
    """
    if not LAUNCHER_SELECTION_FILE.exists():
        return {}

    try:
        with open(LAUNCHER_SELECTION_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, dict):
            return {}

        return payload

    except Exception:
        return {}


def get_launcher_selected_model(default_model="T1XX-1"):
    """
    Regresa el modelo seleccionado desde el launcher.

    Si no existe archivo de launcher, usa default_model.
    """
    payload = load_launcher_selection()
    selected = str(payload.get("selected_model", default_model)).strip()

    if selected in ["T1XX-1", "T1XX-2"]:
        return selected

    return default_model


def filter_status_for_model(status_df, selected_model, devices_for_model):
    """
    Filtra la tabla de estado para que Vista general no muestre estaciones
    de otro modelo que hayan quedado de una corrida anterior.
    """
    if status_df is None or status_df.empty:
        return pd.DataFrame()

    filtered = status_df.copy()

    allowed_devices = []

    if devices_for_model is not None and not devices_for_model.empty:
        if "device_id" in devices_for_model.columns:
            allowed_devices = (
                devices_for_model["device_id"]
                .dropna()
                .astype(str)
                .str.strip()
                .tolist()
            )

    if allowed_devices and "device_id" in filtered.columns:
        filtered["device_id"] = filtered["device_id"].astype(str).str.strip()
        filtered = filtered[filtered["device_id"].isin(allowed_devices)].copy()
        return filtered

    if "running_model" in filtered.columns:
        filtered = filtered[filtered["running_model"] == selected_model].copy()

    return filtered


def filter_plc_status_for_model(status_df, devices_for_model):
    if status_df is None or status_df.empty:
        return pd.DataFrame()

    if devices_for_model is None or devices_for_model.empty:
        return status_df.copy()

    asg_ids = (
        devices_for_model["device_id"]
        .dropna()
        .astype(str)
        .str.strip()
        .tolist()
    )
    allowed_plc_ids = {
        "PLC_" + value.split("_", 1)[1]
        for value in asg_ids
        if value.upper().startswith("ASG_")
    }

    filtered = status_df.copy()
    if "device_id" in filtered.columns:
        filtered["device_id"] = filtered["device_id"].astype(str).str.strip()
        filtered = filtered[filtered["device_id"].isin(allowed_plc_ids)]

    return filtered.copy()



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


def render_dashboard_header(selected_model, selected_page):
    with st.container(border=True):
        title_col, info_col = st.columns([2.5, 1.5], vertical_alignment="center")
        with title_col:
            st.caption("SISTEMA DE MONITOREO · LÍNEA F09")
            st.title("ASG Data Acquisition")
            st.caption(
                "Supervisión de aprietes, conectividad y trazabilidad de producción."
            )

        with info_col:
            with st.container(horizontal_alignment="right"):
                st.badge(
                    selected_model,
                    icon=":material/precision_manufacturing:",
                    color="green",
                )
                st.badge(
                    selected_page,
                    icon=":material/dashboard:",
                    color="gray",
                )
            st.caption(
                datetime.now().strftime("%d/%m/%Y · %H:%M"),
                text_alignment="right",
            )


def render_result_badge(result):
    normalized_result = str(result).strip().upper()
    if normalized_result in {"OK", "PASS"}:
        st.badge(normalized_result, icon=":material/check_circle:", color="green")
    elif normalized_result in {"NG", "NOK", "FAIL"}:
        st.badge("NG", icon=":material/error:", color="red")
    else:
        st.badge(
            normalized_result or "Sin datos",
            icon=":material/schedule:",
            color="gray",
        )


def notify_new_result(df, selected_model):
    if df.empty:
        return

    model_df = df
    if "running_model" in model_df.columns:
        model_df = model_df[model_df["running_model"] == selected_model]
    if model_df.empty:
        return

    timestamp_col = (
        "plot_timestamp"
        if "plot_timestamp" in model_df.columns
        else "pc_timestamp"
    )
    if timestamp_col in model_df.columns:
        model_df = model_df.sort_values(timestamp_col)

    latest = model_df.iloc[-1]
    timestamp = pd.to_datetime(latest.get(timestamp_col), errors="coerce")
    timestamp_key = timestamp.isoformat() if pd.notna(timestamp) else ""
    result = str(latest.get("result_label", "")).strip().upper()
    device_id = str(latest.get("device_id", "")).strip()
    tightening_id = str(latest.get("tightening_id", "")).strip()
    result_key = "|".join(
        [device_id, timestamp_key, tightening_id, result]
    )
    state_key = f"dashboard_last_result_{selected_model}"
    previous_result_key = st.session_state.get(state_key)
    st.session_state[state_key] = result_key

    if previous_result_key is None or previous_result_key == result_key:
        return

    if result in {"NG", "NOK", "FAIL"}:
        st.toast(
            f"Nuevo resultado NG en {device_id}",
            icon=":material/error:",
        )
    elif result in {"OK", "PASS"}:
        st.toast(
            f"Nuevo resultado OK en {device_id}",
            icon=":material/check_circle:",
        )


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


def calculate_plc_ok_piece_counts(plc_pieces_df):
    """Cuenta QR finales OK por estación; nunca infiere piezas desde tornillos."""
    empty_columns = ["device_id", "plc_ok_pieces"]

    if plc_pieces_df is None or plc_pieces_df.empty:
        return pd.DataFrame(columns=empty_columns)

    required = {"attempt_result", "qr_code"}
    if not required.issubset(plc_pieces_df.columns):
        return pd.DataFrame(columns=empty_columns)

    work_df = plc_pieces_df.copy()

    if "is_final_attempt" in work_df.columns:
        final_flag = pd.to_numeric(
            work_df["is_final_attempt"], errors="coerce"
        ).fillna(0)
        work_df = work_df[final_flag == 1]

    work_df = work_df[
        work_df["attempt_result"].fillna("").astype(str).str.upper() == "OK"
    ].copy()
    work_df["qr_code"] = work_df["qr_code"].fillna("").astype(str).str.strip()
    work_df = work_df[work_df["qr_code"] != ""]

    device_col = "asg_device_id" if "asg_device_id" in work_df.columns else "device_id"
    if device_col not in work_df.columns or work_df.empty:
        return pd.DataFrame(columns=empty_columns)

    work_df[device_col] = work_df[device_col].fillna("").astype(str).str.strip()
    work_df = work_df[work_df[device_col] != ""]

    # Un mismo QR cuenta una sola vez por estación, aunque tenga reintentos.
    work_df = work_df.drop_duplicates(subset=[device_col, "qr_code"], keep="last")

    return (
        work_df.groupby(device_col, dropna=False)["qr_code"]
        .nunique()
        .rename("plc_ok_pieces")
        .reset_index()
        .rename(columns={device_col: "device_id"})
    )


def calculate_cycle_summary(
    df,
    pieces_df,
    devices_for_model,
    selected_model,
    plc_pieces_df=None,
):
    if devices_for_model is None or devices_for_model.empty:
        return pd.DataFrame()

    rows = []
    plc_counts_df = calculate_plc_ok_piece_counts(plc_pieces_df)
    plc_count_lookup = {}

    if not plc_counts_df.empty:
        plc_count_lookup = dict(
            zip(plc_counts_df["device_id"], plc_counts_df["plc_ok_pieces"])
        )

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
                "plc_ok_pieces": int(plc_count_lookup.get(device_id, 0)),
                "asg_cycle_samples": len(station_pieces),
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
                    "plc_ok_pieces",
                    "asg_cycle_samples",
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
    st.subheader("Estado ASG en vivo")

    if status_df.empty:
        st.warning("Todavía no existe `logs/device_status.json`. Ejecuta primero el colector.")
        return

    connected_count = int(status_df["connected"].sum()) if "connected" in status_df.columns else 0
    total_count = len(status_df)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Equipos monitoreados", total_count, border=True)
    col2.metric("Conectados", connected_count, border=True)
    col3.metric("Desconectados", total_count - connected_count, border=True)

    total_results = 0

    if "total_results" in status_df.columns:
        total_results = pd.to_numeric(status_df["total_results"], errors="coerce").fillna(0).sum()

    col4.metric("Resultados recibidos", int(total_results), border=True)

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


def render_plc_ftp_status(status_df):
    st.subheader("Estado PLC / FTP")

    if status_df.empty:
        st.warning(
            "Todavía no existe estado PLC/FTP para este modelo. "
            "Ejecuta el colector PLC desde el launcher."
        )
        return

    connected = (
        int(status_df["connected"].fillna(False).astype(bool).sum())
        if "connected" in status_df.columns else 0
    )
    files_available = (
        int(status_df["file_available"].fillna(False).astype(bool).sum())
        if "file_available" in status_df.columns else 0
    )
    updated = (
        int(status_df["changed"].fillna(False).astype(bool).sum())
        if "changed" in status_df.columns else 0
    )
    errors = (
        int(status_df["last_error"].fillna("").astype(str).str.strip().ne("").sum())
        if "last_error" in status_df.columns else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PLCs esperados", len(status_df), border=True)
    c2.metric("FTP conectado", connected, border=True)
    c3.metric("Logfile disponible", files_available, border=True)
    c4.metric("Con error", errors, border=True)

    if updated:
        st.caption(f"Archivos actualizados en el último ciclo: {updated}")

    display_cols = [
        "device_id",
        "station",
        "process",
        "ip",
        "connected",
        "authenticated",
        "storage_accessible",
        "file_available",
        "download_mode",
        "remote_size",
        "local_size",
        "last_download_at",
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
        st.caption("Sin resultados de apriete para los filtros seleccionados.")
        return

    total = len(df)

    ok_count = 0
    ng_count = 0

    if "result_label" in df.columns:
        ok_count = int((df["result_label"] == "OK").sum())
        ng_count = int((df["result_label"] == "NG").sum())

    ok_pct = (ok_count / total * 100) if total else 0

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Resultados filtrados", total, border=True)
    col2.metric("OK", ok_count, border=True)
    col3.metric("NG", ng_count, border=True)
    col4.metric("% OK", f"{ok_pct:.1f}%", border=True)

    last_row = df.tail(1)

    if not last_row.empty:
        row = last_row.iloc[0]

        with st.container(border=True):
            st.markdown("**Último resultado**")
            st.caption(
                f"{row.get('device_id', '')} · {row.get('process', '')} · "
                f"{row.get('task_label', '')}"
            )
            with st.container(horizontal=True):
                st.metric(
                    "Torque",
                    fmt_value(row.get("torque_actual", ""), 2),
                    border=True,
                )
                st.metric(
                    "Ángulo",
                    fmt_value(row.get("angle_actual", ""), 0),
                    border=True,
                )
                render_result_badge(row.get("result_label", ""))


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
                    last_timestamp = latest.get(
                        "plot_timestamp",
                        latest.get("pc_timestamp"),
                    )
                    parsed_timestamp = pd.to_datetime(
                        last_timestamp,
                        errors="coerce",
                    )
                    last_reading = (
                        parsed_timestamp.strftime("%H:%M:%S")
                        if pd.notna(parsed_timestamp)
                        else "Sin lectura reciente"
                    )
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
                    last_reading = "Sin lectura reciente"

                with st.container(border=True):
                    st.markdown(f"### {device_id} | {process}")
                    st.caption(f"{station_name} | Módulo: {module_name}")
                    st.caption(f"Última lectura: {last_reading}")
                    render_result_badge(result)

                    with st.container(horizontal=True):
                        st.metric(
                            "Torque",
                            f"{torque_actual} {torque_unit}".strip(),
                            border=True,
                        )
                        st.metric("Ángulo", angle_actual, border=True)
                        st.metric("Ciclos", results_count, border=True)

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


def render_cycle_time_tab(
    cycle_base_df,
    plc_pieces_df,
    devices_for_model,
    selected_model,
    export_base_df=None,
):
    st.subheader("Tiempos de ciclo y piezas producidas")
    st.caption(
        "Producción: QR finales OK del PLC. Tiempos de ciclo: resultados en vivo del ASG."
    )

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
        plc_pieces_df=plc_pieces_df,
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
                    f"{save_info.get('pieces_rows', 0)} grupos de ciclo ASG | "
                    f"{save_info.get('summary_rows', 0)} estaciones."
                )

                st.code(
                    f"""
Grupos ASG usados para tiempo de ciclo:
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
    total_station_ok = int(summary_df["plc_ok_pieces"].sum())
    total_unique_ok = 0

    if plc_pieces_df is not None and not plc_pieces_df.empty:
        plc_ok_df = plc_pieces_df.copy()
        if "attempt_result" in plc_ok_df.columns and "qr_code" in plc_ok_df.columns:
            plc_ok_df = plc_ok_df[
                plc_ok_df["attempt_result"].fillna("").astype(str).str.upper() == "OK"
            ]
            plc_ok_df["qr_code"] = (
                plc_ok_df["qr_code"].fillna("").astype(str).str.strip()
            )
            total_unique_ok = int(
                plc_ok_df.loc[plc_ok_df["qr_code"] != "", "qr_code"].nunique()
            )

    avg_cycle = valid_cycle_values.mean() if not valid_cycle_values.empty else None
    avg_tightening = valid_tightening_values.mean() if not valid_tightening_values.empty else None

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Resultados torque", total_torque_results)
    c2.metric("Piezas únicas OK (PLC)", total_unique_ok)
    c3.metric("OK por estación (PLC)", total_station_ok)
    c4.metric("Tiempo ciclo promedio", fmt_seconds(avg_cycle))

    c5, c6, c7, c8 = st.columns(4)

    c5.metric("Tiempo atornillado prom.", fmt_seconds(avg_tightening))
    c6.metric("Mín. ciclo", fmt_seconds(valid_cycle_values.min() if not valid_cycle_values.empty else None))
    c7.metric("Máx. ciclo", fmt_seconds(valid_cycle_values.max() if not valid_cycle_values.empty else None))
    c8.metric("Std ciclo", fmt_seconds(valid_cycle_values.std() if len(valid_cycle_values) > 1 else None))

    st.divider()

    st.markdown("### Piezas OK por estación (PLC)")

    bar_fig = go.Figure()

    bar_fig.add_trace(
        go.Bar(
            x=summary_df["device_id"],
            y=summary_df["plc_ok_pieces"],
            text=summary_df["plc_ok_pieces"],
            textposition="auto",
            customdata=safe_customdata(
                summary_df,
                [
                    "station",
                    "process",
                    "module_name",
                    "torque_results",
                    "asg_cycle_samples",
                ],
            ),
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Estación: %{customdata[0]}<br>"
                "Proceso: %{customdata[1]}<br>"
                "Módulo: %{customdata[2]}<br>"
                "Resultados torque: %{customdata[3]}<br>"
                "Piezas OK PLC: %{y}<br>"
                "Muestras de ciclo ASG: %{customdata[4]}<br>"
                "<extra></extra>"
            ),
        )
    )

    bar_fig.update_layout(
        xaxis_title="Estación",
        yaxis_title="Piezas OK (QR PLC)",
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
        st.info("Aún no hay suficientes grupos ASG para calcular tiempo de ciclo.")
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
        "plc_ok_pieces",
        "asg_cycle_samples",
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

    st.markdown("### Grupos ASG usados para tiempos")

    if pieces_df.empty:
        st.info("Aún no hay grupos ASG suficientes para calcular tiempos.")
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



def prepare_hybrid_capability_data(asg_df, plc_tightenings_df):
    """
    Usa el logfile PLC como fuente preferida por estación y el ASG como respaldo.

    Esto evita duplicar el mismo apriete y permite calcular capacidad en estaciones
    cuyo ASG no es accesible desde la red de la PC recolectora.
    """
    asg = asg_df.copy() if asg_df is not None else pd.DataFrame()
    plc = (
        plc_tightenings_df.copy()
        if plc_tightenings_df is not None
        else pd.DataFrame()
    )

    if not asg.empty:
        asg["data_source"] = "ASG en vivo"

    if not plc.empty:
        if "asg_device_id" in plc.columns:
            plc["device_id"] = plc["asg_device_id"].fillna("").astype(str)
        elif "device_id" in plc.columns:
            plc["device_id"] = (
                plc["device_id"].fillna("").astype(str)
                .str.replace(r"^PLC_", "ASG_", regex=True)
            )

        if "parameter_set_id" not in plc.columns:
            plc["parameter_set_id"] = None

        def plc_task_label(value):
            try:
                if pd.isna(value):
                    return ""
                return f"PSet {int(float(value)):03d}"
            except Exception:
                return ""

        plc["task_label"] = plc["parameter_set_id"].apply(plc_task_label)
        plc["torque_unit"] = "cN·m"
        plc["result_label"] = plc.get(
            "tightening_result",
            pd.Series("", index=plc.index),
        )
        plc["data_source"] = "PLC logfile"

    plc_devices = set()
    if not plc.empty and {"device_id", "torque_actual"}.issubset(plc.columns):
        valid_plc = plc.dropna(subset=["torque_actual"])
        plc_devices = set(valid_plc["device_id"].astype(str))

    if not asg.empty and plc_devices and "device_id" in asg.columns:
        asg = asg[~asg["device_id"].astype(str).isin(plc_devices)]

    frames = [frame for frame in (plc, asg) if not frame.empty]
    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True, sort=False)


def calculate_torque_capability(
    df,
    minimum_samples=MIN_CAPABILITY_SAMPLES,
    minimum_std=MIN_CAPABILITY_STD,
):
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
        "data_source": "",
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
        "data_source",
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
            and float(std_value) >= minimum_std
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

        if (
            std_value is not None
            and pd.notna(std_value)
            and 0 <= float(std_value) < minimum_std
        ):
            classification = "Variación insuficiente"
        elif sample_count < minimum_samples:
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
                "within_spec_pct": 100 - out_of_spec_pct,
                "cpk_limited_by": (
                    "LSL" if cpl is not None and cpu is not None and cpl <= cpu
                    else "USL" if cpl is not None and cpu is not None
                    else ""
                ),
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


def calculate_cp_cpk(values, lsl, usl, min_std=MIN_CAPABILITY_STD):
    values = pd.to_numeric(values, errors="coerce").dropna()

    if values.empty:
        return None, None, None, None, None, None

    mean = float(values.mean())
    std = float(values.std(ddof=1)) if len(values) > 1 else None

    if (
        std is None
        or pd.isna(std)
        or std < min_std
        or lsl is None
        or usl is None
        or float(usl) <= float(lsl)
    ):
        return mean, std, None, None, None, None

    cp = (usl - lsl) / (6 * std)
    cpu = (usl - mean) / (3 * std)
    cpl = (mean - lsl) / (3 * std)
    cpk = min(cpu, cpl)

    return mean, std, cp, cpl, cpu, cpk


def format_capability_value(value, decimals=3):
    try:
        if value is None or pd.isna(value):
            return "—"

        return f"{float(value):.{decimals}f}"
    except Exception:
        return "—"


def get_capability_status(cpk, sample_count=None, minimum_samples=MIN_CAPABILITY_SAMPLES):
    if sample_count is not None and sample_count < minimum_samples:
        return "Muestra insuficiente"

    if cpk is None or pd.isna(cpk):
        return "Sin evaluar"

    if cpk >= 1.33:
        return "Aceptable"

    if cpk >= 1.00:
        return "Marginal"

    return "No aceptable"


def prepare_homogeneous_capability_groups(df, variable_col):
    """Separa PSet y límites para no mezclar poblaciones en un mismo Cpk."""
    if df is None or df.empty:
        return pd.DataFrame()

    variable_cfg = get_capability_columns(variable_col)
    lsl_col = variable_cfg["lsl"]
    target_col = variable_cfg["target"]
    usl_col = variable_cfg["usl"]

    work_df = df.copy()
    defaults = {
        "task_label": "",
        "parameter_set_id": None,
        lsl_col: None,
        target_col: None,
        usl_col: None,
    }
    for column, default in defaults.items():
        if column and column not in work_df.columns:
            work_df[column] = default

    for column in [variable_col, lsl_col, target_col, usl_col, "parameter_set_id"]:
        if column and column in work_df.columns:
            work_df[column] = pd.to_numeric(work_df[column], errors="coerce")

    work_df = work_df.dropna(subset=[variable_col])

    def group_identity(row):
        task = str(row.get("task_label", "")).strip()
        if task.lower() in {"", "nan", "none"}:
            task = ""

        pset_value = row.get("parameter_set_id")
        try:
            pset = f"PSet {int(float(pset_value)):03d}" if pd.notna(pset_value) else "Sin PSet"
        except Exception:
            pset = f"PSet {pset_value}"

        program = task or pset
        lsl = row.get(lsl_col) if lsl_col else None
        target = row.get(target_col) if target_col else None
        usl = row.get(usl_col) if usl_col else None

        key = repr((program, lsl, target, usl))
        label = (
            f"{program} | LSL {fmt_value(lsl, 3) or '—'} | "
            f"Target {fmt_value(target, 3) or '—'} | "
            f"USL {fmt_value(usl, 3) or '—'}"
        )
        return pd.Series([key, label])

    work_df[["_capability_group_key", "_capability_group_label"]] = work_df.apply(
        group_identity,
        axis=1,
    )
    return work_df


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


def render_capability_tab(
    clean_df=None,
    devices_for_model=None,
    selected_model=None,
    capability_df=None,
    minimum_samples=30,
    **kwargs,
):
    # Compatibilidad:
    # Algunas versiones anteriores llamaban esta función con capability_df.
    # La versión nueva usa clean_df.
    if clean_df is None and capability_df is not None:
        clean_df = capability_df

    if clean_df is None:
        clean_df = pd.DataFrame()

    if selected_model is None:
        selected_model = "T1XX-1"

    st.subheader("Capacidad de proceso")

    st.caption(
        "Distribución con histograma real, campana normal ajustada, límites de "
        "especificación y Cp/Cpk. Se prefiere el logfile PLC; el ASG en vivo se "
        "usa como respaldo cuando el PLC aún no tiene datos."
    )

    device_options = get_device_options_from_config(devices_for_model)

    # La prioridad es usar config/asg_devices.csv.
    # Solo si no viene devices_for_model, usamos los datos como respaldo.
    if not device_options and clean_df is not None and not clean_df.empty and "device_id" in clean_df.columns:
        device_options = (
            clean_df["device_id"]
            .dropna()
            .astype(str)
            .str.strip()
            .drop_duplicates()
            .tolist()
        )

    # Si esta función fue llamada desde una versión anterior sin devices_for_model,
    # usamos las estaciones presentes en los datos como respaldo.
    if not device_options and clean_df is not None and not clean_df.empty and "device_id" in clean_df.columns:
        device_options = (
            clean_df["device_id"]
            .dropna()
            .astype(str)
            .str.strip()
            .drop_duplicates()
            .tolist()
        )

    if not device_options:
        st.warning("No hay estaciones configuradas para el modelo seleccionado.")
        return

    if clean_df.empty:
        st.info("Todavía no hay datos limpios para calcular capacidad.")
        return

    df = clean_df.copy()

    if "running_model" in df.columns:
        df = df[df["running_model"] == selected_model]

    torque_summary_df, _ = calculate_torque_capability(
        df,
        minimum_samples=minimum_samples,
    )

    with st.expander("Resumen Cpk de torque por proceso / PSet", expanded=True):
        st.caption(
            "Cada fila usa una población homogénea: misma estación, PSet y límites. "
            "Cpk = mínimo entre CPL y CPU; incluye resultados OK y NG."
        )

        if torque_summary_df.empty:
            st.info("Todavía no hay grupos de torque con límites válidos.")
        else:
            summary_columns = [
                "data_source",
                "process",
                "device_id",
                "task_label",
                "pset_label",
                "samples",
                "mean",
                "std",
                "torque_min",
                "torque_max",
                "cp",
                "cpl",
                "cpu",
                "cpk",
                "cpk_limited_by",
                "out_of_spec",
                "out_of_spec_pct",
                "classification",
            ]
            summary_columns = [
                column for column in summary_columns
                if column in torque_summary_df.columns
            ]
            st.dataframe(
                torque_summary_df[summary_columns],
                width="stretch",
                hide_index=True,
                column_config={
                    "mean": st.column_config.NumberColumn(format="%.3f"),
                    "std": st.column_config.NumberColumn(format="%.4f"),
                    "cp": st.column_config.NumberColumn(format="%.3f"),
                    "cpl": st.column_config.NumberColumn(format="%.3f"),
                    "cpu": st.column_config.NumberColumn(format="%.3f"),
                    "cpk": st.column_config.NumberColumn(format="%.3f"),
                    "out_of_spec_pct": st.column_config.NumberColumn(format="%.2f%%"),
                },
            )

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
    station_df = prepare_homogeneous_capability_groups(station_df, variable_col)

    group_options = []
    if not station_df.empty:
        group_options = station_df["_capability_group_label"].drop_duplicates().tolist()

    with c3:
        selected_group = st.selectbox(
            "Proceso / PSet y límites",
            group_options,
            key="capability_group_selector_v062",
        )

    if selected_group:
        station_df = station_df[
            station_df["_capability_group_label"] == selected_group
        ]

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

    mean, std, cp, cpl, cpu, cpk = calculate_cp_cpk(values, lsl, usl)

    out_spec = 0

    if lsl is not None and usl is not None:
        out_spec = int(((values < lsl) | (values > usl)).sum())

    out_spec_pct = (out_spec / len(values) * 100) if len(values) else 0

    status = get_capability_status(
        cpk,
        sample_count=len(values),
        minimum_samples=minimum_samples,
    )

    m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)

    m1.metric("N", len(values))
    m2.metric("Media", format_capability_value(mean, 3))
    m3.metric("Std", format_capability_value(std, 4))
    m4.metric("Cp", format_capability_value(cp, 3))
    m5.metric("CPL", format_capability_value(cpl, 3))
    m6.metric("CPU", format_capability_value(cpu, 3))
    m7.metric("Cpk", format_capability_value(cpk, 3))
    m8.metric("Estatus", status)

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

    if std is not None and pd.notna(std) and std < MIN_CAPABILITY_STD:
        st.warning(
            "La variación observada es menor que la resolución mínima configurada. "
            "No se publica Cp/Cpk para evitar un valor artificialmente enorme."
        )

    if len(values) < minimum_samples:
        st.warning(
            f"Hay {len(values)} muestras; se requieren al menos {minimum_samples} "
            "para clasificar formalmente la capacidad."
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

    if len(values) < minimum_samples:
        st.info(
            "El Cp/Cpk mostrado, si está disponible, es preliminar por tamaño de muestra."
        )
    elif cpk is None:
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


def normalize_tightening_id(value):
    if pd.isna(value):
        return ""

    text = str(value).strip()

    if text.lower() in {"", "nan", "none"}:
        return ""

    try:
        return str(int(float(text)))
    except Exception:
        return text


def add_asg_match_status(pieces_df, tightenings_df, asg_df):
    pieces = pieces_df.copy()
    tightenings = tightenings_df.copy()

    if pieces.empty:
        return pieces, tightenings

    if tightenings.empty:
        pieces["matched_tightenings"] = 0
        pieces["plc_tightenings"] = 0
        pieces["torques_obtenidos"] = ""
        pieces["torque_promedio"] = None
        pieces["torque_min_pieza"] = None
        pieces["torque_max_pieza"] = None
        pieces["match_status"] = "Sin atornillados"
        return pieces, tightenings

    tightenings["normalized_tightening_id"] = tightenings["tightening_id"].map(
        normalize_tightening_id
    )
    tightenings["match_key"] = (
        tightenings["asg_device_id"].fillna("").astype(str)
        + "|"
        + tightenings["normalized_tightening_id"]
    )

    asg_keys = set()
    asg_devices_with_data = set()

    if (
        not asg_df.empty
        and "device_id" in asg_df.columns
        and "tightening_id" in asg_df.columns
    ):
        asg_reference = asg_df[["device_id", "tightening_id"]].copy()
        asg_devices_with_data = set(
            asg_reference["device_id"].fillna("").astype(str).str.strip()
        )
        asg_reference["normalized_tightening_id"] = asg_reference["tightening_id"].map(
            normalize_tightening_id
        )
        asg_reference = asg_reference[
            asg_reference["normalized_tightening_id"] != ""
        ]
        asg_keys = set(
            asg_reference["device_id"].fillna("").astype(str)
            + "|"
            + asg_reference["normalized_tightening_id"]
        )

    tightenings["asg_match"] = (
        (tightenings["normalized_tightening_id"] != "")
        & tightenings["match_key"].isin(asg_keys)
    )

    final_tightenings = tightenings.copy()
    if "is_final_attempt" in final_tightenings.columns:
        final_tightenings = final_tightenings[
            final_tightenings["is_final_attempt"] == 1
        ]

    torque_summary_rows = []
    if "torque_actual" in final_tightenings.columns:
        final_tightenings["torque_actual"] = pd.to_numeric(
            final_tightenings["torque_actual"], errors="coerce"
        )

        for attempt_key, attempt_df in final_tightenings.groupby(
            "attempt_key", dropna=False
        ):
            if "screw_position" in attempt_df.columns:
                attempt_df = attempt_df.sort_values("screw_position")

            valid_torques = attempt_df.dropna(subset=["torque_actual"])
            torque_values = valid_torques["torque_actual"]

            torque_labels = []
            for _, tightening_row in valid_torques.iterrows():
                torque_value = (
                    f"{float(tightening_row['torque_actual']):.3f}"
                    .rstrip("0")
                    .rstrip(".")
                )
                screw_position = tightening_row.get("screw_position", "")
                try:
                    screw_label = f"T{int(float(screw_position))}"
                except Exception:
                    screw_label = "T"
                torque_labels.append(f"{screw_label}: {torque_value}")

            torque_summary_rows.append(
                {
                    "attempt_key": attempt_key,
                    "torques_obtenidos": " | ".join(torque_labels),
                    "torque_promedio": torque_values.mean() if not torque_values.empty else None,
                    "torque_min_pieza": torque_values.min() if not torque_values.empty else None,
                    "torque_max_pieza": torque_values.max() if not torque_values.empty else None,
                }
            )

    match_summary = (
        final_tightenings.groupby("attempt_key", dropna=False)
        .agg(
            plc_tightenings=("tightening_id", "count"),
            matched_tightenings=("asg_match", "sum"),
        )
        .reset_index()
    )

    pieces = pieces.merge(match_summary, on="attempt_key", how="left")

    if torque_summary_rows:
        pieces = pieces.merge(
            pd.DataFrame(torque_summary_rows),
            on="attempt_key",
            how="left",
        )
    else:
        pieces["torques_obtenidos"] = ""
        pieces["torque_promedio"] = None
        pieces["torque_min_pieza"] = None
        pieces["torque_max_pieza"] = None

    pieces["plc_tightenings"] = pieces["plc_tightenings"].fillna(0).astype(int)
    pieces["matched_tightenings"] = pieces["matched_tightenings"].fillna(0).astype(int)
    pieces["torques_obtenidos"] = pieces["torques_obtenidos"].fillna("")

    def get_match_status(row):
        total = int(row.get("plc_tightenings", 0))
        matched = int(row.get("matched_tightenings", 0))
        asg_device = str(row.get("asg_device_id", "")).strip()

        if total == 0:
            return "Sin atornillados"
        if asg_device and asg_device not in asg_devices_with_data:
            return "ASG sin datos en la sesión"
        if matched == total:
            return "Confirmado ASG + PLC"
        if matched > 0:
            return "Coincidencia parcial"
        return "Sin coincidencia ASG"

    pieces["match_status"] = pieces.apply(get_match_status, axis=1)
    return pieces, tightenings


def filter_plc_traceability_data(
    df,
    selected_model,
    selected_devices,
    count_scope,
    active_session_info,
    time_window_minutes,
    last_n=None,
):
    if df.empty:
        return df.copy()

    filtered = df.copy()

    if "running_model" in filtered.columns:
        filtered = filtered[filtered["running_model"] == selected_model]

    if "asg_device_id" in filtered.columns:
        filtered = filtered[filtered["asg_device_id"].isin(selected_devices)]

    if "plc_timestamp" in filtered.columns:
        filtered = filtered.dropna(subset=["plc_timestamp"])

        if count_scope == "Sesión actual":
            session_start = pd.to_datetime(
                active_session_info.get("production_session_started_at", ""),
                errors="coerce",
            )
            if pd.notna(session_start):
                filtered = filtered[filtered["plc_timestamp"] >= session_start]

        if time_window_minutes is not None:
            cutoff = pd.Timestamp.now() - pd.Timedelta(minutes=time_window_minutes)
            filtered = filtered[filtered["plc_timestamp"] >= cutoff]

        filtered = filtered.sort_values("plc_timestamp")

    if last_n and not filtered.empty and "device_id" in filtered.columns:
        filtered = filtered.groupby("device_id", group_keys=False).tail(last_n)

    return filtered.copy()


def render_qr_traceability_tab(
    pieces_df,
    attempts_df,
    tightenings_df,
    asg_df,
    selected_model,
    selected_devices,
    selected_result,
    count_scope,
    active_session_info,
    time_window_minutes,
    last_n,
):
    st.subheader("Trazabilidad QR")
    st.caption("Piezas confirmadas por logfile PLC y comparación con datos en vivo ASG.")
    st.caption(
        "Validación ASG compara los IDs de apriete del PLC contra el colector en vivo. "
        "Es una comprobación de integridad; no determina si la pieza es OK o NG."
    )

    pieces_df, tightenings_df = add_asg_match_status(
        pieces_df=pieces_df,
        tightenings_df=tightenings_df,
        asg_df=asg_df,
    )

    pieces = filter_plc_traceability_data(
        pieces_df,
        selected_model,
        selected_devices,
        count_scope,
        active_session_info,
        time_window_minutes,
        last_n,
    )
    attempts = filter_plc_traceability_data(
        attempts_df,
        selected_model,
        selected_devices,
        count_scope,
        active_session_info,
        time_window_minutes,
    )
    tightenings = filter_plc_traceability_data(
        tightenings_df,
        selected_model,
        selected_devices,
        count_scope,
        active_session_info,
        time_window_minutes,
    )

    if pieces.empty:
        st.info(
            f"No hay logfiles procesados para {selected_model} con los filtros actuales. "
            "La vista se llenará automáticamente cuando el PLC tenga SD y genere su logfile."
        )
        return

    if selected_result == "OK":
        pieces = pieces[pieces["attempt_result"] == "OK"]
    elif selected_result == "NG":
        pieces = pieces[pieces["attempt_result"] != "OK"]

    if pieces.empty:
        st.info("No hay piezas con el resultado seleccionado.")
        return

    search_qr = st.text_input(
        "Buscar QR",
        placeholder="Escribe o escanea el código QR",
        key="qr_traceability_search",
    ).strip()

    if search_qr:
        pieces = pieces[
            pieces["qr_code"].fillna("").astype(str).str.contains(
                search_qr,
                case=False,
                regex=False,
            )
        ]

    if pieces.empty:
        st.warning("No se encontró un QR que coincida con la búsqueda.")
        return

    unique_qr = pieces["qr_code"].dropna().astype(str).nunique()
    ok_records = int((pieces["attempt_result"] == "OK").sum())
    issue_records = int((pieces["attempt_result"] != "OK").sum())
    retries = int(
        (pieces["attempts_for_qr_station"].fillna(1).astype(int) - 1)
        .clip(lower=0)
        .sum()
    )
    confirmed = int((pieces["match_status"] == "Confirmado ASG + PLC").sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("QR únicos", unique_qr)
    c2.metric("Registros OK", ok_records)
    c3.metric("NG / incompletos", issue_records)
    c4.metric("Reintentos", retries)
    c5.metric("Confirmados ASG", confirmed)

    st.markdown("### Piezas por estación")

    piece_columns = [
        "plc_timestamp",
        "qr_code",
        "running_model",
        "device_id",
        "station",
        "process",
        "captured_screws",
        "declared_screws",
        "torques_obtenidos",
        "torque_promedio",
        "torque_min_pieza",
        "torque_max_pieza",
        "attempt_result",
        "attempts_for_qr_station",
        "alarm_codes",
        "match_status",
    ]
    piece_columns = [column for column in piece_columns if column in pieces.columns]

    st.dataframe(
        pieces[piece_columns].sort_values("plc_timestamp", ascending=False),
        width="stretch",
        hide_index=True,
        column_config={
            "torques_obtenidos": st.column_config.TextColumn(
                "Torques obtenidos (cN·m)",
                width="large",
            ),
            "torque_promedio": st.column_config.NumberColumn(
                "Torque promedio",
                format="%.3f",
            ),
            "torque_min_pieza": st.column_config.NumberColumn(
                "Torque mín. pieza",
                format="%.3f",
            ),
            "torque_max_pieza": st.column_config.NumberColumn(
                "Torque máx. pieza",
                format="%.3f",
            ),
            "match_status": st.column_config.TextColumn("Validación ASG"),
        },
    )

    qr_options = sorted(
        pieces["qr_code"].dropna().astype(str).unique().tolist()
    )
    selected_qr = st.selectbox(
        "Detalle de pieza",
        qr_options,
        key="qr_traceability_detail",
    )

    qr_attempts = attempts[
        attempts["qr_code"].fillna("").astype(str) == selected_qr
    ].copy()
    qr_tightenings = tightenings[
        tightenings["qr_code"].fillna("").astype(str) == selected_qr
    ].copy()

    st.markdown("### Intentos y recorrido")
    attempt_columns = [
        "plc_timestamp",
        "device_id",
        "station",
        "process",
        "attempt_number",
        "captured_screws",
        "declared_screws",
        "attempt_result",
        "alarm_codes",
        "is_final_attempt",
    ]
    attempt_columns = [column for column in attempt_columns if column in qr_attempts.columns]

    st.dataframe(
        qr_attempts[attempt_columns].sort_values("plc_timestamp"),
        width="stretch",
        hide_index=True,
    )

    st.markdown("### Atornillados asociados")
    tightening_columns = [
        "plc_timestamp",
        "device_id",
        "station",
        "attempt_number",
        "screw_position",
        "tightening_id",
        "torque_actual",
        "torque_min",
        "torque_target",
        "torque_max",
        "angle_actual",
        "tightening_result",
        "asg_match",
    ]
    tightening_columns = [
        column for column in tightening_columns
        if column in qr_tightenings.columns
    ]

    st.dataframe(
        qr_tightenings[tightening_columns].sort_values(
            ["plc_timestamp", "screw_position"]
        ),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Ver todos los campos del QR"):
        st.dataframe(
            qr_attempts.sort_values("plc_timestamp"),
            width="stretch",
            hide_index=True,
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


def _close_easter_egg():
    st.session_state["egg_open"] = False


@st.dialog("🥚 ¡Sorpresa!", on_dismiss=_close_easter_egg)
def show_easter_egg():
    import streamlit.components.v1 as components

    components.html(
        """
        <canvas id="c" style="position:fixed;inset:0;width:100%;height:100%"></canvas>
        <div style="position:relative;font-family:sans-serif;text-align:center;
                    padding-top:70px;font-size:26px;font-weight:700;color:#2e9e5b">
          🎉 Felicidades, encontraste el huevo de pascua 🥚
        </div>
        <script>
        const cv = document.getElementById('c'), ctx = cv.getContext('2d');
        cv.width = innerWidth; cv.height = innerHeight;
        const colors = ['#2e9e5b','#f7c948','#e4572e','#3a86ff','#ff5d8f','#8338ec'];
        const P = Array.from({length: 160}, () => ({
          x: Math.random()*cv.width, y: -Math.random()*cv.height,
          w: 6+Math.random()*6, h: 8+Math.random()*8,
          vy: 2+Math.random()*3, vx: -1+Math.random()*2,
          a: Math.random()*6.28, va: -.2+Math.random()*.4,
          c: colors[Math.floor(Math.random()*colors.length)]
        }));
        (function draw(){
          ctx.clearRect(0,0,cv.width,cv.height);
          P.forEach(p => {
            p.x += p.vx; p.y += p.vy; p.a += p.va;
            if (p.y > cv.height) { p.y = -10; p.x = Math.random()*cv.width; }
            ctx.save(); ctx.translate(p.x,p.y); ctx.rotate(p.a);
            ctx.fillStyle = p.c; ctx.fillRect(-p.w/2,-p.h/2,p.w,p.h); ctx.restore();
          });
          requestAnimationFrame(draw);
        })();
        </script>
        """,
        height=260,
    )


def render_download_banner():
    open_panel = st.session_state.get("dl_panel_open", False)

    with st.container(border=True):
        text_col, btn_col = st.columns([3, 1], vertical_alignment="center")
        text_col.markdown(
            "#### :material/download: Descarga de datos procesados\n"
            "Elige un rango de fechas y descarga el torque de atornillado "
            "o los archivos FTP de los PLC."
        )
        label = "Cerrar" if open_panel else "Descargar datos"
        if btn_col.button(
            label,
            type="primary",
            width="stretch",
            icon=":material/close:" if open_panel else ":material/download:",
            key="dl_toggle",
        ):
            st.session_state["dl_panel_open"] = not open_panel
            st.rerun()

    if open_panel:
        render_downloads_panel()


API_BASE_URL = os.environ.get("ASG_API_URL", "http://127.0.0.1:8600").rstrip("/")


def api_request(path, params, timeout=60):
    """Llama a la API. Devuelve (bytes, content_type) o lanza RuntimeError con un mensaje legible."""
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    url = f"{API_BASE_URL}{path}?{urllib.parse.urlencode(clean)}"
    user = st.session_state.get("auth_user")
    headers = {"Authorization": f"Bearer {auth.create_api_token(user)}"} if user else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(detail or f"La API respondió con error {exc.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(f"No se pudo conectar con la API en {API_BASE_URL}. ¿Está corriendo run_api.bat?")


def render_downloads_panel():
    st.caption("Los datos se obtienen de la API. El refresco automático se pausa mientras este panel está abierto.")

    with st.container(border=True):
        st.markdown("**1. Rango de fechas**")
        today = datetime.now().date()
        date_range = st.date_input(
            "Desde / hasta",
            value=(today - timedelta(days=6), today),
            key="dl_date_range",
        )

        st.markdown("**2. Tipo de dato**")
        data_type = st.radio(
            "Fuente",
            ["Torque de atornillado (ASG)", "Archivos FTP de PLC (logfiles)"],
            horizontal=True,
            key="dl_data_type",
        )

        st.markdown("**3. Filtros**")
        col_a, col_b, col_c = st.columns(3)
        is_torque = data_type.startswith("Torque")
        try:
            station_ids = get_enabled_devices()["device_id"].tolist()
        except Exception:
            station_ids = []
        model = col_a.selectbox("Modelo", ["Todos", "T1XX-1", "T1XX-2"], key="dl_model", disabled=not is_torque)
        stations = col_b.multiselect(
            "Estación" if is_torque else "PLC (ID)",
            station_ids if is_torque else [],
            placeholder="Todas" if is_torque else "Todos",
            key="dl_stations",
            disabled=not is_torque,
        )
        result = col_c.selectbox("Resultado", ["Todos", "OK", "NOK"], key="dl_result", disabled=not is_torque)

        st.markdown("**4. Formato**")
        if is_torque:
            fmt = st.radio("Formato", ["CSV", "Excel (.xlsx)"], horizontal=True, key="dl_fmt")
        else:
            st.radio("Formato", ["ZIP con logfiles originales"], key="dl_fmt_ftp")

    valid_range = isinstance(date_range, (tuple, list)) and len(date_range) == 2
    if not valid_range:
        st.warning("Selecciona fecha inicial y final.")
        return
    start, end = date_range

    params = {"start": start.isoformat(), "end": end.isoformat()}
    if is_torque:
        params.update(
            {
                "station": ",".join(stations),
                "model": None if model == "Todos" else model,
                "result": None if result == "Todos" else result,
            }
        )
        summary_path = "/torque/summary"
    else:
        summary_path = "/ftp/files"

    st.markdown("**Resumen de la descarga**")
    summary = None
    try:
        raw, _ = api_request(summary_path, params, timeout=30)
        summary = json.loads(raw)
    except RuntimeError as exc:
        st.error(str(exc))

    m1, m2, m3 = st.columns(3)
    m1.metric("Días seleccionados", (end - start).days + 1)
    if summary and is_torque:
        m2.metric("Registros", f"{summary['rows']:,}")
        m3.metric("Estaciones con datos", len(summary["stations"]))
    elif summary:
        m2.metric("Archivos", f"{summary['files']:,}")
        m3.metric("Tamaño", f"{summary['bytes'] / 1_048_576:,.1f} MB")
    else:
        m2.metric("Registros", "—")
        m3.metric("Tamaño", "—")

    if st.button("Preparar descarga", type="primary", icon=":material/download:", disabled=summary is None):
        download_params = dict(params)
        if is_torque:
            download_params["format"] = "xlsx" if fmt.startswith("Excel") else "csv"
        try:
            with st.spinner("Generando archivo..."):
                body, content_type = api_request("/torque" if is_torque else "/ftp", download_params, timeout=300)
            if is_torque:
                ext = "xlsx" if fmt.startswith("Excel") else "csv"
                file_name = f"torque_{start}_{end}.{ext}"
            else:
                ext = "zip"
                file_name = f"plc_logs_{start}_{end}.zip"
            st.session_state["dl_ready"] = {"data": body, "name": file_name, "mime": content_type or "application/octet-stream"}
        except RuntimeError as exc:
            st.session_state.pop("dl_ready", None)
            st.error(str(exc))

    ready = st.session_state.get("dl_ready")
    if ready:
        st.download_button(
            f"Guardar {ready['name']}",
            data=ready["data"],
            file_name=ready["name"],
            mime=ready["mime"],
            icon=":material/save:",
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

        **Conteo de producción y tiempos de ciclo:**

        - Las piezas producidas se cuentan por QR final con resultado OK en el PLC.
        - El conteo nunca se infiere dividiendo la cantidad de tornillos.
        - El ASG se usa para estimar los tiempos de ciclo entre grupos consecutivos de apriete.
        - El tiempo de atornillado usa el primer y último torque del grupo ASG.
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
        - Sincronización con launcher externo
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


def render_welcome_curtain():
    if st.session_state.get("welcome_curtain_shown"):
        return

    st.session_state["welcome_curtain_shown"] = True
    theme_type = st.context.theme.type
    if theme_type not in {"light", "dark"}:
        theme_type = "light"

    colors = {
        "light": {
            "background": "#F4F7F4",
            "text": "#1F2328",
            "muted": "#526157",
            "mark": "#166534",
            "track": "#D6E3D8",
            "accent": "#16803C",
        },
        "dark": {
            "background": "#101813",
            "text": "#F4F7F4",
            "muted": "#A7B5AA",
            "mark": "#8DE39E",
            "track": "#29382D",
            "accent": "#36A853",
        },
    }[theme_type]
    logo_data_uri = (
        "data:image/png;base64,"
        + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    )

    curtain_html = """
        <style>
            @keyframes f09-curtain-dismiss {
                0%, 68% { opacity: 1; visibility: visible; }
                100% { opacity: 0; visibility: hidden; }
            }

            @keyframes f09-curtain-arrive {
                from { opacity: 0; transform: translateY(12px); }
                to { opacity: 1; transform: translateY(0); }
            }

            #f09-welcome-curtain {
                position: fixed;
                inset: 0;
                z-index: 999999;
                display: grid;
                place-items: center;
                background: __BACKGROUND__;
                color: __TEXT__;
                pointer-events: none;
                animation: f09-curtain-dismiss 2.2s ease-out forwards;
            }

            #f09-welcome-curtain .f09-welcome-content {
                text-align: center;
                animation: f09-curtain-arrive 0.55s ease-out both;
            }

            #f09-welcome-curtain .f09-welcome-logo {
                display: block;
                width: 220px;
                max-width: 70vw;
                max-height: 96px;
                margin: 0 auto;
                object-fit: contain;
                filter: drop-shadow(0 0 18px __SHADOW__);
            }

            #f09-welcome-curtain .f09-welcome-line-label {
                display: block;
                margin-top: 14px;
                color: __MARK__;
                font: 600 12px/1.2 sans-serif;
                letter-spacing: 0.14em;
            }

            #f09-welcome-curtain .f09-welcome-credit {
                margin: 22px 0 0;
                color: __MUTED__;
                font: 13px/1.5 sans-serif;
                letter-spacing: 0.01em;
            }

            #f09-welcome-curtain h1 {
                margin: 18px 0 8px;
                color: __TEXT__;
                font: 600 30px/1.2 sans-serif;
            }

            #f09-welcome-curtain p {
                margin: 0;
                color: __MUTED__;
                font: 14px/1.5 sans-serif;
                letter-spacing: 0.04em;
            }

            #f09-welcome-curtain .f09-welcome-line {
                width: 150px;
                height: 3px;
                margin: 28px auto 0;
                overflow: hidden;
                border-radius: 3px;
                background: __TRACK__;
            }

            #f09-welcome-curtain .f09-welcome-line::after {
                display: block;
                width: 45%;
                height: 100%;
                border-radius: inherit;
                background: __ACCENT__;
                content: "";
                animation: f09-curtain-progress 1.1s ease-in-out infinite;
            }

            @keyframes f09-curtain-progress {
                from { transform: translateX(-110%); }
                to { transform: translateX(240%); }
            }

            @media (prefers-reduced-motion: reduce) {
                #f09-welcome-curtain,
                #f09-welcome-curtain .f09-welcome-content,
                #f09-welcome-curtain .f09-welcome-line::after {
                    animation-duration: 1ms;
                    animation-iteration-count: 1;
                }
            }
        </style>
        <div id="f09-welcome-curtain" role="status" aria-label="Cargando dashboard F09">
            <div class="f09-welcome-content">
                <img class="f09-welcome-logo" src="__LOGO_DATA_URI__" alt="Logo de la empresa">
                <span class="f09-welcome-line-label">LÍNEA F09</span>
                <h1>ASG Data Acquisition</h1>
                <p>Preparando dashboard de producción</p>
                <div class="f09-welcome-line" aria-hidden="true"></div>
            </div>
        </div>
        """
    for placeholder, color in {
        "__BACKGROUND__": colors["background"],
        "__TEXT__": colors["text"],
        "__MUTED__": colors["muted"],
        "__MARK__": colors["mark"],
        "__TRACK__": colors["track"],
        "__ACCENT__": colors["accent"],
        "__SHADOW__": (
            "rgba(54, 168, 83, 0.25)"
            if theme_type == "dark"
            else "rgba(22, 128, 60, 0.16)"
        ),
        "__LOGO_DATA_URI__": logo_data_uri,
    }.items():
        curtain_html = curtain_html.replace(placeholder, color)

    st.html(curtain_html)


@st.cache_data(show_spinner=False)
def _upscaled_logo_uri(path_str, mtime):
    from io import BytesIO

    from PIL import Image

    img = Image.open(path_str).convert("RGBA")
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def render_login_logo(is_dark):
    if not LOGO_PATH.exists():
        return

    uri = _upscaled_logo_uri(str(LOGO_PATH), LOGO_PATH.stat().st_mtime)
    # El texto del logo es blanco; en tema claro se coloca sobre una placa oscura.
    plate = (
        "background:#16241B;border-radius:14px;padding:12px 22px;"
        if not is_dark
        else ""
    )
    st.html(
        f"""
        <div style="display:flex;justify-content:center;margin-bottom:0.6rem;">
            <div style="{plate}">
                <img src="{uri}" alt="Logo" style="display:block;width:170px;height:auto;">
            </div>
        </div>
        """
    )


def render_login_screen():
    is_dark = st.context.theme.type == "dark"
    palette = {
        "bg1": "#0B120E" if is_dark else "#F4F8F4",
        "bg2": "#14261A" if is_dark else "#DCEBDF",
        "card": "rgba(20, 31, 24, 0.82)" if is_dark else "rgba(255, 255, 255, 0.86)",
        "border": "rgba(141, 227, 158, 0.18)" if is_dark else "rgba(22, 101, 52, 0.14)",
        "text": "#F4F7F4" if is_dark else "#14301F",
        "muted": "#9DB0A2" if is_dark else "#5B6F61",
        "accent": "#8DE39E" if is_dark else "#166534",
        "shadow": "0 30px 80px rgba(0,0,0,0.55)" if is_dark else "0 30px 80px rgba(22,101,52,0.16)",
    }
    css = """
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
        [data-testid="stHeader"] { display: none; }
        html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            overflow: hidden !important;
            height: 100vh;
        }
        [data-testid="stMain"] [data-testid="stMainBlockContainer"] {
            max-height: 100vh;
            overflow: hidden;
        }
        [data-testid="stApp"] {
            background:
                radial-gradient(900px 500px at 15% 10%, __BG2__ 0%, transparent 60%),
                radial-gradient(800px 500px at 90% 95%, __BG2__ 0%, transparent 55%),
                __BG1__;
        }
        .block-container {
            padding-top: 9vh;
            padding-left: 1rem; padding-right: 1rem;
            max-width: 100% !important;
        }
        .st-key-login_card {
            width: min(480px, 100%);
            margin-left: auto !important;
            margin-right: auto !important;
        }
        @keyframes f09-login-in {
            from { opacity: 0; transform: translateY(16px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .st-key-login_card {
            animation: f09-login-in 0.9s cubic-bezier(.2,.7,.2,1) 2.6s both;
            background: __CARD__;
            backdrop-filter: blur(14px);
            border: 1px solid __BORDER__;
            border-top: 3px solid __ACCENT__;
            border-radius: 18px;
            box-shadow: __SHADOW__;
            padding: 2rem 2.2rem 1.6rem;
        }
        .f09-login-head { text-align: center; margin-bottom: 1.2rem; }
        .f09-login-head .eyebrow {
            font-size: 0.72rem; letter-spacing: 0.28em; font-weight: 600;
            color: __ACCENT__;
        }
        .f09-login-head h2 {
            margin: 0.35rem 0 0.2rem; padding: 0; font-size: 1.55rem;
            font-weight: 650; letter-spacing: -0.01em; color: __TEXT__;
        }
        .f09-login-head p { margin: 0; font-size: 0.88rem; color: __MUTED__; }
        .f09-login-rule {
            height: 1px; margin: 1rem auto 0; width: 64px;
            background: linear-gradient(90deg, transparent, __ACCENT__, transparent);
        }
        .st-key-login_card [data-testid="stImage"] img { margin: 0 auto; }
        .st-key-login_card [data-testid="stForm"] {
            border: 0; padding: 0;
        }
        .st-key-login_card label p {
            font-size: 0.78rem; letter-spacing: 0.04em; color: __MUTED__;
        }
        .st-key-login_card [data-baseweb="input"] {
            border-radius: 10px;
        }
        .st-key-login_card button[kind="primaryFormSubmit"],
        .st-key-login_card button[kind="primary"] {
            border-radius: 10px; height: 2.8rem; font-weight: 600;
            letter-spacing: 0.04em; margin-top: 0.4rem;
        }
        .f09-landing-note {
            text-align: center; font-size: 0.9rem; color: __MUTED__;
            margin-bottom: 1rem;
        }
        .st-key-landing_actions { gap: 0.7rem; }
        .st-key-landing_actions button {
            border-radius: 10px; height: 2.9rem; font-weight: 600;
            letter-spacing: 0.04em;
        }
        .f09-login-foot {
            text-align: center; margin-top: 1rem; font-size: 0.72rem;
            color: __MUTED__;
        }
        </style>
        """
    for key, value in {
        "__BG1__": palette["bg1"],
        "__BG2__": palette["bg2"],
        "__CARD__": palette["card"],
        "__BORDER__": palette["border"],
        "__TEXT__": palette["text"],
        "__MUTED__": palette["muted"],
        "__ACCENT__": palette["accent"],
        "__SHADOW__": palette["shadow"],
    }.items():
        css = css.replace(key, value)
    mode_now = st.session_state.get("auth_mode", "landing")
    if not auth.has_users():
        mode_now = "first_admin"
    if mode_now in ("signup", "first_admin"):
        css = css.replace("overflow: hidden !important;\n            height: 100vh;", "overflow-y: auto !important;\n            height: 100vh;")
        css = css.replace("max-height: 100vh;\n            overflow: hidden;", "padding-bottom: 4rem;")
    st.markdown(css, unsafe_allow_html=True)

    with st.container():
        with st.container(key="login_card"):
            render_login_logo(is_dark)
            st.markdown(
                """
                <div class="f09-login-head">
                    <div class="eyebrow">LÍNEA F09</div>
                    <h2>ASG Data Acquisition</h2>
                    <p>Acceso al panel de producción</p>
                    <div class="f09-login-rule"></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            mode = st.session_state.get("auth_mode", "landing")
            if not auth.has_users():
                mode = "first_admin"

            if mode == "landing":
                st.markdown(
                    '<div class="f09-landing-note">Inicia sesión o crea tu cuenta para continuar</div>',
                    unsafe_allow_html=True,
                )
                with st.container(key="landing_actions"):
                    if st.button("Iniciar sesión", type="primary", width="stretch", key="go_login"):
                        st.session_state["auth_mode"] = "login"
                        st.rerun()
                    if st.button("Registrarse", width="stretch", key="go_signup"):
                        st.session_state["auth_mode"] = "signup"
                        st.rerun()

            elif mode == "first_admin":
                st.info("Primer uso: crea la cuenta de administrador.")
                with st.form("first_admin_form"):
                    username = st.text_input("Usuario")
                    name = st.text_input("Nombre completo")
                    password = st.text_input("Contraseña (mín. 8 caracteres)", type="password")
                    confirm = st.text_input("Confirmar contraseña", type="password")
                    submitted = st.form_submit_button(
                        "Crear administrador", type="primary", width="stretch"
                    )
                if submitted:
                    if password != confirm:
                        st.error("Las contraseñas no coinciden.")
                    else:
                        try:
                            auth.create_user(username, password, "admin", name)
                            st.session_state["auth_mode"] = "login"
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))

            elif mode == "signup":
                st.markdown("**Crear cuenta**")
                with st.form("signup_form"):
                    username = st.text_input("Usuario")
                    name = st.text_input("Nombre completo")
                    password = st.text_input("Contraseña (mín. 8 caracteres)", type="password")
                    confirm = st.text_input("Confirmar contraseña", type="password")
                    submitted = st.form_submit_button(
                        "Crear cuenta", type="primary", width="stretch"
                    )
                if submitted:
                    if password != confirm:
                        st.error("Las contraseñas no coinciden.")
                    else:
                        try:
                            auth.register_user(username, password, name)
                            st.session_state["auth_mode"] = "login"
                            st.session_state["auth_notice"] = (
                                "Cuenta creada con rol Operador. Inicia sesión; "
                                "un administrador puede ampliar tus permisos."
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                if st.button("← Volver", key="signup_back", width="stretch"):
                    st.session_state["auth_mode"] = "landing"
                    st.rerun()

            else:
                notice = st.session_state.pop("auth_notice", None)
                if notice:
                    st.success(notice)
                with st.form("login_form"):
                    username = st.text_input("Usuario")
                    password = st.text_input("Contraseña", type="password")
                    submitted = st.form_submit_button(
                        "Iniciar sesión", type="primary", width="stretch"
                    )
                if submitted:
                    user = auth.authenticate(username, password)
                    if user:
                        st.session_state["auth_user"] = user
                        st.session_state["login_transition"] = True
                        render_login_transition(user)
                        st.rerun()
                    else:
                        time.sleep(1)
                        st.error("Usuario o contraseña incorrectos.")
                if st.button("← Volver", key="login_back", width="stretch"):
                    st.session_state["auth_mode"] = "landing"
                    st.rerun()

            st.markdown(
                '<div class="f09-login-foot">Desarrollado por: Ing. Oswaldo Cantú Casillas</div>',
                unsafe_allow_html=True,
            )


ROLE_ICONS = {
    "admin": '<path d="M3 8l4.5 4L12 5l4.5 7L21 8l-2 10H5z"/><rect x="5" y="19" width="14" height="2" rx="1"/>',
    "engineer": '<circle cx="12" cy="12" r="3.2" fill="none" stroke="COLOR" stroke-width="2"/><path d="M12 2l1.2 2.6 2.7.8 2.4-1.5 2 2-1.5 2.4.8 2.7L22 12l-2.4 1.2-.8 2.7 1.5 2.4-2 2-2.4-1.5-2.7.8L12 22l-1.2-2.4-2.7-.8-2.4 1.5-2-2 1.5-2.4-.8-2.7L2 12l2.4-1.2.8-2.7-1.5-2.4 2-2 2.4 1.5 2.7-.8z" fill="none" stroke="COLOR" stroke-width="1.8" stroke-linejoin="round"/>',
    "operator": '<path d="M16 3l5 5-2.2 2.2-5-5z"/><path d="M13.6 6.4l4 4-6.6 6.6-4-4z"/><path d="M9.2 14.8l-3.6 3.6c-.8.8-.8 2 0 2.8.8.8 2 .8 2.8 0l3.6-3.6z"/>',
}


ICON_VIEWBOX = {
    "admin": "0 1 24 24",
    "engineer": "0 0 24 24",
    "operator": "1 0.5 24 24",
}


def render_user_badge(user):
    is_dark = st.context.theme.type == "dark"
    ring = "#8DE39E" if is_dark else "#166534"
    bg = "rgba(141,227,158,0.14)" if is_dark else "rgba(22,101,52,0.10)"
    muted = "#9DB0A2" if is_dark else "#5B6F61"
    role_label = auth.ROLES[user["role"]]
    shapes = ROLE_ICONS.get(user["role"], ROLE_ICONS["operator"]).replace("COLOR", ring)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{ICON_VIEWBOX.get(user['role'], '0 0 24 24')}" fill="{ring}">'
        f"{shapes}</svg>"
    )
    icon_uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    st.html(
        f"""
        <div style="display:flex;align-items:center;justify-content:space-between;gap:14px;padding:6px 0 10px;">
            <div style="flex:1 1 auto;min-width:0;display:flex;flex-direction:column;justify-content:center;line-height:1.3;">
                <div style="font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{html.escape(user['name'])}</div>
                <div style="font-size:0.8rem;color:{muted};">{role_label}</div>
            </div>
            <div title="{role_label}" style="flex:0 0 46px;width:46px;height:46px;border-radius:50%;box-sizing:border-box;
                 display:flex;align-items:center;justify-content:center;
                 background:{bg};border:2px solid {ring};">
                <img src="{icon_uri}" width="24" height="24" alt="{role_label}" style="display:block;">
            </div>
        </div>
        """
    )

def render_login_transition(user):
    is_dark = st.context.theme.type == "dark"
    bg = "#0B120E" if is_dark else "#F4F8F4"
    text = "#F4F7F4" if is_dark else "#14301F"
    accent = "#8DE39E" if is_dark else "#166534"
    muted = "#9DB0A2" if is_dark else "#5B6F61"
    first_name = html.escape(user["name"].split()[0])
    st.html(
        f"""
        <style>
        @keyframes f09-overlay {{
            0%   {{ opacity: 1; visibility: visible; }}
            55%  {{ opacity: 1; visibility: visible; }}
            100% {{ opacity: 0; visibility: hidden; }}
        }}
        @keyframes f09-welcome-text {{
            0%   {{ opacity: 0; transform: translateY(10px); }}
            30%  {{ opacity: 1; transform: translateY(0); }}
            75%  {{ opacity: 1; transform: translateY(0); }}
            100% {{ opacity: 0; transform: translateY(-6px); }}
        }}
        @keyframes f09-app-in {{
            0%, 40% {{ opacity: 0; transform: translateY(14px) scale(0.995); }}
            100%    {{ opacity: 1; transform: none; }}
        }}
        #f09-login-overlay {{
            position: fixed; inset: 0; z-index: 999999; pointer-events: none;
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; gap: 0.5rem; background: {bg};
            animation: f09-overlay 3s ease-in-out forwards;
        }}
        #f09-login-overlay .hello {{
            font-size: 1.8rem; font-weight: 650; color: {text};
            animation: f09-welcome-text 3s ease-in-out forwards;
        }}
        #f09-login-overlay .sub {{
            font-size: 0.85rem; letter-spacing: 0.22em; color: {muted};
            animation: f09-welcome-text 3s ease-in-out forwards;
        }}
        #f09-login-overlay .bar {{
            width: 90px; height: 2px; margin-top: 0.6rem; border-radius: 2px;
            background: linear-gradient(90deg, transparent, {accent}, transparent);
            animation: f09-welcome-text 3s ease-in-out forwards;
        }}
        [data-testid="stMain"] {{ animation: f09-app-in 3s cubic-bezier(.2,.7,.2,1) both; }}
        [data-testid="stSidebar"] {{ animation: f09-app-in 3s cubic-bezier(.2,.7,.2,1) both; }}
        </style>
        <div id="f09-login-overlay" aria-hidden="true">
            <div class="hello">Bienvenido, {first_name}</div>
            <div class="sub">CARGANDO PANEL</div>
            <div class="bar"></div>
        </div>
        """
    )


def render_user_admin():
    st.subheader("Usuarios")
    users = auth.load_users()
    st.dataframe(
        pd.DataFrame(
            [
                {"Usuario": u, "Nombre": d["name"], "Rol": auth.ROLES[d["role"]]}
                for u, d in users.items()
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    with st.form("new_user_form", clear_on_submit=True):
        st.markdown("**Agregar o actualizar usuario**")
        c1, c2 = st.columns(2)
        username = c1.text_input("Usuario")
        name = c2.text_input("Nombre completo")
        c3, c4 = st.columns(2)
        password = c3.text_input("Contraseña (mín. 8 caracteres)", type="password")
        role = c4.selectbox(
            "Rol", list(auth.ROLES), format_func=lambda r: auth.ROLES[r]
        )
        if st.form_submit_button("Guardar usuario", type="primary"):
            try:
                auth.create_user(username, password, role, name)
                st.success("Usuario guardado.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    deletable = [u for u in users if u != st.session_state["auth_user"]["username"]]
    if deletable:
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        to_delete = c1.selectbox("Eliminar usuario", deletable)
        if c2.button("Eliminar", width="stretch"):
            auth.delete_user(to_delete)
            st.rerun()


def main():
    render_welcome_curtain()

    if "auth_user" not in st.session_state:
        render_login_screen()
        st.stop()

    current_user = st.session_state["auth_user"]
    st.session_state.pop("auth_mode", None)
    if st.session_state.pop("login_transition", False):
        render_login_transition(current_user)

    with st.sidebar:
        render_user_badge(current_user)
        if st.button("Cerrar sesión", icon=":material/logout:", width="stretch"):
            for key in ("auth_user", "dl_panel_open", "egg_open"):
                st.session_state.pop(key, None)
            st.rerun()

        st.header("Vista")

        selected_page = st.radio(
            "Selecciona vista",
            [
                "Vista general",
                "Trazabilidad QR",
                "Tiempos de ciclo",
                "Capacidad",
                "MSA",
                "Tabla de datos",
                "Configuración",
                "Acerca del sistema",
            ],
            index=0,
        )

        st.header("Controles")

        model_options = ["T1XX-1", "T1XX-2"]
        launcher_model = get_launcher_selected_model(default_model="T1XX-1")

        selected_model = st.radio(
            "Modelo de corrida",
            model_options,
            index=model_options.index(launcher_model) if launcher_model in model_options else 0,
            key=f"selected_model_from_launcher_{launcher_model}",
        )

        st.caption(f"Modelo recibido del launcher: {launcher_model}")

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
            key=f"selected_devices_{selected_model}",
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

        with st.expander("Ajustes de gráficas"):
            show_markers = st.checkbox("Mostrar marcadores", value=True)
            show_limits = st.checkbox("Mostrar límites de torque", value=True)
            auto_scale = st.checkbox("Autoescala", value=True)

            station_chart_columns = st.slider(
                "Columnas por estación",
                min_value=1,
                max_value=4,
                value=2,
                step=1,
            )

            station_chart_height = st.slider(
                "Altura de gráfica",
                min_value=250,
                max_value=600,
                value=330,
                step=50,
            )

            show_angle_station_grid = st.checkbox(
                "Mostrar gráfica de ángulo",
                value=False,
            )

        st.divider()

        auto_refresh = st.checkbox("Auto refresh", value=True)
        refresh_seconds = st.slider("Refresh segundos", 1, 30, 1)

        st.markdown(
            """
            <style>
            .st-key-easter_egg_box { margin-top: -0.5rem; }
            .st-key-easter_egg_box button {
                opacity: 0;
                min-height: 0.9rem;
                height: 0.9rem;
                padding: 0;
                border: 0;
                cursor: default;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.container(key="easter_egg_box"):
            if st.button("\u200b", key="easter_egg_btn", width="stretch"):
                st.session_state["egg_open"] = True

    if st.session_state.get("egg_open", False):
        show_easter_egg()

    render_dashboard_header(selected_model, selected_page)

    def load_view_data():
        """Carga y filtra los datos; se llama dentro de cada fragmento para leer siempre lo más reciente."""
        status_df = status_to_dataframe(load_status())
        status_df_for_view = filter_status_for_model(
            status_df=status_df,
            selected_model=selected_model,
            devices_for_model=devices_for_model,
        )
        plc_status_df = status_to_dataframe(load_plc_status())
        plc_status_df_for_view = filter_plc_status_for_model(
            status_df=plc_status_df,
            devices_for_model=devices_for_model,
        )

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

        return SimpleNamespace(
            status_df_for_view=status_df_for_view,
            plc_status_df_for_view=plc_status_df_for_view,
            clean_df=clean_df,
            active_session_info=active_session_info,
            clean_df_for_view=clean_df_for_view,
            filtered_df=filtered_df,
        )

    live_pages = [
        "Vista general",
        "Trazabilidad QR",
        "Tabla de datos",
    ]

    # Cada pestaña es un fragmento: solo se redibuja la que está abierta, sin reejecutar el script completo.
    paused = st.session_state.get("dl_panel_open", False) or st.session_state.get("egg_open", False)
    station_run_every = refresh_seconds if auto_refresh and not paused else None
    main_run_every = station_run_every if selected_page in live_pages else None

    tab_main, tab_station = st.tabs(
        ["Vista", "Dashboard por estación"],
        key="main_tabs",
        on_change="rerun",
    )

    def render_main_view():
        data = load_view_data()
        clean_df = data.clean_df
        clean_df_for_view = data.clean_df_for_view
        filtered_df = data.filtered_df
        active_session_info = data.active_session_info

        if selected_page == "Vista general":
            notify_new_result(clean_df, selected_model)
            if auth.can(current_user, "download"):
                render_download_banner()
            render_summary_metrics(filtered_df)
            render_device_status(data.status_df_for_view)
            render_plc_ftp_status(data.plc_status_df_for_view)

        elif selected_page == "Trazabilidad QR":
            plc_pieces_df, plc_attempts_df, plc_tightenings_df = (
                load_plc_traceability_data()
            )

            render_qr_traceability_tab(
                pieces_df=plc_pieces_df,
                attempts_df=plc_attempts_df,
                tightenings_df=plc_tightenings_df,
                asg_df=clean_df_for_view,
                selected_model=selected_model,
                selected_devices=selected_devices,
                selected_result=selected_result,
                count_scope=count_scope,
                active_session_info=active_session_info,
                time_window_minutes=time_window_minutes,
                last_n=last_n,
            )

        elif selected_page == "Tiempos de ciclo":
            cycle_base_df = filter_cycle_base_data(
                clean_df_for_view,
                selected_model=selected_model,
                selected_devices=selected_devices,
                last_n=last_n,
                time_window_minutes=time_window_minutes,
            )

            plc_pieces_df, _, _ = load_plc_traceability_data()
            plc_pieces_for_count = filter_plc_traceability_data(
                plc_pieces_df,
                selected_model=selected_model,
                selected_devices=selected_devices,
                count_scope=count_scope,
                active_session_info=active_session_info,
                time_window_minutes=None,
                last_n=None,
            )

            render_cycle_time_tab(
                cycle_base_df=cycle_base_df,
                plc_pieces_df=plc_pieces_for_count,
                devices_for_model=devices_for_model,
                selected_model=selected_model,
            )

        elif selected_page == "Capacidad":
            asg_capability_df = filter_data(
                clean_df_for_view,
                selected_model=selected_model,
                selected_devices=selected_devices,
                selected_result="Todos",
                last_n=last_n,
                time_window_minutes=time_window_minutes,
            )
            _, _, plc_tightenings_df = load_plc_traceability_data()
            plc_capability_df = filter_plc_traceability_data(
                plc_tightenings_df,
                selected_model=selected_model,
                selected_devices=selected_devices,
                count_scope=count_scope,
                active_session_info=active_session_info,
                time_window_minutes=time_window_minutes,
                last_n=last_n,
            )
            capability_base_df = prepare_hybrid_capability_data(
                asg_df=asg_capability_df,
                plc_tightenings_df=plc_capability_df,
            )

            render_capability_tab(
                capability_df=capability_base_df,
                devices_for_model=devices_for_model,
                selected_model=selected_model,
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
            if auth.can(current_user, "manage_users"):
                render_user_admin()
        elif selected_page == "Acerca del sistema":
            render_about_tab()

        if auto_refresh and selected_page not in live_pages:
            st.caption(
                "Auto-refresh pausado en esta vista para evitar el efecto nublado. "
                "Solo se actualiza en Vista general, Trazabilidad QR, Tabla de datos "
                "y Dashboard por estación."
            )

    def render_station_view():
        data = load_view_data()
        render_station_dashboard_grid(
            data.filtered_df,
            selected_devices=selected_devices,
            devices_for_model=devices_for_model,
            show_markers=show_markers,
            show_limits=show_limits,
            auto_scale=auto_scale,
            columns_per_row=station_chart_columns,
            show_angle_chart=show_angle_station_grid,
            chart_height=station_chart_height,
        )

    if tab_main.open:
        with tab_main:
            st.fragment(run_every=main_run_every)(render_main_view)()

    if tab_station.open:
        with tab_station:
            st.fragment(run_every=station_run_every)(render_station_view)()


if __name__ == "__main__":
    main()
