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
        
        # Determinar a URL base automaticamente
        if base_url:
            # Se URL fornecida, usar ela
            self.base_url = base_url
        elif self.is_render:
            # No Render, usar URL externa da variável de ambiente
            self.base_url = os.getenv('RENDER_SERVICE_URL', '')
            if not self.base_url:
                logger.warning("⚠️ RENDER_SERVICE_URL não definida. Usando URL padrão do Render.")
                # Fallback: tentar construir a URL do serviço
                service_name = os.getenv('RENDER_SERVICE_NAME', 'okx-eth-trading-bot')
                render_domain = os.getenv('RENDER_EXTERNAL_URL', 'onrender.com')
                self.base_url = f"https://{service_name}.{render_domain}"
        else:
            # Ambiente local
            port = os.getenv('PORT', '10000')
            self.base_url = f"http://localhost:{port}"
        
        logger.info(f"✅ KeepAliveSystem inicializado: is_render={self.is_render}, base_url={self.base_url}")
        
        # URLs dos endpoints internos
        self.internal_signal_1_url = f"{self.base_url}/ping-internal-1"
        self.internal_signal_2_url = f"{self.base_url}/ping-internal-2"
        self.render_ping_url = f"{self.base_url}/render-ping"
        
        # Configuração do UptimeRobot (externo)
        self.uptimerobot_url = os.getenv('UPTIMEROBOT_URL', '')
        self.cycle_count = 0
    
    def send_internal_signal_1(self):
        """PRIMEIRO sinal interno - Ping endpoint #1"""
        try:
            response = requests.get(self.internal_signal_1_url, timeout=10)
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
            response = requests.get(self.internal_signal_2_url, timeout=10)
            if response.status_code == 200:
                logger.info(f"[🖥️ INTERNO 2] Sinal enviado - {datetime.now().strftime('%H:%M:%S')}")
                self.last_signal_time = time.time()
                return True
        except Exception as e:
            logger.error(f"Erro no sinal interno 2: {e}")
        return False
    
    def send_render_ping(self):
        """Sinal para endpoint público do Render"""
        try:
            response = requests.get(self.render_ping_url, timeout=10)
            if response.status_code == 200:
                logger.info(f"[🌐 RENDER] Ping enviado - {datetime.now().strftime('%H:%M:%S')}")
                return True
        except Exception as e:
            logger.error(f"Erro no ping Render: {e}")
        return False
    
    def send_external_signal(self):
        """Sinal externo para UptimeRobot"""
        if self.uptimerobot_url:
            try:
                response = requests.get(self.uptimerobot_url, timeout=10)
                if response.status_code == 200:
                    logger.info("[🌐 EXTERNO] UptimeRobot notificado")
                    return True
            except Exception as e:
                logger.error(f"Erro no sinal externo: {e}")
        return False
    
    def start_keep_alive(self):
        """Inicia o sistema de keep-alive com múltiplos endpoints"""
        def keep_alive_loop():
            logger.info(f"[🚀] Sistema de keep-alive iniciado (Render={self.is_render})")
            
            while self.is_running:
                try:
                    # No Render, usar URL pública para todos os pings
                    if self.is_render:
                        # Envia 4 requisições em sequência (total ~4s)
                        self.send_render_ping()
                        time.sleep(1)
                        self.send_internal_signal_1()
                        time.sleep(1)
                        self.send_internal_signal_2()
                        time.sleep(1)
                        self.send_internal_signal_1()  # Segundo ciclo
                        
                        self.cycle_count += 1
                        
                        # A cada 5 minutos, envia sinal externo para UptimeRobot
                        if self.cycle_count % 12 == 0:  # 12 ciclos * ~26s = ~5min
                            self.send_external_signal()
                        
                        # Aguarda 22 segundos (total ~26s)
                        time.sleep(22)
                    
                    else:
                        # Ambiente local: comportamento original
                        self.send_internal_signal_1()
                        time.sleep(1)
                        self.send_internal_signal_2()
                        
                        self.cycle_count += 1
                        
                        if self.cycle_count % 12 == 0:
                            self.send_external_signal()
                        
                        time.sleep(24)
                        
                except Exception as e:
                    logger.error(f"Erro no loop keep-alive: {e}")
                    time.sleep(30)
        
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("[✅] Thread de keep-alive em execução")
    
    def stop_keep_alive(self):
        """Para o sistema de keep-alive"""
        self.is_running = False
        logger.info("[⏹️] Sistema de keep-alive parado")
