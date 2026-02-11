# utils/env_loader.py
import os

def env(key: str, default=None):
    """Retorna variável de ambiente do sistema (Render dashboard)."""
    return os.environ.get(key, default)

def env_int(key: str, default=0):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default

def env_float(key: str, default=0.0):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default

def env_bool(key: str, default=False):
    val = os.environ.get(key, '').lower()
    if val in ('true', '1', 'yes', 'on'):
        return True
    if val in ('false', '0', 'no', 'off'):
        return False
    return default
