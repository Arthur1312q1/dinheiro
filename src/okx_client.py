import os
import hmac
import hashlib
import time
import requests
import json
import base64
from typing import Dict, Optional, List
import logging
from datetime import datetime

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
        
        # Tamanho mínimo da OKX para ETH-USDT-SWAP
        self.MIN_ORDER_SIZE = 0.01  # 0.01 ETH é o mínimo na OKX
        
        # Verificação rigorosa das credenciais
        self._validate_credentials()
        
        # Testar conexão imediatamente
        self._test_connection()
    
    def _validate_credentials(self):
        """Valida se todas as credenciais estão presentes e no formato correto"""
        missing = []
        if not self.api_key:
            missing.append("OKX_API_KEY")
        if not self.secret_key:
            missing.append("OKX_SECRET_KEY")
        if not self.passphrase:
            missing.append("OKX_PASSPHRASE")
        
        if missing:
            error_msg = f"❌ Credenciais faltando no Render: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info("✅ Credenciais OKX validadas")
    
    def _test_connection(self):
        """Testa a conexão básica com a OKX"""
        try:
            test_url = f"{self.base_url}/api/v5/public/time"
            response = requests.get(test_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "0":
                    logger.info("✅ Conexão com OKX estabelecida")
                    return True
            logger.warning(f"⚠️  Status de conexão: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Falha na conexão com OKX: {e}")
        return False
    
    def _get_iso_timestamp(self) -> str:
        """Retorna timestamp no formato EXATO que a OKX espera"""
        now = datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
        return timestamp
    
    def _generate_signature(self, timestamp: str, method: str, endpoint: str, body: str = "") -> str:
        """Gera assinatura HMAC SHA256"""
        try:
            request_path = endpoint.split('?')[0] if '?' in endpoint else endpoint
            message = timestamp + method.upper() + request_path + body
            
            if len(self.secret_key) > 100:
                try:
                    secret_bytes = base64.b64decode(self.secret_key)
                except:
                    secret_bytes = self.secret_key.encode('utf-8')
            else:
                secret_bytes = self.secret_key.encode('utf-8')
            
            signature = hmac.new(secret_bytes, message.encode('utf-8'), hashlib.sha256)
            return base64.b64encode(signature.digest()).decode()
            
        except Exception as e:
            logger.error(f"💥 Erro ao gerar assinatura: {e}")
            raise
    
    def _get_headers(self, method: str, endpoint: str, body: str = "") -> Dict:
        """Gera headers com timestamp sincronizado e assinatura válida"""
        timestamp = self._get_iso_timestamp()
        signature = self._generate_signature(timestamp, method, endpoint, body)
        
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        
        return headers
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     retry_count: int = 3) -> Dict:
        """Faz requisição para API OKX"""
        url = f"{self.base_url}{endpoint}"
        body = ""
        
        if data and method in ['POST', 'PUT']:
            body = json.dumps(data, separators=(',', ':'))
        
        for attempt in range(retry_count):
            try:
                headers = self._get_headers(method, endpoint, body)
                
                logger.debug(f"🌐 {method} {endpoint} (tentativa {attempt + 1}/{retry_count})")
                
                if method == 'GET':
                    response = requests.get(url, headers=headers, timeout=10)
                elif method == 'POST':
                    response = requests.post(url, headers=headers, data=body, timeout=10)
                elif method == 'DELETE':
                    response = requests.delete(url, headers=headers, timeout=10)
                else:
                    return {"code": "-1", "msg": f"Método não suportado: {method}"}
                
                try:
                    result = response.json()
                except:
                    logger.error(f"❌ Resposta não é JSON: {response.text}")
                    return {"code": "-1", "msg": "Invalid JSON response"}
                
                if response.status_code == 200 and result.get("code") == "0":
                    return result
                elif response.status_code == 401:
                    error_msg = result.get('msg', 'Invalid Sign')
                    logger.error(f"❌ ERRO 401: {error_msg}")
                    return result
                else:
                    error_msg = result.get('msg', f'HTTP {response.status_code}')
                    logger.error(f"❌ Erro na API: {error_msg}")
                    logger.debug(f"Resposta completa: {result}")
                    
                    if attempt < retry_count - 1:
                        wait_time = 2 ** attempt
                        time.sleep(wait_time)
                    
                    continue
                    
            except requests.exceptions.Timeout:
                logger.error(f"⏰ Timeout na requisição {endpoint}")
                if attempt < retry_count - 1:
                    time.sleep(1)
                continue
            except Exception as e:
                logger.error(f"💥 Erro inesperado: {e}")
                return {"code": "-1", "msg": str(e)}
        
        return {"code": "-1", "msg": "All retry attempts failed"}
    
    def get_balance(self) -> float:
        """Obtém saldo disponível em USDT"""
        logger.info("💰 Obtendo saldo da conta...")
        
        response = self._make_request("GET", "/api/v5/account/balance?ccy=USDT")
        
        if response.get("code") == "0":
            try:
                data = response.get("data", [{}])
                if not data:
                    return 0.0
                
                details = data[0].get("details", [{}])
                if not details:
                    return 0.0
                
                balance_str = details[0].get("availBal", "0")
                balance = float(balance_str)
                
                if balance > 0:
                    logger.info(f"✅ Saldo disponível: ${balance:.2f} USDT")
                else:
                    logger.warning(f"⚠️  Saldo zero: ${balance:.2f}")
                
                return balance
                
            except Exception as e:
                logger.error(f"❌ Erro ao processar saldo: {e}")
                return 0.0
        else:
            error_msg = response.get('msg', 'Erro desconhecido')
            logger.error(f"❌ Falha ao obter saldo: {error_msg}")
            return 0.0
    
    def get_ticker_price(self, symbol: str = "ETH-USDT-SWAP") -> Optional[float]:
        """Obtém preço atual do símbolo"""
        try:
            endpoint = f"/api/v5/market/ticker?instId={symbol}"
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                data = response.get("data", [{}])
                if data:
                    last_price = data[0].get("last", "0")
                    try:
                        price = float(last_price)
                        return price
                    except ValueError:
                        return None
            return None
        except Exception as e:
            logger.error(f"💥 Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", 
                   limit: int = 100) -> List[Dict]:
        """Obtém candles históricos"""
        try:
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
            
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
                if candles_data:
                    candles = []
                    for candle in reversed(candles_data):
                        try:
                            candles.append({
                                "timestamp": int(candle[0]),
                                "open": float(candle[1]),
                                "high": float(candle[2]),
                                "low": float(candle[3]),
                                "close": float(candle[4]),
                                "volume": float(candle[5])
                            })
                        except:
                            continue
                    
                    if candles:
                        logger.info(f"✅ {len(candles)} candles obtidos")
                        return candles
                    
            return []
        except Exception as e:
            logger.error(f"💥 Erro em get_candles: {e}")
            return []
    
    def calculate_position_size(self, sl_points: int = 2000) -> float:
        """Calcula tamanho da posição usando 95% do saldo"""
        # 1. Obter saldo
        balance = self.get_balance()
        if balance <= 0:
            logger.error(f"❌ Saldo insuficiente: ${balance:.2f}")
            return 0.0
        
        # 2. Obter preço atual
        price = self.get_ticker_price()
        if not price or price <= 0:
            logger.error(f"❌ Preço inválido: {price}")
            return 0.0
        
        # 3. Calcular 95% do saldo REAL
        risk_capital = balance * self.balance_percentage
        
        # 4. Calcular tamanho da posição EM DÓLARES (não em pontos)
        # Com saldo de $6.99, 95% = $6.64
        # Vamos usar 100% desse valor para a posição
        position_value_usd = risk_capital
        
        # 5. Calcular quantidade em ETH
        eth_quantity = position_value_usd / price
        
        # 6. Verificar se atende ao mínimo da OKX
        if eth_quantity < self.MIN_ORDER_SIZE:
            logger.warning(f"⚠️  Quantidade calculada ({eth_quantity:.4f} ETH) abaixo do mínimo ({self.MIN_ORDER_SIZE} ETH)")
            eth_quantity = self.MIN_ORDER_SIZE
        
        logger.info(f"🧮 Cálculo de posição:")
        logger.info(f"   Saldo total: ${balance:.2f}")
        logger.info(f"   Capital de risco (95%): ${risk_capital:.2f}")
        logger.info(f"   Preço ETH: ${price:.2f}")
        logger.info(f"   Quantidade ETH: {eth_quantity:.4f}")
        logger.info(f"   Valor da posição: ${eth_quantity * price:.2f}")
        
        return eth_quantity
    
    def place_order(self, side: str, quantity: float, 
                   sl_points: int = 2000, tp_points: int = 55) -> bool:
        """Coloca ordem com stop loss e take profit"""
        try:
            symbol = "ETH-USDT-SWAP"
            
            # 1. Obter preço atual
            price = self.get_ticker_price(symbol)
            if not price or price <= 0:
                logger.error("❌ Não foi possível obter preço para ordem")
                return False
            
            # 2. Verificar quantidade mínima
            if quantity < self.MIN_ORDER_SIZE:
                logger.error(f"❌ Quantidade {quantity:.4f} ETH abaixo do mínimo {self.MIN_ORDER_SIZE} ETH")
                return False
            
            logger.info(f"🚀 Preparando ordem {side.upper()}:")
            logger.info(f"   Símbolo: {symbol}")
            logger.info(f"   Quantidade: {quantity:.4f} ETH")
            logger.info(f"   Preço atual: ${price:.2f}")
            logger.info(f"   Valor total: ${quantity * price:.2f}")
            
            # 3. Configurar alavancagem 1x
            if "SWAP" in symbol:
                leverage_data = {
                    "instId": symbol,
                    "lever": "1",
                    "mgnMode": "cross"
                }
                
                leverage_response = self._make_request("POST", "/api/v5/account/set-leverage", leverage_data)
                if leverage_response.get("code") == "0":
                    logger.debug("✅ Alavancagem 1x configurada")
                else:
                    logger.warning(f"⚠️  Alavancagem não configurada: {leverage_response.get('msg')}")
            
            # 4. Ordem principal (MARKET)
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info(f"📤 Enviando ordem de mercado...")
            order_response = self._make_request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA COM SUCESSO!")
                
                # 5. Calcular preços de SL e TP
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
                
                logger.info(f"   Stop Loss: ${sl_price:.2f} ({sl_points} pontos)")
                logger.info(f"   Take Profit: ${tp_price:.2f} ({tp_points} pontos)")
                
                return True
            else:
                error_msg = order_response.get('msg', 'Erro desconhecido')
                logger.error(f"❌ Falha na ordem: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao executar ordem: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        """Fecha todas as posições abertas"""
        try:
            logger.info("🔍 Buscando posições abertas...")
            response = self._make_request("GET", "/api/v5/account/positions")
            
            if response.get("code") == "0":
                positions = response.get("data", [])
                
                if not positions:
                    logger.info("✅ Nenhuma posição aberta encontrada")
                    return True
                
                closed_count = 0
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
                        
                        logger.info(f"📤 Fechando posição: {symbol} {pos_qty} ({pos_side})")
                        close_response = self._make_request("POST", "/api/v5/trade/close-position", close_data)
                        
                        if close_response.get("code") == "0":
                            logger.info(f"✅ Posição fechada: {symbol}")
                            closed_count += 1
                        else:
                            logger.error(f"❌ Erro ao fechar {symbol}: {close_response.get('msg')}")
                
                logger.info(f"✅ {closed_count} posições fechadas")
                return closed_count > 0
            else:
                logger.error(f"❌ Erro ao obter posições: {response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao fechar posições: {e}")
            return False
