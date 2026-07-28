from pathlib import Path
from datetime import datetime
import argparse
import csv
import json
import logging
import queue
import socket
import threading
import time

from src.asg_config import get_devices_by_model
from src.asg_acop import (
    connect_asg,
    start_communication,
    subscribe_tightening_results,
    acknowledge_tightening_result,
    send_keepalive,
    split_null_terminated_packets,
    decode_packet,
    get_mid_from_raw,
    get_revision_from_raw,
    KEEPALIVE_SECONDS,
)
from src.asg_parser import parse_mid0061_rev001, parse_packet
from src.asg_storage import save_clean_result, sanitize_folder_name


PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_LIVE_DIR = DATA_DIR / "raw_live"
LOG_DIR = PROJECT_ROOT / "logs"

STATUS_FILE = LOG_DIR / "device_status.json"
COLLECTOR_LOG = LOG_DIR / "collector.log"

RAW_COLUMNS = [
    "pc_timestamp",
    "device_id",
    "station",
    "module_name",
    "ip",
    "port",
    "models_supported",
    "running_model",
    "process",
    "station_type",
    "mid",
    "revision",
    "length",
    "data",
    "raw",
]

STATUS_LOCK = threading.Lock()
STATUS_FILE_LOCK = threading.Lock()
DEVICE_STATUS = {}


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=COLLECTOR_LOG,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        encoding="utf-8",
    )


def normalize_device_info(device_row) -> dict:
    """
    Convierte una fila de pandas a dict simple.
    """
    info = device_row.to_dict()

    return {
        "device_id": str(info.get("device_id", "")).strip(),
        "station": str(info.get("station", "")).strip(),
        "module_name": str(info.get("module_name", "")).strip(),
        "ip": str(info.get("ip", "")).strip(),
        "port": int(info.get("port", 4545)),
        "models": str(info.get("models", "")).strip(),
        "process": str(info.get("process", "")).strip(),
        "station_type": str(info.get("station_type", "")).strip(),
        "torque_unit": str(info.get("torque_unit", "cN·m")).strip(),
    }


def init_device_status(device_info: dict, running_model: str):
    device_id = device_info["device_id"]

    with STATUS_LOCK:
        DEVICE_STATUS[device_id] = {
            "device_id": device_id,
            "station": device_info["station"],
            "module_name": device_info["module_name"],
            "ip": device_info["ip"],
            "port": device_info["port"],
            "models_supported": device_info["models"],
            "running_model": running_model,
            "process": device_info["process"],
            "station_type": device_info["station_type"],
            "connected": False,
            "state": "initialized",
            "last_heartbeat": now_str(),
            "last_mid": "",
            "last_result_pc_timestamp": "",
            "last_error": "",
            "total_messages": 0,
            "total_results": 0,
        }

    write_status_file()


def update_device_status(device_id: str, **kwargs):
    with STATUS_LOCK:
        if device_id not in DEVICE_STATUS:
            DEVICE_STATUS[device_id] = {}

        DEVICE_STATUS[device_id].update(kwargs)
        DEVICE_STATUS[device_id]["last_heartbeat"] = now_str()

    write_status_file()

def write_status_file():
    """
    Escribe el estado de dispositivos.

    En Windows evitamos usar archivo .tmp + replace porque puede generar
    PermissionError si el JSON está siendo leído por otro proceso.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        with STATUS_LOCK:
            devices_copy = json.loads(json.dumps(DEVICE_STATUS, ensure_ascii=False))
            payload = {
                "collector_timestamp": now_str(),
                "devices": devices_copy,
            }

        with STATUS_FILE_LOCK:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)

    except Exception as e:
        logging.warning("No se pudo escribir device_status.json: %s", e)

def append_csv(file_path: Path, row: dict, columns: list[str]):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = file_path.exists()

    clean_row = {col: row.get(col, "") for col in columns}

    with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if not file_exists:
            writer.writeheader()

        writer.writerow(clean_row)


def get_master_raw_file() -> Path:
    return RAW_LIVE_DIR / f"asg_raw_live_{today_str()}.csv"


def get_station_raw_file(raw_row: dict) -> Path:
    running_model = sanitize_folder_name(raw_row.get("running_model", "UNKNOWN_MODEL"))
    station = sanitize_folder_name(raw_row.get("station", raw_row.get("device_id", "UNKNOWN_STATION")))

    return (
        DATA_DIR
        / "stations"
        / running_model
        / station
        / "raw"
        / f"asg_raw_{today_str()}.csv"
    )


def build_raw_row(raw_packet: str, device_info: dict, running_model: str) -> dict:
    packet = parse_packet(raw_packet)

    return {
        "pc_timestamp": now_str(),
        "device_id": device_info.get("device_id", ""),
        "station": device_info.get("station", ""),
        "module_name": device_info.get("module_name", ""),
        "ip": device_info.get("ip", ""),
        "port": device_info.get("port", ""),
        "models_supported": device_info.get("models", ""),
        "running_model": running_model,
        "process": device_info.get("process", ""),
        "station_type": device_info.get("station_type", ""),
        "mid": packet.get("mid", ""),
        "revision": packet.get("revision", ""),
        "length": packet.get("length", ""),
        "data": packet.get("data", ""),
        "raw": packet.get("raw", ""),
    }


def writer_worker(event_queue: queue.Queue, stop_event: threading.Event):
    """
    Un solo hilo escritor.

    Esto evita que 10 hilos intenten escribir SQLite al mismo tiempo.
    """
    logging.info("Writer iniciado")

    while not stop_event.is_set() or not event_queue.empty():
        try:
            event = event_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            event_type = event.get("type")
            row = event.get("row", {})

            if event_type == "raw":
                append_csv(get_master_raw_file(), row, RAW_COLUMNS)
                append_csv(get_station_raw_file(row), row, RAW_COLUMNS)

            elif event_type == "clean":
                save_clean_result(row)

        except Exception as e:
            logging.exception("Error guardando evento: %s", e)

        finally:
            event_queue.task_done()

    logging.info("Writer detenido")


def device_worker(
    device_info: dict,
    running_model: str,
    event_queue: queue.Queue,
    stop_event: threading.Event,
):
    device_id = device_info["device_id"]
    ip = device_info["ip"]
    port = device_info["port"]

    logging.info("Iniciando worker %s | %s:%s", device_id, ip, port)

    retry_seconds = 5

    while not stop_event.is_set():
        sock = None

        try:
            update_device_status(
                device_id,
                connected=False,
                state="connecting",
                last_error="",
            )

            print(f"[{device_id}] Conectando a {ip}:{port}...")
            sock = connect_asg(ip, port)

            update_device_status(
                device_id,
                connected=True,
                state="connected",
                last_error="",
            )

            print(f"[{device_id}] Conexión TCP exitosa.")
            logging.info("%s conectado", device_id)

            start_communication(sock)
            time.sleep(0.5)
            subscribe_tightening_results(sock)

            update_device_status(device_id, state="subscribed")

            print(f"[{device_id}] Suscrito a resultados MID 0061.")

            buffer = b""
            last_keepalive = time.time()

            while not stop_event.is_set():
                if time.time() - last_keepalive >= KEEPALIVE_SECONDS:
                    send_keepalive(sock)
                    last_keepalive = time.time()
                    update_device_status(device_id, last_mid="9999")

                try:
                    chunk = sock.recv(4096)

                    if not chunk:
                        raise ConnectionError("El ASG cerró la conexión.")

                    buffer += chunk
                    packets, buffer = split_null_terminated_packets(buffer)

                    for packet in packets:
                        raw_packet = decode_packet(packet)
                        mid = get_mid_from_raw(raw_packet)
                        revision = get_revision_from_raw(raw_packet)

                        raw_row = build_raw_row(raw_packet, device_info, running_model)
                        event_queue.put({"type": "raw", "row": raw_row})

                        with STATUS_LOCK:
                            total_messages = DEVICE_STATUS[device_id].get("total_messages", 0) + 1

                        update_device_status(
                            device_id,
                            connected=True,
                            state="receiving",
                            last_mid=mid,
                            total_messages=total_messages,
                        )

                        if mid == "0002":
                            print(f"[{device_id}] MID 0002 Comunicación iniciada.")

                        elif mid == "0005":
                            print(f"[{device_id}] MID 0005 Comando aceptado.")

                        elif mid == "0004":
                            print(f"[{device_id}] MID 0004 Comando rechazado.")
                            logging.warning("%s recibió MID 0004", device_id)

                        elif mid == "0061":
                            clean_row = parse_mid0061_rev001(
                                raw_packet=raw_packet,
                                device_info=device_info,
                                running_model=running_model,
                            )

                            event_queue.put({"type": "clean", "row": clean_row})

                            with STATUS_LOCK:
                                total_results = DEVICE_STATUS[device_id].get("total_results", 0) + 1

                            update_device_status(
                                device_id,
                                connected=True,
                                state="result_received",
                                last_mid=mid,
                                last_result_pc_timestamp=now_str(),
                                total_results=total_results,
                            )

                            torque = clean_row.get("torque_actual", "")
                            angle = clean_row.get("angle_actual", "")
                            result = clean_row.get("tightening_status", "")
                            tightening_id = clean_row.get("tightening_id", "")

                            print(
                                f"[{device_id}] Resultado | "
                                f"Torque: {torque} | "
                                f"Ángulo: {angle} | "
                                f"ASG: {result} | "
                                f"ID: {tightening_id}"
                            )

                            acknowledge_tightening_result(sock)

                except socket.timeout:
                    continue

        except Exception as e:
            msg = str(e)
            print(f"[{device_id}] Error: {msg}")
            logging.exception("%s error: %s", device_id, msg)

            update_device_status(
                device_id,
                connected=False,
                state="error_retrying",
                last_error=msg,
            )

            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

            if not stop_event.is_set():
                time.sleep(retry_seconds)

    update_device_status(
        device_id,
        connected=False,
        state="stopped",
    )

    logging.info("Worker %s detenido", device_id)


def print_startup_summary(devices, running_model: str):
    print("=" * 90)
    print("ASG DATA ACQUISITION SPC F09 - MULTI IP COLLECTOR")
    print("=" * 90)
    print(f"Modelo de corrida: {running_model}")
    print(f"Equipos a monitorear: {len(devices)}")
    print()
    print("Equipos:")
    for _, row in devices.iterrows():
        print(
            f"- {row['device_id']} | "
            f"{row['station']} | "
            f"{row['module_name']} | "
            f"{row['ip']}:{row['port']} | "
            f"{row['models']} | "
            f"{row['process']} | "
            f"{row['station_type']}"
        )
    print()
    print(f"RAW master:     {get_master_raw_file()}")
    print(f"DB master:      {PROJECT_ROOT / 'database' / 'asg_torque_master.db'}")
    print(f"Status JSON:    {STATUS_FILE}")
    print(f"Log collector:  {COLLECTOR_LOG}")
    print("=" * 90)
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Colector multi-IP para ASG-NW2500 / Open Protocol"
    )

    parser.add_argument(
        "--model",
        choices=["T1XX-1", "T1XX-2"],
        help="Modelo de corrida. Ejemplo: --model T1XX-1",
    )

    return parser.parse_args()


def ask_running_model() -> str:
    print("Selecciona modelo de corrida:")
    print("1) T1XX-1")
    print("2) T1XX-2")
    print()

    option = input("Opción [1/2]: ").strip()

    if option == "2":
        return "T1XX-2"

    return "T1XX-1"


def main():
    setup_logging()

    args = parse_args()
    running_model = args.model or ask_running_model()

    devices = get_devices_by_model(running_model)

    if devices.empty:
        print(f"No hay equipos habilitados para el modelo {running_model}.")
        return

    stop_event = threading.Event()
    event_queue = queue.Queue()

    for _, row in devices.iterrows():
        device_info = normalize_device_info(row)
        init_device_status(device_info, running_model)

    print_startup_summary(devices, running_model)

    writer_thread = threading.Thread(
        target=writer_worker,
        args=(event_queue, stop_event),
        name="writer",
        daemon=True,
    )
    writer_thread.start()

    device_threads = []

    for _, row in devices.iterrows():
        device_info = normalize_device_info(row)

        thread = threading.Thread(
            target=device_worker,
            args=(device_info, running_model, event_queue, stop_event),
            name=device_info["device_id"],
            daemon=True,
        )

        thread.start()
        device_threads.append(thread)

    print("Colector corriendo. Presiona CTRL + C para detener.")
    print()

    try:
        while True:
            time.sleep(5)

            with STATUS_LOCK:
                connected = sum(1 for d in DEVICE_STATUS.values() if d.get("connected"))
                total_results = sum(d.get("total_results", 0) for d in DEVICE_STATUS.values())

            print(
                f"[{now_str()}] Estado: "
                f"{connected}/{len(devices)} conectados | "
                f"Resultados recibidos: {total_results}"
            )

    except KeyboardInterrupt:
        print("\nDeteniendo colector...")
        logging.info("Deteniendo colector por KeyboardInterrupt")
        stop_event.set()

        for thread in device_threads:
            thread.join(timeout=3)

        event_queue.join()
        writer_thread.join(timeout=3)

        write_status_file()

        print("Colector detenido correctamente.")
        print(f"Status: {STATUS_FILE}")
        print(f"Log:    {COLLECTOR_LOG}")


if __name__ == "__main__":
    main()
