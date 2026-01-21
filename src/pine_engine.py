def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
    """
    Processa um candle e retorna sinais RAW (igual Pine Script).
    NÃO executa trades aqui - isso é feito pelo StrategyRunner.
    """
    self.candle_count += 1
    src = candle['close']
    self.series_data['src'].append(src)
    
    # Calcula EMA e EC (Zero Lag)
    ema, ec, least_error = self.calculate_zero_lag_ema(src)
    
    # Valores anteriores para crossover/crossunder
    ec_prev = self.series_data['EC'][1] if len(self.series_data['EC'].values) > 1 else ec
    ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA'].values) > 1 else ema
    
    # --- SINAIS RAW (IGUAL AO PINE SCRIPT) ---
    # crossover(EC, EMA) no Pine
    crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
    
    # crossunder(EC, EMA) no Pine  
    crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
    
    # Threshold (100*LeastError/src > Threshold)
    error_pct = 100 * least_error / src if src > 0 else 0
    threshold_check = error_pct > self.threshold
    
    # Estas são as variáveis buy_signal e sell_signal do seu Pine
    buy_signal_raw = crossover_signal and threshold_check
    sell_signal_raw = crossunder_signal and threshold_check
    
    # --- NÃO DETERMINA AÇÃO AQUI ---
    # No Pine, a ação é determinada por:
    # 1. pendingBuy/pendingSell flags
    # 2. strategy.position_size
    # 3. Só executa na PRÓXIMA barra (buy_signal[1])
    
    result = {
        'signal': 'HOLD',  # Sempre HOLD - execução é no StrategyRunner
        'strength': 0,
        'buy_signal_raw': buy_signal_raw,    # Para usar na PRÓXIMA barra
        'sell_signal_raw': sell_signal_raw,  # Para usar na PRÓXIMA barra
        'price': src,
        'ema': ema,
        'ec': ec,
        'least_error': least_error,
        'error_pct': error_pct,
        'candle_number': self.candle_count,
        'timestamp': datetime.now().isoformat()
    }
    
    # Log apenas para debug (sinais raw)
    if buy_signal_raw or sell_signal_raw:
        logger.info(f"📊 Candle {self.candle_count}: ${src:.2f}")
        logger.info(f"   EMA={ema:.2f}, EC={ec:.2f}, Erro={error_pct:.2f}%")
        logger.info(f"   Sinal RAW: {'BUY' if buy_signal_raw else 'SELL' if sell_signal_raw else 'NONE'}")
    
    return result
