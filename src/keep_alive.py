import threading
import time
import requests
import logging
from datetime import datetime
import os

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
        self.cycle_count = 0  # Contador de ciclos
    
    def send_internal_signal_1(self):
        """Primeiro sinal interno - Heartbeat principal"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Log mais destacado para criar atividade
            logger.info(f"[💓 HEARTBEAT] Bot ativo e rodando - {current_time}")
            self.last_signal_time = time.time()
            return True
        except Exception as e:
            logger.error(f"Erro no heartbeat: {e}")
            return False
    
    def send_internal_signal_2(self):
        """Segundo sinal interno - Status e uptime"""
        try:
            uptime = time.time() - self.last_signal_time
            self.cycle_count += 1
            logger.info(f"[📈 STATUS] Ciclo #{self.cycle_count} | Uptime contínuo: {uptime:.0f}s")
            self.last_signal_time = time.time()
            return True
        except Exception as e:
            logger.error(f"Erro no status: {e}")
            return False
    
    def send_external_signal(self):
        """Sinal externo para UptimeRobot (se configurado)"""
        if self.uptimerobot_url:
            try:
                response = requests.get(self.uptimerobot_url, timeout=10)
                if response.status_code == 200:
                    logger.info("[🌐 UPTIMEROBOT] Ping enviado com sucesso para monitoramento externo.")
                    return True
                else:
                    logger.warning(f"[🌐 UPTIMEROBOT] Resposta inesperada: {response.status_code}")
            except requests.exceptions.Timeout:
                logger.warning("[🌐 UPTIMEROBOT] Timeout ao tentar conectar.")
            except Exception as e:
                logger.error(f"[🌐 UPTIMEROBOT] Erro: {e}")
        else:
            # Log informativo se a URL não estiver configurada
            if self.cycle_count % 20 == 0:  # A cada 20 ciclos (~8 minutos)
                logger.info("[ℹ️ INFO] UPTIMEROBOT_URL não configurada. Monitoramento apenas interno.")
        return False
    
    def start_keep_alive(self):
        """Inicia o sistema de keep-alive com loop otimizado"""
        def keep_alive_loop():
            logger.info("[🚀] Sistema de keep-alive AGGRESSIVE iniciado.")
            
            while self.is_running:
                try:
                    # **NOVO:** Ciclo a cada 25 segundos (mais rápido que 5 minutos)
                    # Alterna entre os dois sinais internos a cada ciclo
                    if self.cycle_count % 2 == 0:
                        self.send_internal_signal_1()
                    else:
                        self.send_internal_signal_2()
                    
                    # A cada 12 ciclos (~5 minutos), envia sinal externo se houver
                    if self.cycle_count % 12 == 0:
                        self.send_external_signal()
                    
                    # Aguarda 25 segundos até o próximo ciclo
                    time.sleep(25)
                    
                except Exception as e:
                    logger.error(f"[💥] Erro crítico no loop keep-alive: {e}")
                    # Espera um pouco mais em caso de erro, mas continua
                    time.sleep(60)
        
        # Inicia a thread como daemon para não bloquear a saída
        thread = threading.Thread(target=keep_alive_loop, daemon=True)
        thread.start()
        logger.info("[✅] Thread de keep-alive em execução em background.")
    
    def stop_keep_alive(self):
        """Para o sistema de keep-alive de forma limpa"""
        self.is_running = False
        logger.info("[⏹️] Sistema de keep-alive parado por solicitação.")
