import threading
import time
import requests
import logging
from datetime import datetime
import os  # <-- LINHA CRÍTICA ADICIONADA

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeepAliveSystem:
    def __init__(self):
        self.last_signal_time = time.time()
        self.is_running = True
        
        # Sinais internos
        self.internal_signal_1 = "HEARTBEAT_1"
        self.internal_signal_2 = "STRATEGY_ACTIVE"
        
        # Configuração do UptimeRobot
        self.uptimerobot_url = os.getenv('UPTIMEROBOT_URL', '')
    
    def send_internal_signal_1(self):
        """Primeiro sinal interno - Heartbeat"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[SINAL INTERNO 1] Heartbeat ativo - {current_time}")
            self.last_signal_time = time.time()
            return True
        except Exception as e:
            logger.error(f"Erro no sinal interno 1: {e}")
            return False
    
    def send_internal_signal_2(self):
        """Segundo sinal interno - Status da estratégia"""
        try:
            uptime = time.time() - self.last_signal_time
            logger.info(f"[SINAL INTERNO 2] Estratégia ativa - Uptime: {uptime:.0f}s")
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
                    logger.info("[SINAL EXTERNO] UptimeRobot notificado")
                    return True
            except Exception as e:
                logger.error(f"Erro no sinal externo: {e}")
        return False
    
    def start_keep_alive(self):
        """Inicia o sistema de keep-alive"""
        def keep_alive_loop():
            counter = 0
            while self.is_running:
                try:
                    # A cada 30 segundos, alterna entre sinais internos
                    if counter % 2 == 0:
                        self.send_internal_signal_1()
                    else:
                        self.send_internal_signal_2()
                    
                    # A cada 5 minutos, envia sinal externo
                    if counter % 10 == 0:
                        self.send_external_signal()
                    
                    counter += 1
                    time.sleep(30)  # Espera 30 segundos
                    
                except Exception as e:
                    logger.error(f"Erro no loop keep-alive: {e}")
                    time.sleep(60)
        
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("Sistema de keep-alive iniciado")
    
    def stop_keep_alive(self):
        """Para o sistema de keep-alive"""
        self.is_running = False
        logger.info("Sistema de keep-alive parado")
