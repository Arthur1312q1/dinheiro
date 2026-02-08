#!/usr/bin/env python3
"""
BALANCE_MANAGER.PY - Balance dinâmico EXATO como Pine Script
"""
import logging
import json
import os
import time

logger = logging.getLogger(__name__)

class ExactBalanceManager:
    """Gerenciador de balance que replica EXATAMENTE o TradingView"""
    
    def __init__(self, initial_capital=1000.0, file_path=None):
        # EXATO: strategy.initial_capital
        self.initial_capital = initial_capital
        
        # EXATO: strategy.netprofit
        self.netprofit = 0.0
        
        self.trade_count = 0
        self.closed_trades_pnl = []  # Histórico de PnL
        
        if file_path is None:
            if os.path.exists('/data'):
                self.file_path = "/data/exact_balance.json"
            else:
                self.file_path = "exact_balance.json"
        else:
            self.file_path = file_path
        
        self.load_balance()
        
        logger.info("💰 ExactBalanceManager inicializado")
        logger.info(f"   Initial Capital: ${self.initial_capital:.2f} (EXATO Pine)")
        logger.info(f"   Net Profit: ${self.netprofit:.2f} (EXATO Pine)")
        logger.info(f"   Current Balance: ${self.get_balance():.2f}")
    
    def get_balance(self):
        """
        EXATAMENTE: balance = strategy.initial_capital + strategy.netprofit
        Retorna o balance ATUAL (dinâmico)
        """
        return self.initial_capital + self.netprofit
    
    def update_from_closed_trade(self, trade_result: dict):
        """
        Atualiza netprofit com PnL de trade fechado
        trade_result deve conter: {'pnl_usdt': float, 'trade_id': int}
        
        EXATO: strategy.netprofit acumula PnL de todos os trades fechados
        """
        old_balance = self.get_balance()
        old_netprofit = self.netprofit
        
        pnl_usdt = trade_result['pnl_usdt']
        self.netprofit += pnl_usdt
        self.trade_count += 1
        
        # Registrar no histórico
        self.closed_trades_pnl.append({
            'trade_id': trade_result.get('trade_id'),
            'pnl_usdt': pnl_usdt,
            'timestamp': time.time()
        })
        
        new_balance = self.get_balance()
        
        # Log EXATO como TradingView
        logger.info("=" * 60)
        logger.info("💰 ATUALIZAÇÃO EXATA DE BALANCE (Pine Script)")
        logger.info(f"   Trade ID: {trade_result.get('trade_id', 'N/A')}")
        logger.info(f"   PnL do trade: ${pnl_usdt:.2f}")
        logger.info(f"   Net Profit anterior: ${old_netprofit:.2f}")
        logger.info(f"   Net Profit atual: ${self.netprofit:.2f}")
        logger.info(f"   Balance anterior: ${old_balance:.2f}")
        logger.info(f"   Balance atual: ${new_balance:.2f}")
        logger.info(f"   FÓRMULA EXATA: {new_balance:.2f} = {self.initial_capital:.2f} + {self.netprofit:.2f}")
        logger.info("=" * 60)
        
        self.save_balance()
        
        return new_balance
    
    def calculate_exact_position_size(self, risk_percent: float, fixed_sl_points: int, 
                                     mintick: float = 0.01, limit: int = 100) -> float:
        """
        Calcula tamanho da posição EXATAMENTE como Pine Script linhas 108-110:
        
        riskAmount = risk * balance
        stopLossUSDT = fixedSL * syminfo.mintick
        lots = riskAmount / stopLossUSDT
        """
        balance = self.get_balance()
        
        if balance <= 0:
            logger.error(f"❌ Balance inválido para cálculo: ${balance:.2f}")
            return 0.0
        
        # riskAmount = risk * balance
        risk_amount = risk_percent * balance
        
        # stopLossUSDT = fixedSL * syminfo.mintick
        stop_loss_usdt = fixed_sl_points * mintick
        
        if stop_loss_usdt <= 0:
            logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
            return 0.0
        
        # lots = riskAmount / stopLossUSDT
        quantity = risk_amount / stop_loss_usdt
        
        # Aplicar limite máximo (input 'limit' do Pine)
        if quantity > limit:
            quantity = limit
        
        # Arredondar para 4 casas decimais (ETH)
        quantity = round(quantity, 4)
        
        # Log detalhado EXATO
        logger.info("   📊 CÁLCULO EXATO DE POSIÇÃO (Pine Script):")
        logger.info(f"     Balance atual: ${balance:.2f}")
        logger.info(f"     Risk: {risk_percent*100}% = ${risk_amount:.2f}")
        logger.info(f"     Stop Loss: {fixed_sl_points}p = ${stop_loss_usdt:.2f}")
        logger.info(f"     Quantidade calculada: {quantity:.6f} ETH")
        logger.info(f"     Limit máximo: {limit} ETH")
        
        return quantity
    
    def get_stats(self):
        """Retorna estatísticas EXATAS"""
        return {
            'initial_capital': self.initial_capital,
            'netprofit': self.netprofit,
            'current_balance': self.get_balance(),
            'trade_count': self.trade_count,
            'avg_pnl_per_trade': self.netprofit / self.trade_count if self.trade_count > 0 else 0,
            'total_closed_trades': len(self.closed_trades_pnl),
            'formula': 'balance = initial_capital + netprofit'
        }
    
    def reset_balance(self, new_initial_capital=None):
        """Reseta balance (apenas para testes)"""
        if new_initial_capital is not None:
            self.initial_capital = new_initial_capital
        
        self.netprofit = 0.0
        self.trade_count = 0
        self.closed_trades_pnl = []
        
        logger.info("🔄 Balance resetado para testes")
        self.save_balance()
    
    def save_balance(self):
        """Salva estado do balance"""
        try:
            data = {
                'initial_capital': self.initial_capital,
                'netprofit': self.netprofit,
                'trade_count': self.trade_count,
                'closed_trades_count': len(self.closed_trades_pnl),
                'last_update': time.time(),
                'version': 'exact_pine_script'
            }
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"❌ Erro ao salvar balance: {e}")
    
    def load_balance(self):
        """Carrega balance do arquivo"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self.initial_capital = data.get('initial_capital', 1000.0)
                self.netprofit = data.get('netprofit', 0.0)
                self.trade_count = data.get('trade_count', 0)
                
                logger.info(f"💰 Balance EXATO carregado: ${self.get_balance():.2f}")
                return True
            else:
                logger.info("💰 Nenhum balance anterior, usando valores padrão")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao carregar balance: {e}")
            return False
