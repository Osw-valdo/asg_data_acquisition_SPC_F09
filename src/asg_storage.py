from pathlib import Path
from datetime import datetime
import csv
import re
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
DATABASE_DIR = PROJECT_ROOT / "database"

MASTER_DB = DATABASE_DIR / "asg_torque_master.db"

TABLE_NAME = "tightening_results"


CLEAN_COLUMNS = [
    "pc_timestamp",
    "mid",
    "revision",
    "length",

    "device_id",
    "station",
    "module_name",
    "ip",
    "port",
    "models_supported",
    "running_model",
    "process",
    "station_type",
    "torque_unit",

    "is_tightening_result",
    "parse_status",

    "cell_id",
    "channel_id",
    "controller_name",
    "vin",
    "job_id",

    "parameter_set_id",
    "batch_size",
    "batch_counter",

    "tightening_status_code",
    "tightening_status",

    "torque_status_code",
    "torque_status",

    "angle_status_code",
    "angle_status",

    "torque_min",
    "torque_max",
    "torque_target",
    "torque_actual",

    "angle_min",
    "angle_max",
    "angle_target",
    "angle_actual",

    "timestamp_asg_raw",
    "timestamp_asg",

    "parameter_change_timestamp_raw",
    "parameter_change_timestamp",

    "batch_status_code",
    "batch_status",

    "tightening_id",

    "torque_valid_python",
    "angle_valid_python",
    "result_valid_python",
    "validation_match_asg",

    "raw",
]


INTEGER_COLUMNS = {
    "port",
    "is_tightening_result",
    "parameter_set_id",
    "batch_size",
    "batch_counter",
    "angle_min",
    "angle_max",
    "angle_target",
    "angle_actual",
    "torque_valid_python",
    "angle_valid_python",
    "result_valid_python",
    "validation_match_asg",
}


REAL_COLUMNS = {
    "torque_min",
    "torque_max",
    "torque_target",
    "torque_actual",
}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def sanitize_folder_name(value: str) -> str:
    """
    Convierte nombres de estación en nombres seguros para carpetas.
    """
    value = str(value).strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^\w\s\-]", "", value)
    value = re.sub(r"\s+", "_", value)
    return value or "UNKNOWN"


def normalize_value(value):
    """
    Convierte valores Python a valores seguros para CSV/SQLite.
    """
    if value is None:
        return ""

    if isinstance(value, bool):
        return int(value)

    return value


def normalize_row(row: dict) -> dict:
    """
    Asegura que todas las columnas existan y estén en orden.
    """
    return {col: normalize_value(row.get(col, "")) for col in CLEAN_COLUMNS}


def get_station_base_dir(row: dict) -> Path:
    """
    Carpeta base por modelo y estación.

    Ejemplo:
    data/stations/T1XX-1/Heatsink_Assy_and_Screwing/
    """
    running_model = sanitize_folder_name(row.get("running_model", "UNKNOWN_MODEL"))
    station = row.get("station") or row.get("device_id") or "UNKNOWN_STATION"
    station = sanitize_folder_name(station)

    return DATA_DIR / "stations" / running_model / station


def get_station_paths(row: dict) -> dict:
    """
    Regresa las rutas específicas de una estación.
    """
    base_dir = get_station_base_dir(row)
    date = today_str()

    return {
        "base_dir": base_dir,
        "processed_dir": base_dir / "processed",
        "database_dir": base_dir / "database",
        "clean_csv": base_dir / "processed" / f"asg_clean_{date}.csv",
        "station_db": base_dir / "database" / "asg_torque.db",
    }


def get_master_paths() -> dict:
    """
    Regresa rutas master generales del proyecto.
    """
    date = today_str()

    return {
        "master_clean_csv": PROCESSED_DIR / f"asg_clean_master_{date}.csv",
        "master_db": MASTER_DB,
    }


def append_csv(file_path: Path, row: dict):
    """
    Agrega una fila a un CSV. Si el archivo no existe, crea encabezados.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    clean_row = normalize_row(row)
    file_exists = file_path.exists()

    with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CLEAN_COLUMNS)

        if not file_exists:
            writer.writeheader()

        writer.writerow(clean_row)


def sqlite_column_type(column: str) -> str:
    if column in INTEGER_COLUMNS:
        return "INTEGER"
    if column in REAL_COLUMNS:
        return "REAL"
    return "TEXT"


def ensure_database(db_path: Path):
    """
    Crea la base de datos y tabla principal si no existen.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    columns_sql = []
    for col in CLEAN_COLUMNS:
        columns_sql.append(f"{col} {sqlite_column_type(col)}")

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inserted_at TEXT NOT NULL,
        {", ".join(columns_sql)}
    );
    """

    with sqlite3.connect(db_path) as conn:
        conn.execute(create_sql)
        conn.commit()


def insert_sqlite(db_path: Path, row: dict):
    """
    Inserta una fila cocinada en SQLite.
    """
    ensure_database(db_path)

    clean_row = normalize_row(row)

    columns = ["inserted_at"] + CLEAN_COLUMNS
    placeholders = ", ".join(["?"] * len(columns))
    columns_joined = ", ".join(columns)

    values = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    values.extend([clean_row[col] for col in CLEAN_COLUMNS])

    insert_sql = f"""
    INSERT INTO {TABLE_NAME} ({columns_joined})
    VALUES ({placeholders});
    """

    with sqlite3.connect(db_path) as conn:
        conn.execute(insert_sql, values)
        conn.commit()


def save_clean_result(row: dict) -> dict:
    """
    Guarda un resultado cocinado en:

    1. CSV por estación
    2. DB por estación
    3. CSV master
    4. DB master
    """
    row = normalize_row(row)

    station_paths = get_station_paths(row)
    master_paths = get_master_paths()

    append_csv(station_paths["clean_csv"], row)
    insert_sqlite(station_paths["station_db"], row)

    append_csv(master_paths["master_clean_csv"], row)
    insert_sqlite(master_paths["master_db"], row)

    return {
        "station_clean_csv": str(station_paths["clean_csv"]),
        "station_db": str(station_paths["station_db"]),
        "master_clean_csv": str(master_paths["master_clean_csv"]),
        "master_db": str(master_paths["master_db"]),
    }


def print_storage_summary(row: dict):
    """
    Imprime dónde se guardaría una fila.
    """
    station_paths = get_station_paths(row)
    master_paths = get_master_paths()

    print("=" * 80)
    print("RUTAS DE GUARDADO")
    print("=" * 80)
    print(f"CSV estación: {station_paths['clean_csv']}")
    print(f"DB estación:  {station_paths['station_db']}")
    print(f"CSV master:   {master_paths['master_clean_csv']}")
    print(f"DB master:    {master_paths['master_db']}")
    print("=" * 80)


if __name__ == "__main__":
    sample_row = {
        "pc_timestamp": "2026-07-14 12:00:00",
        "mid": "0061",
        "revision": "001",
        "length": "0231",

        "device_id": "ASG_TEST",
        "station": "TEST STATION",
        "module_name": "TEST-MODULE",
        "ip": "10.132.160.000",
        "port": 4545,
        "models_supported": "T1XX-1|T1XX-2",
        "running_model": "T1XX-1",
        "process": "TEST",
        "station_type": "shared",
        "torque_unit": "cN·m",

        "is_tightening_result": True,
        "parse_status": "OK",

        "torque_min": 90.0,
        "torque_max": 118.0,
        "torque_target": 103.0,
        "torque_actual": 103.0,

        "angle_min": 0,
        "angle_max": 0,
        "angle_target": 0,
        "angle_actual": 80,

        "tightening_status": "OK",
        "torque_status": "OK",
        "angle_status": "OK",
        "batch_status": "NOT_USED",

        "tightening_id": "7803",

        "torque_valid_python": True,
        "angle_valid_python": True,
        "result_valid_python": True,
        "validation_match_asg": True,

        "raw": "RAW_TEST",
    }

    print_storage_summary(sample_row)
    saved_paths = save_clean_result(sample_row)

    print()
    print("Guardado de prueba terminado:")
    for key, value in saved_paths.items():
        print(f"{key}: {value}")
