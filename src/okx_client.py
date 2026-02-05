#!/usr/bin/env python3
"""
OKX_CLIENT.PY - VERSÃO CORRIGIDA PARA PRECISÃO DE FECHAMENTO
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
        
        # Para PRECISÃO de fechamento
        self.last_real_price = None
        self.price_history = []  # Histórico para média
        self.max_history = 10
        
        if self.has_credentials:
            logger.info("✅ Credenciais OKX configuradas. Modo REAL ativado.")
        else:
            logger.warning("⚠️ Credenciais OKX não configuradas. Modo SIMULAÇÃO ativado.")
        
        # Para simulação controlada - OTIMIZADO PARA TRAILING STOP
        self.simulation_trend = "sideways"  # Inicia sideways para evitar viés
        self.simulation_volatility = 0.5  # REDUZIDO para precisão de trailing
        self.last_simulation_update = time.time()
        self.trend_duration = random.randint(300, 1800)  # 5-30 minutos
        
        # Preço alvo para simulação realista
        self.simulation_target = random.uniform(2200, 2800)
        
        logger.info("✅ Cliente OKX inicializado (PRECISÃO OTIMIZADA)")
    
    def _sign_request(self, method: str, endpoint: str, body: str = "") -> dict:
        """Assina requisição para API privada OKX"""
        if not self.has_credentials:
            return {}
        
        try:
            timestamp = str(time.time())
            message = timestamp + method + endpoint + body
            
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
                    for balance_info in data['data']:
                        if balance_info.get('ccy') == currency:
                            available = float(balance_info.get('availBal', 0))
                            logger.debug(f"✅ Saldo real obtido: {available} {currency}")
                            return available
                    
                    return 1000.0  # Fallback
                else:
                    logger.error(f"❌ Erro API saldo: {data.get('msg', 'Unknown error')}")
                    return 1000.0
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição saldo: {e}")
                return 1000.0
        else:
            # Saldo simulado
            return 1000.0
    
    def get_ticker_price(self, symbol: str = "ETH-USDT-SWAP") -> float:
        """Retorna preço real ou simulado - COM MÁXIMA PRECISÃO PARA TRAILING"""
        if self.has_credentials:
            endpoint = f"/api/v5/market/ticker?instId={symbol}"
            url = self.base_url + endpoint
            
            try:
                response = requests.get(url, timeout=10)
                data = response.json()
                
                if data.get('code') == '0' and data['data']:
                    raw_price = float(data['data'][0]['last'])
                    
                    # Arredondar para 2 casas decimais (como TradingView)
                    price = round(raw_price, 2)
                    
                    # Atualizar histórico para média
                    self.price_history.append(price)
                    if len(self.price_history) > self.max_history:
                        self.price_history.pop(0)
                    
                    self.last_real_price = price
                    self.simulated_price = price  # Mantém simulação sincronizada
                    
                    logger.debug(f"📈 Preço REAL: ${price:.2f}")
                    return price
                else:
                    logger.error(f"❌ Erro API ticker: {data.get('msg', 'Unknown error')}")
                    return self._simulate_price_for_trailing()
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição ticker: {e}")
                return self._simulate_price_for_trailing()
        else:
            return self._simulate_price_for_trailing()
    
    def _simulate_price_for_trailing(self) -> float:
        """Simula preço OTIMIZADO para teste de trailing stop"""
        current_time = time.time()
        
        # Mudar tendência apenas quando a duração acabar
        if current_time - self.last_simulation_update > self.trend_duration:
            # Escolher nova tendência baseada no preço atual
            if self.simulated_price > 2700:
                self.simulation_trend = "down"
            elif self.simulated_price < 2300:
                self.simulation_trend = "up"
            else:
                trends = ["down", "up", "sideways"]
                self.simulation_trend = random.choice(trends)
            
            # Novo alvo e duração
            self.simulation_target = random.uniform(2200, 2800)
            self.trend_duration = random.randint(300, 1800)
            self.last_simulation_update = current_time
            
            logger.info(f"🔁 Mudança de tendência: {self.simulation_trend} | Alvo: ${self.simulation_target:.2f}")
        
        # Calcular variação baseada na tendência e no alvo
        price_diff = self.simulation_target - self.simulated_price
        distance_factor = abs(price_diff) / 100
        
        if self.simulation_trend == "down":
            # Move em direção ao alvo, mas com alguma aleatoriedade
            base_move = random.uniform(-distance_factor * 0.8, -distance_factor * 0.2)
        elif self.simulation_trend == "up":
            base_move = random.uniform(distance_factor * 0.2, distance_factor * 0.8)
        else:  # sideways
            base_move = random.uniform(-self.simulation_volatility, self.simulation_volatility)
        
        # Adicionar ruído pequeno
        noise = random.uniform(-0.1, 0.1)
        variation = base_move + noise
        
        # Limitar variação para evitar saltos bruscos
        max_variation = 2.0  # Máximo $2 por chamada
        if abs(variation) > max_variation:
            variation = max_variation if variation > 0 else -max_variation
        
        self.simulated_price += variation
        
        # Limites realistas para ETH
        if self.simulated_price < 2000:
            self.simulated_price = 2000 + random.uniform(0, 50)
            self.simulation_trend = "up"
        elif self.simulated_price > 3500:
            self.simulated_price = 3500 - random.uniform(0, 50)
            self.simulation_trend = "down"
        
        # Arredondar para 2 casas decimais (como TradingView)
        price = round(self.simulated_price, 2)
        
        # Log apenas se mudança significativa
        if hasattr(self, 'last_logged_price'):
            if abs(price - self.last_logged_price) > 1.0:
                logger.debug(f"📊 Preço SIMULADO: ${price:.2f} (tendência: {self.simulation_trend})")
                self.last_logged_price = price
        else:
            self.last_logged_price = price
        
        return price
    
    def get_precise_price_for_close(self, position_side: str, trigger_price: float) -> float:
        """
        Obtém preço PRECISO para fechamento de posição
        Simula o comportamento do TradingView para trailing stop
        """
        current_price = self.get_ticker_price()
        
        # Para trailing stop, o TradingView fecha no PRIMEIRO preço que atinge o stop
        # Precisamos simular isso corretamente
        
        if position_side == 'long':
            # Para LONG: fecha quando preço CAI para ou abaixo do stop
            # O TradingView usa o preço de BID para venda
            # Simulamos um pequeno spread
            spread = random.uniform(0.01, 0.10)  # Spread de 1-10 centavos
            close_price = current_price - spread
            
            # Garantir que não fique abaixo do stop por muito
            if close_price < trigger_price - 0.5:  # Máximo 50 centavos abaixo
                close_price = trigger_price - random.uniform(0.01, 0.50)
            
        else:  # short
            # Para SHORT: fecha quando preço SOBE para ou acima do stop
            # O TradingView usa o preço de ASK para compra
            spread = random.uniform(0.01, 0.10)
            close_price = current_price + spread
            
            if close_price > trigger_price + 0.5:  # Máximo 50 centavos acima
                close_price = trigger_price + random.uniform(0.01, 0.50)
        
        # Arredondar para 2 casas decimais
        close_price = round(close_price, 2)
        
        logger.debug(f"🎯 Preço de fechamento calculado: ${close_price:.2f}")
        logger.debug(f"   Preço atual: ${current_price:.2f}")
        logger.debug(f"   Trigger: ${trigger_price:.2f}")
        logger.debug(f"   Lado: {position_side}")
        
        return close_price
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", limit: int = 100) -> list:
        """Retorna candles reais ou simulados"""
        if self.has_credentials:
            tf_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"
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
                            "timestamp": int(c[0]),
                            "open": round(float(c[1]), 2),
                            "high": round(float(c[2]), 2),
                            "low": round(float(c[3]), 2),
                            "close": round(float(c[4]), 2),
                            "volume": round(float(c[5]), 2)
                        })
                    
                    logger.info(f"✅ {len(candles)} candles reais obtidos")
                    return candles
                else:
                    logger.error(f"❌ Erro API candles: {data.get('msg', 'Unknown error')}")
                    return self._simulate_precise_candles(limit)
                    
            except Exception as e:
                logger.error(f"❌ Erro requisição candles: {e}")
                return self._simulate_precise_candles(limit)
        else:
            return self._simulate_precise_candles(limit)
    
    def _simulate_precise_candles(self, limit: int = 100) -> list:
        """Gera candles simulados PRECISOS para backtesting"""
        candles = []
        base_price = 2630.0
        
        for i in range(limit):
            timestamp = int((datetime.now().timestamp() - i * 1800) * 1000)
            
            if not candles:
                open_price = base_price
            else:
                open_price = candles[-1]['close']
            
            # Determinar movimento baseado em padrões realistas
            # 60% de chance de continuar tendência, 40% de reversão
            if i > 0 and random.random() < 0.6:
                # Continua tendência
                prev_move = candles[-1]['close'] - candles[-1]['open']
                if abs(prev_move) > 10:
                    base_move = prev_move * random.uniform(0.3, 0.7)
                else:
                    base_move = random.uniform(-15, 15)
            else:
                # Movimento aleatório
                base_move = random.uniform(-20, 20)
            
            close_price = open_price + base_move
            
            # Calcular high e low de forma realista
            range_size = abs(base_move) * random.uniform(1.5, 3.0)
            high_price = max(open_price, close_price) + random.uniform(0, range_size/2)
            low_price = min(open_price, close_price) - random.uniform(0, range_size/2)
            
            # Garantir que high > low
            if high_price <= low_price:
                high_price = low_price + 0.01
            
            # Volume correlacionado com movimento
            price_range = high_price - low_price
            volume = price_range * random.uniform(100, 500)
            
            candles.append({
                "timestamp": timestamp,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": round(volume, 2)
            })
        
        # Ordenar do mais antigo para o mais recente
        candles.reverse()
        
        logger.debug(f"📊 {len(candles)} candles simulados PRECISOS gerados")
        return candles
    
    def place_order(self, symbol: str, side: str, quantity: float, 
                   price: float = None, order_type: str = "market") -> dict:
        """
        Coloca ordem REAL na OKX ou simula
        """
        if self.has_credentials:
            endpoint = "/api/v5/trade/order"
            url = self.base_url + endpoint
            
            order_data = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side.lower(),
                "ordType": order_type.lower(),
                "sz": str(round(quantity, 4))
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
                return {"success": False, "order_id": None, "message": f"Erro: {str(e)}"}
        else:
            # SIMULAÇÃO: sempre sucesso
            order_id = f"SIM_{int(time.time())}_{random.randint(1000, 9999)}"
            
            logger.info(f"✅ [SIM] Ordem {side} de {quantity} {symbol} colocada: {order_id}")
            
            return {
                "success": True,
                "order_id": order_id,
                "message": f"Ordem simulada {order_id} colocada com sucesso"
            }
    
    def get_average_price(self, window: int = 5) -> float:
        """Retorna preço médio dos últimos N ticks"""
        if not self.price_history:
            return self.get_ticker_price()
        
        # Usar os últimos N preços
        recent_prices = self.price_history[-window:] if len(self.price_history) >= window else self.price_history
        
        if not recent_prices:
            return self.get_ticker_price()
        
        avg_price = sum(recent_prices) / len(recent_prices)
        return round(avg_price, 2)
