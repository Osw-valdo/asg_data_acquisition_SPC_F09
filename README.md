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

En el primer inicio, los archivos `.bat` usan `py` para crear `.venv` e instalar
las dependencias de `requirements.txt`. Se requiere Python 3.10 o superior con
el Python Launcher instalado. Los siguientes inicios reutilizan el entorno.

O manualmente:

```bat
cd C:\asg_data_acquisition_SPC_F09
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502
```

## Ejecutar colector T1XX-1

Doble clic en:

```text
run_collector_T1XX1.bat
```

O manualmente:

```bat
.venv\Scripts\python.exe collector_multi_ip.py --model T1XX-1
```

## Ejecutar colector T1XX-2

Doble clic en:

```text
run_collector_T1XX2.bat
```

O manualmente:

```bat
.venv\Scripts\python.exe collector_multi_ip.py --model T1XX-2
```

## Verificar conexiones FTP de los PLC

Con el entorno virtual instalado, ejecuta:

```bat
.venv\Scripts\python.exe check_ftp_stations.py
```

El script revisa todos los PLC habilitados en `config\plc_devices.csv`, prueba
login, acceso a la carpeta FTP y listado pasivo; también indica si encuentra el
logfile del día. No descarga ni modifica archivos. Las credenciales se leen de
`SPC_PLC_FTP_USER` y `SPC_PLC_FTP_PASSWORD`, o se solicitan al ejecutar. Para
consultar otra fecha: `.venv\Scripts\python.exe check_ftp_stations.py --date 2026-10-21`.

## Prueba de logfile FTP para un PLC de ensamble

El script `read_assembly_log_ftp.py` descarga un único logfile sin interpretar
su contenido ni modificar el PLC. Para un equipo que ya esté en la configuración:

```bat
.venv\Scripts\python.exe read_assembly_log_ftp.py --device PLC_11
```

Para probar una IP que aún no está configurada (reemplaza la IP de ejemplo):

```bat
.venv\Scripts\python.exe read_assembly_log_ftp.py --ip 192.0.2.10 --port 21 --device-id PLC_ENSAMBLE_01
```

Por defecto busca `/0_CARD/Atornillado_AAMM/AAMMDD_Log.csv`, siguiendo el patrón
de los PLC de atornillado conocidos. Si el PLC de ensamble usa otra ruta o
nombre, se pueden indicar con `--remote-dir` y `--remote-name`. Las credenciales
se leen de `SPC_PLC_FTP_USER` y `SPC_PLC_FTP_PASSWORD`, o se solicitan al
ejecutar. La copia local queda en `data\plc_raw\<PLC>\`. El logfile de ensamble
debe revisarse primero para confirmar su formato y decidir cómo extraer QR/OK/NG.

## Iniciar la adquisición FTP

En `asg_launcher.py`, selecciona el modelo y pulsa **Iniciar sistema completo**
para abrir el dashboard, el colector ASG y el colector PLC/FTP, o pulsa **Solo
iniciar Colector PLC / FTP** para iniciar únicamente las descargas FTP. La
ventana del colector solicita usuario y contraseña FTP si no están definidas
en `SPC_PLC_FTP_USER` y `SPC_PLC_FTP_PASSWORD`. Los logs descargados se guardan
en `data\plc_raw\<PLC>\` y los archivos procesados en `data\plc_processed\`.

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


## Autor

Ing. Oswaldo Cantú Casillas
