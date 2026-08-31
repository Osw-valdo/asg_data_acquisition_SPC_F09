from __future__ import annotations

from csv import DictReader
from datetime import datetime
from pathlib import Path
import argparse
import ipaddress
import json


ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
EXPECTED_COUNTS = {"T1XX-1": 6, "T1XX-2": 8}


def load_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return [
            {key: str(value or "").strip() for key, value in row.items()}
            for row in DictReader(source)
        ]


def enabled_for_model(rows: list[dict], model: str) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("enabled", "").lower() in {"1", "true"}
        and model in row.get("models", "").split("|")
    ]


def load_status(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def suffix(device_id: str) -> str:
    return str(device_id).split("_", 1)[-1]


def validate_config(asg_rows: list[dict], plc_rows: list[dict], model: str) -> list[str]:
    errors = []
    asg = enabled_for_model(asg_rows, model)
    plc = enabled_for_model(plc_rows, model)
    asg_by_suffix = {suffix(row["device_id"]): row for row in asg}
    plc_by_suffix = {suffix(row["device_id"]): row for row in plc}
    expected = EXPECTED_COUNTS[model]

    if len(asg) != expected:
        errors.append(f"Se esperaban {expected} ASG para {model}; hay {len(asg)}.")
    if len(plc) != expected:
        errors.append(f"Se esperaban {expected} PLC para {model}; hay {len(plc)}.")

    if len(asg) != len(asg_by_suffix):
        errors.append("Hay identificadores ASG duplicados.")
    if len(plc) != len(plc_by_suffix):
        errors.append("Hay identificadores PLC duplicados.")
    if len({row.get("ip") for row in asg}) != len(asg):
        errors.append("Hay IP ASG duplicadas.")
    if len({row.get("ip") for row in plc}) != len(plc):
        errors.append("Hay IP PLC duplicadas.")
    if set(asg_by_suffix) != set(plc_by_suffix):
        errors.append("La lista ASG y PLC del modelo no corresponde uno a uno.")

    for key in sorted(set(asg_by_suffix) & set(plc_by_suffix)):
        asg_row = asg_by_suffix[key]
        plc_row = plc_by_suffix[key]
        for field in ("station", "module_name", "process", "station_type"):
            if asg_row.get(field, "").casefold() != plc_row.get(field, "").casefold():
                errors.append(
                    f"ASG_{key}/PLC_{key}: no coincide {field}."
                )
        if asg_row.get("port") != "4545":
            errors.append(f"ASG_{key}: puerto distinto de 4545.")
        if plc_row.get("ftp_port") != "21":
            errors.append(f"PLC_{key}: puerto FTP distinto de 21.")
        try:
            asg_ip = ipaddress.ip_address(asg_row.get("ip", ""))
            plc_ip = ipaddress.ip_address(plc_row.get("ip", ""))
            if int(plc_ip) - int(asg_ip) != 1:
                errors.append(f"ASG_{key}/PLC_{key}: la IP del PLC no es ASG + 1.")
        except ValueError:
            errors.append(f"ASG_{key}/PLC_{key}: dirección IP inválida.")

    return errors


def asg_state(row: dict, status: dict) -> str:
    info = status.get("devices", {}).get(row["device_id"], {})
    if not info:
        return "SIN ESTADO"
    if info.get("connected"):
        return f"CONECTADO | resultados={info.get('total_results', 0)}"
    error = str(info.get("last_error", "")).strip()
    return f"NO CONECTADO | {error or info.get('state', '')}"


def plc_state(row: dict, status: dict) -> str:
    info = status.get("devices", {}).get(row["device_id"], {})
    if not info:
        return "SIN ESTADO"
    if info.get("file_available"):
        changed = "actualizado" if info.get("changed") else "sin cambios"
        return f"FTP OK | LOG OK | {changed}"
    if info.get("connected"):
        error = str(info.get("last_error", "")).strip()
        return f"FTP OK | LOG NO DISPONIBLE | {error}"
    error = str(info.get("last_error", "")).strip()
    return f"FTP NO CONECTADO | {error or 'sin detalle'}"


def count_today(model: str) -> tuple[int, int]:
    day = datetime.now().strftime("%Y-%m-%d")
    asg_file = DATA_DIR / "processed" / f"asg_clean_master_{day}.csv"
    plc_file = DATA_DIR / "plc_processed" / f"plc_pieces_master_{day}.csv"
    asg_results = 0
    plc_ok_qr = set()

    if asg_file.exists():
        for row in load_csv(asg_file):
            if row.get("running_model") == model:
                asg_results += 1

    if plc_file.exists():
        for row in load_csv(plc_file):
            if (
                row.get("running_model") == model
                and row.get("attempt_result", "").upper() == "OK"
                and row.get("qr_code")
            ):
                plc_ok_qr.add(row["qr_code"])

    return asg_results, len(plc_ok_qr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico seguro de configuración y estados ASG/PLC."
    )
    parser.add_argument(
        "--model",
        choices=["T1XX-1", "T1XX-2"],
        default="T1XX-2",
    )
    args = parser.parse_args()

    asg_rows = load_csv(CONFIG_DIR / "asg_devices.csv")
    plc_rows = load_csv(CONFIG_DIR / "plc_devices.csv")
    asg = enabled_for_model(asg_rows, args.model)
    plc = enabled_for_model(plc_rows, args.model)
    asg_status = load_status(LOG_DIR / "device_status.json")
    plc_status = load_status(LOG_DIR / "plc_ftp_status.json")
    errors = validate_config(asg_rows, plc_rows, args.model)

    print("=" * 96)
    print(f"DIAGNÓSTICO SPC F09 | {args.model} | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 96)
    print(f"Configuración: {len(asg)} ASG | {len(plc)} PLC")
    print(f"Validación: {'CORRECTA' if not errors else 'CON ERRORES'}")
    for error in errors:
        print(f"  - {error}")

    print("\nASG EN VIVO")
    for row in asg:
        print(
            f"{row['device_id']:7} | {row['process']:6} | "
            f"{row['ip']:15} | {asg_state(row, asg_status)}"
        )

    print("\nPLC / FTP")
    for row in plc:
        print(
            f"{row['device_id']:7} | {row['process']:6} | "
            f"{row['ip']:15} | {plc_state(row, plc_status)}"
        )

    asg_results, plc_ok_qr = count_today(args.model)
    print("\nDATOS DE HOY")
    print(f"Resultados ASG guardados: {asg_results}")
    print(f"QR únicos finales OK PLC: {plc_ok_qr}")
    print("=" * 96)
    print(
        "Este diagnóstico solo lee archivos locales; no abre otra conexión con los ASG."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
