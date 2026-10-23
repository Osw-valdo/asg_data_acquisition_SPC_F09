from __future__ import annotations

from collections import defaultdict
from csv import DictWriter, reader
from datetime import date, datetime
from pathlib import Path
import json
import os

from src.plc_config import get_enabled_devices


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "plc_raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "plc_processed"

EXPECTED_COLUMNS = 660
SCREW_BLOCK_START = 2
SCREW_BLOCK_WIDTH = 20
SCREW_BLOCK_COUNT = 30
STATION_LOG_COLUMN = 603
ALARM_COLUMN = 604
METADATA_START = 605
METADATA_END = 659

MODEL_CODE_MAP = {
    "1": "T1XX-1",
    "2": "T1XX-2",
}

ATTEMPT_COLUMNS = [
    "attempt_key",
    "source_file",
    "source_row_number",
    "plc_timestamp",
    "qr_read_status",
    "qr_code",
    "device_id",
    "asg_device_id",
    "station",
    "station_log",
    "module_name",
    "ip",
    "models_supported",
    "running_model",
    "model_code",
    "process",
    "station_type",
    "declared_screws",
    "captured_screws",
    "ng_tightenings",
    "is_complete",
    "attempt_result",
    "attempt_number",
    "attempts_for_qr_station",
    "is_repeated_qr",
    "is_final_attempt",
    "alarm_codes",
    "spp",
    "tp",
    "c1",
    "ro",
    "c2",
    "pre_pa",
    "post_pa",
    "jig",
    "sec",
    "step_actual",
    "metadata_json",
]

TIGHTENING_COLUMNS = [
    "attempt_key",
    "source_file",
    "source_row_number",
    "plc_timestamp",
    "qr_code",
    "device_id",
    "asg_device_id",
    "station",
    "module_name",
    "ip",
    "running_model",
    "process",
    "attempt_number",
    "is_final_attempt",
    "screw_position",
    "torque_actual",
    "torque_min",
    "torque_max",
    "torque_target",
    "tightening_id",
    "tightening_status_code",
    "torque_status_code",
    "angle_status_code",
    "batch_status_code",
    "parameter_set_id",
    "aux_value_1",
    "aux_value_2",
    "angle_actual",
    "angle_min",
    "angle_max",
    "angle_target",
    "extra_numeric_value",
    "torque_within_limits",
    "tightening_result",
]


def parse_int(value):
    value = str(value or "").strip()
    if value in {"", "N/A"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_float(value):
    value = str(value or "").strip()
    if value in {"", "N/A"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_plc_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%y %H:%M:%S")
    except ValueError:
        return None


def parse_qr(value: str) -> tuple[str, str]:
    value = str(value or "").strip()
    prefix = value[:2].upper()
    if prefix in {"OK", "NG"}:
        return prefix, value[2:].strip()
    return "UNKNOWN", value


def parse_metadata(row: list[str]) -> dict[str, str]:
    metadata = {}
    for raw_value in row[METADATA_START:METADATA_END]:
        value = str(raw_value or "").strip()
        if not value or ":" not in value:
            continue
        key, parsed_value = value.split(":", 1)
        metadata[key.strip()] = parsed_value.strip()
    return metadata


def infer_model(device: dict, model_code: str) -> str:
    mapped = MODEL_CODE_MAP.get(str(model_code).strip())
    supported = [value.strip() for value in device["models"].split("|") if value.strip()]

    if mapped in supported:
        return mapped
    if len(supported) == 1:
        return supported[0]
    return mapped or "UNKNOWN"


def asg_device_id(plc_device_id: str) -> str:
    if plc_device_id.upper().startswith("PLC_"):
        return "ASG_" + plc_device_id.split("_", 1)[1]
    return plc_device_id


def parse_screw_block(
    block: list[str],
    attempt: dict,
) -> dict | None:
    tightening_id = str(block[4] or "").strip()
    torque_actual = parse_float(block[1])

    if tightening_id in {"", "N/A"} or torque_actual is None:
        return None

    torque_max = parse_float(block[2])
    torque_min = parse_float(block[3])
    torque_status_code = str(block[6] or "").strip()
    angle_status_code = str(block[7] or "").strip()
    tightening_status_code = str(block[5] or "").strip()

    torque_within_limits = (
        torque_min is not None
        and torque_max is not None
        and torque_min <= torque_actual <= torque_max
    )

    tightening_ok = tightening_status_code in {"", "1"}
    torque_ok = torque_status_code in {"", "1"} and torque_within_limits
    angle_ok = angle_status_code in {"", "1"}

    return {
        "attempt_key": attempt["attempt_key"],
        "source_file": attempt["source_file"],
        "source_row_number": attempt["source_row_number"],
        "plc_timestamp": attempt["plc_timestamp"],
        "qr_code": attempt["qr_code"],
        "device_id": attempt["device_id"],
        "asg_device_id": attempt["asg_device_id"],
        "station": attempt["station"],
        "module_name": attempt["module_name"],
        "ip": attempt["ip"],
        "running_model": attempt["running_model"],
        "process": attempt["process"],
        "attempt_number": 0,
        "is_final_attempt": 0,
        "screw_position": parse_int(block[0]),
        "torque_actual": torque_actual,
        "torque_min": torque_min,
        "torque_max": torque_max,
        "torque_target": parse_float(block[12]),
        "tightening_id": tightening_id,
        "tightening_status_code": tightening_status_code,
        "torque_status_code": torque_status_code,
        "angle_status_code": angle_status_code,
        "batch_status_code": str(block[8] or "").strip(),
        "parameter_set_id": parse_int(block[9]),
        "aux_value_1": parse_float(block[10]),
        "aux_value_2": parse_float(block[11]),
        "angle_actual": parse_int(block[13]),
        "angle_min": parse_int(block[14]),
        "angle_max": parse_int(block[15]),
        "angle_target": parse_int(block[16]),
        "extra_numeric_value": parse_float(block[17]),
        "torque_within_limits": int(torque_within_limits),
        "tightening_result": "OK" if tightening_ok and torque_ok and angle_ok else "NG",
    }


def parse_log_file(file_path: Path, device: dict) -> tuple[list[dict], list[dict], int]:
    attempts = []
    tightenings = []
    skipped_rows = 0

    with file_path.open("r", encoding="utf-8-sig", newline="") as source:
        csv_reader = reader(source)

        for row_number, row in enumerate(csv_reader, start=1):
            if len(row) < EXPECTED_COLUMNS:
                skipped_rows += 1
                continue

            timestamp = parse_plc_timestamp(row[0])
            qr_status, qr_code = parse_qr(row[1])
            metadata = parse_metadata(row)
            model_code = metadata.get("Model", "")
            running_model = infer_model(device, model_code)
            attempt_key = f"{device['device_id']}|{file_path.name}|{row_number}"

            attempt = {
                "attempt_key": attempt_key,
                "source_file": file_path.name,
                "source_row_number": row_number,
                "plc_timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "",
                "qr_read_status": qr_status,
                "qr_code": qr_code,
                "device_id": device["device_id"],
                "asg_device_id": asg_device_id(device["device_id"]),
                "station": device["station"],
                "station_log": str(row[STATION_LOG_COLUMN] or "").strip(),
                "module_name": device["module_name"],
                "ip": device["ip"],
                "models_supported": device["models"],
                "running_model": running_model,
                "model_code": model_code,
                "process": device["process"],
                "station_type": device["station_type"],
                "declared_screws": parse_int(metadata.get("No.tornillos")),
                "captured_screws": 0,
                "ng_tightenings": 0,
                "is_complete": 0,
                "attempt_result": "INCOMPLETE",
                "attempt_number": 0,
                "attempts_for_qr_station": 0,
                "is_repeated_qr": 0,
                "is_final_attempt": 0,
                "alarm_codes": str(row[ALARM_COLUMN] or "").strip(),
                "spp": metadata.get("SPP", ""),
                "tp": metadata.get("TP", ""),
                "c1": metadata.get("C1", ""),
                "ro": metadata.get("RO", ""),
                "c2": metadata.get("C2", ""),
                "pre_pa": metadata.get("PRE/P-A", ""),
                "post_pa": metadata.get("POST/P-A", ""),
                "jig": metadata.get("JIG", ""),
                "sec": metadata.get("SEC", ""),
                "step_actual": metadata.get("Step act", ""),
                "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                "_timestamp": timestamp or datetime.min,
                "_trace_qr": qr_code or f"__NO_QR__{attempt_key}",
            }

            attempt_tightenings = []
            for slot in range(SCREW_BLOCK_COUNT):
                start = SCREW_BLOCK_START + slot * SCREW_BLOCK_WIDTH
                block = row[start:start + SCREW_BLOCK_WIDTH]
                parsed = parse_screw_block(block, attempt)
                if parsed is not None:
                    attempt_tightenings.append(parsed)

            declared_screws = attempt["declared_screws"]
            if declared_screws is None:
                config_column = (
                    "screws_per_piece_t1xx1"
                    if running_model == "T1XX-1"
                    else "screws_per_piece_t1xx2"
                )
                declared_screws = parse_int(device.get(config_column))
                attempt["declared_screws"] = declared_screws

            attempt["captured_screws"] = len(attempt_tightenings)
            attempt["ng_tightenings"] = sum(
                row["tightening_result"] == "NG"
                for row in attempt_tightenings
            )
            attempt["is_complete"] = int(
                declared_screws is not None
                and len(attempt_tightenings) == declared_screws
            )

            if qr_status != "OK" or not attempt["is_complete"]:
                attempt["attempt_result"] = "INCOMPLETE"
            elif attempt["ng_tightenings"]:
                attempt["attempt_result"] = "NG"
            else:
                attempt["attempt_result"] = "OK"

            attempts.append(attempt)
            tightenings.extend(attempt_tightenings)

    attempts.sort(
        key=lambda item: (
            item["device_id"],
            item["_trace_qr"],
            item["_timestamp"],
            item["source_row_number"],
        )
    )

    grouped_attempts = defaultdict(list)
    for attempt in attempts:
        grouped_attempts[(attempt["device_id"], attempt["_trace_qr"])].append(attempt)

    attempt_lookup = {}
    for group in grouped_attempts.values():
        total = len(group)
        for attempt_number, attempt in enumerate(group, start=1):
            attempt["attempt_number"] = attempt_number
            attempt["attempts_for_qr_station"] = total
            attempt["is_repeated_qr"] = int(total > 1)
            attempt["is_final_attempt"] = int(attempt_number == total)
            attempt_lookup[attempt["attempt_key"]] = attempt

    for tightening in tightenings:
        attempt = attempt_lookup[tightening["attempt_key"]]
        tightening["attempt_number"] = attempt["attempt_number"]
        tightening["is_final_attempt"] = attempt["is_final_attempt"]

    for attempt in attempts:
        attempt.pop("_timestamp", None)
        attempt.pop("_trace_qr", None)

    return attempts, tightenings, skipped_rows


def get_output_paths(target_date: date) -> dict[str, Path]:
    suffix = target_date.strftime("%Y-%m-%d")
    return {
        "attempts": PROCESSED_DIR / f"plc_piece_attempts_master_{suffix}.csv",
        "pieces": PROCESSED_DIR / f"plc_pieces_master_{suffix}.csv",
        "tightenings": PROCESSED_DIR / f"plc_tightenings_master_{suffix}.csv",
    }


def write_csv_atomic(file_path: Path, rows: list[dict], columns: list[str]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.with_suffix(file_path.suffix + ".tmp")

    try:
        with temp_file.open("w", encoding="utf-8-sig", newline="") as output:
            writer = DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_file, file_path)
    finally:
        if temp_file.exists():
            temp_file.unlink()


def process_plc_date(target_date: date) -> dict:
    file_name = target_date.strftime("%y%m%d_Log.csv")
    all_attempts = []
    all_tightenings = []
    processed_devices = []
    skipped_rows = 0

    for device in get_enabled_devices():
        file_path = RAW_DIR / device["device_id"] / file_name
        if not file_path.exists():
            continue

        attempts, tightenings, skipped = parse_log_file(file_path, device)
        all_attempts.extend(attempts)
        all_tightenings.extend(tightenings)
        skipped_rows += skipped
        processed_devices.append(device["device_id"])

    output_paths = get_output_paths(target_date)

    if processed_devices:
        all_attempts.sort(
            key=lambda item: (
                item["plc_timestamp"],
                item["device_id"],
                item["source_row_number"],
            )
        )
        all_tightenings.sort(
            key=lambda item: (
                item["plc_timestamp"],
                item["device_id"],
                item["source_row_number"],
                item["screw_position"] or 0,
            )
        )

        final_pieces = [
            attempt.copy()
            for attempt in all_attempts
            if attempt["is_final_attempt"] == 1
        ]

        write_csv_atomic(output_paths["attempts"], all_attempts, ATTEMPT_COLUMNS)
        write_csv_atomic(output_paths["pieces"], final_pieces, ATTEMPT_COLUMNS)
        write_csv_atomic(output_paths["tightenings"], all_tightenings, TIGHTENING_COLUMNS)
    else:
        final_pieces = []

    model_summary = {}
    for model in ("T1XX-1", "T1XX-2", "UNKNOWN"):
        model_pieces = [row for row in final_pieces if row["running_model"] == model]
        if model_pieces:
            model_summary[model] = {
                "piece_station_records": len(model_pieces),
                "unique_qr": len({row["qr_code"] for row in model_pieces if row["qr_code"]}),
                "ok": sum(row["attempt_result"] == "OK" for row in model_pieces),
                "ng_or_incomplete": sum(row["attempt_result"] != "OK" for row in model_pieces),
            }

    return {
        "target_date": target_date.isoformat(),
        "processed_devices": processed_devices,
        "attempts": len(all_attempts),
        "pieces": len(final_pieces),
        "tightenings": len(all_tightenings),
        "skipped_rows": skipped_rows,
        "models": model_summary,
        "outputs": {key: str(value) for key, value in output_paths.items()},
    }
