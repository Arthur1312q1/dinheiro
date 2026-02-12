# keepalive/webhook_receiver.py
from flask import Blueprint, request, jsonify
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
webhook_bp = Blueprint('webhook', __name__)

# ⚠️ ROTA RAIZ FOI REMOVIDA – AGORA ESTÁ NO MAIN.PY

@webhook_bp.route('/uptimerobot', methods=['GET', 'POST'])
def uptimerobot_webhook():
    """Endpoint para UptimeRobot – mantém serviço ativo."""
    logger.debug(f"UptimeRobot ping from {request.remote_addr}")
    return jsonify({
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200

@webhook_bp.route('/ping', methods=['GET'])
def ping():
    """Endpoint simples para pings internos."""
    return "pong", 200

@webhook_bp.route('/health', methods=['GET'])
def health():
    """Status de saúde do serviço."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200
