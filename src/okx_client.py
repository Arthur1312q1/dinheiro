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
        
        # Verificar se as credenciais não são os valores placeholder
        if "your_api_key_here" in self.api_key or "example" in self.api_key:
            logger.warning("⚠️  API Key parece ser um valor de exemplo")
        
        logger.info("✅ Credenciais OKX validadas")
    
    def _test_connection(self):
        """Testa a conexão básica com a OKX"""
        try:
            # Teste simples de API pública
            test_url = f"{self.base_url}/api/v5/public/time"
            response = requests.get(test_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "0":
                    logger.info("✅ Conexão com OKX estabelecida")
                    logger.info(f"⏰ Hora do servidor OKX: {data.get('data', [{}])[0].get('ts')}")
                    return True
            logger.warning(f"⚠️  Status de conexão: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Falha na conexão com OKX: {e}")
        return False
    
    def _get_iso_timestamp(self) -> str:
        """Retorna timestamp no formato EXATO que a OKX espera"""
        # Formato: YYYY-MM-DDTHH:MM:SS.sssZ (com 3 casas decimais nos segundos)
        now = datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
        return timestamp
    
    def _generate_signature(self, timestamp: str, method: str, endpoint: str, body: str = "") -> str:
        """
        Gera assinatura HMAC SHA256 no formato EXATO da OKX
        FORMATO: timestamp + method + requestPath + body
        """
        try:
            # IMPORTANTE: A OKX usa o endpoint COMPLETO (incluindo query params)
            # Exemplo: /api/v5/account/balance?ccy=USDT
            request_path = endpoint
            
            # Construir mensagem EXATA
            message = timestamp + method.upper() + request_path + body
            
            logger.debug(f"📝 Mensagem para assinatura: {message}")
            
            # Decodificar a secret key (se estiver em base64)
            if len(self.secret_key) > 100:  # Provavelmente está em base64
                try:
                    secret_bytes = base64.b64decode(self.secret_key)
                except:
                    secret_bytes = self.secret_key.encode('utf-8')
            else:
                secret_bytes = self.secret_key.encode('utf-8')
            
            # Criar HMAC SHA256
            message_bytes = message.encode('utf-8')
            signature = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
            signature_b64 = base64.b64encode(signature.digest()).decode()
            
            logger.debug(f"🔑 Assinatura gerada (Base64): {signature_b64[:30]}...")
            return signature_b64
            
        except Exception as e:
            logger.error(f"💥 Erro ao gerar assinatura: {e}")
            raise
    
    def _get_headers(self, method: str, endpoint: str, body: str = "") -> Dict:
        """Gera headers com timestamp sincronizado e assinatura válida"""
        timestamp = self._get_iso_timestamp()
        
        # Gerar assinatura
        signature = self._generate_signature(timestamp, method, endpoint, body)
        
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
            'User-Agent': 'OKX-Trading-Bot/1.0'
        }
        
        logger.debug(f"📤 Headers preparados:")
        logger.debug(f"  Timestamp: {timestamp}")
        logger.debug(f"  Sign: {signature[:30]}...")
        return headers
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     retry_count: int = 3) -> Dict:
        """
        Faz requisição para API OKX com tratamento de erro e retry
        """
        url = f"{self.base_url}{endpoint}"
        body = ""
        
        # Preparar body para POST/PUT
        if data and method in ['POST', 'PUT']:
            body = json.dumps(data, separators=(',', ':'))  # Sem espaços
            logger.debug(f"📦 Body JSON: {body}")
        
        for attempt in range(retry_count):
            try:
                headers = self._get_headers(method, endpoint, body)
                
                logger.info(f"🌐 {method} {endpoint} (tentativa {attempt + 1}/{retry_count})")
                
                # Fazer requisição
                if method == 'GET':
                    response = requests.get(url, headers=headers, timeout=10)
                elif method == 'POST':
                    response = requests.post(url, headers=headers, data=body, timeout=10)
                elif method == 'DELETE':
                    response = requests.delete(url, headers=headers, timeout=10)
                else:
                    logger.error(f"❌ Método não suportado: {method}")
                    return {"code": "-1", "msg": f"Método não suportado: {method}"}
                
                # Log da resposta
                logger.debug(f"📥 Status: {response.status_code}")
                
                # Verificar se a resposta é JSON válido
                try:
                    result = response.json()
                except json.JSONDecodeError:
                    logger.error(f"❌ Resposta não é JSON válido: {response.text}")
                    return {"code": "-1", "msg": "Invalid JSON response"}
                
                # Analisar resultado
                if response.status_code == 200 and result.get("code") == "0":
                    logger.debug(f"✅ Requisição bem-sucedida")
                    return result
                elif response.status_code == 401:
                    error_msg = result.get('msg', 'Invalid Sign')
                    logger.error(f"❌ ERRO 401 - Autenticação falhou: {error_msg}")
                    logger.error(f"📊 Resposta completa: {result}")
                    
                    # Mostrar detalhes para debug
                    if attempt == 0:  # Apenas na primeira tentativa
                        logger.error("🔍 DEBUG INFO:")
                        logger.error(f"   API Key: {self.api_key[:10]}...")
                        logger.error(f"   Secret Length: {len(self.secret_key)} chars")
                        logger.error(f"   Passphrase: {'*' * len(self.passphrase)}")
                        logger.error(f"   Endpoint: {endpoint}")
                        logger.error(f"   Method: {method}")
                        logger.error(f"   Body: {body}")
                    
                    # Não retentar para erro de autenticação
                    return result
                else:
                    error_msg = result.get('msg', f'HTTP {response.status_code}')
                    logger.error(f"❌ Erro na API: {error_msg}")
                    
                    # Aguardar antes de retentar
                    if attempt < retry_count - 1:
                        wait_time = 2 ** attempt
                        logger.info(f"⏳ Aguardando {wait_time}s antes de retentar...")
                        time.sleep(wait_time)
                    
                    continue
                    
            except requests.exceptions.Timeout:
                logger.error(f"⏰ Timeout na requisição {endpoint}")
                if attempt < retry_count - 1:
                    time.sleep(1)
                continue
            except requests.exceptions.ConnectionError:
                logger.error(f"🔌 Erro de conexão com OKX")
                if attempt < retry_count - 1:
                    time.sleep(2)
                continue
            except Exception as e:
                logger.error(f"💥 Erro inesperado: {e}")
                return {"code": "-1", "msg": str(e)}
        
        return {"code": "-1", "msg": "All retry attempts failed"}
    
    def get_balance(self) -> float:
        """Obtém saldo disponível em USDT"""
        logger.info("💰 Obtendo saldo da conta...")
        
        # ENDPOINT CORRETO para obter saldo de conta spot
        response = self._make_request("GET", "/api/v5/account/balance", data=None)
        
        if response.get("code") == "0":
            try:
                data = response.get("data", [{}])
                if not data:
                    logger.warning("⚠️  Dados de saldo vazios")
                    return 0.0
                
                # Para conta spot, a estrutura pode ser diferente
                details = data[0].get("details", [])
                if details:
                    # Buscar USDT
                    for detail in details:
                        if detail.get("ccy") == "USDT":
                            balance = float(detail.get("availBal", "0"))
                            logger.info(f"✅ Saldo USDT disponível: ${balance:.2f}")
                            return balance
                
                # Se não encontrou em details, tentar outra estrutura
                avail_eq = data[0].get("availEq", "0")
                if avail_eq and avail_eq != "0":
                    balance = float(avail_eq)
                    logger.info(f"✅ Saldo total disponível: ${balance:.2f}")
                    return balance
                
                logger.warning("⚠️  Nenhum saldo encontrado na resposta")
                logger.debug(f"Resposta: {data}")
                return 0.0
                
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"❌ Erro ao processar resposta do saldo: {e}")
                logger.debug(f"Resposta completa: {response}")
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
                        logger.info(f"📈 Preço {symbol}: ${price:.2f}")
                        return price
                    except ValueError:
                        logger.error(f"❌ Formato de preço inválido: {last_price}")
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
            
            logger.info(f"🔍 Buscando {limit} candles: {symbol} | {timeframe}")
            
            response = self._make_request("GET", endpoint)
            
            if response.get("code") == "0":
                candles_data = response.get("data", [])
                if candles_data:
                    candles = []
                    # Converter e inverter ordem (mais antigo primeiro)
                    for candle in reversed(candles_data):
                        try:
                            candles.append({
                                "timestamp": int(candle[0]),  # Timestamp em ms
                                "open": float(candle[1]),
                                "high": float(candle[2]),
                                "low": float(candle[3]),
                                "close": float(candle[4]),
                                "volume": float(candle[5])
                            })
                        except (IndexError, ValueError) as e:
                            logger.warning(f"⚠️  Candle inválido ignorado: {candle}")
                    
                    if candles:
                        logger.info(f"✅ {len(candles)} candles obtidos | Último: ${candles[-1]['close']:.2f}")
                        return candles
                    else:
                        logger.warning("⚠️  Nenhum candle válido na resposta")
                else:
                    logger.warning("⚠️  Dados de candles vazios")
            else:
                logger.error(f"❌ Erro ao obter candles: {response.get('msg')}")
                
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
        
        # 3. Calcular capital de risco (95%)
        risk_capital = balance * self.balance_percentage
        logger.info(f"💵 Capital de risco (95%): ${risk_capital:.2f}")
        
        # 4. Para ETH/USDT, 1 ponto = $0.01
        point_value = 0.01
        
        # 5. Calcular valor da posição: risco / (SL em pontos * valor por ponto)
        position_value = risk_capital / (sl_points * point_value)
        
        # 6. Calcular quantidade em ETH
        eth_quantity = position_value / price
        
        logger.info(f"🧮 Cálculo de posição:")
        logger.info(f"   Saldo total: ${balance:.2f}")
        logger.info(f"   Preço ETH: ${price:.2f}")
        logger.info(f"   Stop Loss: {sl_points} pontos (${sl_points * point_value:.2f})")
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
            
            logger.info(f"🚀 Preparando ordem {side.upper()}:")
            logger.info(f"   Símbolo: {symbol}")
            logger.info(f"   Quantidade: {quantity:.4f} ETH")
            logger.info(f"   Preço atual: ${price:.2f}")
            logger.info(f"   Valor total: ${quantity * price:.2f}")
            
            # 2. Configurar alavancagem 1x (apenas para contratos)
            if "SWAP" in symbol:
                leverage_data = {
                    "instId": symbol,
                    "lever": "1",
                    "mgnMode": "cross"
                }
                
                leverage_response = self._make_request("POST", "/api/v5/account/set-leverage", leverage_data)
                if leverage_response.get("code") == "0":
                    logger.info("✅ Alavancagem 1x configurada")
                else:
                    logger.warning(f"⚠️  Alavancagem não configurada: {leverage_response.get('msg')}")
            
            # 3. Ordem principal (MARKET)
            order_data = {
                "instId": symbol,
                "tdMode": "cross",  # Modo cruzado para contratos
                "side": side.lower(),
                "ordType": "market",
                "sz": str(round(quantity, 4))  # 4 casas decimais para ETH
            }
            
            logger.info(f"📤 Enviando ordem de mercado...")
            order_response = self._make_request("POST", "/api/v5/trade/order", order_data)
            
            if order_response.get("code") == "0":
                logger.info(f"✅✅✅ ORDEM {side.upper()} EXECUTADA COM SUCESSO!")
                
                # 4. Calcular preços de SL e TP
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
                
                # 5. Ordem de Stop Loss
                sl_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": sl_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(sl_price, 2)),
                    "tpOrdPx": "-1"  # Executar a mercado
                }
                
                # 6. Ordem de Take Profit
                tp_data = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": tp_side,
                    "ordType": "market",
                    "sz": str(round(quantity, 4)),
                    "triggerPx": str(round(tp_price, 2)),
                    "tpOrdPx": "-1"
                }
                
                # 7. Enviar ordens SL e TP (não bloqueante)
                sl_response = self._make_request("POST", "/api/v5/trade/order-algo", sl_data)
                if sl_response.get("code") == "0":
                    logger.info("✅ Stop Loss configurado")
                else:
                    logger.warning(f"⚠️  SL não configurado: {sl_response.get('msg')}")
                
                tp_response = self._make_request("POST", "/api/v5/trade/order-algo", tp_data)
                if tp_response.get("code") == "0":
                    logger.info("✅ Take Profit configurado")
                else:
                    logger.warning(f"⚠️  TP não configurado: {tp_response.get('msg')}")
                
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
                        
                        # Determinar lado oposto para fechar
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
    
    def test_auth_simple(self) -> bool:
        """
        Teste SIMPLES de autenticação usando endpoint público + timestamp
        """
        logger.info("🔐 Testando autenticação SIMPLES...")
        
        try:
            # 1. Obter timestamp do servidor OKX
            public_url = f"{self.base_url}/api/v5/public/time"
            response = requests.get(public_url, timeout=5)
            
            if response.status_code != 200:
                logger.error(f"❌ Servidor OKX inacessível: HTTP {response.status_code}")
                return False
            
            server_time = response.json()
            logger.info(f"⏰ OKX Server Time: {server_time}")
            
            # 2. Testar endpoint que requer autenticação (simples)
            test_response = self._make_request("GET", "/api/v5/account/account-position-risk")
            
            if test_response.get("code") == "0":
                logger.info("✅ Autenticação OKX BEM-SUCEDIDA!")
                return True
            else:
                error_msg = test_response.get('msg', 'Unknown error')
                logger.error(f"❌ Falha na autenticação: {error_msg}")
                
                # Verificar se é problema de permissões
                if "Incorrect API key permissions" in error_msg:
                    logger.error("🚨 API Key não tem permissões suficientes!")
                    logger.error("   Vá em OKX > API > Sua API Key > Editar")
                    logger.error("   Marque TODAS as permissões (Read, Trade, Withdraw)")
                
                return False
                
        except Exception as e:
            logger.error(f"💥 Erro no teste de autenticação: {e}")
            return False
