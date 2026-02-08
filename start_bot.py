#!/usr/bin/env python3
"""
START_BOT.py - Script simplificado para iniciar o bot
"""
import os
import sys
import logging
import time

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Verifica se tudo está configurado corretamente"""
    logger.info("🔍 Verificando ambiente...")
    
    # Verificar arquivo Pine Script
    pine_paths = [
        'strategy/Adaptive_Zero_Lag_EMA_v2.pine',
        'Adaptive_Zero_Lag_EMA_v2.pine'
    ]
    
    pine_found = False
    for path in pine_paths:
        if os.path.exists(path):
            pine_found = True
            logger.info(f"✅ Pine Script encontrado: {path}")
            break
    
    if not pine_found:
        logger.error("❌ Arquivo Pine Script não encontrado!")
        logger.info("💡 Coloque o arquivo .pine na pasta 'strategy/' ou na raiz")
        return False
    
    # Verificar variáveis de ambiente
    okx_vars = ['OKX_API_KEY', 'OKX_SECRET_KEY', 'OKX_PASSPHRASE']
    missing_vars = []
    
    for var in okx_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        logger.warning(f"⚠️ Variáveis OKX faltando: {missing_vars}")
        logger.info("💡 Modo SIMULAÇÃO será ativado")
    else:
        logger.info("✅ Credenciais OKX configuradas (Modo REAL)")
    
    logger.info("✅ Ambiente verificado com sucesso")
    return True

def main():
    """Função principal"""
    print("=" * 60)
    print("🤖 BOT TRADING ETH/USDT - IDÊNTICO AO TRADINGVIEW")
    print("=" * 60)
    
    # Verificar ambiente
    if not check_environment():
        print("❌ Problemas no ambiente detectados")
        sys.exit(1)
    
    # Importar main
    try:
        import main as bot_main
        print("✅ Bot carregado com sucesso")
        print("💡 Acesse: http://localhost:10000")
        print("=" * 60)
        
        # Manter o script rodando
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n⏹️ Bot interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Erro ao iniciar bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
