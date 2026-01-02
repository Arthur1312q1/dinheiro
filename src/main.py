import time
from datetime import datetime, timedelta

def trading_loop():
    """Loop principal de trading sincronizado com candles de 45m."""
    logger.info("Loop de trading (45m) iniciado.")
    
    # Sincronização inicial: espera até o próximo múltiplo de 45 minutos
    now = datetime.utcnow()
    next_run = now.replace(second=0, microsecond=0)
    
    # Calcula minutos para o próximo candle de 45m
    minutes_since_epoch = (next_run - next_run.replace(minute=0, hour=0)).minutes
    remainder = minutes_since_epoch % 45
    if remainder > 0:
        wait_minutes = 45 - remainder
    else:
        # Estamos exatamente no fechamento, espera o próximo ciclo
        wait_minutes = 45
    
    next_run = next_run + timedelta(minutes=wait_minutes)
    logger.info(f"Próxima execução agendada para: {next_run} UTC")
    time.sleep((next_run - datetime.utcnow()).total_seconds())
    
    while trading_active:
        cycle_start = datetime.utcnow()
        logger.info(f"--- Início do ciclo de trading {cycle_start} UTC ---")
        
        try:
            # 1. OBTER DADOS: Busca candles FECHADOS de 45m (ex: último candle de 45m)
            candles = okx_client.get_candles(timeframe="45m", limit=100)
            
            if len(candles) < 30:
                logger.warning(f"Dados insuficientes: {len(candles)} candles. Aguardando próximo ciclo.")
                time.sleep(2700)  # Espera 45 minutos
                continue
            
            # O último candle na lista é o mais recente (candle atual, ainda não fechado?)
            # Vamos usar o penúltimo como último fechado para análise
            last_closed_candle = candles[-2] if len(candles) > 1 else candles[-1]
            logger.info(f"Último candle fechado em: {last_closed_candle['timestamp']}, Preço de fechamento: {last_closed_candle['close']}")
            
            # 2. CALCULAR SINAL: Envia TODOS os candles fechados para a estratégia
            # A lógica interna da estratégia deve usar confirmação de 1 barra (como o PineScript)
            signal = strategy.calculate_signals(candles)
            
            logger.info(f"Sinal calculado: {signal}")
            
            # 3. EXECUTAR ORDEM: Se houver sinal (BUY/SELL) confirmado
            if signal["signal"] in ["BUY", "SELL"]:
                logger.info(f"SINAL CONFIRMADO: {signal['signal']} a {signal.get('price', 'N/A')}")
                
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
                        logger.info(f"Ordem {signal['signal']} executada com sucesso. Tamanho: {position_size:.4f} ETH")
                    else:
                        logger.error(f"Falha ao executar ordem {signal['signal']}")
                else:
                    logger.warning("Tamanho da posição calculado como 0. Ordem não enviada.")
            else:
                logger.info("Nenhum sinal de trade confirmado neste ciclo.")
        
        except Exception as e:
            logger.error(f"Erro no ciclo de trading: {e}", exc_info=True)
        
        # 4. SINCRONIZAÇÃO: Espera até o próximo fechamento de 45 minutos
        now = datetime.utcnow()
        next_run = now.replace(second=0, microsecond=0)
        minutes_since_epoch = (next_run - next_run.replace(minute=0, hour=0)).minutes
        remainder = minutes_since_epoch % 45
        wait_minutes = 45 - remainder if remainder > 0 else 45
        
        # Garante que esperamos pelo menos 1 minuto para evitar execuções consecutivas
        wait_minutes = max(wait_minutes, 1)
        next_run = next_run + timedelta(minutes=wait_minutes)
        
        wait_seconds = (next_run - datetime.utcnow()).total_seconds()
        logger.info(f"Próxima execução em {wait_seconds:.0f} segundos (~{wait_minutes} min), às {next_run} UTC")
        
        # Aguarda o tempo calculado, checando periodicamente se trading_active ainda é True
        while wait_seconds > 0 and trading_active:
            time.sleep(min(30, wait_seconds))  # Dorme em blocos de até 30s
            wait_seconds = (next_run - datetime.utcnow()).total_seconds()
