"""
Sistema de histórico de operações
"""
import json
import os
import pytz
from datetime import datetime
from typing import List, Dict

class TradeHistory:
    def __init__(self, file_path=None):
        # Configurar timezone do Brasil
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Determinar caminho do arquivo
        if file_path is None:
            # Se estiver no Render, usar /data, senão usar pasta local
            if os.path.exists('/data'):
                self.file_path = "/data/trade_history.json"
            else:
                # Voltar um nível (para raiz) e criar pasta data
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                data_dir = os.path.join(base_dir, 'data')
                if not os.path.exists(data_dir):
                    os.makedirs(data_dir)
                self.file_path = os.path.join(data_dir, 'trade_history.json')
        else:
            self.file_path = file_path
        
        self.trades = []
        self.load_trades()
    
    def get_brazil_time(self):
        """Retorna horário atual no fuso do Brasil"""
        return datetime.now(self.tz_brazil)
    
    def add_trade(self, side, entry_price, quantity):
        """Registra uma nova trade"""
        try:
            trade_id = len(self.trades) + 1
            entry_time = self.get_brazil_time()
            
            trade = {
                'id': trade_id,
                'side': side,
                'entry_price': entry_price,
                'quantity': quantity,
                'entry_time': entry_time.isoformat(),
                'entry_time_str': entry_time.strftime('%d/%m/%Y %H:%M:%S'),
                'exit_price': None,
                'exit_time': None,
                'pnl_percent': 0.0,
                'pnl_usdt': 0.0,
                'status': 'open',
                'duration': None
            }
            
            self.trades.append(trade)
            self.save_trades()
            return trade_id
            
        except Exception as e:
            import logging
            logging.error(f"Erro ao registrar trade: {e}")
            return None
    
    def close_trade(self, trade_id, exit_price):
        """Fecha uma trade existente"""
        try:
            for trade in self.trades:
                if trade['id'] == trade_id and trade['status'] == 'open':
                    exit_time = self.get_brazil_time()
                    entry_price = trade['entry_price']
                    
                    # Calcular PnL
                    if trade['side'] == 'buy':
                        pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                    else:  # sell
                        pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                    
                    pnl_usdt = (entry_price * trade['quantity'] * pnl_percent) / 100
                    
                    # Calcular duração
                    entry_time = datetime.fromisoformat(trade['entry_time'].replace('Z', '+00:00'))
                    duration_seconds = (exit_time - entry_time).total_seconds()
                    
                    # Formatar duração
                    if duration_seconds < 60:
                        duration = f"{duration_seconds:.0f}s"
                    elif duration_seconds < 3600:
                        duration = f"{duration_seconds/60:.1f}m"
                    else:
                        duration = f"{duration_seconds/3600:.2f}h"
                    
                    # Atualizar trade
                    trade['exit_price'] = exit_price
                    trade['exit_time'] = exit_time.isoformat()
                    trade['exit_time_str'] = exit_time.strftime('%d/%m/%Y %H:%M:%S')
                    trade['pnl_percent'] = round(pnl_percent, 4)
                    trade['pnl_usdt'] = round(pnl_usdt, 2)
                    trade['status'] = 'closed'
                    trade['duration'] = duration
                    
                    self.save_trades()
                    return True
            
            return False
        except Exception as e:
            import logging
            logging.error(f"Erro ao fechar trade: {e}")
            return False
    
    def get_all_trades(self, limit=50):
        """Retorna todas as trades (mais recentes primeiro)"""
        return sorted(self.trades, key=lambda x: x['id'], reverse=True)[:limit]
    
    def get_stats(self):
        """Retorna estatísticas do histórico"""
        closed_trades = [t for t in self.trades if t['status'] == 'closed']
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl_percent': 0,
                'total_pnl_usdt': 0
            }
        
        winning = [t for t in closed_trades if t['pnl_percent'] > 0]
        losing = [t for t in closed_trades if t['pnl_percent'] < 0]
        
        total_pnl_percent = sum(t['pnl_percent'] for t in closed_trades)
        total_pnl_usdt = sum(t['pnl_usdt'] for t in closed_trades)
        
        return {
            'total_trades': len(closed_trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': (len(winning) / len(closed_trades)) * 100,
            'total_pnl_percent': round(total_pnl_percent, 4),
            'total_pnl_usdt': round(total_pnl_usdt, 2)
        }
    
    def save_trades(self):
        """Salva trades em arquivo JSON"""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            import logging
            logging.error(f"Erro ao salvar histórico: {e}")
    
    def load_trades(self):
        """Carrega trades do arquivo"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self.trades = json.load(f)
        except:
            self.trades = []
    
    def clear_history(self):
        """Limpa todo o histórico"""
        self.trades = []
        if os.path.exists(self.file_path):
            os.remove(self.file_path)
