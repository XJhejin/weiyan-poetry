"""Bounded HTTP keep-alive reuse with read-only retry after a stale connection."""
import http.client
import queue
import ssl
import threading
import time
from urllib.parse import urlsplit


class HTTPPool:
    def __init__(self, url):
        self.url = urlsplit(url)
        self.idle = queue.LifoQueue(maxsize=6)
        # Reuse the verified TLS configuration as well as established sockets.
        self.tls_context = ssl.create_default_context() if self.url.scheme == 'https' else None

    def request(self, method, path, body=None, headers=None, timeout=20, max_response_bytes=None):
        # POST may already have committed when a connection fails. Its caller
        # retains the signed message identity and decides how to retry safely.
        for attempt in range(2 if method == 'GET' else 1):
            connection = None
            try:
                while True:
                    try:
                        connection, used = self.idle.get_nowait()
                    except queue.Empty:
                        connection = None
                        break
                    if time.monotonic() - used < 25:
                        break
                    connection.close()
                if connection is None:
                    kind = http.client.HTTPSConnection if self.url.scheme == 'https' else http.client.HTTPConnection
                    options = {'context': self.tls_context} if self.tls_context else {}
                    connection = kind(self.url.hostname, self.url.port, timeout=timeout, **options)
                connection.timeout = timeout
                if connection.sock:
                    connection.sock.settimeout(timeout)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                raw = response.read() if max_response_bytes is None else response.read(max_response_bytes + 1)
                if max_response_bytes is not None and len(raw) > max_response_bytes:
                    # An incompletely consumed response must never re-enter the pool.
                    raise ValueError('HTTP response exceeds the permitted size')
                response_headers = response.getheaders()
                status = response.status
                if not response.will_close:
                    try:
                        self.idle.put_nowait((connection, time.monotonic()))
                        connection = None
                    except queue.Full:
                        pass
                return status, response_headers, raw
            except (OSError, http.client.HTTPException):
                if method != 'GET' or attempt:
                    raise
            finally:
                if connection:
                    connection.close()


_pools, _guard = {}, threading.Lock()


def pool_for(url):
    with _guard:
        if url not in _pools:
            if len(_pools) >= 4:
                _, previous = _pools.popitem()
                while not previous.idle.empty():
                    try: previous.idle.get_nowait()[0].close()
                    except queue.Empty: break
            _pools[url] = HTTPPool(url)
        return _pools[url]
