from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collector_multi_ip.py"
APP = ROOT / "app.py"

backup_collector = ROOT / f"collector_backup_before_v053_sessions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
backup_app = ROOT / f"app_backup_before_v053_sessions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

collector_text = COLLECTOR.read_text(encoding="utf-8")
app_text = APP.read_text(encoding="utf-8")

backup_collector.write_text(collector_text, encoding="utf-8")
backup_app.write_text(app_text, encoding="utf-8")

print(f"Backup collector creado: {backup_collector}")
print(f"Backup app creado: {backup_app}")

# ============================================================
# PATCH COLLECTOR
# ============================================================

# 1. Agregar SESSION_FILE
if "SESSION_FILE = LOG_DIR / \"production_session.json\"" not in collector_text:
    collector_text = collector_text.replace(
        'COLLECTOR_LOG = LOG_DIR / "collector.log"',
        'COLLECTOR_LOG = LOG_DIR / "collector.log"\nSESSION_FILE = LOG_DIR / "production_session.json"'
    )

# 2. Agregar columnas de sesión al RAW
if '"production_session_id",' not in collector_text:
    collector_text = collector_text.replace(
        '''    "running_model",
    "process",''',
        '''    "running_model",
    "production_session_id",
    "production_session_started_at",
    "production_session_mode",
    "process",'''
    )

# 3. Agregar funciones de sesión
session_functions = r'''
def make_production_session_id(running_model: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = str(running_model).replace("/", "_").replace("\\", "_").replace(" ", "_")
    return f"{safe_model}_{stamp}"


def load_session_state() -> dict:
    if not SESSION_FILE.exists():
        return {"models": {}}

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if "models" not in payload:
            payload["models"] = {}

        return payload

    except Exception:
        return {"models": {}}


def save_session_state(payload: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    payload["updated_at"] = now_str()

    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def create_new_production_session(running_model: str, mode: str = "new") -> dict:
    session_info = {
        "production_session_id": make_production_session_id(running_model),
        "production_session_started_at": now_str(),
        "production_session_mode": mode,
        "running_model": running_model,
    }

    payload = load_session_state()
    payload.setdefault("models", {})
    payload["models"][running_model] = session_info

    save_session_state(payload)

    return session_info


def continue_production_session(running_model: str, existing_session: dict) -> dict:
    session_info = dict(existing_session)
    session_info["production_session_mode"] = "continue"
    session_info["last_continued_at"] = now_str()

    payload = load_session_state()
    payload.setdefault("models", {})
    payload["models"][running_model] = session_info

    save_session_state(payload)

    return session_info


def resolve_production_session(
    running_model: str,
    force_new: bool = False,
    force_continue: bool = False,
) -> dict:
    payload = load_session_state()
    existing_session = payload.get("models", {}).get(running_model)

    if force_new:
        return create_new_production_session(running_model, mode="new")

    if force_continue and existing_session:
        return continue_production_session(running_model, existing_session)

    if force_continue and not existing_session:
        print()
        print(f"No existe sesión previa para {running_model}. Se creará una nueva.")
        return create_new_production_session(running_model, mode="new")

    print()
    print("=" * 90)
    print("CONFIGURACIÓN DE CONTEO DE PIEZAS")
    print("=" * 90)
    print(f"Modelo: {running_model}")
    print()

    if existing_session:
        print("Sesión actual encontrada:")
        print(f"- ID:      {existing_session.get('production_session_id', '')}")
        print(f"- Inicio:  {existing_session.get('production_session_started_at', '')}")
        print()
        print("1) Continuar conteo actual")
        print("2) Iniciar nuevo conteo")
        print()
        option = input("Opción [1/2]: ").strip()

        if option == "2":
            return create_new_production_session(running_model, mode="new")

        return continue_production_session(running_model, existing_session)

    print("No existe sesión previa para este modelo.")
    print("Se iniciará un nuevo conteo.")
    print()

    return create_new_production_session(running_model, mode="new")
'''

if "def make_production_session_id" not in collector_text:
    collector_text = collector_text.replace(
        "\n\ndef init_device_status",
        f"\n\n{session_functions}\n\ndef init_device_status"
    )

# 4. init_device_status con sesión
collector_text = collector_text.replace(
    "def init_device_status(device_info: dict, running_model: str):",
    "def init_device_status(device_info: dict, running_model: str, session_info=None):\n    session_info = session_info or {}"
)

if '"production_session_id": session_info.get("production_session_id", ""),' not in collector_text:
    collector_text = collector_text.replace(
        '''            "running_model": running_model,
            "process": device_info["process"],''',
        '''            "running_model": running_model,
            "production_session_id": session_info.get("production_session_id", ""),
            "production_session_started_at": session_info.get("production_session_started_at", ""),
            "production_session_mode": session_info.get("production_session_mode", ""),
            "process": device_info["process"],'''
    )

# 5. build_raw_row con sesión
collector_text = collector_text.replace(
    "def build_raw_row(raw_packet: str, device_info: dict, running_model: str) -> dict:\n    packet = parse_packet(raw_packet)",
    "def build_raw_row(raw_packet: str, device_info: dict, running_model: str, session_info=None) -> dict:\n    session_info = session_info or {}\n    packet = parse_packet(raw_packet)"
)

if '"production_session_id": session_info.get("production_session_id", ""),' not in collector_text.split("def writer_worker")[0]:
    collector_text = collector_text.replace(
        '''        "running_model": running_model,
        "process": device_info.get("process", ""),''',
        '''        "running_model": running_model,
        "production_session_id": session_info.get("production_session_id", ""),
        "production_session_started_at": session_info.get("production_session_started_at", ""),
        "production_session_mode": session_info.get("production_session_mode", ""),
        "process": device_info.get("process", ""),'''
    )

# 6. device_worker recibe session_info
collector_text = collector_text.replace(
    '''def device_worker(
    device_info: dict,
    running_model: str,
    event_queue: queue.Queue,
    stop_event: threading.Event,
):''',
    '''def device_worker(
    device_info: dict,
    running_model: str,
    session_info: dict,
    event_queue: queue.Queue,
    stop_event: threading.Event,
):'''
)

collector_text = collector_text.replace(
    "raw_row = build_raw_row(raw_packet, device_info, running_model)",
    "raw_row = build_raw_row(raw_packet, device_info, running_model, session_info=session_info)"
)

if "clean_row.update(" not in collector_text:
    collector_text = collector_text.replace(
        '''                            clean_row = parse_mid0061_rev001(
                                raw_packet=raw_packet,
                                device_info=device_info,
                                running_model=running_model,
                            )

                            event_queue.put({"type": "clean", "row": clean_row})''',
        '''                            clean_row = parse_mid0061_rev001(
                                raw_packet=raw_packet,
                                device_info=device_info,
                                running_model=running_model,
                            )

                            clean_row.update(
                                {
                                    "production_session_id": session_info.get("production_session_id", ""),
                                    "production_session_started_at": session_info.get("production_session_started_at", ""),
                                    "production_session_mode": session_info.get("production_session_mode", ""),
                                }
                            )

                            event_queue.put({"type": "clean", "row": clean_row})'''
    )

# 7. print_startup_summary con sesión
collector_text = collector_text.replace(
    "def print_startup_summary(devices, running_model: str):",
    "def print_startup_summary(devices, running_model: str, session_info=None):\n    session_info = session_info or {}"
)

if 'print(f"Sesión conteo:' not in collector_text:
    collector_text = collector_text.replace(
        '''    print(f"Modelo de corrida: {running_model}")
    print(f"Equipos a monitorear: {len(devices)}")''',
        '''    print(f"Modelo de corrida: {running_model}")
    print(f"Sesión conteo: {session_info.get('production_session_id', '')}")
    print(f"Inicio conteo:  {session_info.get('production_session_started_at', '')}")
    print(f"Modo conteo:    {session_info.get('production_session_mode', '')}")
    print(f"Equipos a monitorear: {len(devices)}")'''
    )

# 8. Agregar flags opcionales
if "--new-count" not in collector_text:
    collector_text = collector_text.replace(
        '''    parser.add_argument(
        "--model",
        choices=["T1XX-1", "T1XX-2"],
        help="Modelo de corrida. Ejemplo: --model T1XX-1",
    )

    return parser.parse_args()''',
        '''    parser.add_argument(
        "--model",
        choices=["T1XX-1", "T1XX-2"],
        help="Modelo de corrida. Ejemplo: --model T1XX-1",
    )

    parser.add_argument(
        "--new-count",
        action="store_true",
        help="Inicia una nueva sesión de conteo sin preguntar.",
    )

    parser.add_argument(
        "--continue-count",
        action="store_true",
        help="Continúa la sesión de conteo actual sin preguntar.",
    )

    return parser.parse_args()'''
    )

# 9. main: resolver sesión después del modelo
if "session_info = resolve_production_session" not in collector_text:
    collector_text = collector_text.replace(
        '''    args = parse_args()
    running_model = args.model or ask_running_model()

    devices = get_devices_by_model(running_model)''',
        '''    args = parse_args()
    running_model = args.model or ask_running_model()

    session_info = resolve_production_session(
        running_model=running_model,
        force_new=args.new_count,
        force_continue=args.continue_count,
    )

    devices = get_devices_by_model(running_model)'''
    )

collector_text = collector_text.replace(
    "init_device_status(device_info, running_model)",
    "init_device_status(device_info, running_model, session_info)"
)

collector_text = collector_text.replace(
    "print_startup_summary(devices, running_model)",
    "print_startup_summary(devices, running_model, session_info)"
)

collector_text = collector_text.replace(
    '''            target=device_worker,
            args=(device_info, running_model, event_queue, stop_event),''',
    '''            target=device_worker,
            args=(device_info, running_model, session_info, event_queue, stop_event),'''
)

# ============================================================
# PATCH APP
# ============================================================

# 1. Versión
app_text = re.sub(r'APP_VERSION = "v[^"]+"', 'APP_VERSION = "v0.5.3"', app_text)

# 2. SESSION_FILE
if "SESSION_FILE = LOG_DIR / \"production_session.json\"" not in app_text:
    app_text = app_text.replace(
        'STATUS_FILE = LOG_DIR / "device_status.json"',
        'STATUS_FILE = LOG_DIR / "device_status.json"\nSESSION_FILE = LOG_DIR / "production_session.json"'
    )

# 3. Funciones para cargar sesión y filtrar conteo
app_session_functions = r'''
@st.cache_data(ttl=1)
def load_session_state():
    if not SESSION_FILE.exists():
        return {"models": {}}

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if "models" not in payload:
            payload["models"] = {}

        return payload

    except Exception:
        return {"models": {}}


def get_active_session_info(session_payload, selected_model):
    if not session_payload:
        return {}

    return session_payload.get("models", {}).get(selected_model, {}) or {}


def apply_count_scope(df, selected_model, count_scope, session_payload):
    """
    Controla si el dashboard muestra:
    - Sesión actual: datos desde que inició el conteo actual del modelo.
    - Todo el día: todos los datos del CSV diario.

    Esto permite iniciar nuevo conteo sin borrar archivos anteriores.
    """
    if df.empty:
        return df

    if count_scope != "Sesión actual":
        return df

    session_info = get_active_session_info(session_payload, selected_model)
    session_start = session_info.get("production_session_started_at", "")

    if not session_start:
        return df

    filtered = df.copy()

    time_col = "plot_timestamp" if "plot_timestamp" in filtered.columns else "pc_timestamp"

    if time_col not in filtered.columns:
        return filtered

    filtered[time_col] = pd.to_datetime(filtered[time_col], errors="coerce")
    start_dt = pd.to_datetime(session_start, errors="coerce")

    if pd.isna(start_dt):
        return filtered

    filtered = filtered[filtered[time_col] >= start_dt]

    return filtered
'''

if "def apply_count_scope" not in app_text:
    app_text = app_text.replace(
        "\n\ndef status_to_dataframe",
        f"\n\n{app_session_functions}\n\ndef status_to_dataframe"
    )

# 4. Sidebar: agregar selector de alcance de conteo después del modelo
model_block = '''        selected_model = st.radio(
            "Modelo de corrida",
            ["T1XX-1", "T1XX-2"],
            index=0,
        )'''

count_scope_block = '''        selected_model = st.radio(
            "Modelo de corrida",
            ["T1XX-1", "T1XX-2"],
            index=0,
        )

        count_scope = st.radio(
            "Conteo de piezas",
            ["Sesión actual", "Todo el día"],
            index=0,
        )

        st.caption(
            "La sesión actual se define al iniciar el colector."
        )'''

if "count_scope = st.radio" not in app_text:
    app_text = app_text.replace(model_block, count_scope_block)

# 5. Cargar sesión y aplicar filtro global de conteo
if "session_payload = load_session_state()" not in app_text:
    app_text = app_text.replace(
        '''    clean_df = load_clean_data()

    filtered_df = filter_data(''',
        '''    clean_df = load_clean_data()

    session_payload = load_session_state()
    active_session_info = get_active_session_info(session_payload, selected_model)

    clean_df_for_view = apply_count_scope(
        df=clean_df,
        selected_model=selected_model,
        count_scope=count_scope,
        session_payload=session_payload,
    )

    if count_scope == "Sesión actual":
        session_id = active_session_info.get("production_session_id", "")
        session_start = active_session_info.get("production_session_started_at", "")

        if session_id:
            st.caption(f"Conteo activo: {session_id} | Inicio: {session_start}")
        else:
            st.caption("No hay sesión activa registrada. Inicia el colector para crear una sesión.")

    filtered_df = filter_data('''
    )

# 6. Reemplazar clean_df por clean_df_for_view en llamadas principales
app_text = app_text.replace(
    '''    filtered_df = filter_data(
        clean_df,''',
    '''    filtered_df = filter_data(
        clean_df_for_view,'''
)

app_text = app_text.replace(
    '''    cycle_base_df = filter_cycle_base_data(
        clean_df,''',
    '''    cycle_base_df = filter_cycle_base_data(
        clean_df_for_view,'''
)

app_text = app_text.replace(
    '''    cycle_export_base_df = filter_cycle_base_data(
        clean_df,''',
    '''    cycle_export_base_df = filter_cycle_base_data(
        clean_df_for_view,'''
)

app_text = app_text.replace(
    "clean_df=clean_df,",
    "clean_df=clean_df_for_view,"
)

# 7. Acerca del sistema
app_text = app_text.replace(
    "- Auto-refresh no bloqueante",
    "- Auto-refresh no bloqueante\n        - Sesiones de conteo de producción por modelo"
)

# ============================================================
# VALIDACIONES
# ============================================================

collector_required = [
    "SESSION_FILE = LOG_DIR",
    "def resolve_production_session",
    "production_session_id",
    "--new-count",
    "--continue-count",
    "session_info = resolve_production_session",
]

app_required = [
    'APP_VERSION = "v0.5.3"',
    "SESSION_FILE = LOG_DIR",
    "def apply_count_scope",
    "count_scope = st.radio",
    "clean_df_for_view",
]

missing_collector = [x for x in collector_required if x not in collector_text]
missing_app = [x for x in app_required if x not in app_text]

if missing_collector:
    print("Advertencia en collector_multi_ip.py:")
    for item in missing_collector:
        print(f"- Falta: {item}")
else:
    print("Validación collector OK.")

if missing_app:
    print("Advertencia en app.py:")
    for item in missing_app:
        print(f"- Falta: {item}")
else:
    print("Validación app OK.")

COLLECTOR.write_text(collector_text, encoding="utf-8")
APP.write_text(app_text, encoding="utf-8")

print("Parche v0.5.3 aplicado correctamente.")