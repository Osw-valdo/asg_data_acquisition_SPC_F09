# Versionado del proyecto

## v0.6.3 - Revisión integral T1XX-2

Fecha: 2026-08-31

Cambios principales:

- El launcher transmite el modelo seleccionado al colector PLC/FTP.
- T1XX-2 consulta únicamente sus 8 PLCs aplicables.
- Estado PLC/FTP visible en la Vista general.
- Diagnóstico local seguro para T1XX-1 y T1XX-2.
- Cp/Cpk usa PLC logfile como fuente preferida y ASG como respaldo.
- Los ASG inaccesibles reintentan con espera progresiva sin bloquear los demás.
- Se distinguen conexión FTP, acceso a SD y disponibilidad del logfile.
- `data/plc_processed/` queda excluido de Git.

## Autor

Ing. Oswaldo Cantú Casillas
