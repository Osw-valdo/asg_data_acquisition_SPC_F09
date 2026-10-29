"""API de solo lectura para descargar datos procesados de la línea F09.

Ejecutar:  .venv\\Scripts\\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8600
Documentación interactiva: http://<servidor>:8600/docs
"""

import csv
import io
import re
import zipfile
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from threading import Lock

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import auth

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PLC_RAW_DIR = PROJECT_ROOT / "data" / "plc_raw"
LOG_FILE = PROJECT_ROOT / "logs" / "api_downloads.csv"

MAX_RANGE_DAYS = 31
MAX_ZIP_BYTES = 500 * 1024 * 1024
MASTER_PATTERN = re.compile(r"asg_clean_master_(\d{4}-\d{2}-\d{2})\.csv$")
PLC_LOG_PATTERN = re.compile(r"^(\d{6})_Log\.csv$")
PLC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")

LOG_COLUMNS = ["timestamp", "client_ip", "user", "endpoint", "params", "rows_or_files", "bytes"]
_log_lock = Lock()

bearer_scheme = HTTPBearer(auto_error=False, description="Token emitido por el dashboard al iniciar sesión.")


def require_download(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(401, "Falta el token de autenticación.", headers={"WWW-Authenticate": "Bearer"})
    user = auth.verify_api_token(credentials.credentials)
    if user is None:
        raise HTTPException(401, "Token inválido o vencido.", headers={"WWW-Authenticate": "Bearer"})
    if not auth.can(user, "download"):
        raise HTTPException(403, "Tu rol no tiene permiso para descargar datos.")
    request.state.user = user["username"]
    return user


app = FastAPI(
    title="API de datos F09",
    version="0.2.0",
    description="Descarga de datos procesados de atornillado (ASG) y logfiles de PLC por FTP.",
)


class TorqueFormat(str, Enum):
    csv = "csv"
    xlsx = "xlsx"


def _validate_range(start: date, end: date):
    if end < start:
        raise HTTPException(400, "La fecha final no puede ser anterior a la inicial.")
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS:
        raise HTTPException(400, f"El rango máximo es de {MAX_RANGE_DAYS} días (solicitaste {days}).")


def _log_download(request: Request, endpoint: str, params: dict, count: int, size: int):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = [
        datetime.now().isoformat(timespec="seconds"),
        request.client.host if request.client else "",
        getattr(request.state, "user", ""),
        endpoint,
        "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, "")),
        count,
        size,
    ]
    with _log_lock:
        is_new = not LOG_FILE.exists()
        with LOG_FILE.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(LOG_COLUMNS)
            writer.writerow(row)


def _torque_files(start: date, end: date):
    files = []
    if not PROCESSED_DIR.exists():
        return files
    for path in sorted(PROCESSED_DIR.glob("asg_clean_master_*.csv")):
        match = MASTER_PATTERN.search(path.name)
        if match and start <= date.fromisoformat(match.group(1)) <= end:
            files.append(path)
    return files


def _load_torque(start, end, station, model, result, include_raw):
    frames = [pd.read_csv(p, dtype=str, keep_default_na=False) for p in _torque_files(start, end)]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    if station:
        wanted = {s.strip() for s in station.split(",") if s.strip()}
        df = df[df["device_id"].isin(wanted)]
    if model:
        df = df[df["running_model"] == model]
    if result:
        df = df[df["tightening_status"].str.upper() == result.upper()]
    if not include_raw and "raw" in df.columns:
        df = df.drop(columns=["raw"])
    return df.reset_index(drop=True)


def _plc_files(start: date, end: date, plc_id: str | None):
    if not PLC_RAW_DIR.exists():
        return []
    if plc_id and not PLC_ID_PATTERN.match(plc_id):
        raise HTTPException(400, "Identificador de PLC inválido.")

    folders = [PLC_RAW_DIR / plc_id] if plc_id else sorted(p for p in PLC_RAW_DIR.iterdir() if p.is_dir())
    found = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*_Log.csv")):
            match = PLC_LOG_PATTERN.match(path.name)
            if not match:
                continue
            try:
                day = datetime.strptime(match.group(1), "%y%m%d").date()
            except ValueError:
                continue
            if start <= day <= end:
                found.append(path)
    return found


@app.get("/health", summary="Estado del servicio")
def health():
    return {
        "status": "ok",
        "torque_days_available": len(list(PROCESSED_DIR.glob("asg_clean_master_*.csv"))) if PROCESSED_DIR.exists() else 0,
        "plc_folders": len([p for p in PLC_RAW_DIR.iterdir() if p.is_dir()]) if PLC_RAW_DIR.exists() else 0,
    }


@app.get("/torque/summary", dependencies=[Depends(require_download)], summary="Resumen previo a la descarga de torque")
def torque_summary(
    start: date,
    end: date,
    station: str | None = Query(None, description="IDs separados por coma, p. ej. ASG_01,ASG_02"),
    model: str | None = None,
    result: str | None = Query(None, description="OK o NOK"),
):
    _validate_range(start, end)
    df = _load_torque(start, end, station, model, result, include_raw=False)
    return {
        "days_with_data": len(_torque_files(start, end)),
        "rows": int(len(df)),
        "stations": sorted(df["device_id"].unique().tolist()) if not df.empty else [],
    }


@app.get("/torque", dependencies=[Depends(require_download)], summary="Descargar torque de atornillado (CSV o Excel)")
def torque_download(
    request: Request,
    start: date,
    end: date,
    station: str | None = Query(None, description="IDs separados por coma"),
    model: str | None = None,
    result: str | None = Query(None, description="OK o NOK"),
    format: TorqueFormat = TorqueFormat.csv,
    include_raw: bool = False,
):
    _validate_range(start, end)
    df = _load_torque(start, end, station, model, result, include_raw)
    if df.empty:
        raise HTTPException(404, "No hay datos para los filtros seleccionados.")

    name = f"torque_{start}_{end}"
    if format == TorqueFormat.xlsx:
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, sheet_name="torque")
        body = buffer.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        body = df.to_csv(index=False).encode("utf-8-sig")
        media = "text/csv"
        ext = "csv"

    _log_download(
        request,
        "/torque",
        {"start": start, "end": end, "station": station, "model": model, "result": result, "format": format.value},
        len(df),
        len(body),
    )
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'},
    )


@app.get("/ftp/files", dependencies=[Depends(require_download)], summary="Listar logfiles de PLC disponibles en el rango")
def ftp_files(start: date, end: date, plc_id: str | None = None):
    _validate_range(start, end)
    files = _plc_files(start, end, plc_id)
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "plcs": sorted({p.parent.name for p in files}),
    }


@app.get("/ftp", dependencies=[Depends(require_download)], summary="Descargar logfiles de PLC en un ZIP")
def ftp_download(request: Request, start: date, end: date, plc_id: str | None = None):
    _validate_range(start, end)
    files = _plc_files(start, end, plc_id)
    if not files:
        raise HTTPException(404, "No hay logfiles para los filtros seleccionados.")
    if sum(p.stat().st_size for p in files) > MAX_ZIP_BYTES:
        raise HTTPException(413, "La selección excede el tamaño máximo permitido; reduce el rango.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=f"{path.parent.name}/{path.name}")
    size = buffer.getbuffer().nbytes
    buffer.seek(0)

    _log_download(request, "/ftp", {"start": start, "end": end, "plc_id": plc_id}, len(files), size)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="plc_logs_{start}_{end}.zip"'},
    )
