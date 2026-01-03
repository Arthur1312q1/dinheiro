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
        self.leverage = 1
        self.balance_percentage = 0.95
        
        if not all([self.api_key, self.secret_key, self.passphrase]):
            raise ValueError("❌ Credenciais OKX não configuradas")
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            hashlib.sha256
        )
        return mac.hexdigest()
    
    def _headers(self, method: str, request_path: str, body: str = "") -> Dict:
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
            return response.json()
        except Exception as e:
            logger.error(f"Erro na requisição: {e}")
            return {"code": "-1", "msg": str(e)}
    
    def get_balance(self) -> float:
        try:
            data = self._request("GET", "/api/v5/account/balance?ccy=USDT")
            if data.get("code") == "0":
                details = data.get("data", [{}])[0].get("details", [{}])[0]
                balance = float(details.get("availBal", 0))
                logger.info(f"💰 Saldo: ${balance:.2f}")
                return balance
            return 0
        except Exception as e:
            logger.error(f"Erro ao obter saldo: {e}")
            return 0
    
    def get_ticker_price(self, symbol: str = "ETH-USDT-SWAP") -> Optional[float]:
        try:
            response = self._request("GET", f"/api/v5/market/ticker?instId={symbol}")
            if response.get("code") == "0":
                price = float(response["data"][0]["last"])
                logger.info(f"📈 Preço {symbol}: ${price:.2f}")
                return price
            return None
        except Exception as e:
            logger.error(f"Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "1H", limit: int = 100) -> List[Dict]:
        try:
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
            logger.info(f"🔍 Buscando candles: {symbol} | Timeframe: {timeframe}")
            
            response = self._request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
                if candles_data:
                    candles = []
                    for c in reversed(candles_data):
                        candles.append({
                            "timestamp": int(c[0]),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        })
                    logger.info(f"✅ {len(candles)} candles obtidos | Último: ${candles[-1]['close']:.2f}")
                    return candles
                else:
                    logger.warning(f"⚠️  API retornou lista vazia")
                    return []
            else:
                logger.error(f"❌ Erro API: {response.get('msg')}")
                return []
        except Exception as e:
            logger.error(f"💥 Erro: {e}")
            return []
    
    def calculate_position_size(self) -> float:
        balance = self.get_balance()
        if balance <= 0:
            logger.error("❌ Saldo zero")
            return 0
        
        price = self.get_ticker_price()
        if not price:
            logger.error("❌ Não obteve preço")
            return 0
        
        risk_capital = balance * self.balance_percentage
        point_value = 0.01
        sl_points = 2000
        
        position_value = risk_capital / (sl_points * point_value)
        eth_quantity = position_value / price
        
        logger.info(f"🧮 Posição: {eth_quantity:.4f} ETH (${risk_capital:.2f} de risco)")
        return eth_quantity
    
    def place_order(self, side: str, quantity: float) -> bool:
        try:
            symbol = "ETH-USDT-SWAP"
            price = self.get_ticker_price(symbol)
            if not price:
                return False
            
            logger.info(f"🚀 Ordem {side}: {quantity:.4f} ETH")
            
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            order_response = self._request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅ Ordem {side} executada!")
                
                sl_points = 2000
                tp_points = 55
                
                if side.upper() == "BUY":
                    sl_price = price - (sl_points * 0.01)
                    tp_price = price + (tp_points * 0.01)
                    sl_side = "sell"
                    tp_side = "sell"
                else:
                    sl_price = price + (sl_points * 0.01)
                    tp_price = price - (tp_points * 0.01)
                    sl_side = "buy"
                    tp_side = "buy"
                
                sl_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": sl_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(sl_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                tp_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": tp_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(tp_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                self._request("POST", "/api/v5/trade/order-algo", sl_data)
                self._request("POST", "/api/v5/trade/order-algo", tp_data)
                
                logger.info(f"✅ SL e TP configurados")
                return True
            
            logger.error(f"❌ Erro: {order_response}")
            return False
            
        except Exception as e:
            logger.error(f"💥 Erro: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        try:
            response = self._request("GET", "/api/v5/account/positions")
            if response.get("code") == "0":
                positions = response.get("data", [])
                for pos in positions:
                    if float(pos.get("pos", 0)) != 0:
                        close_data = {
                            "instId": pos["instId"],
                            "tdMode": "cross",
                            "side": "buy" if pos["posSide"] == "short" else "sell",
                            "ordType": "market",
                            "sz": pos["pos"]
                        }
                        self._request("POST", "/api/v5/trade/close-position", close_data)
                return True
            return False
        except Exception as e:
            logger.error(f"Erro: {e}")
            return False
