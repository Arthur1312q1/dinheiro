import time
import requests
import threading
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PingService:
    def __init__(self, app_url):
        """
        Inicializa o serviço de ping.
        
        :param app_url: URL completa do seu bot no Render (ex: https://dinheiro.onrender.com)
        """
        self.app_url = app_url
        self.health_endpoint = f"{app_url}/health"
        self.is_running = True
        self.interval = 300  # 300 segundos = 5 minutos (menor que 15 min do Render)
        
    def send_ping(self):
        """Envia um ping (GET request) para o endpoint de saúde do bot."""
        try:
            response = requests.get(self.health_endpoint, timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ Ping enviado para {self.app_url} às {datetime.now().strftime('%H:%M:%S')}")
                return True
            else:
                logger.warning(f"⚠️  Ping recebeu status {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Falha no ping para {self.app_url}: {e}")
            return False
    
    def start_ping_loop(self):
        """Inicia o loop de ping em uma thread separada."""
        def ping_loop():
            logger.info(f"🚀 Serviço de ping iniciado para {self.app_url}")
            logger.info(f"⏰ Intervalo: {self.interval//60} minutos")
            
            while self.is_running:
                self.send_ping()
                for _ in range(self.interval):
                    if not self.is_running:
                        break
                    time.sleep(1)
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        logger.info("✅ Thread de ping iniciada em background")
    
    def stop_ping(self):
        """Para o serviço de ping."""
        self.is_running = False
        logger.info("⏹️ Serviço de ping parado")

# Ponto de entrada se executado diretamente
if __name__ == '__main__':
    # 🔽 **SUBSTITUA ESTA URL PELA URL DO SEU BOT NO RENDER** 🔽
    YOUR_RENDER_APP_URL = "https://dinheiro.onrender.com"
    
    ping_service = PingService(YOUR_RENDER_APP_URL)
    
    try:
        ping_service.start_ping_loop()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ping_service.stop_ping()
        logger.info("👋 Serviço de ping encerrado pelo usuário")
