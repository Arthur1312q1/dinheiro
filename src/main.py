# src/main.py (Versão Corrigida)
import os
import sys
import logging
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify

# ============================================================================
# 1. CONFIGURAÇÃO INICIAL (DEVE VIR ANTES DAS IMPORTAÇÕES LOCAIS)
# ============================================================================
# Definir o caminho para que Python encontre os módulos 'src'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# 2. CRIAR A APLICAÇÃO FLASK (VARIÁVEL 'app' GLOBAL)
# ============================================================================
app = Flask(__name__)  # <--- VARIÁVEL 'app' DEFINIDA GLOBALMENTE

# ============================================================================
# 3. IMPORTAR MÓDULOS INTERNOS APÓS CRIAR 'app'
# ============================================================================
try:
    from okx_client import OKXClient
    from trading_logic import AdaptiveZeroLagEMA
    from keep_alive import KeepAliveSystem
    logger.info("✅ Módulos internos importados com sucesso.")
except ImportError as e:
    logger.error(f"❌ Erro ao importar módulos: {e}")
    # Em caso de erro, defina as variáveis como None para evitar erros
    OKXClient = None
    AdaptiveZeroLagEMA = None
    KeepAliveSystem = None

# ============================================================================
# 4. INICIALIZAR COMPONENTES
# ============================================================================
# Nota: A inicialização real depende das credenciais OKX. Ela falhará
# se as variáveis de ambiente não estiverem configuradas, mas o app Flask
# deve subir mesmo assim para testes.
try:
    okx_client = OKXClient() if OKXClient else None
    strategy = AdaptiveZeroLagEMA() if AdaptiveZeroLagEMA else None
    keep_alive = KeepAliveSystem() if KeepAliveSystem else None
    logger.info("✅ Componentes do bot inicializados.")
except Exception as e:
    logger.error(f"⚠️  Falha na inicialização dos componentes (API Keys faltando?): {e}")
    okx_client = None
    strategy = None
    keep_alive = None

# ============================================================================
# 5. VARIÁVEIS DE ESTADO DO BOT
# ============================================================================
trading_active = False
trade_thread = None

# ============================================================================
# 6. ENDPOINTS DA API FLASK
# ============================================================================
@app.route('/')
def home():
    """Página inicial do bot."""
    return jsonify({
        "status": "online",
        "service": "OKX ETH Trading Bot (AZLEMA v2)",
        "timeframe": "45 minutos",
        "symbol": "ETH-USDT",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint crítico para keep-alive e monitoramento."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "last_signal": getattr(keep_alive, 'last_signal_time', None) if keep_alive else None,
        "trading_active": trading_active
    })

@app.route('/start', methods=['POST'])
def start_trading():
    """Inicia o loop de trading."""
    global trading_active, trade_thread
    
    if trading_active:
        return jsonify({"status": "error", "message": "Trading já está ativo."}), 400
    
    if not okx_client:
        return jsonify({"status": "error", "message": "Cliente OKX não configurado. Verifique as API Keys."}), 500
    
    try:
        # Iniciar sistema de keep-alive
        if keep_alive:
            keep_alive.start_keep_alive()
            logger.info("✅ Sistema de keep-alive iniciado.")
        
        # Iniciar thread de trading
        trading_active = True
        trade_thread = threading.Thread(target=trading_loop, daemon=True)
        trade_thread.start()
        
        logger.info("🚀 Bot de trading iniciado com sucesso.")
        return jsonify({
            "status": "success",
            "message": "Bot de trading iniciado.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar trading: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    """Para o loop de trading e fecha posições."""
    global trading_active
    
    try:
        trading_active = False
        logger.info("⏹️  Parando bot de trading...")
        
        # Parar keep-alive
        if keep_alive:
            keep_alive.stop_keep_alive()
        
        # Fechar posições na OKX
        if okx_client:
            okx_client.close_all_positions()
        
        logger.info("✅ Bot de trading parado e posições fechadas.")
        return jsonify({
            "status": "success",
            "message": "Bot de trading parado.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao parar trading: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Retorna o status atual do bot."""
    balance = okx_client.get_balance() if okx_client else 0
    price = okx_client.get_ticker_price() if okx_client else None
    
    return jsonify({
        "trading_active": trading_active,
        "balance_usdt": balance,
        "current_eth_price": price,
        "component_status": {
            "okx_client": okx_client is not None,
            "strategy": strategy is not None,
            "keep_alive": keep_alive is not None
        },
        "timestamp": datetime.now().isoformat()
    })

# ============================================================================
# 7. LÓGICA PRINCIPAL DE TRADING (EXECUTADA EM SEGUNDO PLANO)
# ============================================================================
def trading_loop():
    """Loop principal que verifica sinais e executa trades."""
    logger.info("🔄 Loop de trading iniciado.")
    
    while trading_active and okx_client and strategy:
        try:
            # 1. Obter candles de 45 minutos
            candles = okx_client.get_candles(timeframe="45m", limit=100)
            if len(candles) < 30:
                logger.warning("⚠️  Dados insuficientes para análise. Aguardando...")
                time.sleep(300)  # Espera 5 minutos
                continue
            
            # 2. Calcular sinal da estratégia
            signal = strategy.calculate_signals(candles)
            
            # 3. Executar se houver sinal válido
            if signal.get("signal") in ["BUY", "SELL"] and signal.get("strength", 0) > 0:
                logger.info(f"📈 Sinal {signal['signal']} detectado (Força: {signal['strength']}).")
                
                # Calcular tamanho da posição (95% do saldo, SL=2000 pontos)
                position_size = okx_client.calculate_position_size(sl_points=2000)
                
                if position_size > 0:
                    success = okx_client.place_order(
                        side=signal["signal"],
                        quantity=position_size,
                        sl_points=2000,
                        tp_points=55
                    )
                    
                    if success:
                        logger.info(f"✅ Ordem {signal['signal']} executada: {position_size:.4f} ETH")
                    else:
                        logger.error(f"❌ Falha na ordem {signal['signal']}")
            
            # 4. Aguardar próximo candle (45 minutos)
            # Em vez de sleep fixo, podemos esperar até o próximo candle de 45m
            # Simples por enquanto: espera 5 minutos para re-verificar
            time.sleep(300)
            
        except Exception as e:
            logger.error(f"💥 Erro no loop de trading: {e}")
            time.sleep(60)  # Espera 1 minuto antes de tentar novamente

# ============================================================================
# 8. PONTO DE ENTRADA PARA EXECUÇÃO LOCAL (NÃO USADO PELO RENDER)
# ============================================================================
if __name__ == '__main__':
    # Este bloco só é executado quando rodamos o script localmente
    # No Render, o Gunicorn importa o módulo diretamente
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Iniciando servidor Flask na porta {port}...")
    
    # Iniciar keep-alive se disponível
    if keep_alive:
        keep_alive.start_keep_alive()
    
    app.run(host='0.0.0.0', port=port, debug=False)
