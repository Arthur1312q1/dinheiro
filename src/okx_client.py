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
    
    # ... resto dos métodos permanecem iguais ...
