"""Minimal TCP proxy with a kill switch, for T6c connection-drop tests.

A worker pointed at the proxy port talks to the real server through it.
``drop()`` closes every live tunnel and refuses new connections; ``restore()``
lets traffic flow again, exercising the worker's reconnect-with-backoff path.
"""

from __future__ import annotations

import socket
import threading


class TcpProxy:
    def __init__(self, upstream_host: str, upstream_port: int):
        self.upstream = (upstream_host, upstream_port)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._dropped = threading.Event()
        self._stopping = threading.Event()
        self._conns: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> TcpProxy:
        self._listener.listen(64)
        self._thread.start()
        return self

    def stop(self):
        self._stopping.set()
        self.drop()
        self._listener.close()
        self._thread.join(5)

    def drop(self):
        """Sever all tunnels and refuse new connections until restore()."""
        self._dropped.set()
        with self._lock:
            conns, self._conns = self._conns, set()
        for c in conns:
            try:
                c.close()
            except OSError:
                pass

    def restore(self):
        self._dropped.clear()

    # -- internals -----------------------------------------------------------

    def _accept_loop(self):
        while not self._stopping.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            if self._dropped.is_set():
                client.close()
                continue
            try:
                server = socket.create_connection(self.upstream, timeout=10)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._conns.update((client, server))
            threading.Thread(
                target=self._pump,
                args=(client, server),
                daemon=True,
            ).start()
            threading.Thread(
                target=self._pump,
                args=(server, client),
                daemon=True,
            ).start()

    def _pump(self, src: socket.socket, dst: socket.socket):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass
