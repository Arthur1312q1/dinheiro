# keepalive/webhook_receiver.py
from flask import Blueprint, request, jsonify
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
webhook_bp = Blueprint('webhook', __name__)

@webhook_bp.route('/')
def index():
    return jsonify({
        "service": "AZLEMA Backtest Engine",
        "status": "running",
        "endpoints": ["/", "/ping", "/health", "/uptimerobot", "/backtest"],
        "docs": "https://github.com/Arthur1312q1/dinheiro"
    }), 200

@webhook_bp.route('/uptimerobot', methods=['GET', 'POST'])
def uptimerobot_webhook():
    logger.debug(f"UptimeRobot ping from {request.remote_addr}")
    return jsonify({"status": "alive", "timestamp": datetime.utcnow().isoformat() + "Z"}), 200

@webhook_bp.route('/ping', methods=['GET'])
def ping():
    return "pong", 200

@webhook_bp.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat() + "Z"}), 200
