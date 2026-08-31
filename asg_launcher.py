from pathlib import Path
import subprocess
import sys
import webbrowser
import tkinter as tk
from tkinter import messagebox
import json
from datetime import datetime


APP_NAME = "SPC Híbrido ASG + PLC F09"
PORT = 8502


def get_project_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = get_project_root()
PYTHON_EXE = ROOT / ".venv" / "Scripts" / "python.exe"

def save_launcher_selection():
    selection_file = ROOT / "logs" / "launcher_selection.json"
    selection_file.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "selected_model": model_var.get(),
        "count_mode": count_mode_var.get(),
        "source": "asg_launcher",
    }

    selection_file.write_text(
        json.dumps(payload, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

def get_python_cmd():
    if PYTHON_EXE.exists():
        return f'"{PYTHON_EXE}"'
    return "python"


def validate_project():
    required_files = [
        ROOT / "app.py",
        ROOT / "collector_multi_ip.py",
        ROOT / "collector_ftp_plc.py",
        ROOT / "config" / "asg_devices.csv",
        ROOT / "config" / "plc_devices.csv",
    ]

    missing = [str(path) for path in required_files if not path.exists()]

    if missing:
        messagebox.showerror(
            "Proyecto incompleto",
            "No se encontraron estos archivos:\n\n" + "\n".join(missing),
        )
        return False

    return True


def make_launcher_bat(title, command):
    launcher_dir = ROOT / "logs" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (
        title.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )

    bat_path = launcher_dir / f"{safe_name}.bat"

    bat_path.write_text(
        f"""@echo off
chcp 65001 >nul
title {title}
cd /d "{ROOT}"

echo ============================================================
echo {APP_NAME}
echo {title}
echo ============================================================
echo.

{command}

echo.
echo ============================================================
echo Proceso finalizado.
echo Puedes cerrar esta ventana.
echo ============================================================
pause
""",
        encoding="utf-8",
    )

    return bat_path


def run_in_new_window(title, command):
    if not validate_project():
        return

    bat_path = make_launcher_bat(title, command)

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", str(bat_path)],
            cwd=str(ROOT),
        )
    except Exception as e:
        messagebox.showerror("Error al iniciar", str(e))


def start_dashboard():
    save_launcher_selection()

    python_cmd = get_python_cmd()

    safe_launcher = ROOT / "run_dashboard_safe.py"

    if safe_launcher.exists():
        command = f'{python_cmd} "{safe_launcher}"'
    else:
        command = f'{python_cmd} -m streamlit run app.py --server.port {PORT}'

    run_in_new_window("ASG Dashboard", command)


def start_asg_collector():
    save_launcher_selection()

    model = model_var.get()
    mode = count_mode_var.get()

    python_cmd = get_python_cmd()

    extra_flag = ""

    if mode == "new":
        extra_flag = "--new-count"
    elif mode == "continue":
        extra_flag = "--continue-count"

    command = f'{python_cmd} collector_multi_ip.py --model {model} {extra_flag}'

    run_in_new_window(f"ASG Collector {model}", command)


def start_ftp_collector():
    save_launcher_selection()

    model = model_var.get()
    python_cmd = get_python_cmd()
    command = (
        f'{python_cmd} collector_ftp_plc.py '
        f'--model {model} --interval 10'
    )

    run_in_new_window(f"PLC FTP Collector {model}", command)


def start_complete_system():
    save_launcher_selection()
    start_dashboard()
    start_asg_collector()
    start_ftp_collector()

def open_browser():
    webbrowser.open(f"http://localhost:{PORT}")


def open_project_folder():
    try:
        subprocess.Popen(["explorer", str(ROOT)])
    except Exception as e:
        messagebox.showerror("Error", str(e))


def show_info():
    messagebox.showinfo(
        "Información",
        f"{APP_NAME}\n\n"
        f"Ruta:\n{ROOT}\n\n"
        f"Dashboard local:\nhttp://localhost:{PORT}\n\n"
        "Recomendación:\n"
        "1. Selecciona modelo.\n"
        "2. Selecciona modo de conteo.\n"
        "3. Inicia el sistema completo.\n\n"
        "Se abrirán tres ventanas:\n"
        "- Dashboard Streamlit\n"
        "- Colector ASG en vivo\n"
        "- Colector PLC por FTP\n\n"
        "La ventana PLC FTP solicitará las credenciales.",
    )


root = tk.Tk()
root.title(APP_NAME)
root.geometry("520x560")
root.resizable(False, False)

root.configure(bg="#111827")

title_label = tk.Label(
    root,
    text="ASG SPC F09 Launcher",
    font=("Segoe UI", 18, "bold"),
    fg="#F9FAFB",
    bg="#111827",
)

subtitle_label = tk.Label(
    root,
    text="Dashboard + ASG en vivo + trazabilidad PLC/QR",
    font=("Segoe UI", 10),
    fg="#9CA3AF",
    bg="#111827",
)

title_label.pack(pady=(20, 4))
subtitle_label.pack(pady=(0, 18))

main_frame = tk.Frame(root, bg="#1F2937", padx=20, pady=18)
main_frame.pack(padx=20, pady=10, fill="x")

model_label = tk.Label(
    main_frame,
    text="Modelo a correr",
    font=("Segoe UI", 11, "bold"),
    fg="#F9FAFB",
    bg="#1F2937",
)
model_label.pack(anchor="w")

model_var = tk.StringVar(value="T1XX-1")

model_frame = tk.Frame(main_frame, bg="#1F2937")
model_frame.pack(anchor="w", pady=(6, 14))

tk.Radiobutton(
    model_frame,
    text="T1XX-1",
    variable=model_var,
    value="T1XX-1",
    fg="#F9FAFB",
    bg="#1F2937",
    selectcolor="#374151",
    activebackground="#1F2937",
    activeforeground="#FFFFFF",
).pack(side="left", padx=(0, 20))

tk.Radiobutton(
    model_frame,
    text="T1XX-2",
    variable=model_var,
    value="T1XX-2",
    fg="#F9FAFB",
    bg="#1F2937",
    selectcolor="#374151",
    activebackground="#1F2937",
    activeforeground="#FFFFFF",
).pack(side="left")

count_label = tk.Label(
    main_frame,
    text="Modo de conteo",
    font=("Segoe UI", 11, "bold"),
    fg="#F9FAFB",
    bg="#1F2937",
)
count_label.pack(anchor="w")

count_mode_var = tk.StringVar(value="ask")

count_frame = tk.Frame(main_frame, bg="#1F2937")
count_frame.pack(anchor="w", pady=(6, 8))

tk.Radiobutton(
    count_frame,
    text="Preguntar",
    variable=count_mode_var,
    value="ask",
    fg="#F9FAFB",
    bg="#1F2937",
    selectcolor="#374151",
    activebackground="#1F2937",
    activeforeground="#FFFFFF",
).pack(side="left", padx=(0, 14))

tk.Radiobutton(
    count_frame,
    text="Conteo nuevo",
    variable=count_mode_var,
    value="new",
    fg="#F9FAFB",
    bg="#1F2937",
    selectcolor="#374151",
    activebackground="#1F2937",
    activeforeground="#FFFFFF",
).pack(side="left", padx=(0, 14))

tk.Radiobutton(
    count_frame,
    text="Continuar conteo actual",
    variable=count_mode_var,
    value="continue",
    fg="#F9FAFB",
    bg="#1F2937",
    selectcolor="#374151",
    activebackground="#1F2937",
    activeforeground="#FFFFFF",
).pack(side="left")

button_frame = tk.Frame(root, bg="#111827")
button_frame.pack(pady=12)

button_style = {
    "font": ("Segoe UI", 10, "bold"),
    "width": 32,
    "height": 2,
    "bd": 0,
    "fg": "#F9FAFB",
    "activeforeground": "#FFFFFF",
}

tk.Button(
    button_frame,
    text="Iniciar sistema completo",
    command=start_complete_system,
    bg="#2563EB",
    activebackground="#1D4ED8",
    **button_style,
).pack(pady=5)

tk.Button(
    button_frame,
    text="Solo iniciar Dashboard",
    command=start_dashboard,
    bg="#374151",
    activebackground="#4B5563",
    **button_style,
).pack(pady=5)

tk.Button(
    button_frame,
    text="Solo iniciar Colector ASG",
    command=start_asg_collector,
    bg="#374151",
    activebackground="#4B5563",
    **button_style,
).pack(pady=5)

tk.Button(
    button_frame,
    text="Solo iniciar Colector PLC / FTP",
    command=start_ftp_collector,
    bg="#374151",
    activebackground="#4B5563",
    **button_style,
).pack(pady=5)

tk.Button(
    button_frame,
    text="Abrir navegador",
    command=open_browser,
    bg="#374151",
    activebackground="#4B5563",
    **button_style,
).pack(pady=5)

bottom_frame = tk.Frame(root, bg="#111827")
bottom_frame.pack(pady=(6, 0))

tk.Button(
    bottom_frame,
    text="Abrir carpeta",
    command=open_project_folder,
    bg="#4B5563",
    fg="#F9FAFB",
    activebackground="#6B7280",
    activeforeground="#FFFFFF",
    width=15,
    bd=0,
).pack(side="left", padx=5)

tk.Button(
    bottom_frame,
    text="Info",
    command=show_info,
    bg="#4B5563",
    fg="#F9FAFB",
    activebackground="#6B7280",
    activeforeground="#FFFFFF",
    width=15,
    bd=0,
).pack(side="left", padx=5)

tk.Button(
    bottom_frame,
    text="Salir",
    command=root.destroy,
    bg="#991B1B",
    fg="#F9FAFB",
    activebackground="#7F1D1D",
    activeforeground="#FFFFFF",
    width=15,
    bd=0,
).pack(side="left", padx=5)

footer = tk.Label(
    root,
    text="Ing. Oswaldo Cantú Casillas",
    font=("Segoe UI", 9),
    fg="#6B7280",
    bg="#111827",
)

footer.pack(side="bottom", pady=10)

root.mainloop()
