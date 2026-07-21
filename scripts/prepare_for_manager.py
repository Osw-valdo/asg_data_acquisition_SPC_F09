from pathlib import Path
from datetime import datetime, timedelta
import random
import textwrap
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DEMO = ROOT / "demo_data"
SCRIPTS = ROOT / "scripts"

APP_VERSION = "v0.3"
TODAY = datetime.now().strftime("%Y-%m-%d")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    print(f"Creado/actualizado: {path}")


def create_bat_files():
    write_file(
        ROOT / "run_dashboard.bat",
        r"""
        @echo off
        cd /d %~dp0
        call .venv\Scripts\activate
        streamlit run app.py --server.port 8502
        pause
        """,
    )

    write_file(
        ROOT / "run_collector_T1XX1.bat",
        r"""
        @echo off
        cd /d %~dp0
        call .venv\Scripts\activate
        python collector_multi_ip.py --model T1XX-1
        pause
        """,
    )

    write_file(
        ROOT / "run_collector_T1XX2.bat",
        r"""
        @echo off
        cd /d %~dp0
        call .venv\Scripts\activate
        python collector_multi_ip.py --model T1XX-2
        pause
        """,
    )


def create_demo_data():
    stations = [
        ("ASG_01", "Heatsink Assy & Screwing", "24001-09-05", "HDS302", "T1XX-1|T1XX-2", "shared", 80, 95, 110),
        ("ASG_02", "Heatsink Assy to Lower Case", "24001-05-04", "HUD02", "T1XX-1|T1XX-2", "shared", 90, 103, 118),
        ("ASG_04", "Rear Cover / PCB Cover", "24001-07-04", "HUD05", "T1XX-1|T1XX-2", "shared", 85, 100, 115),
        ("ASG_05", "Flat Mirror Assy", "24001-06-04", "HUD06", "T1XX-1", "exclusive", 80, 90, 105),
        ("ASG_06", "Sunlight Sensor", "24001-10-04", "HDS503", "T1XX-1", "exclusive", 70, 82, 95),
        ("ASG_07", "Upper Case To Lower Case", "24001-08-04", "HUD07", "T1XX-1|T1XX-2", "shared", 85, 100, 115),
    ]

    rows = []
    base_time = datetime.now() - timedelta(minutes=60)

    tid = 10000
    for i in range(60):
        for device_id, station, module, process, models, station_type, tmin, target, tmax in stations:
            ts = base_time + timedelta(minutes=i)
            torque = round(random.normalvariate(target, 2.2), 1)
            angle = int(random.normalvariate(850, 90))
            ok = tmin <= torque <= tmax

            rows.append({
                "pc_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_asg": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "mid": "0061",
                "revision": "001",
                "length": "0231",
                "device_id": device_id,
                "station": station,
                "module_name": module,
                "ip": "DEMO",
                "port": 4545,
                "models_supported": models,
                "running_model": "T1XX-1",
                "process": process,
                "station_type": station_type,
                "torque_unit": "cN·m",
                "is_tightening_result": True,
                "parse_status": "OK",
                "parameter_set_id": 31,
                "batch_counter": i + 1,
                "tightening_status": "OK" if ok else "NOK",
                "torque_status": "OK" if ok else "HIGH",
                "angle_status": "OK",
                "torque_min": tmin,
                "torque_target": target,
                "torque_max": tmax,
                "torque_actual": torque,
                "angle_min": 0,
                "angle_target": 0,
                "angle_max": 0,
                "angle_actual": angle,
                "tightening_id": str(tid).zfill(10),
                "torque_valid_python": ok,
                "angle_valid_python": True,
                "result_valid_python": ok,
                "validation_match_asg": True,
                "raw": "DEMO_RAW",
            })
            tid += 1

    DEMO.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    demo_file = DEMO / "asg_clean_master_demo_T1XX1.csv"
    df.to_csv(demo_file, index=False, encoding="utf-8-sig")
    print(f"Creado demo CSV: {demo_file}")


def create_docs():
    write_file(
        ROOT / "VERSION.md",
        f"""
        # Versionado del proyecto

        ## {APP_VERSION} - Dashboard funcional multi-IP

        Fecha: {TODAY}

        Cambios principales:

        - Colector multi-IP funcional.
        - Separación por modelo de corrida: T1XX-1 y T1XX-2.
        - Estaciones compartidas y exclusivas por modelo.
        - Guardado RAW.
        - Guardado CSV procesado.
        - Guardado SQLite.
        - Dashboard Streamlit.
        - Vista general.
        - Gráficas fusionadas.
        - Dashboard visual por estación.
        - Tabla de datos.
        - Configuración de estaciones.
        """,
    )

    write_file(
        DOCS / "RESUMEN_EJECUTIVO.md",
        """
        # Resumen ejecutivo — ASG Data Acquisition SPC F09

        ## Objetivo

        Implementar una herramienta local para adquirir, procesar, visualizar y almacenar datos de torque y ángulo de los módulos ASG-NW2500 de la línea F09.

        ## Beneficio principal

        El sistema permite tener trazabilidad automática de resultados de atornillado por estación, proceso, modelo y módulo ASG, reduciendo la revisión manual y facilitando análisis de calidad, mantenimiento e ingeniería.

        ## Qué captura

        - Torque mínimo.
        - Torque objetivo.
        - Torque máximo.
        - Torque real.
        - Ángulo real.
        - Resultado OK/NOK.
        - Parameter Set.
        - Tightening ID.
        - Estación.
        - Proceso.
        - Modelo de corrida.
        - IP del módulo.
        - Fecha/hora.

        ## Modelos soportados

        - T1XX-1.
        - T1XX-2.

        El sistema diferencia estaciones compartidas y estaciones exclusivas por modelo.

        ## Salidas del sistema

        - Dashboard en vivo.
        - CSV procesado master.
        - CSV por estación.
        - Base de datos SQLite master.
        - Base de datos SQLite por estación.
        - Logs de conexión.
        - RAW original de comunicación.

        ## Estado actual

        Proyecto en etapa funcional inicial con adquisición real multi-IP y visualización en Streamlit.
        """,
    )

    write_file(
        DOCS / "PITCH_GERENCIA.md",
        """
        # Pitch breve para gerencia

        Este proyecto permite capturar automáticamente los resultados de torque y ángulo de los atornilladores ASG-NW2500 de la línea F09.

        Actualmente la herramienta puede conectarse a múltiples módulos por IP, separar la información por modelo de corrida, visualizar el comportamiento en vivo y guardar la data en CSV y base de datos.

        El principal valor es generar trazabilidad automática por estación, proceso y modelo, facilitando análisis de calidad, detección de desviaciones, soporte a SPC y revisión histórica sin depender únicamente de extracción manual.

        En una siguiente etapa, el sistema puede evolucionar hacia reportes automáticos, alarmas, análisis CPK y conexión con Power BI o SQL Server.
        """,
    )

    write_file(
        DOCS / "CHECKLIST_ANTES_DE_COMPARTIR.md",
        """
        # Checklist antes de compartir el proyecto

        Antes de enviar o subir el proyecto, verificar lo siguiente:

        ## No compartir

        - `.venv/`
        - `logs/`
        - `database/`
        - `data/raw_live/`
        - `data/processed/`
        - `data/stations/`
        - Bases `.db` reales.
        - CSVs reales de producción, si contienen información sensible.

        ## Sí compartir

        - `app.py`
        - `collector_multi_ip.py`
        - `requirements.txt`
        - `README.md`
        - `VERSION.md`
        - `config/asg_devices.csv`
        - `src/`
        - `docs/`
        - `demo_data/`
        - `run_dashboard.bat`
        - `run_collector_T1XX1.bat`
        - `run_collector_T1XX2.bat`

        ## Revisión rápida

        - Ejecutar `python -m py_compile app.py`.
        - Ejecutar `python -m py_compile collector_multi_ip.py`.
        - Confirmar que el dashboard abre en `localhost:8502`.
        - Confirmar que el README explica cómo operar el sistema.
        """,
    )

    write_file(
        ROOT / "README.md",
        """
        # ASG Data Acquisition SPC F09

        Sistema local para adquisición, procesamiento, visualización y almacenamiento de datos de torque de módulos ASG-NW2500 en línea F09.

        ## Funciones principales

        - Adquisición multi-IP.
        - Comunicación TCP / ACOP Open Protocol.
        - Separación por modelo: T1XX-1 y T1XX-2.
        - Estaciones compartidas y exclusivas.
        - Visualización Streamlit.
        - Dashboard visual por estación.
        - Guardado RAW.
        - Guardado CSV procesado.
        - Guardado SQLite.
        - Logs de conexión.

        ## Ejecutar dashboard

        Doble clic en:

        ```text
        run_dashboard.bat
        ```

        O manualmente:

        ```bat
        cd C:\\asg_data_acquisition_SPC_F09
        .venv\\Scripts\\activate
        streamlit run app.py --server.port 8502
        ```

        ## Ejecutar colector T1XX-1

        Doble clic en:

        ```text
        run_collector_T1XX1.bat
        ```

        O manualmente:

        ```bat
        python collector_multi_ip.py --model T1XX-1
        ```

        ## Ejecutar colector T1XX-2

        Doble clic en:

        ```text
        run_collector_T1XX2.bat
        ```

        O manualmente:

        ```bat
        python collector_multi_ip.py --model T1XX-2
        ```

        ## Dónde se guarda la data

        RAW master:

        ```text
        data/raw_live/
        ```

        CSV procesado master:

        ```text
        data/processed/
        ```

        Data por estación:

        ```text
        data/stations/
        ```

        Base de datos master:

        ```text
        database/asg_torque_master.db
        ```

        Logs:

        ```text
        logs/
        ```

        ## Documentación

        Ver carpeta:

        ```text
        docs/
        ```

        Archivos recomendados:

        - `RESUMEN_EJECUTIVO.md`
        - `PITCH_GERENCIA.md`
        - `CHECKLIST_ANTES_DE_COMPARTIR.md`
        """,
    )


def patch_app_about_tab():
    app = ROOT / "app.py"
    if not app.exists():
        print("No existe app.py, se omite parche de About.")
        return

    text = app.read_text(encoding="utf-8")

    if 'APP_VERSION = "' not in text:
        text = text.replace(
            "STATUS_FILE = LOG_DIR / \"device_status.json\"",
            f"STATUS_FILE = LOG_DIR / \"device_status.json\"\nAPP_VERSION = \"{APP_VERSION}\"",
        )

    if "def render_about_tab():" not in text:
        about_func = r'''
def render_about_tab():
    st.subheader("Acerca del sistema")

    c1, c2, c3 = st.columns(3)
    c1.metric("Proyecto", "ASG SPC F09")
    c2.metric("Versión", APP_VERSION)
    c3.metric("Línea", "F09")

    st.markdown(
        """
        Este sistema permite adquirir, procesar, visualizar y almacenar datos de torque y ángulo
        desde módulos ASG-NW2500 de la línea F09.

        **Objetivo principal:** generar trazabilidad automática de resultados de atornillado
        por estación, proceso, modelo de corrida e IP.

        **Modelos soportados:**

        - T1XX-1
        - T1XX-2

        **Salidas disponibles:**

        - CSV RAW
        - CSV procesado
        - SQLite master
        - SQLite por estación
        - Logs de conexión
        - Dashboard Streamlit
        """
    )

    st.info(
        "Proyecto desarrollado como herramienta interna para soporte de producción, "
        "calidad, mantenimiento e ingeniería."
    )
'''
        text = text.replace("\ndef main():", "\n" + about_func + "\n\ndef main():")

    old_tabs = '''tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Vista general",
            "Gráficas fusionadas",
            "Dashboard por estación",
            "Tabla de datos",
            "Configuración",
        ]
    )'''

    new_tabs = '''tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "Vista general",
            "Gráficas fusionadas",
            "Dashboard por estación",
            "Tabla de datos",
            "Configuración",
            "Acerca del sistema",
        ]
    )'''

    if old_tabs in text:
        text = text.replace(old_tabs, new_tabs)

    if "with tab6:\n        render_about_tab()" not in text:
        text = text.replace(
            "with tab5:\n        render_config_tab()",
            "with tab5:\n        render_config_tab()\n\n    with tab6:\n        render_about_tab()",
        )

    app.write_text(text, encoding="utf-8")
    print("app.py actualizado con pestaña Acerca del sistema.")


def main():
    create_bat_files()
    create_demo_data()
    create_docs()
    patch_app_about_tab()
    print()
    print("Preparación para gerencia terminada correctamente.")


if __name__ == "__main__":
    main()