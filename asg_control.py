"""Panel de control: inicia, detiene y monitorea API, dashboard y colectores."""
import json
import socket
import subprocess
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "logs" / "launcher"
DASHBOARD_PORT = 8501
API_PORT = 8600
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class Service:
    def __init__(self, key, label, port=None):
        self.key = key
        self.label = label
        self.port = port
        self.proc = None
        self.log_path = LOG_DIR / f"{key}.log"

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def external(self):
        return not self.running() and self.port is not None and port_open(self.port)

    def start(self, args, extra_env=None):
        if self.running():
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {' '.join(args)}\n")
        log.flush()
        self.proc = subprocess.Popen(
            args,
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
            env={
                **__import__("os").environ,
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                **(extra_env or {}),
            },
        )

    def stop(self):
        if self.running():
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=NO_WINDOW,
            )
        self.proc = None


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SPC ASG + PLC F09 - Panel de control")
        self.geometry("820x600")
        self.minsize(720, 520)

        py = str(PYTHON if PYTHON.exists() else sys.executable)
        self.py = py
        self.services = {
            "api": Service("api", "API de datos (:8600)", API_PORT),
            "dashboard": Service("dashboard", "Dashboard (:8501)", DASHBOARD_PORT),
            "asg": Service("asg", "Colector ASG"),
            "ftp": Service("ftp", "Colector PLC FTP"),
        }
        self.model = tk.StringVar(value="T1XX-1")
        self.count_mode = tk.StringVar(value="continue")
        self.status_labels = {}
        self.dots = {}
        self.log_service = tk.StringVar(value="dashboard")
        self.log_pos = (None, 0)
        self.ftp_creds = None

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()

    def build_ui(self):
        pad = {"padx": 10, "pady": 4}

        opts = ttk.LabelFrame(self, text="Opciones de colectores")
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Modelo:").grid(row=0, column=0, padx=6, pady=6)
        ttk.Combobox(
            opts, textvariable=self.model, values=["T1XX-1", "T1XX-2"],
            state="readonly", width=10,
        ).grid(row=0, column=1, padx=6)
        ttk.Label(opts, text="Conteo:").grid(row=0, column=2, padx=(20, 6))
        ttk.Combobox(
            opts, textvariable=self.count_mode, values=["continue", "new"],
            state="readonly", width=10,
        ).grid(row=0, column=3, padx=6)
        ttk.Label(opts, text="(continue = seguir, new = nueva sesión)").grid(row=0, column=4, padx=6)

        box = ttk.LabelFrame(self, text="Servicios")
        box.pack(fill="x", **pad)
        for row, (key, svc) in enumerate(self.services.items()):
            dot = tk.Label(box, text="●", font=("Segoe UI", 16), fg="#999")
            dot.grid(row=row, column=0, padx=(10, 4), pady=4)
            ttk.Label(box, text=svc.label, width=24).grid(row=row, column=1, sticky="w")
            status = ttk.Label(box, text="Detenido", width=22)
            status.grid(row=row, column=2, sticky="w")
            ttk.Button(box, text="Iniciar", command=lambda k=key: self.start(k)).grid(row=row, column=3, padx=3)
            ttk.Button(box, text="Detener", command=lambda k=key: self.stop(k)).grid(row=row, column=4, padx=3)
            ttk.Radiobutton(
                box, text="Ver log", value=key, variable=self.log_service,
            ).grid(row=row, column=5, padx=8)
            self.dots[key] = dot
            self.status_labels[key] = status

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        ttk.Button(bar, text="▶ Iniciar todo", command=self.start_all).pack(side="left", padx=3)
        ttk.Button(bar, text="■ Detener todo", command=self.stop_all).pack(side="left", padx=3)
        ttk.Button(bar, text="Abrir dashboard", command=lambda: webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")).pack(side="left", padx=18)
        ttk.Button(bar, text="Documentación API", command=lambda: webbrowser.open(f"http://localhost:{API_PORT}/docs")).pack(side="left", padx=3)
        ttk.Button(bar, text="Carpeta", command=lambda: subprocess.Popen(["explorer", str(ROOT)])).pack(side="left", padx=3)

        logbox = ttk.LabelFrame(self, text="Log")
        logbox.pack(fill="both", expand=True, **pad)
        self.text = tk.Text(logbox, height=12, bg="#111", fg="#ddd", font=("Consolas", 9), state="disabled", wrap="none")
        scroll = ttk.Scrollbar(logbox, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)

    def command(self, key):
        model = self.model.get()
        if key == "api":
            return [self.py, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", str(API_PORT)]
        if key == "dashboard":
            return [self.py, "-m", "streamlit", "run", "app.py", "--server.port", str(DASHBOARD_PORT), "--server.headless", "true"]
        if key == "asg":
            flag = "--new-count" if self.count_mode.get() == "new" else "--continue-count"
            return [self.py, "collector_multi_ip.py", "--model", model, flag]
        return [self.py, "collector_ftp_plc.py", "--model", model, "--interval", "10"]

    def save_selection(self):
        path = ROOT / "logs" / "launcher_selection.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "selected_model": self.model.get(),
                    "count_mode": self.count_mode.get(),
                    "source": "asg_control",
                },
                indent=4,
            ),
            encoding="utf-8",
        )

    def ask_ftp_credentials(self):
        import os
        if self.ftp_creds:
            return self.ftp_creds
        user = os.getenv("SPC_PLC_FTP_USER", "").strip()
        password = os.getenv("SPC_PLC_FTP_PASSWORD", "")
        if user and password:
            self.ftp_creds = (user, password)
            return self.ftp_creds

        dlg = tk.Toplevel(self)
        dlg.title("Credenciales FTP de los PLC")
        dlg.transient(self)
        dlg.resizable(False, False)
        result = {}
        user_var = tk.StringVar(value=user)
        pass_var = tk.StringVar()
        ttk.Label(dlg, text="Usuario:").grid(row=0, column=0, padx=10, pady=(12, 4), sticky="e")
        user_entry = ttk.Entry(dlg, textvariable=user_var, width=28)
        user_entry.grid(row=0, column=1, padx=10, pady=(12, 4))
        ttk.Label(dlg, text="Contraseña:").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        pass_entry = ttk.Entry(dlg, textvariable=pass_var, show="•", width=28)
        pass_entry.grid(row=1, column=1, padx=10, pady=4)
        ttk.Label(dlg, text="Solo se guardan en memoria mientras el panel esté abierto.").grid(
            row=2, column=0, columnspan=2, padx=10, pady=4
        )

        def accept(_event=None):
            if user_var.get().strip() and pass_var.get():
                result["creds"] = (user_var.get().strip(), pass_var.get())
                dlg.destroy()

        buttons = ttk.Frame(dlg)
        buttons.grid(row=3, column=0, columnspan=2, pady=(4, 12))
        ttk.Button(buttons, text="Aceptar", command=accept).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancelar", command=dlg.destroy).pack(side="left", padx=4)
        dlg.bind("<Return>", accept)
        (pass_entry if user_var.get() else user_entry).focus_set()
        dlg.grab_set()
        self.wait_window(dlg)

        self.ftp_creds = result.get("creds")
        return self.ftp_creds

    def start(self, key):
        svc = self.services[key]
        if svc.external():
            return
        extra_env = None
        if key == "ftp":
            creds = self.ask_ftp_credentials()
            if not creds:
                return
            extra_env = {"SPC_PLC_FTP_USER": creds[0], "SPC_PLC_FTP_PASSWORD": creds[1]}
        self.save_selection()
        svc.start(self.command(key), extra_env)

    def stop(self, key):
        self.services[key].stop()

    def start_all(self):
        for key in self.services:
            self.start(key)

    def stop_all(self):
        for key in self.services:
            self.stop(key)

    def refresh(self):
        for key, svc in self.services.items():
            if svc.running():
                text, color = "Corriendo", "#2ea043"
                if svc.port and not port_open(svc.port):
                    text, color = "Iniciando...", "#d29922"
            elif svc.external():
                text, color = "Corriendo (externo)", "#2ea043"
            elif svc.proc is None and not svc.log_path.exists():
                text, color = "Detenido", "#999"
            else:
                code = svc.proc.poll() if svc.proc else None
                text, color = ("Detenido", "#999") if code is None else (f"Terminó (código {code})", "#e5484d")
            self.dots[key].configure(fg=color)
            self.status_labels[key].configure(text=text)
        self.update_log()
        self.after(1000, self.refresh)

    def update_log(self):
        key = self.log_service.get()
        path = self.services[key].log_path
        last_key, pos = self.log_pos
        if not path.exists():
            return
        size = path.stat().st_size
        if last_key != key or size < pos:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
            pos = max(size - 20000, 0)
        if size == pos and last_key == key:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(pos)
            chunk = fh.read()
            pos = fh.tell()
        self.text.configure(state="normal")
        self.text.insert("end", chunk)
        self.text.see("end")
        self.text.configure(state="disabled")
        self.log_pos = (key, pos)

    def on_close(self):
        if any(s.running() for s in self.services.values()):
            from tkinter import messagebox
            answer = messagebox.askyesnocancel(
                "Salir",
                "Hay servicios corriendo.\n\nSí = detenerlos y salir\nNo = salir y dejarlos corriendo",
            )
            if answer is None:
                return
            if answer:
                self.stop_all()
        self.destroy()


if __name__ == "__main__":
    ControlPanel().mainloop()
