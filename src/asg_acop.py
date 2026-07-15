import socket
import time
from typing import Iterator


DEFAULT_PORT = 4545
KEEPALIVE_SECONDS = 10


def build_mid(mid: str, revision: str = "001", data: str = "") -> bytes:
    """
    Construye un paquete ACOP/Open Protocol terminado en NULL.

    Header:
    - length: 4 caracteres
    - mid: 4 caracteres
    - revision: 3 caracteres
    - espacios/reservado hasta completar 20 caracteres
    """
    mid = str(mid).zfill(4)
    revision = str(revision).zfill(3)
    data = data or ""

    length = 20 + len(data)
    header = f"{length:04d}{mid}{revision}{' ' * 9}"

    return (header + data).encode("ascii") + b"\x00"


def send_mid(sock: socket.socket, mid: str, revision: str = "001", data: str = ""):
    """
    Envía un MID al ASG.
    """
    packet = build_mid(mid=mid, revision=revision, data=data)
    sock.sendall(packet)


def connect_asg(ip: str, port: int = DEFAULT_PORT, timeout: float = 5.0) -> socket.socket:
    """
    Crea conexión TCP con un ASG-NW2500.
    """
    sock = socket.create_connection((ip, port), timeout=timeout)
    sock.settimeout(1.0)
    return sock


def split_null_terminated_packets(buffer: bytes) -> tuple[list[bytes], bytes]:
    """
    Separa paquetes terminados en NULL.

    Regresa:
    - lista de paquetes completos
    - buffer restante incompleto
    """
    packets = []

    while b"\x00" in buffer:
        packet, buffer = buffer.split(b"\x00", 1)
        if packet:
            packets.append(packet)

    return packets, buffer


def decode_packet(packet: bytes) -> str:
    """
    Convierte bytes a texto ASCII seguro.
    """
    return packet.decode("ascii", errors="replace")


def get_mid_from_raw(raw_packet: str) -> str:
    """
    Extrae el MID de un paquete RAW.
    """
    if len(raw_packet) < 8:
        return ""
    return raw_packet[4:8]


def get_revision_from_raw(raw_packet: str) -> str:
    """
    Extrae la revisión de un paquete RAW.
    """
    if len(raw_packet) < 11:
        return ""
    return raw_packet[8:11]


def listen_packets(sock: socket.socket) -> Iterator[str]:
    """
    Generador que escucha paquetes del socket y regresa RAWs completos.
    """
    buffer = b""

    while True:
        chunk = sock.recv(4096)

        if not chunk:
            break

        buffer += chunk
        packets, buffer = split_null_terminated_packets(buffer)

        for packet in packets:
            yield decode_packet(packet)


def start_communication(sock: socket.socket):
    """
    Inicia comunicación ACOP con el ASG.
    """
    send_mid(sock, "0001", "001")


def subscribe_tightening_results(sock: socket.socket):
    """
    Se suscribe a resultados de atornillado.

    El ASG responderá con MID 0061 cada vez que haya un resultado.
    """
    send_mid(sock, "0060", "001")


def acknowledge_tightening_result(sock: socket.socket):
    """
    Confirma recepción de un MID 0061.
    """
    send_mid(sock, "0062", "001")


def send_keepalive(sock: socket.socket):
    """
    Envía keep alive al ASG.
    """
    send_mid(sock, "9999", "001")


def basic_connection_test(ip: str, port: int = DEFAULT_PORT):
    """
    Prueba básica de conexión con un ASG.

    No es el colector final.
    Solo sirve para validar:
    - TCP
    - MID 0001
    - MID 0060
    - recepción de mensajes
    """
    print("=" * 80)
    print("PRUEBA BÁSICA ACOP / ASG")
    print("=" * 80)
    print(f"IP:     {ip}")
    print(f"Puerto: {port}")
    print("=" * 80)

    with connect_asg(ip, port) as sock:
        print("Conexión TCP exitosa.")

        print("Enviando MID 0001...")
        start_communication(sock)

        time.sleep(0.5)

        print("Enviando MID 0060...")
        subscribe_tightening_results(sock)

        print()
        print("Escuchando mensajes. Presiona CTRL + C para detener.")
        print()

        last_keepalive = time.time()
        buffer = b""

        while True:
            if time.time() - last_keepalive >= KEEPALIVE_SECONDS:
                send_keepalive(sock)
                print(">> MID 9999 keep alive")
                last_keepalive = time.time()

            try:
                chunk = sock.recv(4096)

                if not chunk:
                    print("El ASG cerró la conexión.")
                    break

                buffer += chunk
                packets, buffer = split_null_terminated_packets(buffer)

                for packet in packets:
                    raw = decode_packet(packet)
                    mid = get_mid_from_raw(raw)
                    revision = get_revision_from_raw(raw)

                    print(f"<< MID {mid} Rev {revision} | RAW length: {len(raw)}")

                    if mid == "0061":
                        print("   Resultado de atornillado recibido.")
                        acknowledge_tightening_result(sock)
                        print(">> MID 0062 acknowledge")

            except socket.timeout:
                continue


if __name__ == "__main__":
    # Cambia esta IP si quieres probar con otro ASG.
    TEST_IP = "10.132.160.190"
    TEST_PORT = 4545

    try:
        basic_connection_test(TEST_IP, TEST_PORT)
    except KeyboardInterrupt:
        print("\nPrueba detenida por usuario.")
    except Exception as e:
        print(f"\nError en prueba ACOP: {e}")
