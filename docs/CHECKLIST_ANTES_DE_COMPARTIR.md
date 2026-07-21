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
