import threading
import time
import requests
import logging
from datetime import datetime
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeepAliveSystem:
    def __init__(self, base_url=None):
        self.last_signal_time = time.time()
        self.is_running = True
        
        self.is_render = os.getenv('RENDER', '').lower() == 'true'
        
        if self.is_render:
            SERVICE_NAME = os.getenv('RENDER_SERVICE_NAME', 'okx-eth-trading-bot')
            self.base_url = f"https://{SERVICE_NAME}.onrender.com"
            logger.info(f"✅ KeepAliveSystem (RENDER): usando {self.base_url}")
        else:
            if base_url:
                self.base_url = base_url
            else:
                port = os.getenv('PORT', '10000')
                self.base_url = f"http://localhost:{port}"
            logger.info(f"✅ KeepAliveSystem (LOCAL): usando {self.base_url}")
        
        self.health_url = f"{self.base_url}/health"
        
        self.uptimerobot_url = os.getenv('UPTIMEROBOT_URL', '')
        self.cycle_count = 0
    
    def send_health_ping(self):
        try:
            response = requests.get(self.health_url, timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ Health ping enviado - {datetime.now().strftime('%H:%M:%S')}")
                self.last_signal_time = time.time()
                return True
        except Exception as e:
            logger.error(f"❌ Erro no health ping: {e}")
        return False
    
    def start_keep_alive(self):
        def keep_alive_loop():
            logger.info("[🚀] Sistema de keep-alive iniciado (modo simplificado)")
            
            while self.is_running:
                try:
                    if self.is_render:
                        self.send_health_ping()
                        self.cycle_count += 1
                        time.sleep(300)
                    
                    else:
                        self.send_health_ping()
                        self.cycle_count += 1
                        time.sleep(30)
                        
                except Exception as e:
                    logger.error(f"Erro no loop keep-alive: {e}")
                    time.sleep(60)
        
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("[✅] Thread de keep-alive em execução")
    
    def stop_keep_alive(self):
        self.is_running = False
        logger.info("[⏹️] Sistema de keep-alive parado")
