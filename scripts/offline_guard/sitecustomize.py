"""Guard deterministic check subprocesses: allow only local loopback transport."""
import ipaddress
import socket


def _check(host):
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except (ValueError, TypeError):
        pass
    raise RuntimeError("Offline checks prohibit non-loopback network access")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def connect(sock, address):
    if isinstance(address, tuple):
        _check(address[0])
    else:
        raise RuntimeError("Offline checks require loopback IP transport")
    return _connect(sock, address)


def connect_ex(sock, address):
    _check(address[0])
    return _connect_ex(sock, address)


def getaddrinfo(host, *args, **kwargs):
    _check(host)
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
