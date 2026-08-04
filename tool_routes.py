from flask import Blueprint, render_template
from datetime import datetime

tool_bp = Blueprint('tools_public', __name__)

@tool_bp.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# ═══════════════════════════════════════════════
# BATCH 1
# ═══════════════════════════════════════════════

@tool_bp.route('/tools/position-size-calculator')
def position_size_calculator():
    return render_template('tools/position-size-calculator.html')

@tool_bp.route('/tools/risk-reward-calculator')
def risk_reward_calculator():
    return render_template('tools/risk-reward-calculator.html')

@tool_bp.route('/tools/pip-calculator')
def pip_calculator():
    return render_template('tools/pip-calculator.html')

@tool_bp.route('/tools/lot-size-calculator')
def lot_size_calculator():
    return render_template('tools/lot-size-calculator.html')

@tool_bp.route('/tools/forex-profit-calculator')
def forex_profit_calculator():
    return render_template('tools/forex-profit-calculator.html')

# ═══════════════════════════════════════════════
# BATCH 2
# ═══════════════════════════════════════════════

@tool_bp.route('/tools/drawdown-calculator')
def drawdown_calculator():
    return render_template('tools/drawdown-calculator.html')

@tool_bp.route('/tools/win-rate-calculator')
def win_rate_calculator():
    return render_template('tools/win-rate-calculator.html')

@tool_bp.route('/tools/crypto-profit-calculator')
def crypto_profit_calculator():
    return render_template('tools/crypto-profit-calculator.html')

@tool_bp.route('/tools/dca-calculator')
def dca_calculator():
    return render_template('tools/dca-calculator.html')

@tool_bp.route('/tools/compounding-calculator')
def compounding_calculator():
    return render_template('tools/compounding-calculator.html')

# ═══════════════════════════════════════════════
# BATCH 3
# ═══════════════════════════════════════════════

@tool_bp.route('/tools/futures-liquidation-calculator')
def futures_liquidation_calculator():
    return render_template('tools/futures-liquidation-calculator.html')

@tool_bp.route('/tools/expectancy-calculator')
def expectancy_calculator():
    return render_template('tools/expectancy-calculator.html')

@tool_bp.route('/tools/risk-of-ruin-calculator')
def risk_of_ruin_calculator():
    return render_template('tools/risk-of-ruin-calculator.html')

@tool_bp.route('/tools/goal-calculator')
def goal_calculator():
    return render_template('tools/goal-calculator.html')

@tool_bp.route('/tools/ai-trade-review')
def ai_trade_review():
    return render_template('tools/ai-trade-review.html')