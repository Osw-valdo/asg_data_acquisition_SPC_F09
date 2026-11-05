"""Export torque CSV data as a Microsoft Access database."""

from __future__ import annotations

from io import BytesIO
import re
import tempfile
from pathlib import Path

import pandas as pd


_FLOAT_COLUMNS = {
    "length",
    "port",
    "batch_size",
    "batch_counter",
}
_BOOLEAN_COLUMNS = {
    "is_tightening_result",
    "torque_valid_python",
    "angle_valid_python",
    "result_valid_python",
    "validation_match_asg",
}


def _column_type(name: str, values: pd.Series) -> tuple[str, str]:
    lower_name = name.lower()
    nonempty = values[values != ""]

    if lower_name.endswith("_timestamp") or lower_name.endswith("_datetime"):
        parsed = pd.to_datetime(nonempty, errors="coerce")
        if nonempty.empty or parsed.notna().all():
            return "DATETIME", "datetime"

    if lower_name in _BOOLEAN_COLUMNS:
        return "BIT", "boolean"

    if lower_name.startswith(("torque_", "angle_")) or lower_name in _FLOAT_COLUMNS:
        parsed = pd.to_numeric(nonempty, errors="coerce")
        if nonempty.empty or parsed.notna().all():
            return "DOUBLE", "number"

    max_length = int(values.str.len().max()) if not values.empty else 0
    if max_length <= 255:
        return f"VARCHAR({max(max_length, 1)})", "text"
    return "LONGCHAR", "text"


def _quoted_identifier(name: str) -> str:
    if len(name) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise RuntimeError(f"Nombre de columna no válido para Access: {name!r}")
    return f"[{name}]"


def _create_database(path: Path) -> None:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "La exportación Access requiere pywin32. Actualiza el entorno con "
            "setup_environment.bat."
        ) from exc

    pythoncom.CoInitialize()
    errors = []
    try:
        for provider in ("Microsoft.ACE.OLEDB.16.0", "Microsoft.ACE.OLEDB.12.0"):
            catalog = None
            try:
                catalog = win32com.client.Dispatch("ADOX.Catalog")
                catalog.Create(f"Provider={provider};Data Source={path};")
                catalog.ActiveConnection.Close()
                return
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
            finally:
                catalog = None
    finally:
        pythoncom.CoUninitialize()

    detail = " | ".join(errors)
    raise RuntimeError(
        "No se pudo crear el archivo .accdb. Instala Microsoft Access Database "
        f"Engine (ACE) compatible con Python. Detalle: {detail}"
    )


def torque_csv_to_access(csv_data: bytes) -> bytes:
    """Return a self-contained .accdb with one TorqueResults table."""
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "La exportación Access requiere pyodbc. Actualiza el entorno con "
            "setup_environment.bat."
        ) from exc

    df = pd.read_csv(BytesIO(csv_data), dtype=str, keep_default_na=False)
    if len(df.columns) == 0:
        raise RuntimeError("La descarga no contiene columnas para exportar.")
    if len(df.columns) > 255:
        raise RuntimeError("Access admite hasta 255 columnas por tabla.")

    driver = next(
        (
            name
            for name in pyodbc.drivers()
            if "access" in name.lower() and "accdb" in name.lower()
        ),
        None,
    )
    if driver is None:
        raise RuntimeError(
            "No se encontró el controlador ODBC de Microsoft Access para .accdb. "
            "Instala Microsoft Access Database Engine de la misma arquitectura "
            "que Python (32 o 64 bits)."
        )

    column_specs = [
        (name, *_column_type(name, df[name]))
        for name in df.columns
    ]
    identifiers = [_quoted_identifier(name) for name, _, _ in column_specs]

    with tempfile.TemporaryDirectory(prefix="asg_access_") as folder:
        db_path = Path(folder) / "torque_export.accdb"
        _create_database(db_path)
        connection = pyodbc.connect(
            f"DRIVER={{{driver}}};DBQ={db_path};",
            autocommit=False,
        )
        try:
            definitions = ", ".join(
                f"{identifier} {sql_type}"
                for identifier, (_, sql_type, _) in zip(identifiers, column_specs)
            )
            connection.execute(f"CREATE TABLE [TorqueResults] ({definitions})")

            if not df.empty:
                placeholders = ", ".join("?" for _ in identifiers)
                statement = (
                    f"INSERT INTO [TorqueResults] ({', '.join(identifiers)}) "
                    f"VALUES ({placeholders})"
                )
                cursor = connection.cursor()
                for start in range(0, len(df), 250):
                    records = df.iloc[start : start + 250].itertuples(
                        index=False, name=None
                    )
                    rows = []
                    for record in records:
                        converted = []
                        for value, (_, _, kind) in zip(record, column_specs):
                            if value == "":
                                converted.append(None)
                            elif kind == "number":
                                converted.append(float(value))
                            elif kind == "boolean":
                                converted.append(
                                    value.strip().lower() in {"1", "true", "yes"}
                                )
                            elif kind == "datetime":
                                parsed = pd.to_datetime(value, errors="raise")
                                converted.append(
                                    parsed.to_pydatetime().replace(tzinfo=None)
                                )
                            else:
                                converted.append(value)
                        rows.append(tuple(converted))
                    cursor.executemany(statement, rows)

            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(
                f"No se pudo escribir la base Access: {exc}"
            ) from exc
        finally:
            connection.close()

        return db_path.read_bytes()
