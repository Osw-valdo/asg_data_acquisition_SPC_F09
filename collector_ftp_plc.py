from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from ftplib import FTP, all_errors
from getpass import getpass
from pathlib import Path
import argparse
import json
import logging
import os
import shutil
import time

from src.plc_config import get_enabled_devices
from src.plc_log_parser import get_output_paths, process_plc_date


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data" / "plc_raw"
LOG_DIR = PROJECT_ROOT / "logs"
STATUS_FILE = LOG_DIR / "plc_ftp_status.json"
LOG_FILE = LOG_DIR / "collector_ftp_plc.log"

REMOTE_CARD_ROOT = "/0_CARD"
DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_TIMEOUT_SECONDS = 8


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError("Usa la fecha AAAA-MM-DD.") from error


def build_remote_paths(target_date: date) -> tuple[str, str, str]:
    month_code = target_date.strftime("%y%m")
    day_code = target_date.strftime("%y%m%d")
    remote_dir = f"{REMOTE_CARD_ROOT}/Atornillado_{month_code}"
    remote_name = f"{day_code}_Log.csv"
    return remote_dir, remote_name, f"{remote_dir}/{remote_name}"


def get_local_file(device: dict, remote_name: str) -> Path:
    return DATA_DIR / device["device_id"] / remote_name


def atomic_full_download(
    ftp: FTP,
    remote_name: str,
    local_file: Path,
    expected_size: int | None,
) -> int:
    local_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = local_file.with_suffix(local_file.suffix + ".tmp")

    try:
        with temp_file.open("wb") as output:
            ftp.retrbinary(f"RETR {remote_name}", output.write)

        downloaded_size = temp_file.stat().st_size

        if expected_size is not None and downloaded_size != expected_size:
            raise OSError(
                f"Descarga incompleta: {downloaded_size}/{expected_size} bytes."
            )

        os.replace(temp_file, local_file)
        return downloaded_size
    finally:
        if temp_file.exists():
            temp_file.unlink()


def atomic_incremental_download(
    ftp: FTP,
    remote_name: str,
    local_file: Path,
    local_size: int,
    remote_size: int,
) -> int:
    temp_file = local_file.with_suffix(local_file.suffix + ".tmp")

    try:
        shutil.copyfile(local_file, temp_file)

        with temp_file.open("ab") as output:
            ftp.retrbinary(
                f"RETR {remote_name}",
                output.write,
                rest=local_size,
            )

        downloaded_size = temp_file.stat().st_size

        if downloaded_size != remote_size:
            raise OSError(
                f"Actualización incompleta: {downloaded_size}/{remote_size} bytes."
            )

        os.replace(temp_file, local_file)
        return downloaded_size
    finally:
        if temp_file.exists():
            temp_file.unlink()


def download_device(
    device: dict,
    username: str,
    password: str,
    target_date: date,
    timeout_seconds: int,
) -> dict:
    remote_dir, remote_name, remote_path = build_remote_paths(target_date)
    local_file = get_local_file(device, remote_name)

    result = {
        "device_id": device["device_id"],
        "station": device["station"],
        "process": device["process"],
        "ip": device["ip"],
        "models_supported": device["models"],
        "connected": False,
        "authenticated": False,
        "storage_accessible": False,
        "file_available": False,
        "changed": False,
        "download_mode": "none",
        "remote_file": remote_path,
        "local_file": str(local_file),
        "remote_size": None,
        "local_size": local_file.stat().st_size if local_file.exists() else 0,
        "last_check_at": now_str(),
        "last_download_at": "",
        "last_error": "",
    }

    ftp = FTP()

    try:
        ftp.connect(device["ip"], device["ftp_port"], timeout=timeout_seconds)
        ftp.login(username, password)
        result["connected"] = True
        result["authenticated"] = True
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)
        result["storage_accessible"] = True
        ftp.voidcmd("TYPE I")

        remote_size = ftp.size(remote_name)
        result["file_available"] = True
        result["remote_size"] = remote_size

        local_size = local_file.stat().st_size if local_file.exists() else 0
        result["local_size"] = local_size

        if remote_size is not None and local_size == remote_size:
            return result

        if remote_size is not None and 0 < local_size < remote_size:
            try:
                new_size = atomic_incremental_download(
                    ftp=ftp,
                    remote_name=remote_name,
                    local_file=local_file,
                    local_size=local_size,
                    remote_size=remote_size,
                )
                result["download_mode"] = "incremental"
            except all_errors:
                logging.info(
                    "%s no permitió descarga incremental; se descargará completo.",
                    device["device_id"],
                )
                ftp.cwd(remote_dir)
                new_size = atomic_full_download(
                    ftp=ftp,
                    remote_name=remote_name,
                    local_file=local_file,
                    expected_size=remote_size,
                )
                result["download_mode"] = "full_fallback"
        else:
            new_size = atomic_full_download(
                ftp=ftp,
                remote_name=remote_name,
                local_file=local_file,
                expected_size=remote_size,
            )
            result["download_mode"] = "full"

        result["changed"] = True
        result["local_size"] = new_size
        result["last_download_at"] = now_str()
        return result

    except all_errors as error:
        result["last_error"] = str(error)
        return result
    finally:
        try:
            ftp.quit()
        except all_errors:
            ftp.close()


def write_status(results: list[dict], target_date: date) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = STATUS_FILE.with_suffix(".json.tmp")

    payload = {
        "updated_at": now_str(),
        "target_date": target_date.isoformat(),
        "devices": {
            result["device_id"]: result
            for result in sorted(results, key=lambda item: item["device_id"])
        },
    }

    temp_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp_file, STATUS_FILE)


def run_cycle(
    devices: list[dict],
    username: str,
    password: str,
    target_date: date,
    timeout_seconds: int,
) -> list[dict]:
    results = []
    workers = min(10, len(devices))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                download_device,
                device,
                username,
                password,
                target_date,
                timeout_seconds,
            ): device
            for device in devices
        }

        for future in as_completed(futures):
            device = futures[future]

            try:
                result = future.result()
            except Exception as error:
                logging.exception(
                    "%s produjo un error inesperado.",
                    device["device_id"],
                )
                result = {
                    "device_id": device["device_id"],
                    "station": device["station"],
                    "process": device["process"],
                    "ip": device["ip"],
                    "models_supported": device["models"],
                    "connected": False,
                    "authenticated": False,
                    "storage_accessible": False,
                    "file_available": False,
                    "changed": False,
                    "download_mode": "none",
                    "remote_file": "",
                    "local_file": "",
                    "remote_size": None,
                    "local_size": 0,
                    "last_check_at": now_str(),
                    "last_download_at": "",
                    "last_error": f"Error inesperado: {error}",
                }

            results.append(result)

            if result["last_error"]:
                logging.warning(
                    "%s | %s | %s",
                    result["device_id"],
                    result["ip"],
                    result["last_error"],
                )
            elif result["changed"]:
                logging.info(
                    "%s | actualizado | %s bytes | %s",
                    result["device_id"],
                    result["local_size"],
                    result["download_mode"],
                )

    write_status(results, target_date)
    return results


def print_summary(results: list[dict]) -> None:
    connected = sum(result["connected"] for result in results)
    files_available = sum(result.get("file_available", False) for result in results)
    updated = sum(result["changed"] for result in results)
    errors = sum(bool(result["last_error"]) for result in results)

    print(
        f"[{now_str()}] "
        f"Conectados: {connected}/{len(results)} | "
        f"Archivo disponible: {files_available}/{len(results)} | "
        f"Actualizados: {updated} | "
        f"Errores: {errors}"
    )


def process_downloaded_logs(target_date: date, results: list[dict]) -> None:
    output_paths = get_output_paths(target_date)
    outputs_exist = all(path.exists() for path in output_paths.values())

    if outputs_exist and not any(result["changed"] for result in results):
        return

    try:
        summary = process_plc_date(target_date)
        models = summary.get("models", {})
        print(
            f"Procesados: {summary['attempts']} intentos | "
            f"{summary['pieces']} piezas/estación | "
            f"{summary['tightenings']} atornillados"
        )

        for model in ("T1XX-1", "T1XX-2"):
            values = models.get(model)
            if values:
                print(
                    f"  {model}: {values['unique_qr']} QR únicos | "
                    f"OK: {values['ok']} | "
                    f"NG/incompletos: {values['ng_or_incomplete']}"
                )

    except Exception as error:
        logging.exception("No se pudieron procesar los logfiles PLC: %s", error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Descarga multi-PLC de CSV Keyence mediante FTP."
    )
    parser.add_argument(
        "--model",
        choices=["T1XX-1", "T1XX-2"],
        help="Limita la descarga a los PLC aplicables al modelo.",
    )
    parser.add_argument(
        "--device",
        action="append",
        help="PLC a descargar. Puede repetirse. Ejemplo: --device PLC_01",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola descarga y termina.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Segundos entre revisiones. Predeterminado: {DEFAULT_INTERVAL_SECONDS}.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout FTP por PLC. Predeterminado: {DEFAULT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--date",
        type=parse_date,
        help="Fecha fija AAAA-MM-DD. Si se omite, usa la fecha actual.",
    )
    return parser.parse_args()


def select_devices(
    devices: list[dict],
    requested_ids: list[str] | None,
    model: str | None = None,
) -> list[dict]:
    if model:
        devices = [
            device
            for device in devices
            if model in device["models"].split("|")
        ]

    if not requested_ids:
        return devices

    requested = {value.strip().upper() for value in requested_ids}
    selected = [
        device
        for device in devices
        if device["device_id"].upper() in requested
    ]
    found = {device["device_id"].upper() for device in selected}
    missing = sorted(requested - found)

    if missing:
        raise ValueError(f"PLCs no encontrados en configuración: {missing}")

    return selected


def get_credentials() -> tuple[str, str]:
    username = os.getenv("SPC_PLC_FTP_USER", "").strip()
    password = os.getenv("SPC_PLC_FTP_PASSWORD", "")

    if not username:
        username = input("Usuario FTP de los PLC: ").strip()

    if not password:
        password = getpass("Contraseña FTP: ")

    if not username or not password:
        raise ValueError("Usuario y contraseña FTP son obligatorios.")

    return username, password


def main() -> None:
    setup_logging()
    args = parse_args()

    if args.interval < 2:
        raise ValueError("El intervalo mínimo permitido es de 2 segundos.")

    devices = select_devices(
        get_enabled_devices(),
        requested_ids=args.device,
        model=args.model,
    )
    if not devices:
        raise ValueError("No hay PLC habilitados para la selección indicada.")
    username, password = get_credentials()

    print("=" * 72)
    print("SPC PLC F09 | Colector FTP Keyence")
    print(f"Modelo: {args.model or 'Todos'}")
    print(f"PLCs seleccionados: {len(devices)}")
    print(f"Intervalo: {args.interval} segundos")
    print(f"Datos locales: {DATA_DIR}")
    print("=" * 72)

    try:
        while True:
            target_date = args.date or datetime.now().date()
            results = run_cycle(
                devices=devices,
                username=username,
                password=password,
                target_date=target_date,
                timeout_seconds=args.timeout,
            )
            print_summary(results)
            process_downloaded_logs(target_date, results)

            if args.once:
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nColector detenido correctamente.")


if __name__ == "__main__":
    main()
