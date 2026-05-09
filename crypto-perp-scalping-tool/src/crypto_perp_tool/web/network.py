from __future__ import annotations

import socket


def normalize_bind_host(host: str | None, mobile: bool) -> str:
    if host:
        return host
    return "0.0.0.0" if mobile else "127.0.0.1"


def dashboard_urls(host: str, port: int, lan_ips: list[str] | None = None) -> dict[str, list[str] | str]:
    ips = lan_ips if lan_ips is not None else local_lan_ips()
    lan_urls = [f"http://{ip}:{port}" for ip in ips] if host in ("0.0.0.0", "::") else []
    local_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return {
        "local": f"http://{local_host}:{port}",
        "lan": lan_urls,
    }


def local_lan_ips() -> list[str]:
    candidates: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidates.add(sock.getsockname()[0])
    except OSError:
        pass

    return sorted(ip for ip in candidates if not ip.startswith("127."))
