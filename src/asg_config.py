from pathlib import Path
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "asg_devices.csv"

REQUIRED_COLUMNS = [
    "enabled",
    "device_id",
    "station",
    "module_name",
    "ip",
    "port",
    "models",
    "process",
    "station_type",
    "torque_unit",
]


def load_devices(config_file: Path = CONFIG_FILE) -> pd.DataFrame:
    """
    Carga la configuración de los ASG desde config/asg_devices.csv.
    """
    if not config_file.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración: {config_file}")

    df = pd.read_csv(config_file)

    missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Faltan columnas en asg_devices.csv: {missing_columns}")

    df["enabled"] = df["enabled"].astype(int)
    df["port"] = df["port"].astype(int)
    df["device_id"] = df["device_id"].astype(str).str.strip()
    df["station"] = df["station"].astype(str).str.strip()
    df["module_name"] = df["module_name"].astype(str).str.strip()
    df["ip"] = df["ip"].astype(str).str.strip()
    df["models"] = df["models"].astype(str).str.strip()
    df["process"] = df["process"].astype(str).str.strip()
    df["station_type"] = df["station_type"].astype(str).str.strip()
    df["torque_unit"] = df["torque_unit"].astype(str).str.strip()

    return df


def get_enabled_devices() -> pd.DataFrame:
    """
    Regresa solo los equipos habilitados.
    """
    df = load_devices()
    return df[df["enabled"] == 1].copy()


def get_devices_by_model(model: str) -> pd.DataFrame:
    """
    Regresa los equipos que aplican para un modelo específico.

    Ejemplo:
    - T1XX-1 mostrará compartidas + exclusivas T1XX-1
    - T1XX-2 mostrará compartidas + exclusivas T1XX-2
    """
    df = get_enabled_devices()
    model = model.strip()

    return df[df["models"].str.contains(model, regex=False)].copy()


def get_shared_devices() -> pd.DataFrame:
    """
    Regresa estaciones compartidas entre modelos.
    """
    df = get_enabled_devices()
    return df[df["station_type"].str.lower() == "shared"].copy()


def get_exclusive_devices(model: str) -> pd.DataFrame:
    """
    Regresa estaciones exclusivas de un modelo.
    """
    df = get_devices_by_model(model)
    return df[df["station_type"].str.lower() == "exclusive"].copy()


def print_summary():
    """
    Imprime resumen de configuración.
    """
    df = get_enabled_devices()

    print("=" * 80)
    print("RESUMEN DE CONFIGURACIÓN ASG")
    print("=" * 80)

    print(f"Archivo: {CONFIG_FILE}")
    print(f"Equipos habilitados: {len(df)}")
    print()

    print("Equipos:")
    for _, row in df.iterrows():
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
    print("Por modelo:")
    for model in ["T1XX-1", "T1XX-2"]:
        model_df = get_devices_by_model(model)
        shared_df = model_df[model_df["station_type"].str.lower() == "shared"]
        exclusive_df = model_df[model_df["station_type"].str.lower() == "exclusive"]

        print(f"{model}:")
        print(f"  Total aplicables: {len(model_df)}")
        print(f"  Compartidas:      {len(shared_df)}")
        print(f"  Exclusivas:       {len(exclusive_df)}")
        print()

    print("=" * 80)


if __name__ == "__main__":
    print_summary()
