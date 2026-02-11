# keepalive/pinger.py
import threading
import time
import logging
import requests
from typing import List, Optional

logger = logging.getLogger(__name__)

class KeepAlivePinger:
    """
    Sistema de pings internos para evitar que o Render coloque o serviço em idle.
    Dispara requisições GET para endpoints locais em múltiplos intervalos simultâneos.
    """

    def __init__(self, base_url: str = "http://localhost:5000", endpoints: Optional[List[str]] = None):
        """
        Args:
            base_url: URL base do próprio serviço (ex: https://meuapp.onrender.com)
            endpoints: lista de caminhos para pingar (ex: ['/ping', '/health'])
        """
        self.base_url = base_url.rstrip('/')
        self.endpoints = endpoints or ['/ping', '/health', '/']
        self.threads = []

    def _ping_worker(self, interval: int):
        """
        Thread que faz requisições GET a cada `interval` segundos.
        """
        while True:
            for endpoint in self.endpoints:
                url = f"{self.base_url}{endpoint}"
                try:
                    response = requests.get(url, timeout=10)
                    logger.debug(f"Keepalive ping {url} | Status: {response.status_code} | Interval: {interval}s")
                except Exception as e:
                    logger.warning(f"Keepalive ping falhou para {url}: {e}")
            time.sleep(interval)

    def start(self, intervals: List[int] = None):
        """
        Inicia uma thread para cada intervalo especificado.
        Args:
            intervals: lista de segundos entre os ciclos de ping (ex: [13, 23, 30])
        """
        if intervals is None:
            intervals = [13, 23, 30]

        for interval in intervals:
            t = threading.Thread(target=self._ping_worker, args=(interval,), daemon=True)
            t.start()
            self.threads.append(t)
            logger.info(f"Keepalive thread iniciada com intervalo de {interval}s")
