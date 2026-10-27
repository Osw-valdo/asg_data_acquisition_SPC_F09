import hashlib
import hmac
import json
import secrets
from pathlib import Path

USERS_FILE = Path(__file__).resolve().parent.parent / "data" / "auth" / "users.json"

ROLES = {
    "admin": "Administrador",
    "engineer": "Gerente / Ingeniero",
    "operator": "Operador",
}

PERMISSIONS = {
    "admin": {"view", "download", "manage_users"},
    "engineer": {"view", "download"},
    "operator": {"view"},
}

PBKDF2_ITERATIONS = 200_000


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()


def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_users(users):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def has_users():
    return bool(load_users())


def create_user(username, password, role, display_name=""):
    username = username.strip().lower()
    if not username:
        raise ValueError("El usuario no puede estar vacío.")
    if role not in ROLES:
        raise ValueError(f"Rol inválido: {role}")
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")

    users = load_users()
    salt = secrets.token_hex(16)
    users[username] = {
        "name": display_name.strip() or username,
        "role": role,
        "salt": salt,
        "hash": _hash_password(password, salt),
    }
    save_users(users)


def register_user(username, password, display_name=""):
    """Autoregistro público: siempre rol operador y nunca sobrescribe cuentas."""
    if username.strip().lower() in load_users():
        raise ValueError("Ese usuario ya existe.")
    create_user(username, password, "operator", display_name)


def delete_user(username):
    users = load_users()
    users.pop(username.strip().lower(), None)
    save_users(users)


def authenticate(username, password):
    username = username.strip().lower()
    user = load_users().get(username)
    if not user:
        # Mantiene el tiempo de respuesta similar si el usuario no existe.
        _hash_password(password, "00" * 16)
        return None

    candidate = _hash_password(password, user["salt"])
    if not hmac.compare_digest(candidate, user["hash"]):
        return None

    return {"username": username, "name": user["name"], "role": user["role"]}


def can(user, permission):
    return bool(user) and permission in PERMISSIONS.get(user["role"], set())
