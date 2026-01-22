"""
Módulo para gerenciar o histórico de operações do bot de trading
Armazena todas as trades simuladas com detalhes completos
"""
import json
import os
from datetime import datetime
import pytz
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class TradeHistory:
    """Gerencia o histórico de operações de trading"""
    
    def __init__(self, file_path="data/trade_history.json"):
        self.file_path = file_path
        self.trades = []
        self.load_trades()
        
        # Configurar timezone do Brasil
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
    
    def _ensure_data_directory(self):
        """Garante que o diretório de dados existe"""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
    
    def get_brazil_time(self):
        """Retorna o horário atual no fuso do Brasil"""
        return datetime.now(self.tz_brazil)
    
    def format_datetime(self, dt):
        """Formata datetime para exibição amigável"""
        return dt.strftime('%d/%m/%Y %H:%M:%S')
    
    def calculate_pnl_percent(self, entry_price: float, exit_price: float, side: str) -> float:
        """
        Calcula o percentual de lucro/prejuízo
        side: 'buy' (long) ou 'sell' (short)
        """
        if side == 'buy':
            # Para posição long: (saída - entrada) / entrada
            return ((exit_price - entry_price) / entry_price) * 100
        else:
            # Para posição short: (entrada - saída) / entrada
            return ((entry_price - exit_price) / entry_price) * 100
    
    def calculate_pnl_usdt(self, entry_price: float, quantity: float, pnl_percent: float) -> float:
        """Calcula o PnL em USDT"""
        position_value = entry_price * quantity
        return (position_value * pnl_percent) / 100
    
    def add_trade(self, trade_data: Dict):
        """Adiciona uma nova trade ao histórico"""
        try:
            # Gerar ID único
            trade_id = len(self.trades) + 1
            
            # Garantir que temos timestamp do Brasil
            if 'entry_time' not in trade_data:
                trade_data['entry_time'] = self.get_brazil_time()
            
            # Formatar horário se for datetime
            if isinstance(trade_data['entry_time'], datetime):
                trade_data['entry_time_str'] = self.format_datetime(trade_data['entry_time'])
            else:
                trade_data['entry_time_str'] = trade_data['entry_time']
            
            # Adicionar campos padrão
            trade_data.update({
                'id': trade_id,
                'status': 'open',
                'pnl_percent': 0.0,
                'pnl_usdt': 0.0,
                'exit_time_str': None,
                'duration': None
            })
            
            # Salvar a trade
            self.trades.append(trade_data)
            self.save_trades()
            
            logger.info(f"📝 Trade #{trade_id} registrada: {trade_data['side'].upper()} {trade_data['quantity']:.4f} ETH @ ${trade_data['entry_price']:.2f}")
            return trade_id
            
        except Exception as e:
            logger.error(f"Erro ao adicionar trade: {e}")
            return None
    
    def close_trade(self, trade_id: int, exit_price: float):
        """Fecha uma trade existente"""
        try:
            for trade in self.trades:
                if trade['id'] == trade_id and trade['status'] == 'open':
                    exit_time = self.get_brazil_time()
                    
                    # Calcular PnL
                    trade['exit_price'] = exit_price
                    trade['pnl_percent'] = self.calculate_pnl_percent(
                        trade['entry_price'], 
                        exit_price, 
                        trade['side']
                    )
                    
                    # Calcular PnL em USDT
                    trade['pnl_usdt'] = self.calculate_pnl_usdt(
                        trade['entry_price'],
                        trade['quantity'],
                        trade['pnl_percent']
                    )
                    
                    # Atualizar outros campos
                    trade['exit_time'] = exit_time
                    trade['exit_time_str'] = self.format_datetime(exit_time)
                    trade['status'] = 'closed'
                    
                    # Calcular duração
                    if 'entry_time' in trade and isinstance(trade['entry_time'], datetime):
                        duration = (exit_time - trade['entry_time']).total_seconds()
                        trade['duration'] = self._format_duration(duration)
                    
                    self.save_trades()
                    
                    # Determinar emoji baseado no resultado
                    emoji = "✅" if trade['pnl_percent'] > 0 else "❌" if trade['pnl_percent'] < 0 else "➖"
                    
                    logger.info(f"{emoji} Trade #{trade_id} fechada: PnL {trade['pnl_percent']:.4f}% (${trade['pnl_usdt']:.2f})")
                    return True
                    
            return False
            
        except Exception as e:
            logger.error(f"Erro ao fechar trade #{trade_id}: {e}")
            return False
    
    def _format_duration(self, seconds: float) -> str:
        """Formata duração em formato legível"""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.2f}h"
    
    def get_all_trades(self, limit: int = 50) -> List[Dict]:
        """Retorna todas as trades (mais recentes primeiro)"""
        # Ordenar por ID decrescente (mais recentes primeiro)
        sorted_trades = sorted(self.trades, key=lambda x: x['id'], reverse=True)
        return sorted_trades[:limit]
    
    def get_open_trades(self) -> List[Dict]:
        """Retorna apenas trades abertas"""
        return [trade for trade in self.trades if trade['status'] == 'open']
    
    def get_closed_trades(self) -> List[Dict]:
        """Retorna apenas trades fechadas"""
        return [trade for trade in self.trades if trade['status'] == 'closed']
    
    def get_stats(self) -> Dict:
        """Retorna estatísticas do histórico"""
        closed_trades = self.get_closed_trades()
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl_percent': 0,
                'total_pnl_usdt': 0,
                'avg_pnl_percent': 0,
                'best_trade': None,
                'worst_trade': None
            }
        
        winning = [t for t in closed_trades if t['pnl_percent'] > 0]
        losing = [t for t in closed_trades if t['pnl_percent'] < 0]
        
        total_pnl_percent = sum(t['pnl_percent'] for t in closed_trades)
        total_pnl_usdt = sum(t['pnl_usdt'] for t in closed_trades)
        
        # Melhor e pior trade
        best_trade = max(closed_trades, key=lambda x: x['pnl_percent']) if closed_trades else None
        worst_trade = min(closed_trades, key=lambda x: x['pnl_percent']) if closed_trades else None
        
        return {
            'total_trades': len(closed_trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': (len(winning) / len(closed_trades)) * 100 if closed_trades else 0,
            'total_pnl_percent': total_pnl_percent,
            'total_pnl_usdt': total_pnl_usdt,
            'avg_pnl_percent': total_pnl_percent / len(closed_trades) if closed_trades else 0,
            'best_trade': best_trade,
            'worst_trade': worst_trade
        }
    
    def save_trades(self):
        """Salva as trades no arquivo JSON"""
        try:
            self._ensure_data_directory()
            
            # Converter datetimes para string
            trades_to_save = []
            for trade in self.trades:
                trade_copy = trade.copy()
                
                # Converter datetime para string para JSON
                for key in ['entry_time', 'exit_time']:
                    if key in trade_copy and isinstance(trade_copy[key], datetime):
                        trade_copy[key] = trade_copy[key].isoformat()
                
                trades_to_save.append(trade_copy)
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(trades_to_save, f, indent=2, ensure_ascii=False)
                
            logger.info(f"💾 Histórico salvo: {len(trades_to_save)} trades")
            
        except Exception as e:
            logger.error(f"Erro ao salvar histórico: {e}")
    
    def load_trades(self):
        """Carrega trades do arquivo JSON"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    trades_data = json.load(f)
                
                # Converter strings de volta para datetime
                for trade in trades_data:
                    for key in ['entry_time', 'exit_time']:
                        if key in trade and trade[key]:
                            try:
                                trade[key] = datetime.fromisoformat(trade[key])
                            except:
                                pass
                
                self.trades = trades_data
                logger.info(f"📂 Histórico carregado: {len(self.trades)} trades")
            else:
                self.trades = []
                logger.info("📂 Nenhum histórico encontrado, iniciando novo")
                
        except Exception as e:
            logger.error(f"Erro ao carregar histórico: {e}")
            self.trades = []
    
    def clear_history(self):
        """Limpa todo o histórico"""
        self.trades = []
        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        logger.info("🗑️ Histórico limpo")
