# O sistema de histórico está implementado diretamente no main.py
# Este arquivo é mantido apenas para compatibilidade

class TradeHistory:
    def __init__(self, file_path=None):
        pass
    
    def add_trade(self, side, entry_price, quantity):
        return None
    
    def close_trade(self, trade_id, exit_price):
        return False
    
    def get_all_trades(self, limit=50):
        return []
    
    def get_stats(self):
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'total_pnl_percent': 0,
            'total_pnl_usdt': 0
        }
