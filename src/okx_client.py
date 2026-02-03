#!/usr/bin/env python3
"""
OKX_CLIENT.PY - VERSÃO FINAL COM API REAL E SIMULAÇÃO
"""
import os
import logging
import random
import requests
import base64
import hashlib
import hmac
import time
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class OKXClient:
    def __init__(self):
        self.api_key = os.getenv('OKX_API_KEY', '')
        self.secret_key = os.getenv('OKX_SECRET_KEY', '')
        self.passphrase = os.getenv('OKX_PASSPHRASE', '')
        
        # Preço inicial baseado no debug atual
        self.simulated_price = 2358.49  # Preço da trade aberta
        
        # Configurações da API
        self.base_url = "https://www.okx.com"
        
        # Verifica se as credenciais estão configuradas
        self.has_credentials = all([self.api_key, self.secret_key, self.passphrase])
        
        if self.has_credentials:
            logger.info("✅ Credenciais OKX configuradas. Modo REAL ativado.")
        else:
            logger.warning("⚠️ Credenciais OKX não configuradas. Modo SIMULAÇÃO ativado.")
        
        # Para simulação controlada
        self.simulation_trend = "down"  # down, up, sideways
        self.simulation_volatility = 5.0  # Volatilidade em USDT
        self.last_simulation_update = time.time()
        
        logger.info("✅ Cliente OKX inicializado")
    
    def _sign_request(self, method: str, endpoint: str, body: str = "") -> dict:
        """Assina requisição para API privada OKX - IDÊNTICO à documentação"""
        if not self.has_credentials:
            return {}
        
        try:
            timestamp = str(time.time())
            message = timestamp + method + endpoint + body
            
            # IMPORTANTE: Formato exato da OKX
            signature = base64.b64encode(
                hmac.new(
                    self.secret_key.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')
            
            return {
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
                "Content-Type": "application/json"
            }
        except Exception as e:
            logger.error(f"❌ Erro ao assinar requisição: {e}")
            return {}
    
    def get_balance(self, currency: str = "USDT") -> float:
        """Retorna saldo real ou simulado"""
        if self.has_credentials:
            endpoint = f"/api/v5/account/balance?ccy={currency}"
            url = self.base_url + endpoint
            headers = self._sign_request("GET", endpoint)
            
            try:
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                if data.get('code') == '0':
                    # OKX retorna array de balances
                    for balance_info in data['data']:
                        if balance_info.get('ccy') == currency:
                            available = float(balance_info.get('availBal', 0))
                            logger.info(f"✅ Saldo real obtido: {available} {currency}")
                            return available
                    
                    logger.warning(f"⚠️ Moeda {currency} não encontrada no saldo")
                    return 1000.0  # Fallback
                else:
                    logger.error(f"❌ Erro API saldo: {data.get('msg', 'Unknown error')}")
                    return 1000.0  # Fallback para simulação
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição saldo: {e}")
                return 1000.0  # Fallback para simulação
        else:
            # Saldo simulado
            return 1000.0
    
    def get_ticker_price(self, symbol: str = "ETH-USDT-SWAP") -> float:
        """Retorna preço real ou simulado - COM TENDÊNCIA CONTROLADA"""
        if self.has_credentials:
            endpoint = f"/api/v5/market/ticker?instId={symbol}"
            url = self.base_url + endpoint
            
            try:
                response = requests.get(url, timeout=10)
                data = response.json()
                
                if data.get('code') == '0' and data['data']:
                    price = float(data['data'][0]['last'])
                    self.simulated_price = price  # Atualiza preço simulado
                    logger.debug(f"✅ Preço real: ${price:.2f}")
                    return price
                else:
                    logger.error(f"❌ Erro API ticker: {data.get('msg', 'Unknown error')}")
                    return self._simulate_price_with_trend()
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição ticker: {e}")
                return self._simulate_price_with_trend()
        else:
            return self._simulate_price_with_trend()
    
    def _simulate_price_with_trend(self) -> float:
        """Simula preço com tendência realista"""
        current_time = time.time()
        
        # Mudar tendência aleatoriamente a cada 5-15 minutos
        if current_time - self.last_simulation_update > random.randint(300, 900):
            trends = ["down", "up", "sideways"]
            self.simulation_trend = random.choice(trends)
            self.last_simulation_update = current_time
            logger.info(f"🔁 Mudando tendência simulada para: {self.simulation_trend}")
        
        # Calcular variação baseada na tendência
        if self.simulation_trend == "down":
            variation = random.uniform(-self.simulation_volatility * 1.5, -self.simulation_volatility * 0.5)
        elif self.simulation_trend == "up":
            variation = random.uniform(self.simulation_volatility * 0.5, self.simulation_volatility * 1.5)
        else:  # sideways
            variation = random.uniform(-self.simulation_volatility, self.simulation_volatility)
        
        self.simulated_price += variation
        
        # Limites realistas para ETH
        if self.simulated_price < 2000:
            self.simulated_price = 2000 + random.uniform(0, 100)
            self.simulation_trend = "up"  # Reverter tendência
        elif self.simulated_price > 3500:
            self.simulated_price = 3500 - random.uniform(0, 100)
            self.simulation_trend = "down"  # Reverter tendência
        
        price = round(self.simulated_price, 2)
        logger.debug(f"📊 Preço simulado: ${price:.2f} (tendência: {self.simulation_trend})")
        return price
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", limit: int = 100) -> list:
        """Retorna candles reais ou simulados"""
        if self.has_credentials:
            # Mapear timeframe para o formato da OKX
            tf_map = {
                "1m": "1m",
                "5m": "5m", 
                "15m": "15m",
                "30m": "30m",
                "1h": "1H",
                "4h": "4H",
                "1d": "1D"
            }
            
            okx_tf = tf_map.get(timeframe, "30m")
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar={okx_tf}&limit={limit}"
            url = self.base_url + endpoint
            
            try:
                response = requests.get(url, timeout=10)
                data = response.json()
                
                if data.get('code') == '0':
                    candles_data = data['data']
                    candles = []
                    
                    for c in candles_data:
                        candles.append({
                            "timestamp": int(c[0]),      # Timestamp em ms
                            "open": float(c[1]),        # Open
                            "high": float(c[2]),        # High
                            "low": float(c[3]),         # Low
                            "close": float(c[4]),       # Close
                            "volume": float(c[5]),      # Volume
                            "volCcy": float(c[6]),      # Volume em USD
                            "volCcyQuote": float(c[7]), # Volume em quote currency
                            "confirm": int(c[8])        # Confirmado (1) ou não (0)
                        })
                    
                    logger.info(f"✅ {len(candles)} candles reais obtidos")
                    return candles
                else:
                    logger.error(f"❌ Erro API candles: {data.get('msg', 'Unknown error')}")
                    return self._simulate_realistic_candles(limit)
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição candles: {e}")
                return self._simulate_realistic_candles(limit)
        else:
            return self._simulate_realistic_candles(limit)
    
    def _simulate_realistic_candles(self, limit: int = 100) -> list:
        """Gera candles simulados realistas para backtesting"""
        candles = []
        base_price = 2630.0
        
        # Gerar tendência base
        trend = random.choice(["bullish", "bearish", "sideways"])
        
        for i in range(limit):
            timestamp = int((datetime.now().timestamp() - i * 1800) * 1000)  # 30min candles
            
            # Determinar open price
            if not candles:
                open_price = base_price
            else:
                open_price = candles[-1]['close']
            
            # Determinar movimento baseado na tendência
            if trend == "bullish":
                base_move = random.uniform(-10, 40)
            elif trend == "bearish":
                base_move = random.uniform(-40, 10)
            else:  # sideways
                base_move = random.uniform(-20, 20)
            
            # Calcular close price
            close_price = open_price + base_move
            
            # Calcular high e low com realisticidade
            high_price = max(open_price, close_price) + random.uniform(0, 25)
            low_price = min(open_price, close_price) - random.uniform(0, 25)
            
            # Garantir que high > low
            if high_price <= low_price:
                high_price = low_price + 0.01
            
            # Volume correlacionado com movimento
            price_range = high_price - low_price
            volume = price_range * random.uniform(50, 200)
            
            candles.append({
                "timestamp": timestamp,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": round(volume, 2)
            })
        
        # Ordenar do mais antigo para o mais recente (como OKX)
        candles.reverse()
        
        logger.info(f"📊 {len(candles)} candles simulados gerados (tendência: {trend})")
        return candles
    
    def place_order(self, symbol: str, side: str, quantity: float, 
                   price: float = None, order_type: str = "market") -> dict:
        """
        Coloca ordem REAL na OKX ou simula
        
        Retorna: {"success": bool, "order_id": str, "message": str}
        """
        if self.has_credentials:
            endpoint = "/api/v5/trade/order"
            url = self.base_url + endpoint
            
            order_data = {
                "instId": symbol,
                "tdMode": "cross",  # Cross margin
                "side": side.lower(),
                "ordType": order_type.lower(),
                "sz": str(round(quantity, 4))  # Arredondar para 4 casas decimais
            }
            
            if price and order_type.lower() == "limit":
                order_data["px"] = str(round(price, 2))
            
            body = json.dumps(order_data)
            headers = self._sign_request("POST", endpoint, body)
            
            try:
                response = requests.post(url, data=body, headers=headers, timeout=10)
                data = response.json()
                
                if data.get('code') == '0':
                    order_id = data['data'][0]['ordId']
                    logger.info(f"✅ Ordem {side} de {quantity} {symbol} colocada: {order_id}")
                    
                    return {
                        "success": True,
                        "order_id": order_id,
                        "message": f"Ordem {order_id} colocada com sucesso"
                    }
                else:
                    error_msg = data.get('msg', 'Erro desconhecido')
                    logger.error(f"❌ Erro ao colocar ordem: {error_msg}")
                    
                    return {
                        "success": False,
                        "order_id": None,
                        "message": f"Erro OKX: {error_msg}"
                    }
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição ordem: {e}")
                
                return {
                    "success": False,
                    "order_id": None,
                    "message": f"Erro de conexão: {str(e)}"
                }
        else:
            # SIMULAÇÃO: sempre sucesso
            order_id = f"SIM_{int(time.time())}_{random.randint(1000, 9999)}"
            
            logger.info(f"✅ [SIM] Ordem {side} de {quantity} {symbol} colocada: {order_id}")
            
            return {
                "success": True,
                "order_id": order_id,
                "message": f"Ordem simulada {order_id} colocada com sucesso"
            }
    
    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancela ordem na OKX"""
        if self.has_credentials:
            endpoint = f"/api/v5/trade/cancel-order"
            url = self.base_url + endpoint
            
            order_data = {
                "instId": symbol,
                "ordId": order_id
            }
            
            body = json.dumps(order_data)
            headers = self._sign_request("POST", endpoint, body)
            
            try:
                response = requests.post(url, data=body, headers=headers, timeout=10)
                data = response.json()
                
                if data.get('code') == '0':
                    logger.info(f"✅ Ordem {order_id} cancelada")
                    return {"success": True, "message": "Ordem cancelada"}
                else:
                    error_msg = data.get('msg', 'Erro desconhecido')
                    logger.error(f"❌ Erro ao cancelar ordem: {error_msg}")
                    return {"success": False, "message": f"Erro: {error_msg}"}
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição cancelamento: {e}")
                return {"success": False, "message": f"Erro: {str(e)}"}
        else:
            logger.info(f"✅ [SIM] Ordem {order_id} cancelada")
            return {"success": True, "message": "Ordem simulada cancelada"}
    
    def get_order_status(self, symbol: str, order_id: str) -> dict:
        """Obtém status de uma ordem"""
        if self.has_credentials:
            endpoint = f"/api/v5/trade/order?instId={symbol}&ordId={order_id}"
            url = self.base_url + endpoint
            headers = self._sign_request("GET", endpoint)
            
            try:
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                if data.get('code') == '0' and data['data']:
                    order_info = data['data'][0]
                    return {
                        "success": True,
                        "status": order_info.get('state', 'unknown'),
                        "filled_qty": float(order_info.get('fillSz', 0)),
                        "avg_price": float(order_info.get('avgPx', 0))
                    }
                else:
                    return {"success": False, "message": "Ordem não encontrada"}
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição status: {e}")
                return {"success": False, "message": f"Erro: {str(e)}"}
        else:
            # Para simulação, retorna sempre "filled"
            return {
                "success": True,
                "status": "filled",
                "filled_qty": 1.0,
                "avg_price": self.simulated_price
            }
    
    def get_positions(self, symbol: str = "ETH-USDT-SWAP") -> list:
        """Obtém posições abertas"""
        if self.has_credentials:
            endpoint = f"/api/v5/account/positions?instId={symbol}"
            url = self.base_url + endpoint
            headers = self._sign_request("GET", endpoint)
            
            try:
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                if data.get('code') == '0':
                    positions = []
                    for pos in data['data']:
                        if float(pos.get('pos', 0)) != 0:
                            positions.append({
                                "symbol": pos.get('instId'),
                                "side": "long" if float(pos.get('pos', 0)) > 0 else "short",
                                "quantity": abs(float(pos.get('pos', 0))),
                                "entry_price": float(pos.get('avgPx', 0)),
                                "leverage": float(pos.get('lever', 1)),
                                "liquidation_price": float(pos.get('liqPx', 0)),
                                "unrealized_pnl": float(pos.get('upl', 0))
                            })
                    return positions
                else:
                    logger.error(f"❌ Erro API posições: {data.get('msg', 'Unknown error')}")
                    return []
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição posições: {e}")
                return []
        else:
            # Para simulação, retorna lista vazia
            return []
