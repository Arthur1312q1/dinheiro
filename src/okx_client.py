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
        
        # Verificar credenciais
        if not self.api_key or not self.secret_key or not self.passphrase:
            logger.error("❌ Credenciais OKX não configuradas. Configure:")
            logger.error("   - OKX_API_KEY")
            logger.error("   - OKX_SECRET_KEY") 
            logger.error("   - OKX_PASSPHRASE")
            raise ValueError("Credenciais OKX não configuradas")
        else:
            logger.info("✅ Credenciais OKX carregadas")
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """Gera assinatura HMAC SHA256 - CORRIGIDA"""
        try:
            # Format: timestamp + method + request_path + body
            if body is None:
                body = ""
            
            # IMPORTANTE: method deve estar em MAIÚSCULAS
            method = method.upper()
            
            message = timestamp + method + request_path + str(body)
            logger.debug(f"📝 Mensagem para assinatura: {message}")
            
            # Decodificar secret_key (deve estar em formato string)
            secret_key_bytes = self.secret_key.encode('utf-8')
            message_bytes = message.encode('utf-8')
            
            # Criar assinatura
            signature = hmac.new(
                secret_key_bytes,
                message_bytes,
                hashlib.sha256
            ).digest()
            
            # Converter para base64
            signature_base64 = signature.hex()
            logger.debug(f"🔑 Assinatura gerada: {signature_base64[:20]}...")
            
            return signature_base64
            
        except Exception as e:
            logger.error(f"💥 Erro ao gerar assinatura: {e}")
            raise
    
    def _headers(self, method: str, request_path: str, body: str = "") -> Dict:
        """Cria headers para requisição - CORRIGIDA"""
        try:
            # Timestamp no formato exato da OKX: ISO 8601 com 3 ms
            timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
            
            # Garantir que o body seja string JSON ou vazio
            if body is None:
                body = ""
            
            # Gerar assinatura
            signature = self._sign(timestamp, method, request_path, body)
            
            headers = {
                'OK-ACCESS-KEY': self.api_key,
                'OK-ACCESS-SIGN': signature,
                'OK-ACCESS-TIMESTAMP': timestamp,
                'OK-ACCESS-PASSPHRASE': self.passphrase,
                'Content-Type': 'application/json'
            }
            
            logger.debug(f"📤 Headers: {headers['OK-ACCESS-KEY'][:10]}... | Timestamp: {timestamp}")
            return headers
            
        except Exception as e:
            logger.error(f"💥 Erro ao criar headers: {e}")
            raise
    
    def _request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """Faz requisição para API OKX - COM TRATAMENTO DE ERROS"""
        try:
            url = f"{self.base_url}{endpoint}"
            body = ""
            
            if data:
                body = json.dumps(data, separators=(',', ':'))
                logger.debug(f"📦 Body: {body}")
            
            # Extrair caminho da requisição (sem o domínio)
            request_path = endpoint.split('?')[0]  # Sem query parameters
            
            headers = self._headers(method, request_path, body)
            
            logger.info(f"🌐 {method} {endpoint}")
            
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                data=body if method in ['POST', 'PUT'] else None,
                params=data if method == 'GET' and '?' in endpoint else None,
                timeout=10
            )
            
            logger.debug(f"📥 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}: {response.text}")
                return {"code": str(response.status_code), "msg": f"HTTP Error: {response.status_code}"}
            
            result = response.json()
            
            if result.get("code") != "0":
                logger.error(f"❌ API Error {result.get('code')}: {result.get('msg')}")
            else:
                logger.debug(f"✅ API Success")
            
            return result
            
        except requests.exceptions.Timeout:
            logger.error("⏰ Timeout na requisição")
            return {"code": "timeout", "msg": "Request timeout"}
        except requests.exceptions.ConnectionError:
            logger.error("🔌 Erro de conexão")
            return {"code": "connection", "msg": "Connection error"}
        except Exception as e:
            logger.error(f"💥 Erro na requisição: {e}")
            return {"code": "exception", "msg": str(e)}
    
    def get_balance(self) -> float:
        """Obtém saldo disponível em USDT - CORRIGIDA"""
        try:
            endpoint = "/api/v5/account/balance"
            
            # Query parameters para GET requests
            params = {"ccy": "USDT"}
            
            # Para GET, os params vão na URL, não no body
            endpoint_with_params = f"{endpoint}?ccy=USDT"
            
            response = self._request("GET", endpoint_with_params)
            
            if response.get("code") == "0":
                data = response.get("data", [{}])
                if data and len(data) > 0:
                    details = data[0].get("details", [{}])
                    if details and len(details) > 0:
                        balance_str = details[0].get("availBal", "0")
                        try:
                            balance = float(balance_str)
                            logger.info(f"💰 Saldo disponível: ${balance:.2f}")
                            return balance
                        except ValueError:
                            logger.error(f"❌ Formato de saldo inválido: {balance_str}")
                            return 0
            else:
                logger.error(f"❌ Erro na API: {response.get('msg')}")
                
            return 0
            
        except Exception as e:
            logger.error(f"💥 Erro ao obter saldo: {e}")
            return 0
    
    def get_ticker_price(self, symbol: str = "ETH-USDT-SWAP") -> Optional[float]:
        """Obtém preço atual"""
        try:
            endpoint = f"/api/v5/market/ticker?instId={symbol}"
            response = self._request("GET", endpoint)
            
            if response.get("code") == "0":
                data = response.get("data", [{}])
                if data and len(data) > 0:
                    price_str = data[0].get("last", "0")
                    try:
                        price = float(price_str)
                        logger.info(f"📈 Preço {symbol}: ${price:.2f}")
                        return price
                    except ValueError:
                        logger.error(f"❌ Formato de preço inválido: {price_str}")
                        return None
            return None
            
        except Exception as e:
            logger.error(f"💥 Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", limit: int = 100) -> List[Dict]:
        """Obtém candles de 30 minutos"""
        try:
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
            
            logger.info(f"🔍 Buscando {limit} candles: {symbol} | {timeframe}")
            
            response = self._request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
                if candles_data:
                    candles = []
                    # Inverter para ter do mais antigo para o mais recente
                    for c in reversed(candles_data):
                        try:
                            candles.append({
                                "timestamp": int(c[0]),
                                "open": float(c[1]),
                                "high": float(c[2]),
                                "low": float(c[3]),
                                "close": float(c[4]),
                                "volume": float(c[5])
                            })
                        except (ValueError, IndexError) as e:
                            logger.warning(f"⚠️  Ignorando candle inválido: {c}")
                    
                    if candles:
                        logger.info(f"✅ {len(candles)} candles obtidos | Último: ${candles[-1]['close']:.2f}")
                        return candles
                    else:
                        logger.warning("⚠️  Nenhum candle válido retornado")
                else:
                    logger.warning("⚠️  Dados vazios da API")
            else:
                logger.error(f"❌ Erro na API: {response.get('msg')}")
                
            return []
            
        except Exception as e:
            logger.error(f"💥 Erro ao obter candles: {e}")
            return []
    
    def calculate_position_size(self, sl_points: int = 2000) -> float:
        """Calcula tamanho da posição usando 95% do saldo"""
        try:
            # Obter saldo
            balance = self.get_balance()
            if balance <= 0:
                logger.error(f"❌ Saldo insuficiente: ${balance:.2f}")
                return 0
            
            # Obter preço atual
            price = self.get_ticker_price()
            if not price or price <= 0:
                logger.error(f"❌ Preço inválido: {price}")
                return 0
            
            # Capital de risco (95% do saldo)
            risk_capital = balance * self.balance_percentage
            
            # Para ETH, 1 ponto = $0.01
            point_value = 0.01
            
            # Cálculo do tamanho da posição
            position_value = risk_capital / (sl_points * point_value)
            eth_quantity = position_value / price
            
            logger.info(f"🧮 Cálculo posição:")
            logger.info(f"   Saldo: ${balance:.2f}")
            logger.info(f"   Capital risco: ${risk_capital:.2f} (95%)")
            logger.info(f"   Preço ETH: ${price:.2f}")
            logger.info(f"   SL pontos: {sl_points} (${sl_points * point_value:.2f})")
            logger.info(f"   Quantidade ETH: {eth_quantity:.4f}")
            
            return eth_quantity
            
        except Exception as e:
            logger.error(f"💥 Erro no cálculo da posição: {e}")
            return 0
    
    def place_order(self, side: str, quantity: float, sl_points: int = 2000, tp_points: int = 55) -> bool:
        """Coloca ordem com stop loss e take profit"""
        try:
            symbol = "ETH-USDT-SWAP"
            
            # Obter preço atual
            price = self.get_ticker_price(symbol)
            if not price or price <= 0:
                logger.error("❌ Não foi possível obter preço")
                return False
            
            logger.info(f"🚀 Preparando ordem {side.upper()}:")
            logger.info(f"   Quantidade: {quantity:.4f} ETH")
            logger.info(f"   Preço: ${price:.2f}")
            logger.info(f"   Valor: ${quantity * price:.2f}")
            
            # Configurar alavancagem 1x
            leverage_data = {
                "instId": symbol,
                "lever": "1",
                "mgnMode": "cross"
            }
            
            leverage_response = self._request("POST", "/api/v5/account/set-leverage", leverage_data)
            if leverage_response.get("code") != "0":
                logger.warning(f"⚠️  Alavancagem não configurada: {leverage_response.get('msg')}")
            
            # Ordem principal (MARKET)
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info(f"📤 Enviando ordem de mercado...")
            order_response = self._request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA!")
                
                # Calcular preços de SL e TP
                if side.upper() == "BUY":
                    sl_price = price - (sl_points * 0.01)
                    tp_price = price + (tp_points * 0.01)
                    sl_side = "sell"
                    tp_side = "sell"
                else:  # SELL
                    sl_price = price + (sl_points * 0.01)
                    tp_price = price - (tp_points * 0.01)
                    sl_side = "buy"
                    tp_side = "buy"
                
                logger.info(f"   Stop Loss: ${sl_price:.2f}")
                logger.info(f"   Take Profit: ${tp_price:.2f}")
                
                # Ordem de Stop Loss
                sl_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": sl_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(sl_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                # Ordem de Take Profit
                tp_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": tp_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(tp_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                # Enviar ordens SL e TP
                sl_response = self._request("POST", "/api/v5/trade/order-algo", sl_data)
                if sl_response.get("code") == "0":
                    logger.info("✅ Stop Loss configurado")
                else:
                    logger.warning(f"⚠️  SL não configurado: {sl_response.get('msg')}")
                
                tp_response = self._request("POST", "/api/v5/trade/order-algo", tp_data)
                if tp_response.get("code") == "0":
                    logger.info("✅ Take Profit configurado")
                else:
                    logger.warning(f"⚠️  TP não configurado: {tp_response.get('msg')}")
                
                return True
            else:
                logger.error(f"❌ Falha na ordem: {order_response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao executar ordem: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        """Fecha todas as posições abertas"""
        try:
            response = self._request("GET", "/api/v5/account/positions")
            
            if response.get("code") == "0":
                positions = response.get("data", [])
                closed = False
                
                for pos in positions:
                    pos_qty = float(pos.get("pos", "0"))
                    if pos_qty != 0:
                        symbol = pos.get("instId", "")
                        pos_side = pos.get("posSide", "")
                        
                        close_side = "sell" if pos_side == "long" else "buy"
                        
                        close_data = {
                            "instId": symbol,
                            "tdMode": "cross",
                            "side": close_side,
                            "ordType": "market",
                            "sz": str(abs(pos_qty))
                        }
                        
                        close_response = self._request("POST", "/api/v5/trade/close-position", close_data)
                        if close_response.get("code") == "0":
                            logger.info(f"✅ Posição fechada: {symbol} {pos_qty}")
                            closed = True
                        else:
                            logger.error(f"❌ Erro ao fechar {symbol}: {close_response.get('msg')}")
                
                if not closed:
                    logger.info("✅ Nenhuma posição aberta para fechar")
                
                return True
            else:
                logger.error(f"❌ Erro ao obter posições: {response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao fechar posições: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Testa a conexão com a OKX"""
        try:
            logger.info("🔗 Testando conexão com OKX...")
            
            # Testar saldo
            balance = self.get_balance()
            if balance > 0:
                logger.info(f"✅ Saldo OK: ${balance:.2f}")
            else:
                logger.warning("⚠️  Saldo zero ou erro")
            
            # Testar preço
            price = self.get_ticker_price()
            if price:
                logger.info(f"✅ Preço OK: ${price:.2f}")
            else:
                logger.error("❌ Erro ao obter preço")
                return False
            
            # Testar candles
            candles = self.get_candles(limit=5)
            if candles:
                logger.info(f"✅ Candles OK: {len(candles)} obtidos")
                return True
            else:
                logger.error("❌ Erro ao obter candles")
                return False
                
        except Exception as e:
            logger.error(f"❌ Teste de conexão falhou: {e}")
            return False
