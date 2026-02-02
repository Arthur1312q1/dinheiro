"""
Sistema de Trailing Stop exatamente como no Pine Script
"""
import logging

logger = logging.getLogger(__name__)

class TrailingStopManager:
    def __init__(self, trail_points=55, trail_offset=15, mintick=0.01):
        """
        trail_points: fixedTP do Pine Script (55)
        trail_offset: 15 (valor fixo no strategy.exit)
        mintick: syminfo.mintick (0.01 para ETH/USDT)
        """
        self.trail_points = trail_points
        self.trail_offset = trail_offset
        self.mintick = mintick
        
        # Estado
        self.entry_price = None
        self.position_side = None
        self.activated = False
        self.current_stop = None
        self.best_price = None
        
        logger.info(f"✅ TrailingStopManager: TP={trail_points}p, Offset={trail_offset}p, Tick={mintick}")
    
    def on_entry(self, entry_price: float, position_side: str):
        """Inicializa trailing stop quando posição é aberta"""
        self.entry_price = entry_price
        self.position_side = position_side
        self.activated = False
        self.best_price = entry_price
        
        # Stop inicial (com trail_offset)
        if position_side == 'long':
            self.current_stop = entry_price - (self.trail_offset * self.mintick)
        else:  # short
            self.current_stop = entry_price + (self.trail_offset * self.mintick)
        
        logger.info(f"📊 TrailingStop inicializado: {position_side} @ ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f} (offset: {self.trail_offset}p)")
    
    def update(self, current_price: float):
        """Atualiza trailing stop com novo preço (chamar a cada tick)"""
        if self.entry_price is None:
            return
        
        # Atualizar melhor preço
        if self.position_side == 'long':
            if current_price > self.best_price:
                self.best_price = current_price
                # Ativar trailing quando preço se move a favor
                if not self.activated:
                    profit_points = (current_price - self.entry_price) / self.mintick
                    if profit_points >= 0:  # Ativa imediatamente para LONG
                        self.activated = True
                        logger.info(f"🎯 Trailing ativado (LONG): ${current_price:.2f}")
                
                # Atualizar stop se ativado
                if self.activated:
                    new_stop = current_price - (self.trail_points * self.mintick)
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                        logger.info(f"📈 Trailing atualizado (LONG): ${self.current_stop:.2f}")
        
        elif self.position_side == 'short':
            if current_price < self.best_price:
                self.best_price = current_price
                # Ativar trailing quando preço se move a favor
                if not self.activated:
                    profit_points = (self.entry_price - current_price) / self.mintick
                    if profit_points >= 0:  # Ativa imediatamente para SHORT
                        self.activated = True
                        logger.info(f"🎯 Trailing ativado (SHORT): ${current_price:.2f}")
                
                # Atualizar stop se ativado
                if self.activated:
                    new_stop = current_price + (self.trail_points * self.mintick)
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
                        logger.info(f"📉 Trailing atualizado (SHORT): ${self.current_stop:.2f}")
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição por trailing stop"""
        if self.current_stop is None:
            return False
        
        if self.position_side == 'long':
            return current_price <= self.current_stop
        else:  # short
            return current_price >= self.current_stop
    
    def get_stop_price(self) -> float:
        """Retorna preço atual do stop"""
        return self.current_stop
    
    def reset(self):
        """Reseta o gerenciador"""
        self.entry_price = None
        self.position_side = None
        self.activated = False
        self.current_stop = None
        self.best_price = None
    
    def get_status(self):
        """Retorna status para debug"""
        return {
            'entry_price': self.entry_price,
            'position_side': self.position_side,
            'activated': self.activated,
            'current_stop': self.current_stop,
            'best_price': self.best_price,
            'trail_points': self.trail_points,
            'trail_offset': self.trail_offset
        }
