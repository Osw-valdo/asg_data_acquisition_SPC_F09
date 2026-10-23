from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from ftplib import FTP, all_errors
from getpass import getpass
from pathlib import Path
import argparse
import csv
import os


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_ROOT / "config" / "plc_devices.csv"
REMOTE_CARD_ROOT = "/0_CARD"
REQUIRED_COLUMNS = {
    "enabled",
    "device_id",
    "ip",
    "ftp_port",
    "models",
}


def load_devices() -> list[dict]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"No existe la configuración: {CONFIG_FILE}")

    with CONFIG_FILE.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"Faltan columnas en plc_devices.csv: {missing}")

        devices = []
        for row_number, row in enumerate(reader, start=2):
            if str(row.get("enabled", "")).strip().lower() not in {"1", "true"}:
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
    if len(ids) != len(set(ids)):
        raise ValueError("Hay device_id duplicados en plc_devices.csv.")
    return devices


def check_device(
    device: dict,
    username: str,
    password: str,
    target_date: date,
    timeout: int,
) -> dict:
    month_code = target_date.strftime("%y%m")
    day_code = target_date.strftime("%y%m%d")
    remote_dir = f"{REMOTE_CARD_ROOT}/Atornillado_{month_code}"
    remote_name = f"{day_code}_Log.csv"
    result = {
        "device_id": device["device_id"],
        "ip": device["ip"],
        "port": device["ftp_port"],
        "connected": False,
        "authenticated": False,
        "directory_accessible": False,
        "listing_success": False,
        "logfile_found": False,
        "logfile_size": None,
        "error": "",
    }
    ftp = FTP()

    try:
        ftp.connect(device["ip"], device["ftp_port"], timeout=timeout)
        result["connected"] = True
        ftp.login(username, password)
        result["authenticated"] = True
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)
        result["directory_accessible"] = True

        # NLST verifies that the passive data connection can list the PLC folder.
        remote_files = ftp.nlst()
        result["listing_success"] = True
        result["logfile_found"] = any(
            item.rstrip("/").rsplit("/", 1)[-1] == remote_name
            for item in remote_files
        )

        if result["logfile_found"]:
            try:
                ftp.voidcmd("TYPE I")
                result["logfile_size"] = ftp.size(remote_name)
            except all_errors as error:
                result["error"] = (
                    f"Log encontrado, pero no se pudo consultar su tamaño: {error}"
                )
    except all_errors as error:
        result["error"] = str(error)
    finally:
        if ftp.sock is not None:
            try:
                ftp.quit()
            except all_errors:
                ftp.close()
        else:
            ftp.close()

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica conectividad FTP y acceso a logs de todos los PLC."
    )
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=date.today(),
        help="Fecha del logfile que se busca (AAAA-MM-DD; predeterminado: hoy).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=8,
        help="Timeout por PLC en segundos (predeterminado: 8).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout < 1:
        raise ValueError("El timeout debe ser de al menos 1 segundo.")

    devices = load_devices()
    if not devices:
        raise ValueError("No hay PLC habilitados en config/plc_devices.csv.")

    username = os.getenv("SPC_PLC_FTP_USER", "").strip()
    password = os.getenv("SPC_PLC_FTP_PASSWORD", "")
    if not username:
        username = input("Usuario FTP de los PLC: ").strip()
    if not password:
        password = getpass("Contraseña FTP: ")
    if not username or not password:
        raise ValueError("Usuario y contraseña FTP son obligatorios.")

    print(f"Verificando {len(devices)} PLC para {args.date:%Y-%m-%d}...")
    results = []
    with ThreadPoolExecutor(max_workers=min(10, len(devices))) as executor:
        futures = {
            executor.submit(
                check_device,
                device,
                username,
                password,
                args.date,
                args.timeout,
            ): device
            for device in devices
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda result: result["device_id"])
    for result in results:
        connection = "OK" if result["connected"] else "FALLO"
        login = "OK" if result["authenticated"] else "FALLO"
        directory = "OK" if result["directory_accessible"] else "FALLO"
        listing = "OK" if result["listing_success"] else "FALLO"
        if result["logfile_found"]:
            size = result["logfile_size"]
            logfile = f"encontrado ({size} bytes)" if size is not None else "encontrado"
        else:
            logfile = "no encontrado"

        print(
            f"{result['device_id']:8} {result['ip']}:{result['port']} | "
            f"conexión {connection} | login {login} | carpeta {directory} | "
            f"listado {listing} | logfile {logfile}"
        )
        if result["error"]:
            print(f"  Detalle: {result['error']}")

    failed = sum(
        not (
            result["connected"]
            and result["authenticated"]
            and result["directory_accessible"]
            and result["listing_success"]
        )
        for result in results
    )
    missing_logs = sum(not result["logfile_found"] for result in results)
    print(
        f"Resultado: {len(results) - failed}/{len(results)} conexiones FTP completas; "
        f"{missing_logs} logfile(s) del día no encontrados."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
