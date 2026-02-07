#!/usr/bin/env python3
"""
BALANCE_MANAGER.PY - Gerenciador de balance dinâmico (igual TradingView)
Calcula: balance = strategy.initial_capital + strategy.netprofit
"""
import logging
import json
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)

class BalanceManager:
    """Gerenciador de balance que replica exatamente o TradingView"""
    
    def __init__(self, initial_capital=1000.0, file_path=None):
        self.initial_capital = initial_capital
        self.netprofit = 0.0
        self.trade_count = 0
        
        # Configurar arquivo de persistência
        if file_path is None:
            if os.path.exists('/data'):
                self.file_path = "/data/balance_history.json"
            else:
                self.file_path = "balance_history.json"
        else:
            self.file_path = file_path
        
        # Carregar histórico
        self.load_balance()
        
        logger.info("💰 Balance Manager inicializado")
        logger.info(f"   Initial Capital: ${self.initial_capital:.2f}")
        logger.info(f"   Net Profit: ${self.netprofit:.2f}")
        logger.info(f"   Current Balance: ${self.get_balance():.2f}")
    
    def get_balance(self):
        """Retorna balance atual: initial_capital + netprofit"""
        return self.initial_capital + self.netprofit
    
    def update_netprofit(self, pnl_usdt: float, trade_id: int = None):
        """Atualiza netprofit com PnL de trade fechado"""
        old_netprofit = self.netprofit
        self.netprofit += pnl_usdt
        self.trade_count += 1
        
        logger.info("💰 ATUALIZAÇÃO DE BALANCE")
        logger.info(f"   Trade ID: {trade_id or 'N/A'}")
        logger.info(f"   PnL: ${pnl_usdt:.2f}")
        logger.info(f"   Net Profit: ${old_netprofit:.2f} → ${self.netprofit:.2f}")
        logger.info(f"   Balance: ${self.get_balance():.2f}")
        
        # Salvar após atualização
        self.save_balance()
        
        return self.netprofit
    
    def reset_balance(self, new_initial_capital=None):
        """Reseta balance para valor inicial"""
        if new_initial_capital is not None:
            self.initial_capital = new_initial_capital
        
        self.netprofit = 0.0
        self.trade_count = 0
        
        logger.info("🔄 Balance resetado")
        logger.info(f"   New Initial Capital: ${self.initial_capital:.2f}")
        
        self.save_balance()
    
    def get_stats(self):
        """Retorna estatísticas do balance"""
        return {
            'initial_capital': self.initial_capital,
            'netprofit': self.netprofit,
            'current_balance': self.get_balance(),
            'trade_count': self.trade_count,
            'avg_pnl_per_trade': self.netprofit / self.trade_count if self.trade_count > 0 else 0
        }
    
    def save_balance(self):
        """Salva estado do balance em arquivo JSON"""
        try:
            data = {
                'initial_capital': self.initial_capital,
                'netprofit': self.netprofit,
                'trade_count': self.trade_count,
                'last_update': time.time()
            }
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            logger.debug(f"💰 Balance salvo em {self.file_path}")
            
        except Exception as e:
            logger.error(f"❌ Erro ao salvar balance: {e}")
    
    def load_balance(self):
        """Carrega balance do arquivo JSON"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self.initial_capital = data.get('initial_capital', 1000.0)
                self.netprofit = data.get('netprofit', 0.0)
                self.trade_count = data.get('trade_count', 0)
                
                logger.info(f"💰 Balance carregado: ${self.get_balance():.2f}")
                return True
            else:
                logger.info("💰 Nenhum balance anterior encontrado, usando valores padrão")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao carregar balance: {e}")
            return False
    
    def calculate_position_size(self, risk_percent: float, stop_loss_points: int, 
                               mintick: float = 0.01) -> float:
        """
        Calcula tamanho da posição EXATO como Pine Script
        Fórmula: lots = (risk × balance) / (fixedSL × syminfo.mintick)
        """
        balance = self.get_balance()
        risk_amount = risk_percent * balance
        stop_loss_usdt = stop_loss_points * mintick
        
        if stop_loss_usdt <= 0:
            logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
            return 0.0
        
        quantity = risk_amount / stop_loss_usdt
        
        logger.debug(f"   Cálculo position size:")
        logger.debug(f"     Balance: ${balance:.2f}")
        logger.debug(f"     Risk: {risk_percent*100}% = ${risk_amount:.2f}")
        logger.debug(f"     Stop Loss: {stop_loss_points}p = ${stop_loss_usdt:.2f}")
        logger.debug(f"     Quantity: {quantity:.6f}")
        
        return quantity
