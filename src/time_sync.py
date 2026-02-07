#!/usr/bin/env python3
"""
TIME_SYNC.PY - Sincronização temporal perfeita com NTP
Detecta exatamente os horários de abertura/fechamento das barras de 30min
"""
import ntplib
import time
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

class TimeSync:
    """Sincroniza tempo UTC e detecta barras de timeframe"""
    
    def __init__(self, timeframe_minutes=30):
        self.timeframe = timeframe_minutes
        self.utc = pytz.utc
        self.ntp_servers = [
            'pool.ntp.org',
            'time.google.com',
            'time.windows.com',
            'time.apple.com'
        ]
        self.time_offset = 0
        self.last_sync = None
        
        # Sincronizar inicialmente
        self.sync_with_ntp()
    
    def sync_with_ntp(self):
        """Sincroniza horário com servidores NTP"""
        for server in self.ntp_servers:
            try:
                client = ntplib.NTPClient()
                response = client.request(server, timeout=5)
                ntp_time = datetime.fromtimestamp(response.tx_time, self.utc)
                system_time = datetime.now(self.utc)
                
                self.time_offset = (ntp_time - system_time).total_seconds()
                self.last_sync = datetime.now(self.utc)
                
                logger.info(f"✅ Sincronizado com {server}")
                logger.info(f"   Horário NTP: {ntp_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
                logger.info(f"   Horário Sistema: {system_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
                logger.info(f"   Offset: {self.time_offset:.6f} segundos")
                
                if abs(self.time_offset) > 0.5:
                    logger.warning(f"⚠️ Offset maior que 500ms: {self.time_offset:.3f}s")
                
                return True
                
            except Exception as e:
                logger.warning(f"❌ Falha com {server}: {e}")
                continue
        
        logger.error("❌ Não foi possível sincronizar com NTP, usando horário do sistema")
        self.time_offset = 0
        return False
    
    def get_synchronized_time(self):
        """Retorna horário UTC sincronizado"""
        system_time = datetime.now(self.utc)
        synchronized = system_time + timedelta(seconds=self.time_offset)
        return synchronized
    
    def is_bar_start(self, timestamp=None):
        """
        Verifica se é início de uma nova barra
        Retorna: (is_bar_start, bar_timestamp, seconds_to_next_bar)
        """
        if timestamp is None:
            timestamp = self.get_synchronized_time()
        
        # Calcular início da barra atual
        current_minute = timestamp.minute
        bar_minute = (current_minute // self.timeframe) * self.timeframe
        
        current_bar_start = timestamp.replace(
            minute=bar_minute,
            second=0,
            microsecond=0
        )
        
        # Próxima barra
        next_bar_start = current_bar_start + timedelta(minutes=self.timeframe)
        
        # Verificar se estamos no início exato da barra (janela de 500ms)
        time_since_bar_start = (timestamp - current_bar_start).total_seconds()
        
        is_bar_start = time_since_bar_start < 0.5  # 500ms de tolerância
        
        seconds_to_next_bar = (next_bar_start - timestamp).total_seconds()
        
        return {
            'is_bar_start': is_bar_start,
            'current_bar_timestamp': current_bar_start,
            'next_bar_timestamp': next_bar_start,
            'seconds_to_next_bar': seconds_to_next_bar,
            'time_since_bar_start': time_since_bar_start,
            'current_timestamp': timestamp
        }
    
    def get_current_bar_info(self):
        """Retorna informações detalhadas da barra atual"""
        sync_time = self.get_synchronized_time()
        bar_info = self.is_bar_start(sync_time)
        
        # Se não for início da barra, calcular barra atual
        if not bar_info['is_bar_start']:
            bar_info['current_bar_timestamp'] = bar_info['current_bar_timestamp']
        
        return bar_info
    
    def wait_for_next_bar(self):
        """Aguarda até o início da próxima barra"""
        while True:
            bar_info = self.get_current_bar_info()
            seconds_to_next = bar_info['seconds_to_next_bar']
            
            if seconds_to_next <= 0.5:  # Próxima barra em menos de 500ms
                time.sleep(seconds_to_next + 0.01)  # Pequeno buffer
                break
            else:
                # Aguardar em blocos menores
                sleep_time = min(seconds_to_next / 2, 1.0)
                time.sleep(sleep_time)
        
        # Verificar que estamos no início da barra
        time.sleep(0.05)  # Pequeno atraso para garantir
        return self.get_current_bar_info()
    
    def validate_sync(self, duration_seconds=300):
        """Valida sincronização por um período"""
        logger.info("⏰ VALIDAÇÃO DE SINCRONIZAÇÃO")
        logger.info(f"   Duração: {duration_seconds} segundos")
        
        start_time = time.time()
        bar_count = 0
        
        while time.time() - start_time < duration_seconds:
            sync_time = self.get_synchronized_time()
            bar_info = self.is_bar_start(sync_time)
            
            if bar_info['is_bar_start']:
                logger.info(f"   Barra #{bar_count}: {sync_time.strftime('%H:%M:%S.%f')}")
                bar_count += 1
            
            time.sleep(0.1)
        
        logger.info(f"   Total barras detectadas: {bar_count}")
        logger.info(f"   Esperado: {duration_seconds / (self.timeframe * 60):.1f}")
        
        return bar_count
