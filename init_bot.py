#!/usr/bin/env python3
"""
Script de inicialização do bot
"""
import os
import sys
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Verifica o ambiente e arquivos necessários"""
    logger.info("=" * 60)
    logger.info("🔧 VERIFICAÇÃO DO AMBIENTE DO BOT")
    logger.info("=" * 60)
    
    # Verificar se estamos no Render
    is_render = os.getenv('RENDER', '').lower() == 'true'
    
    if is_render:
        logger.info("🌍 Ambiente: RENDER")
        # Verificar variáveis de ambiente do Render
        if os.getenv('RENDER_SERVICE_NAME'):
            logger.info(f"✅ Nome do serviço: {os.getenv('RENDER_SERVICE_NAME')}")
        else:
            logger.warning("⚠️  RENDER_SERVICE_NAME não definido")
    else:
        logger.info("💻 Ambiente: LOCAL")
    
    # Verificar variáveis de ambiente da OKX
    okx_vars = ['OKX_API_KEY', 'OKX_SECRET_KEY', 'OKX_PASSPHRASE']
    missing_vars = []
    
    for var in okx_vars:
        value = os.getenv(var)
        if value:
            logger.info(f"✅ {var}: {'***' + value[-4:] if len(value) > 4 else '***'}")
        else:
            missing_vars.append(var)
            logger.error(f"❌ {var}: NÃO DEFINIDO")
    
    if missing_vars:
        logger.error(f"⚠️  Variáveis faltando: {missing_vars}")
    else:
        logger.info("✅ Todas as variáveis da OKX estão configuradas")
    
    # Verificar arquivos necessários
    required_files = [
        'requirements.txt',
        'main.py',
        'src/okx_client.py',
        'src/strategy_runner.py',
        'src/pine_engine.py',
        'src/keep_alive.py',
        'src/trade_history.py'
    ]
    
    for file_path in required_files:
        if os.path.exists(file_path):
            logger.info(f"✅ Arquivo encontrado: {file_path}")
        else:
            logger.error(f"❌ Arquivo não encontrado: {file_path}")
    
    # Verificar arquivo Pine Script
    pine_paths = [
        'strategy/Adaptive_Zero_Lag_EMA_v2.pine',
        'Adaptive_Zero_Lag_EMA_v2.pine'
    ]
    
    pine_found = False
    for pine_path in pine_paths:
        if os.path.exists(pine_path):
            pine_found = True
            logger.info(f"✅ Arquivo Pine Script encontrado: {pine_path}")
            # Verificar tamanho
            try:
                size = os.path.getsize(pine_path)
                logger.info(f"   Tamanho: {size} bytes")
            except:
                pass
            break
    
    if not pine_found:
        logger.error("❌ Arquivo Pine Script NÃO ENCONTRADO!")
        logger.info("💡 Crie o diretório 'strategy' e cole o arquivo .pine lá")
    
    logger.info("=" * 60)
    logger.info("✅ Verificação concluída")
    logger.info("=" * 60)
    
    return True

if __name__ == "__main__":
    check_environment()
