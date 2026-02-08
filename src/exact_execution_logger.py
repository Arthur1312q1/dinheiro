#!/usr/bin/env python3
"""
EXACT_EXECUTION_LOGGER.py - Logger para fluxo temporal exato (ATUALIZADO)
"""
import logging
import json
import os
from datetime import datetime
import pytz

class ExactExecutionLogger:
    """Registra cada etapa do fluxo exato para comparação"""
    
    def __init__(self, log_dir=None):
        self.tz_utc = pytz.utc
        
        if log_dir is None:
            if os.path.exists('/data'):
                self.log_dir = "/data/exact_execution_logs"
            else:
                self.log_dir = "exact_execution_logs"
        else:
            self.log_dir = log_dir
        
        os.makedirs(self.log_dir, exist_ok=True)
        
        self.current_date = datetime.now(self.tz_utc).strftime('%Y-%m-%d')
        self.log_file = os.path.join(self.log_dir, f"exact_execution_{self.current_date}.log")
        
        self.logger = logging.getLogger('exact_execution')
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            
            formatter = logging.Formatter('%(message)s')
            file_handler.setFormatter(formatter)
            
            self.logger.addHandler(file_handler)
        
        self._write_header()
    
    def _write_header(self):
        """Escreve cabeçalho"""
        header = """
================================================================================
EXECUÇÃO EXATA - FLUXO IDÊNTICO AO TRADINGVIEW
Estratégia: Adaptive Zero Lag EMA v2
Timeframe: 30min
Parâmetros: Period=20, GainLimit=900, Threshold=0.0, fixedSL=2000, fixedTP=55, risk=0.01
================================================================================
FORMATO:
[TIMESTAMP] EVENTO
  Detalhes...
================================================================================
"""
        self.logger.info(header.strip())
    
    def log_bar_close(self, timestamp, bar_number, close_price, signals, next_flags, executed_trades=None):
        """Registra fechamento de barra"""
        try:
            log_lines = [
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] BARRA #{bar_number} FECHANDO",
                f"  Preço Fechamento: ${close_price:.2f}",
                f"  Sinais calculados (barra N): buy_signal={signals['buy_signal']}, sell_signal={signals['sell_signal']}",
                f"  Flags atualizadas: pendingBuy={next_flags['pending_buy']}, pendingSell={next_flags['pending_sell']}",
                f"  Sinais [1] usados: buy={next_flags.get('buy_signal_prev', 'N/A')}, sell={next_flags.get('sell_signal_prev', 'N/A')}",
                f"  Nota: Flags setadas com sinais da barra N-1, execução IMEDIATA se condições atendidas"
            ]
            
            if executed_trades:
                for trade in executed_trades:
                    log_lines.append(f"  🎯 Trade executado: {trade['side'].upper()} {trade['quantity']:.4f} ETH @ ${trade['price']:.2f}")
            else:
                log_lines.append(f"  ⏭️ Nenhum trade executado (condições não atendidas)")
            
            log_lines.append("-" * 70)
            
            for line in log_lines:
                self.logger.info(line)
            
            print(f"\n📊 FECHAMENTO Barra #{bar_number}:")
            print(f"   Preço: ${close_price:.2f}")
            print(f"   Sinais: buy={signals['buy_signal']}, sell={signals['sell_signal']}")
            print(f"   Flags: buy={next_flags['pending_buy']}, sell={next_flags['pending_sell']}")
            if executed_trades:
                for trade in executed_trades:
                    print(f"   Trade: {trade['side'].upper()} {trade['quantity']:.4f} ETH")
            
        except Exception as e:
            print(f"❌ Erro log fechamento: {e}")
    
    def log_bar_open(self, timestamp, bar_number, open_price, flags_state, position_size):
        """Registra abertura de barra"""
        try:
            position_str = f"{abs(position_size):.4f} ETH {'LONG' if position_size > 0 else 'SHORT' if position_size < 0 else 'FLAT'}"
            
            log_lines = [
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] BARRA #{bar_number} ABRINDO",
                f"  Preço Abertura: ${open_price:.2f}",
                f"  Flags recebidas: pendingBuy={flags_state['pending_buy']}, pendingSell={flags_state['pending_sell']}",
                f"  Posição atual: {position_str}",
                f"  Sinais [1] atuais: buy={flags_state.get('buy_signal_prev', False)}, sell={flags_state.get('sell_signal_prev', False)}"
            ]
            
            log_lines.append("-" * 70)
            
            for line in log_lines:
                self.logger.info(line)
            
            print(f"\n📊 ABERTURA Barra #{bar_number}:")
            print(f"   Preço: ${open_price:.2f}")
            print(f"   Flags: buy={flags_state['pending_buy']}, sell={flags_state['pending_sell']}")
            print(f"   Posição: {position_str}")
            
        except Exception as e:
            print(f"❌ Erro log abertura: {e}")
    
    def log_trade_execution(self, trade_data):
        """Registra execução de trade"""
        try:
            timestamp = datetime.now(self.tz_utc)
            timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + " UTC"
            
            log_entry = f"""
[{timestamp_str}] 🎯 EXECUÇÃO DE TRADE
  Tipo: {trade_data['side'].upper()}
  Preço: ${trade_data['price']:.2f}
  Quantidade: {trade_data['quantity']:.4f} ETH
  Balance antes: ${trade_data['balance_before']:.2f}
  Balance após: ${trade_data['balance_after']:.2f}
  Motivo: {trade_data.get('reason', 'N/A')}
"""
            self.logger.info(log_entry.strip())
            
            print(f"\n🎯 TRADE EXECUTADO: {trade_data['side'].upper()} {trade_data['quantity']:.4f} ETH @ ${trade_data['price']:.2f}")
            
        except Exception as e:
            print(f"❌ Erro log trade: {e}")
    
    def log_flag_update(self, timestamp, buy_signal_prev, sell_signal_prev, new_flags):
        """Registra atualização de flags"""
        try:
            log_entry = f"""
[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] 🏁 ATUALIZAÇÃO DE FLAGS
  Sinais [1] usados: buy_signal[1]={buy_signal_prev}, sell_signal[1]={sell_signal_prev}
  Flags antes: pendingBuy[1]={new_flags.get('pending_buy_prev', 'N/A')}, pendingSell[1]={new_flags.get('pending_sell_prev', 'N/A')}
  Flags depois: pendingBuy={new_flags['pending_buy']}, pendingSell={new_flags['pending_sell']}
  Regra: pendingBuy := nz(pendingBuy[1]); if (buy_signal[1]) pendingBuy := true
"""
            self.logger.info(log_entry.strip())
            
        except Exception as e:
            print(f"❌ Erro log flags: {e}")
    
    def log_condition_check(self, timestamp, condition_type, pending_flag, position_size, result):
        """Registra verificação de condição"""
        try:
            log_entry = f"""
[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] 🔍 VERIFICAÇÃO {condition_type.upper()}
  Condição: pending{condition_type.upper()}={pending_flag} AND position_size={'<= 0' if condition_type == 'buy' else '>= 0'}
  Position size: {position_size:.4f}
  Resultado: {'✅ EXECUTAR' if result else '⏭️ IGNORAR'}
"""
            self.logger.info(log_entry.strip())
            
        except Exception as e:
            print(f"❌ Erro log condição: {e}")
