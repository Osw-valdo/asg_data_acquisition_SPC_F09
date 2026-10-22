BITACORA DEL PROYECTO
Actualizada: 2026-10-21

CAMBIOS RECIENTES
- README.md se amplió como guía de mantenimiento para otros agentes: arquitectura,
  flujos ASG/FTP, comandos, configuración, salidas, seguridad, diagnóstico y
  limitaciones actuales para PLC de ensamble.
- Se corrigio el requisito documentado de Python a 3.12+, que es el minimo
  indicado por NumPy 2.5.1 incluido en requirements.txt.
- read_assembly_log_ftp.py ahora puede ejecutarse sin argumentos: pregunta IP,
  puerto FTP y credenciales; oculta la contraseña y crea una carpeta local basada
  en la IP. Las opciones --device y --ip siguen disponibles.
- Se agregaron ASG_11 y ASG_12, estaciones CLON compartidas para T1XX-1 y T1XX-2.
- Se agregaron PLC_11 y PLC_12; ambos usan FTP por el puerto 21.
- Las tablas ASG/PLC coinciden en estación, modulo, proceso, modelos y tornillos por pieza.
- Configuraciones guardadas en el commit 7387c3f y subidas a la rama
  osw-valdo-analisis-repositorio. Aun no estan integradas en main.
- Se corrigieron los archivos .bat para no depender del alias python.exe de
  WindowsApps. En el primer inicio, setup_environment.bat crea .venv con py -3
  e instala las dependencias de requirements.txt; los siguientes inicios usan
  directamente el Python del entorno virtual.
- README.md ahora explica el primer inicio y los comandos manuales.
- Se agrego check_ftp_stations.py para verificar conexion, login, carpeta, listado
  pasivo y logfile del dia en todos los PLC habilitados. Es de solo lectura y no
  descarga ni modifica los logs.
- Se corrigio el cierre de conexion del verificador FTP para que un fallo de
  conexion no provoque un AttributeError al intentar enviar QUIT sin socket.
- Se corrigio el mismo cierre en collector_ftp_plc.py: cuando la conexion falla
  antes de crear el socket, el error original ya no queda oculto por un fallo en QUIT.
- Se agrego read_assembly_log_ftp.py para descargar de forma puntual un logfile
  FTP de un PLC de ensamble. Permite elegir un ID configurado o una IP directa y
  cambiar ruta/nombre remotos; no interpreta datos ni modifica el PLC.
- README.md documenta la prueba FTP de ensamble y la ruta local de la copia RAW.
- Se incorporaron src/plc_config.py y src/plc_log_parser.py desde los archivos
  compartidos, para que collector_ftp_plc.py pueda arrancar con config/plc_devices.csv.
- README.md explica como iniciar el colector FTP desde el launcher; los logs RAW
  se guardan en data\plc_raw\<PLC>\ y los procesados en data\plc_processed\.

PARA CONTINUAR
- Abrir el repositorio en la rama osw-valdo-analisis-repositorio para ver estos cambios.
- Ejecutar run_launcher.bat; el primer inicio requiere Python 3.12+ y puede tardar
  mientras se instala requirements.txt.
- Para probar FTP, ejecutar:
  .venv\Scripts\python.exe check_ftp_stations.py
- Para descargar el logfile de prueba de un PLC registrado:
  .venv\Scripts\python.exe read_assembly_log_ftp.py --device PLC_11
- Para modo interactivo (pregunta IP, puerto y credenciales):
  .venv\Scripts\python.exe read_assembly_log_ftp.py
- Para un PLC no registrado, usar --ip, --port y --device-id; si la ruta o nombre
  remoto difiere, indicar --remote-dir y --remote-name.
- Para recolectar, abrir asg_launcher.py y seleccionar "Iniciar sistema completo"
  o "Solo iniciar Colector PLC / FTP". Se requieren credenciales FTP locales.
- Los archivos de data/ que aparecen modificados localmente son datos de prueba y no se
  incluyeron en el commit de configuracion.
