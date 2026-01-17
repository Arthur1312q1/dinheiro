import threading
import time
import requests
import logging
from datetime import datetime
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeepAliveSystem:
    def __init__(self, base_url="http://localhost:10000"):
        self.last_signal_time = time.time()
        self.is_running = True
        
        # URLs dos DOIS endpoints internos que criamos
        self.internal_signal_1_url = f"{base_url}/ping-internal-1"
        self.internal_signal_2_url = f"{base_url}/ping-internal-2"
        
        # Configuração do UptimeRobot (externo)
        self.uptimerobot_url = os.getenv('UPTIMEROBOT_URL', '')
        self.cycle_count = 0
    
    def send_internal_signal_1(self):
        """PRIMEIRO sinal interno - Ping endpoint #1"""
        try:
            response = requests.get(self.internal_signal_1_url, timeout=5)
            if response.status_code == 200:
                logger.info(f"[🖥️ INTERNO 1] Sinal enviado - {datetime.now().strftime('%H:%M:%S')}")
                self.last_signal_time = time.time()
                return True
        except Exception as e:
            logger.error(f"Erro no sinal interno 1: {e}")
        return False
    
    def send_internal_signal_2(self):
        """SEGUNDO sinal interno - Ping endpoint #2"""
        try:
            response = requests.get(self.internal_signal_2_url, timeout=5)
            if response.status_code == 200:
                logger.info(f"[🖥️ INTERNO 2] Sinal enviado - {datetime.now().strftime('%H:%M:%S')}")
                self.last_signal_time = time.time()
                return True
        except Exception as e:
            logger.error(f"Erro no sinal interno 2: {e}")
        return False
    
    def send_external_signal(self):
        """Sinal externo para UptimeRobot"""
        if self.uptimerobot_url:
            try:
                response = requests.get(self.uptimerobot_url, timeout=5)
                if response.status_code == 200:
                    logger.info("[🌐 EXTERNO] UptimeRobot notificado")
                    return True
            except Exception as e:
                logger.error(f"Erro no sinal externo: {e}")
        return False
    
    def start_keep_alive(self):
        """Inicia o sistema de keep-alive com 2 sinais internos a cada 25 segundos"""
        def keep_alive_loop():
            logger.info("[🚀] Sistema de keep-alive interno (2 sinais) iniciado para Render")
            
            while self.is_running:
                try:
                    # SEMPRE envia os DOIS sinais internos em cada ciclo (25 segundos)
                    # Isso garante 2+ requisições HTTP a cada 25s, mantendo o Render ativo
                    self.send_internal_signal_1()
                    time.sleep(1)  # Pequena pausa entre os sinais
                    self.send_internal_signal_2()
                    
                    self.cycle_count += 1
                    
                    # A cada 5 minutos, envia sinal externo para UptimeRobot
                    if self.cycle_count % 12 == 0:  # 12 ciclos * 25s = 300s = 5min
                        self.send_external_signal()
                    
                    # Aguarda 25 segundos até o próximo ciclo (total ~26s com sleep)
                    # Isso garante que o Render receba tráfego a cada ~26s < 50s
                    time.sleep(24)
                    
                except Exception as e:
                    logger.error(f"Erro no loop keep-alive: {e}")
                    time.sleep(30)  # Em caso de erro, tenta novamente em 30s
        
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("[✅] Thread de keep-alive interno em execução")
    
    def stop_keep_alive(self):
        """Para o sistema de keep-alive"""
        self.is_running = False
        logger.info("[⏹️] Sistema de keep-alive interno parado")
