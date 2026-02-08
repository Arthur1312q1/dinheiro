#!/usr/bin/env python3
"""
TIME_SYNC.PY - Sincronização temporal PRECISA (50ms)
"""
import ntplib
import time
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

class TimeSync:
    """Sincroniza tempo com precisão de 50ms"""
    
    def __init__(self, timeframe_minutes=30):
        self.timeframe = timeframe_minutes
        self.utc = pytz.utc
        self.ntp_servers = [
            'time.google.com',  # Mais preciso
            'time.windows.com',
            'time.apple.com',
            'pool.ntp.org'
        ]
        self.time_offset = 0
        self.last_sync = None
        
        # Precisão de 50ms
        self.tolerance_ms = 50
        self.tolerance_sec = self.tolerance_ms / 1000.0
        
        self.sync_with_ntp()
    
    def sync_with_ntp(self):
        """Sincroniza com NTP com múltiplas tentativas"""
        best_offset = 0
        best_server = None
        
        for server in self.ntp_servers:
            try:
                client = ntplib.NTPClient()
                response = client.request(server, timeout=2, version=3)
                
                ntp_time = datetime.fromtimestamp(response.tx_time, self.utc)
                system_time = datetime.now(self.utc)
                offset = (ntp_time - system_time).total_seconds()
                
                logger.debug(f"   {server}: offset={offset:.3f}s")
                
                # Escolher servidor com menor offset absoluto
                if best_server is None or abs(offset) < abs(best_offset):
                    best_offset = offset
                    best_server = server
                    
            except Exception as e:
                logger.debug(f"   {server}: falha - {e}")
                continue
        
        if best_server:
            self.time_offset = best_offset
            self.last_sync = datetime.now(self.utc)
            logger.info(f"✅ Sincronizado com {best_server}: offset={self.time_offset:.3f}s")
            
            if abs(self.time_offset) > self.tolerance_sec:
                logger.warning(f"⚠️ Offset maior que {self.tolerance_ms}ms: {self.time_offset*1000:.1f}ms")
            
            return True
        else:
            logger.error("❌ Todos servidores NTP falharam")
            self.time_offset = 0
            return False
    
    def get_synchronized_time(self):
        """Retorna horário UTC sincronizado"""
        system_time = datetime.now(self.utc)
        synchronized = system_time + timedelta(seconds=self.time_offset)
        return synchronized
    
    def is_exact_bar_close(self, timestamp=None):
        """
        Detecta últimos 100ms antes do fechamento da barra
        Para processar candle no exato momento do fechamento
        """
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        bar_info = self._get_bar_info(timestamp)
        seconds_to_next = bar_info['seconds_to_next_bar']
        
        # Últimos 100ms antes da próxima barra
        return seconds_to_next < 0.1
    
    def is_exact_bar_open(self, timestamp=None):
        """
        Detecta primeiros 50ms após abertura da barra
        Para executar ordens no exato momento da abertura
        """
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        bar_info = self._get_bar_info(timestamp)
        time_since_open = bar_info['time_since_bar_start']
        
        # Primeiros 50ms após abertura
        return bar_info['is_bar_start'] and time_since_open < 0.05
    
    def _get_bar_info(self, timestamp):
        """Calcula informações da barra atual"""
        current_minute = timestamp.minute
        bar_minute = (current_minute // self.timeframe) * self.timeframe
        
        current_bar_start = timestamp.replace(
            minute=bar_minute,
            second=0,
            microsecond=0
        )
        
        next_bar_start = current_bar_start + timedelta(minutes=self.timeframe)
        
        time_since_bar_start = (timestamp - current_bar_start).total_seconds()
        seconds_to_next_bar = (next_bar_start - timestamp).total_seconds()
        
        # Janela de 50ms para início de barra
        is_bar_start = time_since_bar_start < self.tolerance_sec
        
        return {
            'is_bar_start': is_bar_start,
            'current_bar_timestamp': current_bar_start,
            'next_bar_timestamp': next_bar_start,
            'seconds_to_next_bar': seconds_to_next_bar,
            'time_since_bar_start': time_since_bar_start,
            'current_timestamp': timestamp,
            'milliseconds_since_open': time_since_bar_start * 1000,
            'milliseconds_to_next': seconds_to_next_bar * 1000
        }
    
    def get_precise_bar_info(self, timestamp=None):
        """Retorna informações precisas da barra"""
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        bar_info = self._get_bar_info(timestamp)
        
        # Adicionar precisão
        bar_info['is_exact_close'] = self.is_exact_bar_close(timestamp)
        bar_info['is_exact_open'] = self.is_exact_bar_open(timestamp)
        
        return bar_info
    
    def wait_for_exact_close(self, check_interval=0.01):
        """Aguarda até o momento exato do fechamento"""
        while True:
            bar_info = self.get_precise_bar_info()
            
            if bar_info['is_exact_close']:
                # Aguardar até os últimos 10ms
                if bar_info['milliseconds_to_next'] > 10:
                    time.sleep(0.001)  # 1ms
                else:
                    break
            else:
                # Aguardar em intervalos curtos
                sleep_time = min(bar_info['seconds_to_next_bar'] - 0.1, check_interval)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        # Pequena pausa para garantir timing exato
        time.sleep(0.005)  # 5ms
        return self.get_precise_bar_info()
    
    def wait_for_exact_open(self, check_interval=0.01):
        """Aguarda até o momento exato da abertura"""
        while True:
            bar_info = self.get_precise_bar_info()
            
            if bar_info['is_bar_start']:
                if bar_info['milliseconds_since_open'] < self.tolerance_ms:
                    break
                else:
                    # Já passou do momento exato
                    break
            else:
                sleep_time = min(bar_info['seconds_to_next_bar'], check_interval)
                time.sleep(sleep_time)
        
        return self.get_precise_bar_info()
    
    def validate_precision(self, duration_seconds=60):
        """Valida precisão do timing"""
        logger.info("⏰ Validando precisão temporal...")
        
        start = time.time()
        close_count = 0
        open_count = 0
        
        while time.time() - start < duration_seconds:
            bar_info = self.get_precise_bar_info()
            
            if bar_info['is_exact_close']:
                close_count += 1
                logger.info(f"   Fechamento detectado: {bar_info['milliseconds_to_next']:.1f}ms para próxima")
            
            if bar_info['is_exact_open']:
                open_count += 1
                logger.info(f"   Abertura detectada: {bar_info['milliseconds_since_open']:.1f}ms após abertura")
            
            time.sleep(0.001)  # 1ms
        
        logger.info(f"   Total fechamentos: {close_count}")
        logger.info(f"   Total aberturas: {open_count}")
        
        return close_count > 0 and open_count > 0
