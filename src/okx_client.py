import os
import hmac
import hashlib
import time
import requests
import json
from typing import Dict, Optional, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OKXClient:
    def __init__(self):
        self.api_key = os.getenv('OKX_API_KEY')
        self.secret_key = os.getenv('OKX_SECRET_KEY')
        self.passphrase = os.getenv('OKX_PASSPHRASE')
        self.base_url = "https://www.okx.com"
        self.symbol = "ETH-USDT-SWAP"  # ETH/USDT perpetual swap
        self.leverage = 1
        self.balance_percentage = 0.95  # 95% do saldo
        
        if not all([self.api_key, self.secret_key, self.passphrase]):
            raise ValueError("Credenciais OKX não configuradas nas variáveis de ambiente")
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """Gera assinatura para a API da OKX"""
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            hashlib.sha256
        )
        return mac.hexdigest()
    
    def _headers(self, method: str, request_path: str, body: str = "") -> Dict:
        """Cria headers para requisição"""
        timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        signature = self._sign(timestamp, method, request_path, body)
        
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
    
    def _request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """Faz requisição para API OKX"""
        url = f"{self.base_url}{endpoint}"
        body = json.dumps(data) if data else ""
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(method, endpoint, body),
                data=body,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Erro na requisição OKX: {e}")
            return {"code": "-1", "msg": str(e)}
    
    def get_balance(self) -> float:
        """Obtém saldo disponível em USDT"""
        try:
            data = self._request("GET", "/api/v5/account/balance?ccy=USDT")
            if data.get("code") == "0":
                balance_info = data.get("data", [{}])[0]
                details = balance_info.get("details", [{}])[0]
                return float(details.get("availBal", 0))
            return 0
        except Exception as e:
            logger.error(f"Erro ao obter saldo: {e}")
            return 0
    
    def set_leverage(self) -> bool:
        """Configura alavancagem para 1x"""
        try:
            data = {
                "instId": self.symbol,
                "lever": str(self.leverage),
                "mgnMode": "cross"  # Modo cruzado
            }
            response = self._request("POST", "/api/v5/account/set-leverage", data)
            return response.get("code") == "0"
        except Exception as e:
            logger.error(f"Erro ao configurar alavancagem: {e}")
            return False
    
    def get_ticker_price(self) -> Optional[float]:
        """Obtém preço atual do ETH-USDT"""
        try:
            response = self._request("GET", f"/api/v5/market/ticker?instId={self.symbol}")
            if response.get("code") == "0":
                return float(response["data"][0]["last"])
            return None
        except Exception as e:
            logger.error(f"Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, timeframe: str = "45m", limit: int = 100) -> List[Dict]:
        """Obtém candles de 45 minutos"""
        try:
            response = self._request(
                "GET", 
                f"/api/v5/market/candles?instId={self.symbol}&bar={timeframe}&limit={limit}"
            )
            if response.get("code") == "0":
                # Formato: [timestamp, open, high, low, close, volume]
                candles = []
                for c in reversed(response["data"]):  # Inverter para mais antigo primeiro
                    candles.append({
                        "timestamp": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5])
                    })
                return candles
            return []
        except Exception as e:
            logger.error(f"Erro ao obter candles: {e}")
            return []
    
    def calculate_position_size(self, sl_points: int = 2000) -> float:
        """Calcula tamanho da posição baseado em 95% do saldo"""
        balance = self.get_balance()
        if balance <= 0:
            return 0
        
        # Usar 95% do saldo
        risk_capital = balance * self.balance_percentage
        
        # Obter preço atual
        price = self.get_ticker_price()
        if not price:
            return 0
        
        # Calcular valor do ponto (para ETH, 1 ponto = $0.01)
        point_value = 0.01
        
        # Calcular tamanho da posição
        # risk_capital / (sl_points * point_value)
        position_size = risk_capital / (sl_points * point_value)
        
        # Converter para quantidade de contratos (ETH)
        # Cada contrato = 1 ETH
        eth_quantity = position_size / price
        
        logger.info(f"Saldo: ${balance:.2f}, Capital de risco: ${risk_capital:.2f}, Quantidade ETH: {eth_quantity:.4f}")
        return eth_quantity
    
    def place_order(self, side: str, quantity: float, sl_points: int = 2000, tp_points: int = 55) -> bool:
        """Coloca ordem com stop loss e take profit"""
        try:
            # Primeiro configurar alavancagem
            if not self.set_leverage():
                logger.error("Falha ao configurar alavancagem")
                return False
            
            # Obter preço atual
            price = self.get_ticker_price()
            if not price:
                return False
            
            # Calcular preços de SL e TP
            if side.upper() == "BUY":
                sl_price = price - (sl_points * 0.01)
                tp_price = price + (tp_points * 0.01)
                ord_side = "buy"
                sl_side = "sell"
                tp_side = "sell"
            else:  # SELL
                sl_price = price + (sl_points * 0.01)
                tp_price = price - (tp_points * 0.01)
                ord_side = "sell"
                sl_side = "buy"
                tp_side = "buy"
            
            # Colocar ordem principal
            order_data = {
                "instId": self.symbol,
                "tdMode": "cross",  # Modo cruzado
                "side": ord_side,
                "ordType": "market",  # Ordem a mercado
                "sz": str(round(quantity, 4))  # Quantidade de ETH
            }
            
            order_response = self._request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"Ordem {side} executada: {quantity:.4f} ETH")
                
                # Colocar ordem de stop loss
                sl_data = {
                    "instId": self.symbol,
                    "tdMode": "cross",
                    "side": sl_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(sl_price, 2)),
                    "tpOrdPx": "-1"  # Executar a mercado quando atingir trigger
                }
                
                sl_response = self._request("POST", "/api/v5/trade/order-algo", sl_data)
                
                # Colocar ordem de take profit
                tp_data = {
                    "instId": self.symbol,
                    "tdMode": "cross",
                    "side": tp_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(tp_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                tp_response = self._request("POST", "/api/v5/trade/order-algo", tp_data)
                
                if sl_response.get("code") == "0" and tp_response.get("code") == "0":
                    logger.info("SL e TP configurados com sucesso")
                    return True
                
            logger.error(f"Erro na ordem: {order_response}")
            return False
            
        except Exception as e:
            logger.error(f"Erro ao colocar ordem: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        """Fecha todas as posições abertas"""
        try:
            # Obter posições abertas
            response = self._request("GET", "/api/v5/account/positions")
            
            if response.get("code") == "0":
                positions = response.get("data", [])
                for pos in positions:
                    if float(pos.get("pos", 0)) != 0:
                        # Fechar posição
                        close_data = {
                            "instId": pos["instId"],
                            "tdMode": "cross",
                            "side": "buy" if pos["posSide"] == "short" else "sell",
                            "ordType": "market",
                            "sz": pos["pos"]
                        }
                        self._request("POST", "/api/v5/trade/close-position", close_data)
                
                logger.info("Todas as posições fechadas")
                return True
            return False
        except Exception as e:
            logger.error(f"Erro ao fechar posições: {e}")
            return False
