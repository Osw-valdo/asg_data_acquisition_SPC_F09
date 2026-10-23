from __future__ import annotations

from csv import DictReader
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "plc_devices.csv"

REQUIRED_COLUMNS = {
    "enabled",
    "device_id",
    "station",
    "module_name",
    "ip",
    "ftp_port",
    "models",
    "process",
    "station_type",
    "torque_unit",
    "screws_per_piece_t1xx1",
    "screws_per_piece_t1xx2",
}


def load_devices(config_file: Path = CONFIG_FILE) -> list[dict]:
    if not config_file.exists():
        raise FileNotFoundError(f"No existe la configuración: {config_file}")

    with config_file.open("r", encoding="utf-8-sig", newline="") as file:
        reader = DictReader(file)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - columns)

        if missing:
            raise ValueError(f"Faltan columnas en plc_devices.csv: {missing}")

        devices = []

        for row_number, row in enumerate(reader, start=2):
            if str(row.get("enabled", "")).strip() not in {"1", "true", "True"}:
                continue

            device = {key: str(value or "").strip() for key, value in row.items()}

            if not device["device_id"] or not device["ip"]:
                raise ValueError(
                    f"Fila {row_number}: device_id e ip son obligatorios."
                )

            try:
                device["ftp_port"] = int(device["ftp_port"])
            except ValueError as error:
                raise ValueError(
                    f"Fila {row_number}: ftp_port debe ser numérico."
                ) from error

            devices.append(device)

    ids = [device["device_id"] for device in devices]
    ips = [device["ip"] for device in devices]

    if len(ids) != len(set(ids)):
        raise ValueError("Hay device_id duplicados en plc_devices.csv.")

    if len(ips) != len(set(ips)):
        raise ValueError("Hay IP duplicadas en plc_devices.csv.")

    return devices


def get_enabled_devices() -> list[dict]:
    return load_devices()


def get_devices_by_model(model: str) -> list[dict]:
    model = model.strip()
    return [
        device
        for device in load_devices()
        if model in device["models"].split("|")
    ]
