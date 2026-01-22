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
        
        # Detectar se está rodando no Render
        self.is_render = os.getenv('RENDER', '').lower() == 'true'
        
        # NO RENDER: Usamos apenas 1 endpoint (/health)
        if self.is_render:
            # URL fixa para o Render
            SERVICE_NAME = os.getenv('RENDER_SERVICE_NAME', 'okx-eth-trading-bot')
            self.base_url = f"https://{SERVICE_NAME}.onrender.com"
            logger.info(f"✅ KeepAliveSystem (RENDER): usando {self.base_url}")
        else:
            # Ambiente local
            if base_url:
                self.base_url = base_url
            else:
                port = os.getenv('PORT', '10000')
                self.base_url = f"http://localhost:{port}"
            logger.info(f"✅ KeepAliveSystem (LOCAL): usando {self.base_url}")
        
        # Endpoint de health check
        self.health_url = f"{self.base_url}/health"
        
        # Configuração do UptimeRobot (externo)
        self.uptimerobot_url = os.getenv('UPTIMEROBOT_URL', '')
        self.cycle_count = 0
    
    def send_health_ping(self):
        """Envia ping para endpoint /health"""
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
        """Inicia o sistema de keep-alive SIMPLIFICADO"""
        def keep_alive_loop():
            logger.info("[🚀] Sistema de keep-alive iniciado (modo simplificado)")
            
            while self.is_running:
                try:
                    # NO RENDER: Apenas 1 ping a cada 5 minutos
                    if self.is_render:
                        self.send_health_ping()
                        self.cycle_count += 1
                        
                        # Aguarda 5 minutos (300 segundos)
                        time.sleep(300)
                    
                    else:
                        # AMBIENTE LOCAL: comportamento antigo
                        self.send_health_ping()
                        self.cycle_count += 1
                        
                        # Aguarda 30 segundos
                        time.sleep(30)
                        
                except Exception as e:
                    logger.error(f"Erro no loop keep-alive: {e}")
                    time.sleep(60)  # Em caso de erro, tenta novamente em 1 minuto
        
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("[✅] Thread de keep-alive em execução")
    
    def stop_keep_alive(self):
        """Para o sistema de keep-alive"""
        self.is_running = False
        logger.info("[⏹️] Sistema de keep-alive parado")
