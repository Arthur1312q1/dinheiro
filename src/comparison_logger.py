#!/usr/bin/env python3
"""
COMPARISON_LOGGER.PY - Sistema de logs estruturados para comparação com TradingView
"""
import logging
import json
import os
from datetime import datetime
import pytz

class ComparisonLogger:
    """Gera logs formatados para comparação manual com TradingView"""
    
    def __init__(self, log_dir=None):
        self.tz_utc = pytz.utc
        
        # Configurar diretório de logs
        if log_dir is None:
            if os.path.exists('/data'):
                self.log_dir = "/data/comparison_logs"
            else:
                self.log_dir = "comparison_logs"
        else:
            self.log_dir = log_dir
        
        # Criar diretório se não existir
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Arquivo de log atual (por dia)
        self.current_date = datetime.now(self.tz_utc).strftime('%Y-%m-%d')
        self.log_file = os.path.join(self.log_dir, f"comparison_{self.current_date}.log")
        
        # Configurar logger separado
        self.logger = logging.getLogger('comparison')
        self.logger.setLevel(logging.INFO)
        
        # Evitar handlers duplicados
        if not self.logger.handlers:
            # Handler para arquivo
            file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            
            # Formato específico para comparação
            formatter = logging.Formatter(
                '%(message)s'
            )
            file_handler.setFormatter(formatter)
            
            self.logger.addHandler(file_handler)
        
        # Inicializar arquivo de log
        self._write_header()
    
    def _write_header(self):
        """Escreve cabeçalho no arquivo de log"""
        header = """
================================================================================
COMPARAÇÃO BOT vs TRADINGVIEW
Estratégia: Adaptive Zero Lag EMA v2
Timeframe: 30min
Parâmetros: Period=20, GainLimit=900, Threshold=0.0, fixedSL=2000, fixedTP=55, risk=0.01
================================================================================
FORMATO:
[TIMESTAMP] BARRA #N
  Preço: Aberta=XXXX.XX, Fechamento=XXXX.XX
  EC=XXXX.XX, EMA=XXXX.XX, LeastError=X.XX, Error%=X.XXX%
  Sinais: buy_signal=BOOL, sell_signal=BOOL
  Pending: pendingBuy=BOOL, pendingSell=BOOL
  Position: size=X.XXXX, side=STRING
  Balance: $XXXX.XX
================================================================================
"""
        self.logger.info(header.strip())
    
    def log_bar_data(self, bar_data: dict):
        """
        Registra dados de uma barra completa
        
        bar_data deve conter:
        {
            'timestamp': datetime,
            'bar_number': int,
            'open_price': float,
            'close_price': float,
            'ec_value': float,
            'ema_value': float,
            'least_error': float,
            'error_percent': float,
            'buy_signal': bool,
            'sell_signal': bool,
            'pending_buy': bool,
            'pending_sell': bool,
            'position_size': float,
            'position_side': str,
            'balance': float,
            'notes': str (opcional)
        }
        """
        try:
            timestamp_str = bar_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')
            
            log_lines = [
                f"[{timestamp_str}] BARRA #{bar_data['bar_number']}",
                f"  Preço: Aberta={bar_data['open_price']:.2f}, Fechamento={bar_data['close_price']:.2f}",
                f"  EC={bar_data['ec_value']:.6f}, EMA={bar_data['ema_value']:.6f}, "
                f"LeastError={bar_data['least_error']:.6f}, Error%={bar_data['error_percent']:.6f}%",
                f"  Sinais: buy_signal={bar_data['buy_signal']}, sell_signal={bar_data['sell_signal']}",
                f"  Pending: pendingBuy={bar_data['pending_buy']}, pendingSell={bar_data['pending_sell']}",
                f"  Position: size={bar_data['position_size']:.4f}, side={bar_data['position_side']}",
                f"  Balance: ${bar_data['balance']:.2f}"
            ]
            
            if 'notes' in bar_data and bar_data['notes']:
                log_lines.append(f"  Notas: {bar_data['notes']}")
            
            # Adicionar linha separadora
            log_lines.append("-" * 80)
            
            # Escrever no log
            for line in log_lines:
                self.logger.info(line)
            
            # Também imprimir no console para debug
            print(f"\n📊 LOG COMPARAÇÃO Barra #{bar_data['bar_number']}:")
            print(f"   Sinais: buy={bar_data['buy_signal']}, sell={bar_data['sell_signal']}")
            print(f"   Pending: buy={bar_data['pending_buy']}, sell={bar_data['pending_sell']}")
            print(f"   Position: {bar_data['position_side']} {bar_data['position_size']:.4f}")
            
            return True
            
        except Exception as e:
            print(f"❌ Erro ao registrar log de comparação: {e}")
            return False
    
    def log_trade_execution(self, trade_data: dict):
        """Registra execução de trade"""
        timestamp = datetime.now(self.tz_utc)
        timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + " UTC"
        
        log_entry = (
            f"[{timestamp_str}] 🎯 EXECUÇÃO DE TRADE\n"
            f"  Tipo: {trade_data['side'].upper()}\n"
            f"  Preço: ${trade_data['price']:.2f}\n"
            f"  Quantidade: {trade_data['quantity']:.4f} ETH\n"
            f"  Balance antes: ${trade_data['balance_before']:.2f}\n"
            f"  Balance após: ${trade_data['balance_after']:.2f}\n"
            f"  Motivo: {trade_data.get('reason', 'N/A')}"
        )
        
        self.logger.info(log_entry)
        print(f"\n🎯 TRADE EXECUTADO: {trade_data['side'].upper()} {trade_data['quantity']:.4f} ETH @ ${trade_data['price']:.2f}")
    
    def log_trailing_stop(self, trailing_data: dict):
        """Registra atualização de trailing stop"""
        timestamp = datetime.now(self.tz_utc)
        timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + " UTC"
        
        log_entry = (
            f"[{timestamp_str}] 🎯 TRAILING STOP\n"
            f"  Lado: {trailing_data['side'].upper()}\n"
            f"  Preço atual: ${trailing_data['current_price']:.2f}\n"
            f"  Best Price: ${trailing_data['best_price']:.2f}\n"
            f"  Stop atual: ${trailing_data['current_stop']:.2f}\n"
            f"  Trailing ativado: {trailing_data['trailing_activated']}\n"
            f"  Motivo: {trailing_data.get('reason', 'N/A')}"
        )
        
        self.logger.info(log_entry)
    
    def export_comparison_data(self, output_file=None):
        """Exporta dados de comparação em formato JSON"""
        if output_file is None:
            output_file = os.path.join(self.log_dir, f"comparison_data_{self.current_date}.json")
        
        try:
            # Ler arquivo de log e converter para JSON estruturado
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Processar logs (implementação básica)
            comparison_data = {
                'export_date': datetime.now(self.tz_utc).isoformat(),
                'total_bars': 0,
                'bars': []
            }
            
            # Aqui você implementaria a lógica para parser dos logs
            # Para simplificar, vamos apenas criar um arquivo vazio
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(comparison_data, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Dados de comparação exportados: {output_file}")
            return output_file
            
        except Exception as e:
            print(f"❌ Erro ao exportar dados: {e}")
            return None
