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
cd C:\asg_data_acquisition_SPC_F09
.venv\Scripts\activate
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


## Autor

Ing. Oswaldo Cantú Casillas
