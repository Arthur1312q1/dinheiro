#!/usr/bin/env python3
"""
FLAG_SYSTEM.py - Sistema de flags EXATO do Pine Script (CORRIGIDO)
"""
import logging

logger = logging.getLogger(__name__)

class FlagSystem:
    """Gerencia flags pendingBuy/pendingSell EXATAMENTE como Pine Script"""
    
    def __init__(self):
        # Estado atual (como no Pine: pendingBuy, pendingSell)
        self.pending_buy = False
        self.pending_sell = False
        
        # Valores anteriores para nz()[1]
        self.pending_buy_prev = False
        self.pending_sell_prev = False
        
        # Sinais da barra anterior [1]
        self.buy_signal_prev = False
        self.sell_signal_prev = False
        
        logger.info("✅ FlagSystem inicializado (Pine Script exact)")
    
    def update_flags(self, buy_signal_prev: bool, sell_signal_prev: bool):
        """
        Atualiza flags EXATAMENTE como Pine Script linhas 63-71:
        
        pendingBuy := nz(pendingBuy[1])
        pendingSell := nz(pendingSell[1])
        
        if (buy_signal[1])
            pendingBuy := true
        if (sell_signal[1])
            pendingSell := true
        
        IMPORTANTE: Esta função deve ser chamada NO FECHAMENTO da barra N,
        usando buy_signal[1]/sell_signal[1] (que são os sinais da barra N-1)
        """
        # Log dos valores recebidos
        logger.debug(f"📌 Atualizando flags com sinais [1]: buy={buy_signal_prev}, sell={sell_signal_prev}")
        logger.debug(f"   Valores anteriores: pendingBuy[1]={self.pending_buy_prev}, pendingSell[1]={self.pending_sell_prev}")
        
        # 1. Persistir valores anteriores (pendingBuy := nz(pendingBuy[1]))
        # Se não houver valor anterior (nz), mantém False
        self.pending_buy = self.pending_buy_prev if self.pending_buy_prev is not None else False
        self.pending_sell = self.pending_sell_prev if self.pending_sell_prev is not None else False
        
        logger.debug(f"   Flags após persistência: pendingBuy={self.pending_buy}, pendingSell={self.pending_sell}")
        
        # 2. Setar flags com sinais da barra ANTERIOR [1]
        # Nota: buy_signal_prev/sell_signal_prev são os sinais da barra N-1
        if buy_signal_prev:
            self.pending_buy = True
            logger.debug(f"   ↳ pendingBuy = True (porque buy_signal[1] == True)")
        
        if sell_signal_prev:
            self.pending_sell = True
            logger.debug(f"   ↳ pendingSell = True (porque sell_signal[1] == True)")
        
        # 3. Salvar flags atuais como [1] para próxima iteração
        self.pending_buy_prev = self.pending_buy
        self.pending_sell_prev = self.pending_sell
        
        # 4. Salvar sinais atuais como [1] para próxima iteração
        # (Estes serão os sinais da barra N, que serão [1] na próxima)
        # NOTA: Esta atualização é feita separadamente pelo strategy runner
        
        logger.info(f"   FLAGS ATUALIZADAS: pendingBuy={self.pending_buy}, pendingSell={self.pending_sell}")
        
        return {
            'pending_buy': self.pending_buy,
            'pending_sell': self.pending_sell,
            'pending_buy_prev': self.pending_buy_prev,
            'pending_sell_prev': self.pending_sell_prev
        }
    
    def reset_buy_flag(self):
        """Reseta pendingBuy após execução bem-sucedida"""
        self.pending_buy = False
        self.pending_buy_prev = False
        logger.debug("   ↳ pendingBuy = False (reset após execução bem-sucedida)")
    
    def reset_sell_flag(self):
        """Reseta pendingSell após execução bem-sucedida"""
        self.pending_sell = False
        self.pending_sell_prev = False
        logger.debug("   ↳ pendingSell = False (reset após execução bem-sucedida)")
    
    def set_previous_signals(self, buy_signal: bool, sell_signal: bool):
        """Define os sinais da barra atual (que serão [1] na próxima)"""
        self.buy_signal_prev = buy_signal
        self.sell_signal_prev = sell_signal
        logger.debug(f"   Sinais salvos como [1] para próxima: buy={buy_signal}, sell={sell_signal}")
    
    def get_state(self):
        """Retorna estado atual completo"""
        return {
            'pending_buy': self.pending_buy,
            'pending_sell': self.pending_sell,
            'pending_buy_prev': self.pending_buy_prev,
            'pending_sell_prev': self.pending_sell_prev,
            'buy_signal_prev': self.buy_signal_prev,
            'sell_signal_prev': self.sell_signal_prev
        }
    
    def set_state(self, state: dict):
        """Define estado (para recuperação)"""
        self.pending_buy = state.get('pending_buy', False)
        self.pending_sell = state.get('pending_sell', False)
        self.pending_buy_prev = state.get('pending_buy_prev', False)
        self.pending_sell_prev = state.get('pending_sell_prev', False)
        self.buy_signal_prev = state.get('buy_signal_prev', False)
        self.sell_signal_prev = state.get('sell_signal_prev', False)
        
        logger.info(f"🔄 Estado das flags restaurado")
        logger.info(f"   pendingBuy={self.pending_buy}, pendingSell={self.pending_sell}")
    
    def should_execute_buy(self, position_size: float) -> bool:
        """Verifica se deve executar BUY (Pine linha 113)"""
        # Pine: if (pendingBuy and strategy.position_size <= 0)
        should_execute = self.pending_buy and position_size <= 0
        
        logger.debug(f"   Condição BUY: pendingBuy={self.pending_buy} AND position_size={position_size} <= 0 → {should_execute}")
        
        return should_execute
    
    def should_execute_sell(self, position_size: float) -> bool:
        """Verifica se deve executar SELL (Pine linha 126)"""
        # Pine: if (pendingSell and strategy.position_size >= 0)
        should_execute = self.pending_sell and position_size >= 0
        
        logger.debug(f"   Condição SELL: pendingSell={self.pending_sell} AND position_size={position_size} >= 0 → {should_execute}")
        
        return should_execute
    
    def clear_flags(self):
        """Limpa todas as flags (para reset)"""
        self.pending_buy = False
        self.pending_sell = False
        self.pending_buy_prev = False
        self.pending_sell_prev = False
        self.buy_signal_prev = False
        self.sell_signal_prev = False
        
        logger.info("🧹 Flags limpas (reset completo)")
