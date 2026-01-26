import logging

logger = logging.getLogger(__name__)

class PineScriptInterpreter:
    def __init__(self, pine_code):
        logger.info("✅ Pine Script Interpreter inicializado (simulação)")
    
    def process_candle(self, candle):
        return {
            'signal': 'HOLD',
            'buy_signal_raw': False,
            'sell_signal_raw': False,
            'price': candle.get('close', 2500.0),
            'ema': 2500.0,
            'ec': 2505.0,
            'error_pct': 0.5
        }
