import os
import hmac
import hashlib
import time
import requests
import json
import base64
from typing import Dict, Optional, List
import logging
from datetime import datetime, timezone

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
        self.min_order_size = 0.001  # Tamanho mínimo para ETH-USDT-SWAP (3.33 USD)
        
        # Validação básica
        if not all([self.api_key, self.secret_key, self.passphrase]):
            logger.error("❌ Credenciais da OKX não configuradas.")
            raise ValueError("Credenciais OKX ausentes")
        
        logger.info("✅ Cliente OKX inicializado")
    
    def _get_timestamp(self):
        """Retorna timestamp ISO 8601 no UTC"""
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    
    def _signature(self, timestamp, method, request_path, body=''):
        """Gera assinatura HMAC SHA256"""
        try:
            message = timestamp + method.upper() + request_path + body
            
            # Decodifica a secret key (pode estar em base64)
            if len(self.secret_key) > 50:
                try:
                    secret_decoded = base64.b64decode(self.secret_key)
                except:
                    secret_decoded = self.secret_key.encode('utf-8')
            else:
                secret_decoded = self.secret_key.encode('utf-8')
            
            signature = hmac.new(
                secret_decoded, 
                message.encode('utf-8'), 
                hashlib.sha256
            )
            
            return base64.b64encode(signature.digest()).decode()
            
        except Exception as e:
            logger.error(f"Erro ao gerar assinatura: {e}")
            raise
    
    def _get_headers(self, method, request_path, body=''):
        """Retorna headers completos"""
        timestamp = self._get_timestamp()
        signature = self._signature(timestamp, method, request_path, body)
        
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, method, endpoint, data=None, retry=3):
        """Faz requisição para API OKX"""
        url = self.base_url + endpoint
        body = ''
        
        if data and method in ['POST', 'PUT']:
            body = json.dumps(data, separators=(',', ':'))
        
        for attempt in range(retry):
            try:
                headers = self._get_headers(method, endpoint, body)
                
                if method == 'GET':
                    resp = requests.get(url, headers=headers, timeout=10)
                elif method == 'POST':
                    resp = requests.post(url, headers=headers, data=body, timeout=10)
                else:
                    return {"code": "-1", "msg": f"Método {method} não suportado"}
                
                result = resp.json()
                
                if resp.status_code == 200 and result.get('code') == '0':
                    return result
                else:
                    logger.error(f"Erro API: {result.get('msg')}")
                    if attempt < retry-1:
                        time.sleep(1)
                        continue
                    return result
                    
            except Exception as e:
                logger.error(f"Erro na requisição: {e}")
                if attempt < retry-1:
                    time.sleep(1)
                    continue
                return {"code": "-1", "msg": str(e)}
    
    def get_balance(self):
        """Obtém saldo USDT"""
        logger.info("💰 Obtendo saldo da conta...")
        
        response = self._make_request("GET", "/api/v5/account/balance?ccy=USDT")
        
        if response.get('code') == '0':
            try:
                data = response.get('data', [{}])
                if data and 'details' in data[0]:
                    for detail in data[0]['details']:
                        if detail.get('ccy') == 'USDT':
                            balance = float(detail.get('availBal', 0))
                            logger.info(f"✅ Saldo disponível USDT: ${balance:.2f}")
                            return balance
                
                total_eq = data[0].get('totalEq', '0')
                if total_eq and float(total_eq) > 0:
                    balance = float(total_eq)
                    logger.info(f"✅ Saldo total equivalente: ${balance:.2f}")
                    return balance
                    
            except Exception as e:
                logger.error(f"❌ Erro ao processar saldo: {e}")
        
        logger.error("❌ Falha ao obter saldo")
        return 0.0
    
    def calculate_position_size(self):
        """Calcula tamanho de posição"""
        balance = self.get_balance()
        if balance <= 0:
            return 0.0
        
        price = self.get_ticker_price()
        if not price:
            return 0.0
        
        risk_capital = balance * self.balance_percentage
        
        # Quantidade baseada em 95% do saldo
        desired_qty = risk_capital / price
        
        # Garante o mínimo de 0.001 ETH
        final_qty = max(desired_qty, self.min_order_size)
        
        # Verifica se não excede o saldo
        if final_qty * price > balance:
            final_qty = balance / price
        
        logger.info(f"🧮 Cálculo: Saldo=${balance:.2f}, Preço=${price:.2f}, Qtd={final_qty:.6f}ETH")
        return final_qty
    
    def get_ticker_price(self, symbol="ETH-USDT-SWAP"):
        """Obtém preço atual"""
        try:
            response = self._make_request("GET", f"/api/v5/market/ticker?instId={symbol}")
            if response.get('code') == '0':
                data = response.get('data', [{}])
                if data:
                    price = float(data[0].get('last', '0'))
                    return price
        except Exception as e:
            logger.error(f"❌ Erro ao obter preço: {e}")
        return None
    
    def get_candles(self, symbol="ETH-USDT-SWAP", timeframe="30m", limit=100):
        """Obtém candles históricos"""
        try:
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
            
            response = self._make_request("GET", endpoint)
            
            if response.get('code') == '0':
                candles_data = response.get('data', [])
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
    
    def place_order(self, side, quantity):
        """Executa ordem de mercado"""
        try:
            symbol = "ETH-USDT-SWAP"
            
            # Configurar alavancagem
            leverage_data = {
                "instId": symbol,
                "lever": "1",
                "mgnMode": "cross"
            }
            
            self._make_request("POST", "/api/v5/account/set-leverage", leverage_data)
            
            # Ordem principal
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info(f"📤 Enviando ordem {side} de {quantity:.4f} ETH...")
            response = self._make_request("POST", "/api/v5/trade/order", order_data)
            
            if response.get('code') == '0':
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA!")
                return True
            else:
                logger.error(f"❌ Falha na ordem: {response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro ao executar ordem: {e}")
            return False
    
    def close_all_positions(self):
        """Fecha todas as posições abertas"""
        try:
            response = self._make_request("GET", "/api/v5/account/positions")
            if response.get('code') == '0':
                positions = response.get('data', [])
                for pos in positions:
                    pos_qty = float(pos.get('pos', '0'))
                    if pos_qty != 0:
                        symbol = pos.get('instId')
                        close_side = 'sell' if float(pos_qty) > 0 else 'buy'
                        close_data = {
                            "instId": symbol,
                            "tdMode": "cross",
                            "side": close_side,
                            "ordType": "market",
                            "sz": str(abs(pos_qty))
                        }
                        self._make_request("POST", "/api/v5/trade/close-position", close_data)
                logger.info("Todas as posições fechadas")
                return True
        except Exception as e:
            logger.error(f"❌ Erro ao fechar posições: {e}")
        
        return False
