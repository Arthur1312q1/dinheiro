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
        self.balance_percentage = 0.95  # Usa 95% do saldo
        
        # Validação das credenciais
        self._validate_credentials()
        logger.info("✅ Cliente OKX inicializado.")

    def _validate_credentials(self):
        """Valida se todas as credenciais estão presentes."""
        missing = []
        if not self.api_key:
            missing.append("OKX_API_KEY")
        if not self.secret_key:
            missing.append("OKX_SECRET_KEY")
        if not self.passphrase:
            missing.append("OKX_PASSPHRASE")
        
        if missing:
            error_msg = f"❌ Credenciais faltando: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _get_iso_timestamp(self) -> str:
        """Retorna timestamp no formato ISO 8601 exigido pela OKX."""
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"

    def _generate_signature(self, timestamp: str, method: str, endpoint: str, body: str = "") -> str:
        """Gera assinatura HMAC SHA256 no formato da OKX."""
        try:
            # Formato: timestamp + method + requestPath + body
            request_path = endpoint.split('?')[0] if '?' in endpoint else endpoint
            message = timestamp + method.upper() + request_path + body
            
            # Decodifica a secret key (pode estar em base64)
            if len(self.secret_key) > 100:
                try:
                    secret_bytes = base64.b64decode(self.secret_key)
                except:
                    secret_bytes = self.secret_key.encode('utf-8')
            else:
                secret_bytes = self.secret_key.encode('utf-8')
            
            # Cria assinatura
            signature = hmac.new(secret_bytes, message.encode('utf-8'), hashlib.sha256)
            return base64.b64encode(signature.digest()).decode()
            
        except Exception as e:
            logger.error(f"💥 Erro ao gerar assinatura: {e}")
            raise

    def _get_headers(self, method: str, endpoint: str, body: str = "") -> Dict:
        """Gera headers com autenticação."""
        timestamp = self._get_iso_timestamp()
        signature = self._generate_signature(timestamp, method, endpoint, body)
        
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     retry_count: int = 3) -> Dict:
        """Faz requisição para API OKX com retry."""
        url = f"{self.base_url}{endpoint}"
        body = ""
        
        if data and method in ['POST', 'PUT']:
            body = json.dumps(data, separators=(',', ':'))
        
        for attempt in range(retry_count):
            try:
                headers = self._get_headers(method, endpoint, body)
                
                if method == 'GET':
                    response = requests.get(url, headers=headers, timeout=10)
                elif method == 'POST':
                    response = requests.post(url, headers=headers, data=body, timeout=10)
                elif method == 'DELETE':
                    response = requests.delete(url, headers=headers, timeout=10)
                else:
                    return {"code": "-1", "msg": f"Método não suportado: {method}"}
                
                result = response.json()
                
                if response.status_code == 200 and result.get("code") == "0":
                    return result
                elif response.status_code == 401:
                    error_msg = result.get('msg', 'Invalid Sign')
                    logger.error(f"❌ ERRO 401: {error_msg}")
                    return result
                else:
                    error_msg = result.get('msg', f'HTTP {response.status_code}')
                    logger.error(f"❌ Erro na API: {error_msg}")
                    
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
        
        return {"code": "-1", "msg": "Todas as tentativas falharam"}

    def get_balance(self) -> float:
        """Obtém saldo disponível em USDT."""
        logger.info("💰 Obtendo saldo da conta...")
        
        response = self._make_request("GET", "/api/v5/account/balance?ccy=USDT")
        
        if response.get("code") == "0":
            try:
                data = response.get("data", [{}])
                details = data[0].get("details", [{}])
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
        """Obtém preço atual do símbolo."""
        try:
            endpoint = f"/api/v5/market/ticker?instId={symbol}"
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                data = response.get("data", [{}])
                if data:
                    last_price = data[0].get("last", "0")
                    price = float(last_price)
                    return price
            return None
        except Exception as e:
            logger.error(f"💥 Erro ao obter preço: {e}")
            return None

    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", 
                   limit: int = 100) -> List[Dict]:
        """Obtém candles históricos."""
        try:
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
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
                
                logger.info(f"✅ {len(candles)} candles obtidos para {symbol} ({timeframe})")
                return candles
            return []
        except Exception as e:
            logger.error(f"💥 Erro em get_candles: {e}")
            return []

    def get_min_order_size(self, symbol: str = "ETH-USDT-SWAP") -> float:
        """Obtém o tamanho mínimo de ordem (minSz) da API da OKX."""
        try:
            endpoint = f"/api/v5/public/instruments?instType=SWAP&instId={symbol}"
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                data = response.get("data", [{}])
                if data:
                    min_sz_str = data[0].get("minSz", "0.001")
                    min_sz = float(min_sz_str)
                    logger.info(f"✅ Tamanho mínimo de ordem ({symbol}): {min_sz} ETH")
                    return min_sz
            logger.warning("⚠️  Não foi possível obter o minSz. Usando 0.001 como fallback.")
            return 0.001
        except Exception as e:
            logger.error(f"💥 Erro ao obter min order size: {e}. Usando fallback 0.001.")
            return 0.001

    def calculate_position_size(self, sl_points: int = 2000) -> float:
        """Calcula tamanho da posição usando 95% do saldo, respeitando o mínimo da OKX."""
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
        
        # 3. Obter o mínimo dinamicamente
        min_order_eth = self.get_min_order_size()
        
        # 4. Calcular capital de risco (95%) e quantidade desejada
        risk_capital = balance * self.balance_percentage
        desired_quantity_eth = risk_capital / price
        
        # 5. Garantir que atende ao MÍNIMO da OKX
        # Se 95% do saldo for menor que o mínimo, usa o mínimo.
        final_quantity_eth = max(desired_quantity_eth, min_order_eth)
        
        # 6. Log detalhado
        logger.info(f"🧮 Cálculo de Posição:")
        logger.info(f"   Saldo: ${balance:.2f} | Capital de Risco (95%): ${risk_capital:.2f}")
        logger.info(f"   Preço ETH: ${price:.2f}")
        logger.info(f"   Qtde Desejada: {desired_quantity_eth:.6f} ETH")
        logger.info(f"   Mínimo OKX: {min_order_eth:.6f} ETH (≈${min_order_eth*price:.2f})")
        logger.info(f"   Qtde Final: {final_quantity_eth:.6f} ETH (${final_quantity_eth*price:.2f})")
        
        return final_quantity_eth

    def place_order(self, side: str, quantity: float, 
                   sl_points: int = 2000, tp_points: int = 55) -> bool:
        """Coloca ordem com stop loss e take profit."""
        try:
            symbol = "ETH-USDT-SWAP"
            
            # 1. Obter preço atual
            price = self.get_ticker_price(symbol)
            if not price or price <= 0:
                logger.error("❌ Não foi possível obter preço para ordem")
                return False
            
            logger.info(f"🚀 Preparando ordem {side.upper()}:")
            logger.info(f"   Símbolo: {symbol}")
            logger.info(f"   Quantidade: {quantity:.4f} ETH")
            logger.info(f"   Preço atual: ${price:.2f}")
            logger.info(f"   Valor total: ${quantity * price:.2f}")
            
            # 2. Configurar alavancagem 1x
            leverage_data = {
                "instId": symbol,
                "lever": "1",
                "mgnMode": "cross"
            }
            
            leverage_response = self._make_request("POST", "/api/v5/account/set-leverage", leverage_data)
            if leverage_response.get("code") != "0":
                logger.warning(f"⚠️  Alavancagem não configurada: {leverage_response.get('msg')}")
            
            # 3. Ordem principal (MARKET)
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info("📤 Enviando ordem de mercado...")
            order_response = self._make_request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA COM SUCESSO!")
                return True
            else:
                error_msg = order_response.get('msg', 'Erro desconhecido')
                logger.error(f"❌ Falha na ordem: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao executar ordem: {e}")
            return False

    def close_all_positions(self) -> bool:
        """Fecha todas as posições abertas."""
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
                        
                        close_data = {
                            "instId": symbol,
                            "tdMode": "cross",
                            "side": "sell" if float(pos_qty) > 0 else "buy",
                            "ordType": "market",
                            "sz": str(abs(pos_qty))
                        }
                        
                        logger.info(f"📤 Fechando posição: {symbol} {pos_qty}")
                        close_response = self._make_request("POST", "/api/v5/trade/close-position", close_data)
                        
                        if close_response.get("code") == "0":
                            logger.info(f"✅ Posição fechada: {symbol}")
                            closed_count += 1
                
                logger.info(f"✅ {closed_count} posições fechadas")
                return closed_count > 0
            else:
                logger.error(f"❌ Erro ao obter posições: {response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao fechar posições: {e}")
            return False
