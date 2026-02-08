#!/usr/bin/env python3
"""
EXACT_EXECUTION_LOGGER.PY - Logger para fluxo temporal exato
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
EXECUÇÃO EXATA - FLUXO TEMPORAL CORRETO
Fechamento → Processamento → Execução na abertura seguinte
================================================================================
FORMATO:
[TIMESTAMP] EVENTO
  Detalhes...
================================================================================
"""
        self.logger.info(header.strip())
    
    def log_bar_close(self, timestamp, bar_number, close_price, signals, next_flags):
        """Registra fechamento de barra"""
        try:
            log_lines = [
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] BARRA #{bar_number} FECHANDO",
                f"  Preço Fechamento: ${close_price:.2f}",
                f"  Sinais calculados: buy_signal={signals['buy_signal']}, sell_signal={signals['sell_signal']}",
                f"  Flags para próxima barra: pendingBuy={next_flags['pending_buy']}, pendingSell={next_flags['pending_sell']}",
                f"  Nota: Sinais serão executados na ABERTURA da próxima barra",
                "-" * 70
            ]
            
            for line in log_lines:
                self.logger.info(line)
            
            print(f"\n📊 FECHAMENTO Barra #{bar_number}:")
            print(f"   Sinais: buy={signals['buy_signal']}, sell={signals['sell_signal']}")
            print(f"   Flags próximas: buy={next_flags['pending_buy']}, sell={next_flags['pending_sell']}")
            
        except Exception as e:
            print(f"❌ Erro log fechamento: {e}")
    
    def log_bar_open(self, timestamp, bar_number, open_price, received_flags, position_size, executed):
        """Registra abertura de barra"""
        try:
            log_lines = [
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] BARRA #{bar_number} ABRINDO",
                f"  Preço Abertura: ${open_price:.2f}",
                f"  Flags recebidas: pendingBuy={received_flags['pending_buy']}, pendingSell={received_flags['pending_sell']}",
                f"  Posição atual: size={position_size:.4f}, side={'LONG' if position_size > 0 else 'SHORT' if position_size < 0 else 'FLAT'}"
            ]
            
            if executed:
                for trade in executed:
                    log_lines.append(f"  Execução: {trade['side'].upper()} {trade['quantity']:.4f} ETH @ ${trade['price']:.2f}")
                    log_lines.append(f"    Condição: {trade['condition']}")
            else:
                log_lines.append(f"  Execução: Nenhuma (condições não atendidas)")
            
            log_lines.append("-" * 70)
            
            for line in log_lines:
                self.logger.info(line)
            
            print(f"\n📊 ABERTURA Barra #{bar_number}:")
            print(f"   Flags: buy={received_flags['pending_buy']}, sell={received_flags['pending_sell']}")
            if executed:
                for trade in executed:
                    print(f"   Trade: {trade['side'].upper()} {trade['quantity']:.4f} ETH")
            
        except Exception as e:
            print(f"❌ Erro log abertura: {e}")
    
    def log_flag_update(self, timestamp, buy_signal_prev, sell_signal_prev, new_flags):
        """Registra atualização de flags"""
        try:
            log_entry = f"""
[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC] 🏁 ATUALIZAÇÃO DE FLAGS
  Sinais [1]: buy_signal[1]={buy_signal_prev}, sell_signal[1]={sell_signal_prev}
  Novo estado: pendingBuy={new_flags['pending_buy']}, pendingSell={new_flags['pending_sell']}
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
