#!/usr/bin/env python3
"""
CONFIG_EXACT.py - Configuração EXATA do Pine Script
Copie este arquivo e ajuste os valores conforme seu Pine Script
"""

PINE_CONFIG_EXACT = {
    # ⚠️ IMPORTANTE: Altere estes valores para corresponder EXATAMENTE ao seu Pine Script
    'timeframe': '30m',           # Timeframe usado no TradingView (ex: '15m', '30m', '1h', '4h')
    'period': 20,                 # Period do EMA (default: 20)
    'adaptive': 'Cos IFM',        # Método adaptativo: 'Cos IFM', 'I-Q IFM', 'Average', ou 'Off'
    'gain_limit': 900,            # Gain Limit (default: 900)
    'threshold': 0.0,             # Threshold % (default: 0.0)
    'fixed_sl': 2000,             # Stop Loss em pontos (default: 2000)
    'fixed_tp': 55,               # Take Profit em pontos (default: 55)
    'risk': 0.01,                 # Risk % (default: 0.01 = 1%)
    'limit': 100,                 # Max Lots (default: 100)
    'initial_capital': 1000.0,    # Initial Capital (deve ser IGUAL ao Pine Script)
    'mintick': 0.01,              # syminfo.mintick para ETH/USDT (não altere)
    'symbol': 'ETH-USDT-SWAP'     # Símbolo da OKX
}

# Instruções:
# 1. Abra seu Pine Script no TradingView
# 2. Compare cada parâmetro com os valores acima
# 3. Ajuste os valores neste arquivo para serem IDÊNTICOS
# 4. Salve o arquivo
# 5. Execute o bot novamente

def validate_config():
    """Valida se a configuração está completa"""
    required_keys = [
        'timeframe', 'period', 'adaptive', 'gain_limit', 'threshold',
        'fixed_sl', 'fixed_tp', 'risk', 'limit', 'initial_capital',
        'mintick', 'symbol'
    ]
    
    missing = []
    for key in required_keys:
        if key not in PINE_CONFIG_EXACT:
            missing.append(key)
    
    if missing:
        print(f"❌ Configuração incompleta. Faltando: {missing}")
        return False
    
    print("✅ Configuração válida")
    print(f"   Timeframe: {PINE_CONFIG_EXACT['timeframe']}")
    print(f"   Period: {PINE_CONFIG_EXACT['period']}")
    print(f"   Adaptive: {PINE_CONFIG_EXACT['adaptive']}")
    print(f"   Risk: {PINE_CONFIG_EXACT['risk']*100}%")
    print(f"   SL: {PINE_CONFIG_EXACT['fixed_sl']}p, TP: {PINE_CONFIG_EXACT['fixed_tp']}p")
    print(f"   Initial Capital: ${PINE_CONFIG_EXACT['initial_capital']}")
    
    return True

if __name__ == "__main__":
    validate_config()
