from datetime import datetime


TORQUE_SCALE = 100

TIGHTENING_STATUS_MAP = {
    "0": "NOK",
    "1": "OK",
}

TORQUE_ANGLE_STATUS_MAP = {
    "0": "LOW",
    "1": "OK",
    "2": "HIGH",
}

BATCH_STATUS_MAP = {
    "0": "BATCH_NOK",
    "1": "BATCH_OK",
    "2": "NOT_USED",
}


def field_1b(text: str, start: int, end: int) -> str:
    """
    Extrae un campo usando posiciones 1-based inclusivas.
    Ejemplo: start=3, end=6 extrae caracteres 3,4,5,6.
    """
    if not text:
        return ""
    return text[start - 1:end]


def to_int(value: str):
    value = str(value).strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def to_float_scaled(value: str, scale: int = TORQUE_SCALE):
    number = to_int(value)
    if number is None:
        return None
    return number / scale


def parse_asg_timestamp(value: str):
    value = str(value).strip()
    if value == "":
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d:%H:%M:%S")
    except ValueError:
        return None


def parse_packet(raw_packet: str) -> dict:
    """
    Parsea encabezado general Open Protocol / ACOP.

    Header usado:
    0000-0003 length
    0004-0007 MID
    0008-0010 revision
    data desde posición 20
    """
    text = str(raw_packet).replace("\x00", "").strip("\n\r")

    return {
        "raw": text,
        "length": text[0:4] if len(text) >= 4 else "",
        "mid": text[4:8] if len(text) >= 8 else "",
        "revision": text[8:11] if len(text) >= 11 else "",
        "data": text[20:] if len(text) > 20 else "",
    }


def validate_range(value, min_value, max_value):
    """
    Valida si value está dentro de min/max.

    Si min y max son 0, se interpreta como sin límites activos.
    """
    if value is None or min_value is None or max_value is None:
        return None

    if min_value == 0 and max_value == 0:
        return True

    return min_value <= value <= max_value


def parse_mid0061_rev001(
    raw_packet: str,
    device_info: dict | None = None,
    running_model: str = "UNKNOWN",
) -> dict:
    """
    Convierte un paquete RAW MID 0061 Rev 001 en datos cocinados.

    device_info puede venir desde config/asg_devices.csv:
    {
        "device_id": "ASG_01",
        "station": "Heatsink Assy & Screwing",
        "module_name": "24001-09-05",
        "ip": "10.132.160.190",
        "port": "4545",
        "models": "T1XX-1|T1XX-2",
        "process": "HDS302",
        "station_type": "shared",
        "torque_unit": "cN·m"
    }
    """
    device_info = device_info or {}

    packet = parse_packet(raw_packet)
    data = packet["data"]

    row = {
        # Metadata del paquete
        "pc_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mid": packet["mid"],
        "revision": packet["revision"],
        "length": packet["length"],

        # Metadata del equipo
        "device_id": device_info.get("device_id", ""),
        "station": device_info.get("station", ""),
        "module_name": device_info.get("module_name", ""),
        "ip": device_info.get("ip", ""),
        "port": device_info.get("port", ""),
        "models_supported": device_info.get("models", ""),
        "running_model": running_model,
        "process": device_info.get("process", ""),
        "station_type": device_info.get("station_type", ""),
        "torque_unit": device_info.get("torque_unit", "cN·m"),

        # RAW
        "raw": packet["raw"],
    }

    if packet["mid"] != "0061":
        row["is_tightening_result"] = False
        row["parse_status"] = "NOT_MID_0061"
        return row

    if packet["revision"] != "001":
        row["is_tightening_result"] = True
        row["parse_status"] = f"UNSUPPORTED_REVISION_{packet['revision']}"
        return row

    # Campos propios del MID 0061 Rev 001
    cell_id = field_1b(data, 3, 6)
    channel_id = field_1b(data, 9, 10)
    controller_name = field_1b(data, 13, 37).strip()
    vin = field_1b(data, 40, 64).strip()
    job_id = field_1b(data, 67, 68)

    parameter_set_id = to_int(field_1b(data, 71, 73))
    batch_size = to_int(field_1b(data, 76, 79))
    batch_counter = to_int(field_1b(data, 82, 85))

    tightening_status_code = field_1b(data, 88, 88)
    torque_status_code = field_1b(data, 91, 91)
    angle_status_code = field_1b(data, 94, 94)

    torque_min = to_float_scaled(field_1b(data, 97, 102))
    torque_max = to_float_scaled(field_1b(data, 105, 110))
    torque_target = to_float_scaled(field_1b(data, 113, 118))
    torque_actual = to_float_scaled(field_1b(data, 121, 126))

    angle_min = to_int(field_1b(data, 129, 133))
    angle_max = to_int(field_1b(data, 136, 140))
    angle_target = to_int(field_1b(data, 143, 147))
    angle_actual = to_int(field_1b(data, 150, 154))

    timestamp_asg_raw = field_1b(data, 157, 175)
    parameter_change_timestamp_raw = field_1b(data, 178, 196)

    timestamp_asg = parse_asg_timestamp(timestamp_asg_raw)
    parameter_change_timestamp = parse_asg_timestamp(parameter_change_timestamp_raw)

    batch_status_code = field_1b(data, 199, 199)
    tightening_id = field_1b(data, 202, 211).strip()

    torque_valid_python = validate_range(torque_actual, torque_min, torque_max)
    angle_valid_python = validate_range(angle_actual, angle_min, angle_max)

    result_valid_python = bool(torque_valid_python and angle_valid_python)

    tightening_status = TIGHTENING_STATUS_MAP.get(tightening_status_code, "UNKNOWN")
    torque_status = TORQUE_ANGLE_STATUS_MAP.get(torque_status_code, "UNKNOWN")
    angle_status = TORQUE_ANGLE_STATUS_MAP.get(angle_status_code, "UNKNOWN")
    batch_status = BATCH_STATUS_MAP.get(batch_status_code, "UNKNOWN")

    validation_match_asg = None
    if tightening_status in ["OK", "NOK"]:
        validation_match_asg = (tightening_status == "OK") == result_valid_python

    row.update({
        "is_tightening_result": True,
        "parse_status": "OK",

        "cell_id": cell_id,
        "channel_id": channel_id,
        "controller_name": controller_name,
        "vin": vin,
        "job_id": job_id,

        "parameter_set_id": parameter_set_id,
        "batch_size": batch_size,
        "batch_counter": batch_counter,

        "tightening_status_code": tightening_status_code,
        "tightening_status": tightening_status,

        "torque_status_code": torque_status_code,
        "torque_status": torque_status,

        "angle_status_code": angle_status_code,
        "angle_status": angle_status,

        "torque_min": torque_min,
        "torque_max": torque_max,
        "torque_target": torque_target,
        "torque_actual": torque_actual,

        "angle_min": angle_min,
        "angle_max": angle_max,
        "angle_target": angle_target,
        "angle_actual": angle_actual,

        "timestamp_asg_raw": timestamp_asg_raw,
        "timestamp_asg": timestamp_asg.strftime("%Y-%m-%d %H:%M:%S") if timestamp_asg else "",

        "parameter_change_timestamp_raw": parameter_change_timestamp_raw,
        "parameter_change_timestamp": parameter_change_timestamp.strftime("%Y-%m-%d %H:%M:%S") if parameter_change_timestamp else "",

        "batch_status_code": batch_status_code,
        "batch_status": batch_status,

        "tightening_id": tightening_id,

        "torque_valid_python": torque_valid_python,
        "angle_valid_python": angle_valid_python,
        "result_valid_python": result_valid_python,
        "validation_match_asg": validation_match_asg,
    })

    return row


if __name__ == "__main__":
    sample_raw = (
        "02310061001         "
        "010000020003EST0                     04                         "
        "05000603107000108000109110111112009000130118001401030015010300"
        "1600000170000018000001900080202026-05-25:13:18:55212026-05-25:13:18:55221230000007803"
    )

    sample_device = {
        "device_id": "ASG_TEST",
        "station": "TEST STATION",
        "module_name": "TEST-MODULE",
        "ip": "10.132.160.000",
        "port": "4545",
        "models": "T1XX-1|T1XX-2",
        "process": "TEST",
        "station_type": "shared",
        "torque_unit": "cN·m",
    }

    result = parse_mid0061_rev001(sample_raw, sample_device, running_model="T1XX-1")

    for key, value in result.items():
        print(f"{key}: {value}")
