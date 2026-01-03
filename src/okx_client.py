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
            else:
                logger.error(f"❌ Erro ao obter saldo: {data.get('msg')}")
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
            else:
                logger.error(f"❌ Erro ao obter preço: {response.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"Erro ao obter preço: {e}")
            return None
    
    def get_candles(self, symbol: str = "ETH-USDT-SWAP", timeframe: str = "30m", limit: int = 100) -> List[Dict]:
        """Obtém candles de 30 minutos - com múltiplas tentativas de formato"""
        
        # Lista de formatos possíveis para 30 minutos na OKX
        timeframe_attempts = [
            ("30m", "30m"),      # Formato padrão
            ("30M", "30M"),      # M maiúsculo
            ("30min", "30min"),  # Formato por extenso
            ("30Min", "30Min"),  # Com M maiúsculo
            ("30MIN", "30MIN"),  # Tudo maiúsculo
        ]
        
        candles = []
        working_timeframe = None
        
        for attempt_name, attempt_value in timeframe_attempts:
            try:
                endpoint = f"/api/v5/market/candles?instId={symbol}&bar={attempt_value}&limit={limit}"
                logger.info(f"🔍 Tentando timeframe: {attempt_name} ({attempt_value})")
                
                response = self._request("GET", endpoint)
                
                if response.get("code") == "0":
                    candles_data = response.get("data", [])
                    if candles_data:
                        candles = []
                        # OKX retorna do mais recente para o mais antigo
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
                        working_timeframe = attempt_name
                        logger.info(f"✅ TIMEFRAME ENCONTRADO: {attempt_name} - {len(candles)} candles | Último: ${candles[-1]['close']:.2f}")
                        break
                    else:
                        logger.warning(f"⚠️  Timeframe {attempt_name}: API retornou lista vazia")
                else:
                    error_msg = response.get('msg', 'Sem mensagem')
                    logger.warning(f"⚠️  Timeframe {attempt_name} falhou: {error_msg}")
                    
            except Exception as e:
                logger.error(f"💥 Erro com timeframe {attempt_name}: {e}")
        
        if not candles:
            logger.error("💥 TODOS os timeframes de 30 minutos falharam!")
            logger.error("🔧 Tentando fallback para 1H...")
            
            # Fallback para 1H se 30m não funcionar
            endpoint = f"/api/v5/market/candles?instId={symbol}&bar=1H&limit={limit}"
            response = self._request("GET", endpoint)
            
            if response.get("code") == "0" and response.get("data"):
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
                    logger.info(f"⚠️  Usando FALLBACK 1H: {len(candles)} candles")
        
        if candles:
            logger.info(f"📊 Total candles: {len(candles)} | Timeframe usado: {working_timeframe or '1H (fallback)'}")
            logger.info(f"   Primeiro: {candles[0]['timestamp']} | Último: {candles[-1]['timestamp']}")
            logger.info(f"   Faixa de preço: ${candles[0]['close']:.2f} - ${candles[-1]['close']:.2f}")
        else:
            logger.error("❌ Não foi possível obter candles de nenhum timeframe!")
        
        return candles
    
    def calculate_position_size(self) -> float:
        """Calcula tamanho da posição usando 95% do saldo"""
        
        # Primeiro verificar se temos conexão
        balance = self.get_balance()
        if balance <= 0:
            logger.error("❌ Saldo zero ou não disponível")
            return 0
        
        # Obter preço atual
        price = self.get_ticker_price()
        if not price:
            logger.error("❌ Não foi possível obter preço atual")
            return 0
        
        # Calcular capital de risco (95% do saldo)
        risk_capital = balance * self.balance_percentage
        
        # Para ETH/USDT, 1 ponto = $0.01
        point_value = 0.01
        sl_points = 2000  # Stop Loss de 2000 pontos
        
        # Calcular valor da posição
        position_value = risk_capital / (sl_points * point_value)
        
        # Calcular quantidade em ETH
        eth_quantity = position_value / price
        
        logger.info(f"🧮 Cálculo de posição:")
        logger.info(f"   Saldo total: ${balance:.2f}")
        logger.info(f"   Capital de risco (95%): ${risk_capital:.2f}")
        logger.info(f"   Preço ETH: ${price:.2f}")
        logger.info(f"   Posição calculada: {eth_quantity:.4f} ETH (${position_value:.2f})")
        
        return eth_quantity
    
    def place_order(self, side: str, quantity: float) -> bool:
        """Executa ordem com SL=2000 e TP=55 pontos"""
        try:
            symbol = "ETH-USDT-SWAP"
            
            # Obter preço atual
            price = self.get_ticker_price(symbol)
            if not price:
                logger.error("❌ Não foi possível obter preço para ordem")
                return False
            
            logger.info(f"🚀 Preparando ordem {side.upper()}:")
            logger.info(f"   Símbolo: {symbol}")
            logger.info(f"   Quantidade: {quantity:.4f} ETH")
            logger.info(f"   Preço atual: ${price:.2f}")
            
            # Configurar alavancagem 1x (apenas para contratos)
            if "SWAP" in symbol:
                leverage_data = {
                    "instId": symbol,
                    "lever": "1",
                    "mgnMode": "cross"
                }
                leverage_response = self._request("POST", "/api/v5/account/set-leverage", leverage_data)
                if leverage_response.get("code") == "0":
                    logger.info("✅ Alavancagem 1x configurada")
            
            # Ordem principal (MARKET ORDER)
            order_data = {
                "instId": symbol,
                "tdMode": "cross",  # Modo cruzado para contratos
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))
            }
            
            logger.info(f"📤 Enviando ordem de mercado...")
            order_response = self._request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA COM SUCESSO!")
                
                # Calcular SL e TP
                sl_points = 2000
                tp_points = 55
                
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
                
                logger.info(f"   Stop Loss: ${sl_price:.2f} ({sl_points} pontos)")
                logger.info(f"   Take Profit: ${tp_price:.2f} ({tp_points} pontos)")
                
                # Ordem de Stop Loss
                sl_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": sl_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(sl_price, 2)),
                    "tpOrdPx": "-1"  # Executar a mercado
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
                tp_response = self._request("POST", "/api/v5/trade/order-algo", tp_data)
                
                if sl_response.get("code") == "0" and tp_response.get("code") == "0":
                    logger.info("✅ SL e TP configurados com sucesso")
                else:
                    logger.warning("⚠️  Problema ao configurar SL/TP")
                
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
            response = self._request("GET", "/api/v5/account/positions")
            if response.get("code") == "0":
                positions = response.get("data", [])
                if not positions:
                    logger.info("✅ Nenhuma posição aberta para fechar")
                    return True
                
                for pos in positions:
                    if float(pos.get("pos", 0)) != 0:
                        pos_qty = float(pos.get("pos", 0))
                        pos_side = pos.get("posSide", "")
                        
                        # Determinar lado oposto para fechar
                        close_side = "sell" if pos_side == "long" else "buy"
                        
                        close_data = {
                            "instId": pos["instId"],
                            "tdMode": "cross",
                            "side": close_side,
                            "ordType": "market",
                            "sz": str(abs(pos_qty))
                        }
                        
                        close_response = self._request("POST", "/api/v5/trade/close-position", close_data)
                        if close_response.get("code") == "0":
                            logger.info(f"✅ Posição fechada: {pos['instId']} {pos_qty}")
                        else:
                            logger.error(f"❌ Erro ao fechar posição: {close_response.get('msg')}")
                
                logger.info("✅ Todas as posições processadas")
                return True
            else:
                logger.error(f"❌ Erro ao obter posições: {response.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"Erro ao fechar posições: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Testa a conexão com a OKX"""
        try:
            # Testar balanço
            balance = self.get_balance()
            # Testar preço
            price = self.get_ticker_price()
            # Testar candles
            candles = self.get_candles(limit=5)
            
            if balance is not None and price is not None and candles:
                logger.info("✅ Conexão OKX testada com sucesso!")
                return True
            else:
                logger.error("❌ Teste de conexão falhou")
                return False
        except Exception as e:
            logger.error(f"❌ Erro no teste de conexão: {e}")
            return False
