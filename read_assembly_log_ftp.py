from __future__ import annotations

from datetime import date, datetime
from ftplib import FTP, all_errors
from getpass import getpass
from pathlib import Path
import argparse
import ipaddress
import os
import re

from src.plc_config import get_enabled_devices


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data" / "plc_raw"
REMOTE_CARD_ROOT = "/0_CARD"
DEFAULT_TIMEOUT_SECONDS = 8


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError("Usa la fecha AAAA-MM-DD.") from error


def default_remote_paths(target_date: date) -> tuple[str, str]:
    month_code = target_date.strftime("%y%m")
    day_code = target_date.strftime("%y%m%d")
    return (
        f"{REMOTE_CARD_ROOT}/Atornillado_{month_code}",
        f"{day_code}_Log.csv",
    )


def resolve_device(args: argparse.Namespace) -> dict:
    if args.device:
        devices = get_enabled_devices()
        device = next(
            (
                item
                for item in devices
                if item["device_id"].casefold() == args.device.casefold()
            ),
            None,
        )
        if device is None:
            raise ValueError(
                f"No se encontró un PLC habilitado con ID {args.device} "
                "en config/plc_devices.csv."
            )
        return {
            "device_id": device["device_id"],
            "ip": device["ip"],
            "ftp_port": device["ftp_port"],
        }

    ip = args.ip
    port = args.port
    device_id = args.device_id

    if not ip:
        ip = input("IP del PLC: ").strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError as error:
            raise ValueError(f"Dirección IP inválida: {ip}") from error

        if port is None:
            port_text = input("Puerto FTP [21]: ").strip()
            try:
                port = int(port_text) if port_text else 21
            except ValueError as error:
                raise ValueError("El puerto FTP debe ser numérico.") from error

    try:
        ipaddress.ip_address(ip)
    except ValueError as error:
        raise ValueError(f"Dirección IP inválida: {ip}") from error

    if not device_id:
        safe_ip = re.sub(r"[^A-Za-z0-9_-]", "_", ip)
        device_id = f"PLC_{safe_ip}"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", device_id):
        raise ValueError("--device-id solo puede contener letras, números, _ y -.")

    port = port or 21
    if not 1 <= port <= 65535:
        raise ValueError("El puerto FTP debe estar entre 1 y 65535.")
    return {
        "device_id": device_id,
        "ip": ip,
        "ftp_port": port,
    }


def download_log(
    device: dict,
    username: str,
    password: str,
    remote_dir: str,
    remote_name: str,
    timeout: int,
) -> tuple[Path, int]:
    if (
        not remote_name
        or remote_name in {".", ".."}
        or "/" in remote_name
        or "\\" in remote_name
    ):
        raise ValueError("El nombre remoto debe ser un nombre de archivo, sin ruta.")

    local_file = DATA_DIR / device["device_id"] / remote_name
    temp_file = local_file.with_suffix(local_file.suffix + ".tmp")
    ftp = FTP()

    try:
        ftp.connect(device["ip"], device["ftp_port"], timeout=timeout)
        ftp.login(username, password)
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)
        ftp.voidcmd("TYPE I")

        local_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temp_file.open("wb") as output:
                ftp.retrbinary(f"RETR {remote_name}", output.write)

            downloaded_size = temp_file.stat().st_size
            os.replace(temp_file, local_file)
            return local_file, downloaded_size
        finally:
            if temp_file.exists():
                temp_file.unlink()
    finally:
        if ftp.sock is not None:
            try:
                ftp.quit()
            except all_errors:
                ftp.close()
        else:
            ftp.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Descarga un logfile FTP de prueba de un PLC de ensamble. "
            "No interpreta los datos ni modifica el PLC."
        )
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--device",
        help="ID de un PLC habilitado en config/plc_devices.csv.",
    )
    target.add_argument(
        "--ip",
        help="IP de un PLC que todavía no esté registrado en la configuración.",
    )
    parser.add_argument(
        "--device-id",
        help="Nombre de carpeta local cuando se usa --ip.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Puerto FTP cuando se usa --ip (predeterminado: 21).",
    )
    parser.add_argument(
        "--date",
        type=parse_date,
        default=date.today(),
        help="Fecha del logfile AAAA-MM-DD (predeterminado: hoy).",
    )
    parser.add_argument(
        "--remote-dir",
        help="Carpeta remota; predeterminada: /0_CARD/Atornillado_AAMM.",
    )
    parser.add_argument(
        "--remote-name",
        help="Nombre remoto; predeterminado: AAMMDD_Log.csv.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout FTP en segundos (predeterminado: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout debe ser de al menos 1 segundo.")
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port debe estar entre 1 y 65535.")
    if args.device and (args.device_id or args.port is not None):
        parser.error("--device no se puede combinar con --device-id ni --port.")

    default_dir, default_name = default_remote_paths(args.date)
    args.remote_dir = args.remote_dir or default_dir
    args.remote_name = args.remote_name or default_name
    return args


def main() -> int:
    args = parse_args()
    try:
        device = resolve_device(args)
    except (EOFError, FileNotFoundError, ValueError) as error:
        print(f"Error al seleccionar el PLC: {error}")
        return 1

    interactive = not args.device and not args.ip
    username = "" if interactive else os.getenv("SPC_PLC_FTP_USER", "").strip()
    password = "" if interactive else os.getenv("SPC_PLC_FTP_PASSWORD", "")
    try:
        if not username:
            username = input("Usuario FTP del PLC: ").strip()
        if not password:
            password = getpass("Contraseña FTP: ")
    except EOFError as error:
        print(f"Error al leer las credenciales FTP: {error}")
        return 1
    if not username or not password:
        print("Error: usuario y contraseña FTP son obligatorios.")
        return 1

    remote_path = f"{args.remote_dir.rstrip('/')}/{args.remote_name}"
    print(
        f"Conectando a {device['device_id']} "
        f"({device['ip']}:{device['ftp_port']})..."
    )
    print(f"Logfile remoto: {remote_path}")

    try:
        local_file, size = download_log(
            device=device,
            username=username,
            password=password,
            remote_dir=args.remote_dir,
            remote_name=args.remote_name,
            timeout=args.timeout,
        )
    except all_errors + (ValueError,) as error:
        print(f"Error al leer el logfile por FTP: {error}")
        return 1

    print(f"Descarga correcta: {size} bytes.")
    print(f"Copia local: {local_file}")
    print("El archivo se guardó sin interpretar su contenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
