from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import (
    Trade, TradingAccount, TradingRule, TradeRuleCheck,
    Checklist, ChecklistTask, ChecklistCompletion, TragenePoints
)
from datetime import datetime, date, timedelta
import json

tools_bp = Blueprint('tools', __name__, url_prefix='/tools')

# ─── Helper ───
def get_active_account_id():
    account = current_user.get_active_account()
    return account.id if account else None

# ═══════════════════════════════════════════════════════════
# 🏠 TOOLS HUB
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/')
@login_required
def index():
    """Tools hub page"""
    # Get user's total tragene points
    total_points = db.session.query(
        db.func.sum(TragenePoints.points)
    ).filter_by(user_id=current_user.id).scalar() or 0
    
    # Get active rules count
    rules_count = TradingRule.query.filter_by(
        user_id=current_user.id, 
        is_active=True,
        is_template=False
    ).count()
    
    # Get active checklists count
    today = date.today()
    checklists_count = Checklist.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).filter(Checklist.end_date >= today).count()
    
    # This month's checklist completion rate
    month_start = today.replace(day=1)
    month_completions = ChecklistCompletion.query.filter_by(
        completed=True
    ).filter(
        ChecklistCompletion.date >= month_start,
        ChecklistCompletion.checklist_id.in_(
            db.session.query(Checklist.id).filter_by(user_id=current_user.id)
        )
    ).count()
    
    month_total = ChecklistCompletion.query.filter(
        ChecklistCompletion.date >= month_start,
        ChecklistCompletion.checklist_id.in_(
            db.session.query(Checklist.id).filter_by(user_id=current_user.id)
        )
    ).count()
    
    completion_rate = round((month_completions / month_total) * 100, 1) if month_total > 0 else 0
    
    return render_template('user/tools/index.html',
        total_points=total_points,
        rules_count=rules_count,
        checklists_count=checklists_count,
        completion_rate=completion_rate
    )

# ═══════════════════════════════════════════════════════════
# 📊 RISK CALCULATOR
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/risk-calculator')
@login_required
def risk_calculator():
    """Position size & risk calculator"""
    return render_template('user/tools/risk_calculator.html')

@tools_bp.route('/api/calculate-risk', methods=['POST'])
@login_required
def api_calculate_risk():
    """API endpoint for risk calculation"""
    try:
        data = request.get_json()
        
        account_size = float(data.get('account_size', 0))
        risk_percent = float(data.get('risk_percent', 1))
        entry_price = float(data.get('entry_price', 0))
        stop_loss = float(data.get('stop_loss', 0))
        take_profit = float(data.get('take_profit', 0)) if data.get('take_profit') else None
        
        if not all([account_size, entry_price, stop_loss]):
            return jsonify({'success': False, 'message': 'Missing required fields'})
        
        # Calculate risk amount
        risk_amount = account_size * (risk_percent / 100)
        
        # Calculate stop distance
        if entry_price > stop_loss:
            stop_distance = entry_price - stop_loss
            direction = 'long'
        else:
            stop_distance = stop_loss - entry_price
            direction = 'short'
        
        # Calculate position size
        if stop_distance > 0:
            position_size = risk_amount / stop_distance
            position_size = round(position_size, 4)
        else:
            position_size = 0
        
        # Calculate potential reward
        potential_reward = None
        reward_risk_ratio = None
        if take_profit:
            if direction == 'long':
                reward_distance = take_profit - entry_price
            else:
                reward_distance = entry_price - take_profit
            if reward_distance > 0 and stop_distance > 0:
                potential_reward = position_size * reward_distance
                potential_reward = round(potential_reward, 2)
                reward_risk_ratio = round(reward_distance / stop_distance, 2)
        
        return jsonify({
            'success': True,
            'risk_amount': round(risk_amount, 2),
            'position_size': position_size,
            'stop_distance': round(stop_distance, 4),
            'direction': direction,
            'potential_reward': potential_reward,
            'reward_risk_ratio': reward_risk_ratio,
            'risk_percent': risk_percent
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ═══════════════════════════════════════════════════════════
# 📈 RISK ANALYTICS (Past Trades)
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/risk-analytics')
@login_required
def risk_analytics():
    """Past trades risk review"""
    account_id = get_active_account_id()
    
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(Trade.entry_date.desc()).limit(50).all()
    
    # Calculate risk stats for each trade - convert to JSON-safe dicts
    trade_data = []
    for t in trades:
        risk_pct = None
        if t.stop_loss and t.entry_price:
            risk_distance = abs(t.entry_price - t.stop_loss)
            if risk_distance > 0:
                risk_pct = round((risk_distance / t.entry_price) * 100, 2)
        
        # Calculate R:R
        rr_ratio = None
        if t.stop_loss and t.take_profit and t.entry_price:
            risk = abs(t.entry_price - t.stop_loss)
            reward = abs(t.take_profit - t.entry_price)
            if risk > 0:
                rr_ratio = round(reward / risk, 2)
        
        # Convert rule checks to JSON-safe format
        checks_data = []
        for check in t.rule_checks:
            checks_data.append({
                'id': check.id,
                'passed': check.passed,
                'details': check.details
            })
        
        trade_data.append({
            'trade': {
                'id': t.id,
                'symbol': t.symbol,
                'trade_type': t.trade_type,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'stop_loss': t.stop_loss,
                'take_profit': t.take_profit,
                'profit_loss': t.profit_loss,
                'is_win': t.is_win,
                'entry_date': t.entry_date.isoformat() if t.entry_date else None,
                'session': t.session,
                'notes': t.notes,
                'lot_size': t.quantity
            },
            'risk_pct': risk_pct,
            'rr_ratio': rr_ratio,
            'has_sl': t.stop_loss is not None,
            'has_tp': t.take_profit is not None,
            'checks': checks_data
        })
    
    return render_template('user/tools/risk_analytics.html', trades=trade_data)





@tools_bp.route('/api/recent-trades')
@login_required
def api_recent_trades():
    """Get recent trades for the rule audit dropdown"""
    account_id = get_active_account_id()
    
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(Trade.entry_date.desc()).limit(100).all()
    
    return jsonify({
        'trades': [{
            'id': t.id,
            'symbol': t.symbol,
            'trade_type': t.trade_type,
            'entry_price': t.entry_price,
            'exit_price': t.exit_price,
            'profit_loss': t.profit_loss,
            'entry_date': t.entry_date.isoformat() if t.entry_date else None,
        } for t in trades]
    })


@tools_bp.route('/api/rules/save-audit', methods=['POST'])
@login_required
def api_save_audit():
    """Save manual rule audit for one or multiple trades"""
    data = request.get_json()
    mode = data.get('mode', 'single')  # single, today, month
    trade_id = data.get('trade_id')
    checks = data.get('checks', {})  # { rule_id: true/false }
    
    account_id = get_active_account_id()
    
    # Get trades to audit
    if mode == 'single' and trade_id:
        trades = [Trade.query.filter_by(id=trade_id, user_id=current_user.id).first()]
    elif mode == 'today':
        today = date.today()
        trades = Trade.query.filter_by(
            user_id=current_user.id, account_id=account_id
        ).filter(db.func.date(Trade.entry_date) == today).all()
    elif mode == 'month':
        month_start = date.today().replace(day=1)
        trades = Trade.query.filter_by(
            user_id=current_user.id, account_id=account_id
        ).filter(db.func.date(Trade.entry_date) >= month_start).all()
    else:
        return jsonify({'success': False, 'message': 'Invalid mode'})
    
    if not trades or trades[0] is None:
        return jsonify({'success': False, 'message': 'No trades found'})
    
    total_points = 0
    
    for trade in trades:
        if trade is None:
            continue
            
        for rule_id_str, passed_str in checks.items():
            rule_id = int(rule_id_str)
            passed = bool(passed_str) if isinstance(passed_str, bool) else passed_str == 'true'
            
            # Check if already audited
            existing = TradeRuleCheck.query.filter_by(
                trade_id=trade.id, rule_id=rule_id
            ).first()
            
            if existing:
                existing.passed = passed
                existing.details = json.dumps({'manual_audit': True, 'mode': mode})
            else:
                check = TradeRuleCheck(
                    user_id=current_user.id,
                    trade_id=trade.id,
                    rule_id=rule_id,
                    passed=passed,
                    details=json.dumps({'manual_audit': True, 'mode': mode})
                )
                db.session.add(check)
            
            # Points
            if passed:
                award_points(current_user.id, 5, 'rule_followed', rule_id, f'Rule followed (manual audit)')
                total_points += 5
            else:
                award_points(current_user.id, -10, 'rule_broken', rule_id, f'Rule broken (manual audit)')
                total_points -= 10
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Audited {len(trades)} trade(s)',
        'points': total_points
    })





@tools_bp.route('/api/checklist/<int:checklist_id>/completions')
@login_required
def api_checklist_completions(checklist_id):
    """Get all completion data for a checklist (for calendar)"""
    checklist = Checklist.query.filter_by(id=checklist_id, user_id=current_user.id).first_or_404()
    
    completions = ChecklistCompletion.query.filter_by(checklist_id=checklist.id).all()
    
    # Group by date
    result = {}
    for c in completions:
        date_str = c.date.isoformat()
        if date_str not in result:
            result[date_str] = {'completed': 0, 'total': 0}
        result[date_str]['total'] += 1
        if c.completed:
            result[date_str]['completed'] += 1
    
    return jsonify({'completions': result})


@tools_bp.route('/api/checklist/<int:checklist_id>/day-detail')
@login_required
def api_checklist_day_detail(checklist_id):
    """Get task details for a specific day"""
    checklist = Checklist.query.filter_by(id=checklist_id, user_id=current_user.id).first_or_404()
    
    date_str = request.args.get('date', date.today().isoformat())
    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    tasks = []
    for task in checklist.tasks:
        completion = ChecklistCompletion.query.filter_by(
            checklist_id=checklist.id,
            task_id=task.id,
            date=target_date
        ).first()
        
        tasks.append({
            'task_name': task.task_name,
            'completed': completion.completed if completion else False,
            'has_data': completion is not None
        })
    
    return jsonify({'tasks': tasks})



# ═══════════════════════════════════════════════════════════
# 📋 TRADING RULES
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/rules')
@login_required
def rules():
    """Trading rules page"""
    # Get template rules
    templates = TradingRule.query.filter_by(is_template=True).all()
    
    # Get user's custom rules
    user_rules = TradingRule.query.filter_by(
        user_id=current_user.id,
        is_template=False
    ).order_by(TradingRule.created_at.desc()).all()
    
    return render_template('user/tools/rules.html',
        templates=templates,
        user_rules=user_rules
    )

@tools_bp.route('/api/rules/adopt/<int:template_id>', methods=['POST'])
@login_required
def api_adopt_rule(template_id):
    """Copy a template rule to user's account"""
    template = TradingRule.query.filter_by(id=template_id, is_template=True).first_or_404()
    
    # Check if user already has this rule
    existing = TradingRule.query.filter_by(
        user_id=current_user.id,
        name=template.name,
        is_template=False
    ).first()
    
    if existing:
        return jsonify({'success': False, 'message': 'You already have this rule.'})
    
    new_rule = TradingRule(
        user_id=current_user.id,
        account_id=get_active_account_id(),
        name=template.name,
        rule_type=template.rule_type,
        description=template.description,
        config_json=template.config_json,
        is_template=False,
        is_active=True
    )
    db.session.add(new_rule)
    db.session.commit()
    
    # Award points
    award_points(current_user.id, 5, 'custom_rule_created', new_rule.id, f'Adopted rule: {template.name}')
    
    return jsonify({'success': True, 'message': f'Rule "{template.name}" added!'})

@tools_bp.route('/api/rules/create', methods=['POST'])
@login_required
def api_create_rule():
    """Create a custom rule"""
    data = request.get_json()
    
    rule = TradingRule(
        user_id=current_user.id,
        account_id=get_active_account_id(),
        name=data.get('name', 'Custom Rule'),
        rule_type=data.get('rule_type', 'custom'),
        description=data.get('description', ''),
        config_json=json.dumps(data.get('config', {})),
        is_template=False,
        is_active=True
    )
    db.session.add(rule)
    db.session.commit()
    
    award_points(current_user.id, 5, 'custom_rule_created', rule.id, f'Created rule: {rule.name}')
    
    return jsonify({'success': True, 'message': 'Rule created!', 'rule_id': rule.id})

@tools_bp.route('/api/rules/<int:rule_id>/toggle', methods=['POST'])
@login_required
def api_toggle_rule(rule_id):
    """Enable/disable a rule"""
    rule = TradingRule.query.filter_by(id=rule_id, user_id=current_user.id).first_or_404()
    rule.is_active = not rule.is_active
    db.session.commit()
    return jsonify({'success': True, 'is_active': rule.is_active})

@tools_bp.route('/api/rules/<int:rule_id>/delete', methods=['POST'])
@login_required
def api_delete_rule(rule_id):
    """Delete a custom rule"""
    rule = TradingRule.query.filter_by(id=rule_id, user_id=current_user.id, is_template=False).first_or_404()
    db.session.delete(rule)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Rule deleted.'})

@tools_bp.route('/api/rules/check-trade/<int:trade_id>', methods=['POST'])
@login_required
def api_check_trade_rules(trade_id):
    """Check a specific trade against all active rules"""
    trade = Trade.query.filter_by(id=trade_id, user_id=current_user.id).first_or_404()
    account_id = get_active_account_id()
    
    active_rules = TradingRule.query.filter_by(
        user_id=current_user.id,
        is_active=True,
        is_template=False
    ).all()
    
    results = []
    for rule in active_rules:
        passed, details = check_rule_against_trade(rule, trade)
        
        # Save check result
        check = TradeRuleCheck(
            user_id=current_user.id,
            trade_id=trade.id,
            rule_id=rule.id,
            passed=passed,
            details=json.dumps(details)
        )
        db.session.add(check)
        
        results.append({
            'rule_name': rule.name,
            'passed': passed,
            'details': details
        })
        
        # Award/deduct points
        if passed:
            award_points(current_user.id, 5, 'rule_followed', rule.id, f'Rule followed: {rule.name}')
        else:
            award_points(current_user.id, -10, 'rule_broken', rule.id, f'Rule broken: {rule.name}')
    
    db.session.commit()
    return jsonify({'success': True, 'results': results})

# ═══════════════════════════════════════════════════════════
# ✅ CHECKLISTS
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/checklists')
@login_required
def checklist_list():
    """All checklists view"""
    checklists = Checklist.query.filter_by(
        user_id=current_user.id
    ).order_by(Checklist.created_at.desc()).all()
    
    today = date.today()
    
    return render_template('user/tools/checklist_list.html',
        checklists=checklists,
        today=today
    )

@tools_bp.route('/checklist/create', methods=['GET', 'POST'])
@login_required
def checklist_create():
    """Create new checklist"""
    if request.method == 'POST':
        data = request.get_json()
        
        # Check limit for free users
        if current_user.subscription_tier == 'free':
            active_count = Checklist.query.filter_by(
                user_id=current_user.id,
                is_active=True
            ).filter(Checklist.end_date >= date.today()).count()
            
            if active_count >= 3:
                return jsonify({'success': False, 'message': 'Free plan limited to 3 active checklists. Upgrade to create more.'})
        
        checklist = Checklist(
            user_id=current_user.id,
            account_id=get_active_account_id(),
            name=data.get('name', 'New Checklist'),
            checklist_type=data.get('checklist_type', 'daily'),
            start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date(),
            end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date(),
            is_active=True,
            is_editable=True
        )
        db.session.add(checklist)
        db.session.flush()
        
        # Add tasks
        tasks = data.get('tasks', [])
        for i, task_name in enumerate(tasks[:5], 1):  # Max 5 tasks
            task = ChecklistTask(
                checklist_id=checklist.id,
                task_name=task_name,
                task_order=i,
                applies_to=data.get('applies_to', 'all_days')
            )
            db.session.add(task)
        
        db.session.commit()
        
        award_points(current_user.id, 10, 'checklist_created', checklist.id, f'Created checklist: {checklist.name}')
        
        return jsonify({'success': True, 'message': 'Checklist created!', 'checklist_id': checklist.id})
    
    return render_template('user/tools/checklist_create.html')

@tools_bp.route('/checklist/<int:checklist_id>/day', methods=['GET'])
@login_required
def checklist_day(checklist_id):
    """Daily task ticking view"""
    checklist = Checklist.query.filter_by(
        id=checklist_id,
        user_id=current_user.id
    ).first_or_404()
    
    day_str = request.args.get('date', date.today().isoformat())
    target_date = datetime.strptime(day_str, '%Y-%m-%d').date()
    
    # Get or create completions for this day
    tasks = checklist.tasks
    completions = {}
    
    for task in tasks:
        completion = ChecklistCompletion.query.filter_by(
            checklist_id=checklist.id,
            task_id=task.id,
            date=target_date
        ).first()
        completions[task.id] = completion.completed if completion else False
    
    return render_template('user/tools/checklist_day.html',
        checklist=checklist,
        target_date=target_date,
        tasks=tasks,
        completions=completions
    )

@tools_bp.route('/api/checklist/toggle', methods=['POST'])
@login_required
def api_toggle_task():
    """Toggle a task completion"""
    data = request.get_json()
    
    checklist_id = data['checklist_id']
    task_id = data['task_id']
    date_str = data['date']
    completed = data['completed']
    
    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Verify ownership
    checklist = Checklist.query.filter_by(id=checklist_id, user_id=current_user.id).first_or_404()
    
    # Lock checklist after first completion
    if checklist.is_editable:
        existing_any = ChecklistCompletion.query.filter_by(
            checklist_id=checklist.id
        ).first()
        if existing_any:
            checklist.is_editable = False
    
    # Get or create completion
    completion = ChecklistCompletion.query.filter_by(
        checklist_id=checklist_id,
        task_id=task_id,
        date=target_date
    ).first()
    
    if not completion:
        completion = ChecklistCompletion(
            checklist_id=checklist_id,
            task_id=task_id,
            date=target_date,
            completed=completed
        )
        db.session.add(completion)
    else:
        completion.completed = completed
    
    db.session.commit()
    
    # Award points
    if completed:
        award_points(current_user.id, 3, 'checklist_completed', checklist_id, f'Task completed: {date_str}')
    else:
        award_points(current_user.id, -2, 'checklist_missed', checklist_id, f'Task missed: {date_str}')
    
    # Check if all tasks done today → bonus
    today_completions = ChecklistCompletion.query.filter_by(
        checklist_id=checklist_id,
        date=target_date,
        completed=True
    ).count()
    
    if today_completions == len(checklist.tasks):
        award_points(current_user.id, 10, 'streak_bonus', checklist_id, f'All tasks completed: {date_str}')
    
    return jsonify({'success': True, 'message': 'Updated!'})

@tools_bp.route('/api/checklist/<int:checklist_id>/stats')
@login_required
def api_checklist_stats(checklist_id):
    """Get stats for a checklist"""
    checklist = Checklist.query.filter_by(id=checklist_id, user_id=current_user.id).first_or_404()
    
    total_tasks = len(checklist.tasks)
    days_range = (checklist.end_date - checklist.start_date).days + 1
    
    completions = ChecklistCompletion.query.filter_by(checklist_id=checklist.id).all()
    
    completed_count = sum(1 for c in completions if c.completed)
    total_count = len(completions)
    
    completion_rate = round((completed_count / total_count) * 100, 1) if total_count > 0 else 0
    
    # Perfect days (all tasks completed)
    daily_groups = {}
    for c in completions:
        date_str = c.date.isoformat()
        if date_str not in daily_groups:
            daily_groups[date_str] = {'total': 0, 'completed': 0}
        daily_groups[date_str]['total'] += 1
        if c.completed:
            daily_groups[date_str]['completed'] += 1
    
    perfect_days = sum(1 for d in daily_groups.values() if d['completed'] == d['total'] and d['total'] == total_tasks)
    
    return jsonify({
        'success': True,
        'total_days': days_range,
        'total_tasks_per_day': total_tasks,
        'completion_rate': completion_rate,
        'perfect_days': perfect_days,
        'total_completed': completed_count,
        'total_missed': total_count - completed_count if total_count > 0 else 0
    })

@tools_bp.route('/api/checklist/<int:checklist_id>/delete', methods=['POST'])
@login_required
def api_delete_checklist(checklist_id):
    """Delete a checklist"""
    checklist = Checklist.query.filter_by(id=checklist_id, user_id=current_user.id).first_or_404()
    db.session.delete(checklist)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Checklist deleted.'})

# ═══════════════════════════════════════════════════════════
# 💎 TRAGENE POINTS API
# ═══════════════════════════════════════════════════════════

@tools_bp.route('/api/tragene-points')
@login_required
def api_tragene_points():
    """Get user's tragene points summary"""
    total = db.session.query(
        db.func.sum(TragenePoints.points)
    ).filter_by(user_id=current_user.id).scalar() or 0
    
    # Points by source
    source_breakdown = db.session.query(
        TragenePoints.source,
        db.func.sum(TragenePoints.points).label('total')
    ).filter_by(user_id=current_user.id).group_by(TragenePoints.source).all()
    
    # Recent activity
    recent = TragenePoints.query.filter_by(user_id=current_user.id)\
        .order_by(TragenePoints.created_at.desc()).limit(10).all()
    
    # Current streak (consecutive days with positive points)
    streak = calculate_streak(current_user.id)
    
    return jsonify({
        'total_points': total,
        'sources': {s.source: s.total for s in source_breakdown},
        'recent': [{'points': r.points, 'description': r.description, 'date': r.created_at.strftime('%d %b')} for r in recent],
        'streak': streak
    })

# ═══════════════════════════════════════════════════════════
# 🔧 HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def award_points(user_id, points, source, source_id=None, description=''):
    """Award or deduct tragene points"""
    entry = TragenePoints(
        user_id=user_id,
        account_id=get_active_account_id(),
        points=points,
        source=source,
        source_id=source_id,
        description=description
    )
    db.session.add(entry)

def calculate_streak(user_id):
    """Calculate current consecutive days with net positive points"""
    points_by_day = db.session.query(
        db.func.date(TragenePoints.created_at).label('day'),
        db.func.sum(TragenePoints.points).label('total')
    ).filter_by(user_id=user_id)\
        .group_by(db.func.date(TragenePoints.created_at))\
        .order_by(db.func.date(TragenePoints.created_at).desc()).all()
    
    streak = 0
    for day_data in points_by_day:
        if day_data.total > 0:
            streak += 1
        else:
            break
    return streak

def check_rule_against_trade(rule, trade):
    """Check if a trade passes a rule. Returns (passed, details_dict)"""
    config = json.loads(rule.config_json) if rule.config_json else {}
    details = {'rule': rule.name, 'type': rule.rule_type}
    
    try:
        if rule.rule_type == 'risk_management':
            if 'max_risk_percent' in config and trade.stop_loss and trade.entry_price:
                risk_pct = (abs(trade.entry_price - trade.stop_loss) / trade.entry_price) * 100
                details['risk_pct'] = round(risk_pct, 2)
                details['max_allowed'] = config['max_risk_percent']
                return risk_pct <= config['max_risk_percent'], details
            
            if 'require_stop_loss' in config and config['require_stop_loss']:
                passed = trade.stop_loss is not None
                details['has_stop_loss'] = passed
                return passed, details
            
            if 'require_take_profit' in config and config['require_take_profit']:
                passed = trade.take_profit is not None
                details['has_take_profit'] = passed
                return passed, details
        
        if rule.rule_type == 'trade_frequency':
            if 'max_trades_per_day' in config:
                today_count = Trade.query.filter_by(
                    user_id=trade.user_id,
                    account_id=trade.account_id
                ).filter(
                    db.func.date(Trade.entry_date) == trade.entry_date.date()
                ).count()
                details['trades_today'] = today_count
                details['max_allowed'] = config['max_trades_per_day']
                return today_count < config['max_trades_per_day'], details
        
        # Default: pass
        return True, details
        
    except Exception as e:
        details['error'] = str(e)
        return False, details


# ─── Seed Template Rules (called on first run) ───
def seed_template_rules():
    """Create pre-built rule templates if they don't exist"""
    templates = [
        {
            'name': 'Max 2% Risk Per Trade',
            'rule_type': 'risk_management',
            'description': 'Never risk more than 2% of your account on a single trade.',
            'config_json': json.dumps({'max_risk_percent': 2})
        },
        {
            'name': 'Always Set Stop Loss',
            'rule_type': 'risk_management',
            'description': 'Every trade must have a stop loss defined before entry.',
            'config_json': json.dumps({'require_stop_loss': True})
        },
        {
            'name': 'Always Set Take Profit',
            'rule_type': 'risk_management',
            'description': 'Every trade must have a take profit target.',
            'config_json': json.dumps({'require_take_profit': True})
        },
        {
            'name': 'Max 3 Trades Per Day',
            'rule_type': 'trade_frequency',
            'description': 'Limit yourself to maximum 3 trades per day to avoid overtrading.',
            'config_json': json.dumps({'max_trades_per_day': 3})
        },
        {
            'name': 'Max 5 Trades Per Day',
            'rule_type': 'trade_frequency',
            'description': 'Limit yourself to maximum 5 trades per day.',
            'config_json': json.dumps({'max_trades_per_day': 5})
        },
        {
            'name': 'Risk:Reward Minimum 1:2',
            'rule_type': 'risk_management',
            'description': 'Only take trades where potential reward is at least 2x the risk.',
            'config_json': json.dumps({'min_rr_ratio': 2})
        },
        {
            'name': 'No Trading After 3 Losses',
            'rule_type': 'trade_frequency',
            'description': 'Stop trading for the day after 3 consecutive losses.',
            'config_json': json.dumps({'max_consecutive_losses': 3})
        },
        {
            'name': 'Max 1% Risk Per Trade',
            'rule_type': 'risk_management',
            'description': 'Conservative approach - only risk 1% per trade.',
            'config_json': json.dumps({'max_risk_percent': 1})
        },
        {
            'name': 'No Weekend Holding',
            'rule_type': 'risk_management',
            'description': 'Close all positions before market close on Friday.',
            'config_json': json.dumps({'no_weekend_holding': True})
        },
        {
            'name': 'Only Trade London/NY Overlap',
            'rule_type': 'session',
            'description': 'Only trade during high liquidity session overlap.',
            'config_json': json.dumps({'allowed_sessions': ['london', 'newyork']})
        },
    ]
    
    for t in templates:
        existing = TradingRule.query.filter_by(name=t['name'], is_template=True).first()
        if not existing:
            rule = TradingRule(
                user_id=None,
                name=t['name'],
                rule_type=t['rule_type'],
                description=t['description'],
                config_json=t['config_json'],
                is_template=True,
                is_active=True
            )
            db.session.add(rule)
    
    db.session.commit()
    print("✅ Template rules seeded!")




@tools_bp.route('/api/all-trades-risk')
@login_required
def api_all_trades_risk():
    """Get all trades with risk data for analytics"""
    account_id = get_active_account_id()
    
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(Trade.entry_date.desc()).all()
    
    result = []
    for t in trades:
        risk_pct = None
        risk_level = 'unknown'
        if t.stop_loss and t.entry_price:
            risk_distance = abs(t.entry_price - t.stop_loss)
            if risk_distance > 0:
                risk_pct = round((risk_distance / t.entry_price) * 100, 2)
                if risk_pct <= 1: risk_level = 'low'
                elif risk_pct <= 2: risk_level = 'medium'
                else: risk_level = 'high'
        
        rr_ratio = None
        if t.stop_loss and t.take_profit and t.entry_price:
            risk = abs(t.entry_price - t.stop_loss)
            reward = abs(t.take_profit - t.entry_price)
            if risk > 0:
                rr_ratio = round(reward / risk, 2)
        
        checks = t.rule_checks
        passed = sum(1 for c in checks if c.passed)
        failed = sum(1 for c in checks if not c.passed)
        
        result.append({
            'id': t.id,
            'symbol': t.symbol,
            'trade_type': t.trade_type,
            'entry_price': t.entry_price,
            'exit_price': t.exit_price,
            'profit_loss': t.profit_loss,
            'is_win': t.is_win,
            'entry_date': t.entry_date.isoformat() if t.entry_date else None,
            'stop_loss': t.stop_loss,
            'take_profit': t.take_profit,
            'lot_size': t.quantity,
            'session': t.session,
            'notes': t.notes,
            'risk_pct': risk_pct,
            'risk_level': risk_level,
            'has_sl': t.stop_loss is not None,
            'has_tp': t.take_profit is not None,
            'rr_ratio': rr_ratio,
            'passed_checks': passed,
            'failed_checks': failed,
            'total_checks': len(checks)
        })
    
    return jsonify({'trades': result})
