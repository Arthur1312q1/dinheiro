# keepalive/pinger.py
import threading
import time
import logging
import requests
from typing import List

logger = logging.getLogger(__name__)

class KeepAlivePinger:
    """
    Dispara pings internos para o próprio serviço em intervalos múltiplos.
    Evita que o Render coloque o serviço em idle (aplica-se apenas a planos gratuitos).
    """

    def __init__(self, base_url: str = "http://localhost:5000", endpoints: List[str] = None):
        self.base_url = base_url.rstrip('/')
        self.endpoints = endpoints or ["/ping", "/health", "/"]
        self.threads = []

    def _ping_worker(self, interval: int):
        """Thread worker que faz requisições GET a cada 'interval' segundos."""
        while True:
            for endpoint in self.endpoints:
                try:
                    url = f"{self.base_url}{endpoint}"
                    requests.get(url, timeout=5)
                    logger.debug(f"Keepalive ping {url} at {interval}s interval")
                except Exception as e:
                    logger.warning(f"Keepalive failed {url}: {e}")
            time.sleep(interval)

    def start(self, intervals: List[int] = [13, 23, 30]):
        """Inicia uma thread para cada intervalo especificado."""
        for interval in intervals:
            t = threading.Thread(target=self._ping_worker, args=(interval,), daemon=True)
            t.start()
            self.threads.append(t)
            logger.info(f"Keepalive thread started at {interval}s interval")
