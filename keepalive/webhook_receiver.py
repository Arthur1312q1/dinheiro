# keepalive/webhook_receiver.py
from flask import Blueprint, request, jsonify
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

webhook_bp = Blueprint('webhook', __name__)

@webhook_bp.route('/', methods=['GET'])
def index():
    """Página inicial – lista os endpoints disponíveis."""
    return jsonify({
        "service": "AZLEMA Backtest Engine",
        "status": "running",
        "endpoints": ["/", "/ping", "/health", "/uptimerobot"],
        "documentation": "https://github.com/Arthur1312q1/dinheiro"
    }), 200

@webhook_bp.route('/uptimerobot', methods=['GET', 'POST'])
def uptimerobot_webhook():
    """
    Endpoint público para receber pings do UptimeRobot.
    Qualquer método (GET/POST) retorna 200 OK, mantendo o serviço ativo.
    """
    client_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'unknown')

    logger.debug(f"UptimeRobot ping recebido - IP: {client_ip} | UA: {user_agent}")

    return jsonify({
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "AZLEMA Backtest Engine"
    }), 200

@webhook_bp.route('/ping', methods=['GET'])
def ping():
    """Endpoint simples para pings internos do KeepAlivePinger."""
    return "pong", 200

@webhook_bp.route('/health', methods=['GET'])
def health():
    """Endpoint de saúde – retorna status do serviço."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200
