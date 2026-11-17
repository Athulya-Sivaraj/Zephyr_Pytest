"""Host-side TCP/UDP peers run by the suite itself.

One TCP listener per socket id (base_port + id) so multi-socket tests can tell the
sockets apart, and so each test can ask the peer to echo, stay silent, push data,
close (FIN) or reset (RST) - no manual operator action needed.

On Windows, allow python.exe through the firewall for the chosen ports.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

MODES = ('echo', 'silent', 'close_on_accept', 'rst_on_accept', 'slow_reader')


class TcpPeer:
    """Single-port TCP peer. Keeps the most recent connection as `active`."""

    def __init__(self, bind: str, port: int):
        self.bind, self.port = bind, port
        self.mode = 'echo'
        self.received = bytearray()
        self.accepted = 0
        self.active: socket.socket | None = None
        self._lock = threading.Condition()
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> 'TcpPeer':
        self._stop.clear()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.bind, self.port))
        s.listen(4)
        s.settimeout(0.5)
        self._listener = s
        self._thread = threading.Thread(target=self._accept_loop, name=f'tcp-peer-{self.port}',
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop listening (a later connect gets RST/refused) and drop the client."""
        self._stop.set()
        self.close_active()
        if self._listener:
            self._listener.close()
            self._listener = None
        if self._thread:
            self._thread.join(timeout=2)

    def reset_stats(self) -> None:
        with self._lock:
            self.received.clear()
            self.accepted = 0
            self.mode = 'echo'

    # ------------------------------------------------------------------ control
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode

    def push(self, data: bytes, timeout: float = 10) -> None:
        conn = self.wait_connected(timeout)
        conn.sendall(data)

    def close_active(self, rst: bool = False) -> None:
        with self._lock:
            conn, self.active = self.active, None
        if conn:
            try:
                if rst:
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                else:
                    conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def wait_connected(self, timeout: float = 10) -> socket.socket:
        with self._lock:
            if not self._lock.wait_for(lambda: self.active is not None, timeout):
                raise TimeoutError(f'No TCP client on port {self.port} within {timeout}s')
            return self.active

    def wait_received(self, data: bytes, timeout: float = 10) -> bool:
        with self._lock:
            return self._lock.wait_for(lambda: data in self.received, timeout)

    # ------------------------------------------------------------------ internals
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._listener.accept()
            except (socket.timeout, OSError):
                continue
            logger.info('TCP peer %d: connection from %s (mode=%s)', self.port, addr, self.mode)
            if self.mode == 'rst_on_accept':
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                conn.close()
                continue
            if self.mode == 'close_on_accept':
                conn.close()
                continue
            old = None
            with self._lock:
                old, self.active = self.active, conn
                self.accepted += 1
                self._lock.notify_all()
            if old:
                old.close()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        while not self._stop.is_set():
            if self.mode == 'slow_reader':
                time.sleep(0.2)
                continue
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self.received += data
                self._lock.notify_all()
            if self.mode == 'echo':
                try:
                    conn.sendall(data)
                except OSError:
                    break
        with self._lock:
            if self.active is conn:
                self.active = None
        try:
            conn.close()
        except OSError:
            pass


class UdpPeer:
    def __init__(self, bind: str, port: int):
        self.bind, self.port = bind, port
        self.echo = True
        self.datagrams: list[bytes] = []
        self.last_addr = None
        self._lock = threading.Condition()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> 'UdpPeer':
        self._stop.clear()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.bind, self.port))
        s.settimeout(0.5)
        self._sock = s
        self._thread = threading.Thread(target=self._loop, name=f'udp-peer-{self.port}', daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None
        if self._thread:
            self._thread.join(timeout=2)

    def reset_stats(self) -> None:
        with self._lock:
            self.datagrams.clear()
            self.echo = True

    def send(self, data: bytes) -> None:
        if not self.last_addr:
            raise RuntimeError('No UDP client address known yet')
        self._sock.sendto(data, self.last_addr)

    def wait_received(self, data: bytes, timeout: float = 10) -> bool:
        with self._lock:
            return self._lock.wait_for(lambda: any(data in d for d in self.datagrams), timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            with self._lock:
                self.datagrams.append(data)
                self.last_addr = addr
                self._lock.notify_all()
            if self.echo:
                try:
                    self._sock.sendto(data, addr)
                except OSError:
                    pass


class PeerPool:
    """TCP peers on base..base+n-1 and UDP peers on udp_base..udp_base+n-1."""

    def __init__(self, bind: str, tcp_base: int, udp_base: int, count: int):
        self.tcp = [TcpPeer(bind, tcp_base + i) for i in range(count)]
        self.udp = [UdpPeer(bind, udp_base + i) for i in range(count)]

    def start(self) -> 'PeerPool':
        for p in (*self.tcp, *self.udp):
            p.start()
        return self

    def stop(self) -> None:
        for p in (*self.tcp, *self.udp):
            p.stop()

    def reset(self) -> None:
        for p in (*self.tcp, *self.udp):
            p.reset_stats()
        for p in self.tcp:
            if p._listener is None:      # a test stopped it to simulate "refused"
                p.start()
