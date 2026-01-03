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
            raise ValueError("❌ Credenciais OKX não configuradas. Configure OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE no Render.")
    
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
                balance = float(details.get("availBal", 0))
                logger.info(f"💰 Saldo disponível: ${balance}")
                return balance
            logger.error(f"❌ Erro ao obter saldo: {data}")
            return 0
        except Exception as e:
            logger.error(f"Erro ao obter saldo: {e}")
            return 0
    
    def set_leverage(self, symbol: str) -> bool:
        """Configura alavancagem para 1x"""
        try:
            data = {
                "instId": symbol,
                "lever": str(self.leverage),
                "mgnMode": "cross"
            }
            response = self._request("POST", "/api/v5/account/set-leverage", data)
            success = response.get("code") == "0"
            if success:
                logger.info(f"✅ Alavancagem 1x configurada para {symbol}")
            else:
                logger.error(f"❌ Falha ao configurar alavancagem: {response}")
            return success
        except Exception as e:
            logger.error(f"Erro ao configurar alavancagem: {e}")
            return False
    
    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Obtém preço atual"""
        try:
            response = self._request("GET", f"/api/v5/market/ticker?instId={symbol}")
            if response.get("code") == "0":
                price = float(response["data"][0]["last"])
                logger.info(f"📈 Preço {symbol}: ${price}")
                return price
            logger.error(f"❌ Erro ao obter preço de {symbol}: {response}")
            return None
        except Exception as e:
            logger.error(f"Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", limit: int = 100) -> List[Dict]:
        """Obtém candles de 30 minutos - OKX usa '30min' como parâmetro"""
        try:
            # Mapeamento correto dos timeframes da OKX
            timeframe_map = {
                "30m": "30min",
                "1h": "1H",
                "4h": "4H",
                "1d": "1D",
                "15m": "15min",
                "5m": "5min"
            }
            
            okx_timeframe = timeframe_map.get(timeframe, "30min")
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={okx_timeframe}&limit={limit}"
            
            logger.info(f"🔍 Buscando candles: {symbol} | Timeframe: {okx_timeframe}")
            
            response = self._request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
                if candles_data:
                    candles = []
                    # OKX retorna candles do mais recente para o mais antigo
                    # Precisamos inverter para análise temporal correta
                    for c in reversed(candles_data):
                        candles.append({
                            "timestamp": int(c[0]),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        })
                    logger.info(f"✅ {len(candles)} candles obtidos | Último: ${candles[-1]['close']}")
                    return candles
                else:
                    logger.warning(f"⚠️  API retornou lista vazia para {symbol}")
                    return []
            else:
                logger.error(f"❌ API Error: {response.get('msg')}")
                return []
                
        except Exception as e:
            logger.error(f"💥 Erro em get_candles: {e}")
            return []
    
    def calculate_position_size(self, sl_points: int = 2000) -> float:
        """Calcula tamanho da posição baseado em 95% do saldo"""
        # Primeiro encontramos um símbolo que funcione
        possible_symbols = [
            "ETH-USDT-SWAP",    # Contrato perpétuo (mais provável)
            "ETH-USDT",         # Spot
            "ETH-USD-SWAP",     # Contrato perpétuo USD
            "BTC-USDT-SWAP"     # Para teste
        ]
        
        working_symbol = None
        price = None
        
        # Testa símbolos até encontrar um que funcione
        for symbol in possible_symbols:
            price = self.get_ticker_price(symbol)
            if price:
                working_symbol = symbol
                logger.info(f"✅ Símbolo selecionado: {working_symbol}")
                break
        
        if not working_symbol or not price:
            logger.error("❌ Não foi possível conectar a nenhum símbolo ETH")
            return 0
        
        # Agora usa o símbolo que funcionou
        balance = self.get_balance()
        if balance <= 0:
            logger.error("❌ Saldo zero ou negativo")
            return 0
        
        risk_capital = balance * self.balance_percentage
        logger.info(f"💰 Capital de risco (95%): ${risk_capital:.2f}")
        
        # Para contratos perpétuos ETH, 1 ponto = $0.01
        point_value = 0.01
        position_value = risk_capital / (sl_points * point_value)
        eth_quantity = position_value / price
        
        logger.info(f"🧮 Posição calculada: {eth_quantity:.4f} ETH (${position_value:.2f} em risco)")
        return eth_quantity
    
    def place_order(self, side: str, quantity: float, sl_points: int = 2000, tp_points: int = 55) -> bool:
        """Coloca ordem com stop loss e take profit"""
        try:
            # Encontra símbolo ativo primeiro
            test_symbols = ["ETH-USDT-SWAP", "ETH-USDT", "ETH-USD-SWAP"]
            working_symbol = None
            price = None
            
            for symbol in test_symbols:
                p = self.get_ticker_price(symbol)
                if p:
                    working_symbol = symbol
                    price = p
                    break
            
            if not working_symbol or not price:
                logger.error("❌ Não encontrou símbolo ativo para trade")
                return False
            
            # Configura alavancagem (apenas para contratos)
            if "SWAP" in working_symbol:
                self.set_leverage(working_symbol)
            
            logger.info(f"🚀 Preparando ordem {side} | Símbolo: {working_symbol} | Qtd: {quantity:.4f}")
            
            # Calcula SL e TP
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
            
            # Ordem principal
            order_data = {
                "instId": working_symbol,
                "tdMode": "cross" if "SWAP" in working_symbol else "cash",
                "side": ord_side,
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info(f"📤 Enviando ordem: {order_data}")
            order_response = self._request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅ Ordem {side} executada com sucesso!")
                
                # SL e TP (apenas para contratos)
                if "SWAP" in working_symbol:
                    sl_data = {
                        "instId": working_symbol,
                        "tdMode": "cross",
                        "side": sl_side,
                        "ordType": "market",
                        "sz": str(round(quantity, 4)),
                        "triggerPx": str(round(sl_price, 2)),
                        "tpOrdPx": "-1"
                    }
                    self._request("POST", "/api/v5/trade/order-algo", sl_data)
                    
                    tp_data = {
                        "instId": working_symbol,
                        "tdMode": "cross",
                        "side": tp_side,
                        "ordType": "market",
                        "sz": str(round(quantity, 4)),
                        "triggerPx": str(round(tp_price, 2)),
                        "tpOrdPx": "-1"
                    }
                    self._request("POST", "/api/v5/trade/order-algo", tp_data)
                    
                    logger.info(f"✅ SL ({sl_price}) e TP ({tp_price}) configurados")
                
                return True
            else:
                logger.error(f"❌ Erro na ordem: {order_response}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro em place_order: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        """Fecha todas as posições abertas"""
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
                
                logger.info("✅ Todas as posições fechadas")
                return True
            return False
        except Exception as e:
            logger.error(f"Erro ao fechar posições: {e}")
            return False
