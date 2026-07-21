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


## Autor

Ing. Oswaldo Cantú Casillas
