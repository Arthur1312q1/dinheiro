#!/usr/bin/env python3
"""
TIME_SYNC.py - Sincronização temporal PRECISA com timeframe dinâmico
"""
import ntplib
import time
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

class TimeframeManager:
    """Gerencia timeframe dinamicamente como TradingView"""
    
    def __init__(self, timeframe_str="30m"):
        self.timeframe_str = timeframe_str
        self.minutes = self._parse_timeframe(timeframe_str)
        logger.info(f"⏰ Timeframe configurado: {timeframe_str} ({self.minutes} minutos)")
    
    def _parse_timeframe(self, tf_str):
        """Converte string de timeframe para minutos"""
        tf_str = tf_str.lower().replace(' ', '')
        
        if tf_str.endswith('m'):
            return int(tf_str[:-1])
        elif tf_str.endswith('h'):
            return int(tf_str[:-1]) * 60
        elif tf_str.endswith('d'):
            return int(tf_str[:-1]) * 1440
        elif tf_str.endswith('w'):
            return int(tf_str[:-1]) * 10080
        else:
            # Tentar interpretar como número
            try:
                return int(tf_str)
            except:
                logger.warning(f"⚠️ Timeframe '{tf_str}' inválido, usando 30m padrão")
                return 30
    
    def get_bar_start(self, timestamp):
        """Retorna timestamp do início da barra atual"""
        dt = timestamp.replace(second=0, microsecond=0)
        minute = (dt.minute // self.minutes) * self.minutes
        return dt.replace(minute=minute)
    
    def get_next_bar_start(self, timestamp):
        """Retorna timestamp do início da próxima barra"""
        current_start = self.get_bar_start(timestamp)
        return current_start + timedelta(minutes=self.minutes)


class TimeSync:
    """Sincroniza tempo com precisão de 50ms"""
    
    def __init__(self, timeframe_str="30m"):
        self.timeframe_manager = TimeframeManager(timeframe_str)
        self.timeframe_minutes = self.timeframe_manager.minutes
        self.utc = pytz.utc
        
        self.ntp_servers = [
            'time.google.com',
            'time.windows.com',
            'time.apple.com',
            'pool.ntp.org'
        ]
        
        self.time_offset = 0
        self.last_sync = None
        self.tolerance_ms = 50
        self.tolerance_sec = self.tolerance_ms / 1000.0
        
        self.sync_with_ntp()
    
    def sync_with_ntp(self):
        """Sincroniza com NTP"""
        best_offset = 0
        best_server = None
        
        logger.info("⏰ Sincronizando com servidores NTP...")
        
        for server in self.ntp_servers:
            try:
                client = ntplib.NTPClient()
                response = client.request(server, timeout=2, version=3)
                
                ntp_time = datetime.fromtimestamp(response.tx_time, self.utc)
                system_time = datetime.now(self.utc)
                offset = (ntp_time - system_time).total_seconds()
                
                logger.debug(f"   {server}: offset={offset:.3f}s")
                
                if best_server is None or abs(offset) < abs(best_offset):
                    best_offset = offset
                    best_server = server
                    
            except Exception as e:
                logger.debug(f"   {server}: falha - {str(e)[:50]}")
                continue
        
        if best_server:
            self.time_offset = best_offset
            self.last_sync = datetime.now(self.utc)
            
            offset_ms = self.time_offset * 1000
            status = "✅" if abs(offset_ms) < self.tolerance_ms else "⚠️"
            
            logger.info(f"{status} Sincronizado com {best_server}: offset={offset_ms:.1f}ms")
            
            if abs(offset_ms) > self.tolerance_ms:
                logger.warning(f"   Offset maior que {self.tolerance_ms}ms: {offset_ms:.1f}ms")
            
            return True
        else:
            logger.error("❌ Todos servidores NTP falharam, usando horário local")
            self.time_offset = 0
            return False
    
    def get_synchronized_time(self):
        """Retorna horário UTC sincronizado"""
        system_time = datetime.now(self.utc)
        synchronized = system_time + timedelta(seconds=self.time_offset)
        return synchronized
    
    def get_precise_bar_info(self, timestamp=None):
        """Retorna informações precisas da barra atual"""
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        # Usar timeframe manager para cálculos
        current_bar_start = self.timeframe_manager.get_bar_start(timestamp)
        next_bar_start = self.timeframe_manager.get_next_bar_start(timestamp)
        
        time_since_bar_start = (timestamp - current_bar_start).total_seconds()
        seconds_to_next_bar = (next_bar_start - timestamp).total_seconds()
        
        # Detectar momentos críticos
        is_exact_close = seconds_to_next_bar < 0.1  # Últimos 100ms
        is_exact_open = time_since_bar_start < 0.05  # Primeiros 50ms
        is_bar_start = time_since_bar_start < self.tolerance_sec
        
        return {
            'current_timestamp': timestamp,
            'current_bar_timestamp': current_bar_start,
            'next_bar_timestamp': next_bar_start,
            'seconds_to_next_bar': seconds_to_next_bar,
            'time_since_bar_start': time_since_bar_start,
            'milliseconds_to_next': seconds_to_next_bar * 1000,
            'milliseconds_since_open': time_since_bar_start * 1000,
            'is_bar_start': is_bar_start,
            'is_exact_close': is_exact_close,
            'is_exact_open': is_exact_open,
            'timeframe_minutes': self.timeframe_minutes,
            'timeframe_str': self.timeframe_manager.timeframe_str
        }
    
    def is_exact_close(self, timestamp=None):
        """Detecta últimos 100ms antes do fechamento"""
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        bar_info = self.get_precise_bar_info(timestamp)
        return bar_info['is_exact_close']
    
    def is_exact_open(self, timestamp=None):
        """Detecta primeiros 50ms após abertura"""
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        bar_info = self.get_precise_bar_info(timestamp)
        return bar_info['is_exact_open']
    
    def wait_for_exact_close(self, check_interval=0.01):
        """Aguarda até o momento exato do fechamento"""
        logger.info("⏳ Aguardando fechamento exato da barra...")
        
        while True:
            bar_info = self.get_precise_bar_info()
            
            if bar_info['is_exact_close']:
                if bar_info['milliseconds_to_next'] > 10:
                    time.sleep(0.001)
                else:
                    break
            else:
                sleep_time = min(bar_info['seconds_to_next_bar'] - 0.1, check_interval)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        time.sleep(0.005)  # 5ms extra para precisão
        logger.info(f"✅ Fechamento detectado: {bar_info['milliseconds_to_next']:.1f}ms para próxima barra")
        return self.get_precise_bar_info()
    
    def wait_for_exact_open(self, check_interval=0.01):
        """Aguarda até o momento exato da abertura"""
        logger.info("⏳ Aguardando abertura exata da barra...")
        
        while True:
            bar_info = self.get_precise_bar_info()
            
            if bar_info['is_bar_start']:
                if bar_info['milliseconds_since_open'] < self.tolerance_ms:
                    break
                else:
                    break  # Já passou
            else:
                sleep_time = min(bar_info['seconds_to_next_bar'], check_interval)
                time.sleep(sleep_time)
        
        logger.info(f"✅ Abertura detectada: {bar_info['milliseconds_since_open']:.1f}ms após abertura")
        return self.get_precise_bar_info()
    
    def validate_precision(self, duration_seconds=30):
        """Valida precisão do timing"""
        logger.info("🔬 Validando precisão temporal...")
        
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
            
            time.sleep(0.001)
        
        logger.info(f"📊 Total fechamentos: {close_count}")
        logger.info(f"📊 Total aberturas: {open_count}")
        
        if close_count > 0 and open_count > 0:
            logger.info("✅ Precisão temporal validada com sucesso")
            return True
        else:
            logger.warning("⚠️ Poucas detecções de timing")
            return False
