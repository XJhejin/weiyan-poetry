"""A bounded rolling window of application probes, not ICMP packet statistics."""
from collections import deque
import http.client
import json
import threading
import time
from urllib.parse import urlsplit


class NetworkStatus:
    def __init__(self):
        self.lock = threading.Lock()
        self.target = None
        self.samples = deque(maxlen=30)
        self.last_started = 0
        self.busy = False

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        latest = self.samples[-1] if self.samples else None
        return {'latency_ms': latest[0] if latest else None,
                'loss_rate': round(100 * sum(s[0] is None for s in self.samples) / len(self.samples), 1) if self.samples else None,
                'samples': len(self.samples), 'window': 30, 'updated_at': latest[1] if latest else None,
                'measurement': 'application-probe', 'checking': self.busy}

    def record(self, elapsed_ms, target=None):
        with self.lock:
            if target != self.target:
                self.target = target
                self.samples.clear()
            self.samples.append((round(elapsed_ms, 1) if elapsed_ms is not None else None, time.time()))
            return self._snapshot()

    def probe(self, target):
        with self.lock:
            if target != self.target:
                self.target = target
                self.samples.clear()
                self.last_started = 0
            if self.busy or time.monotonic() - self.last_started < 10:
                return self._snapshot()
            self.busy = True
            self.last_started = time.monotonic()
        elapsed = None
        parsed = urlsplit(target)
        connection = (http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection)(parsed.hostname, parsed.port, timeout=4)
        started = time.perf_counter()
        try:
            connection.request('GET', '/api/health', headers={'X-Weiyan-Client': 'web'})
            response = connection.getresponse()
            raw = response.read(8193)
            if response.status == 200 and len(raw) <= 8192 and json.loads(raw).get('ok') is True:
                elapsed = (time.perf_counter() - started) * 1000
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        with self.lock:
            self.busy = False
            # A response from a previous address must not contaminate the new one.
            if self.target == target:
                self.samples.append((round(elapsed, 1) if elapsed is not None else None, time.time()))
            return self._snapshot()
