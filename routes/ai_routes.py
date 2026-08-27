"""
TRAGENE AI - User Routes
All AI endpoints for users
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db, limiter
from models import (
    AIReport, AIUsageLog, AIPlanDefaults, AIUserOverride,
    TradingGoal, CoachInsight, Trade, DiaryEntry, Checklist,
    AIChatSession, AIChatMessage
)
from rate_limits import ai_rate_limit_key, require_daily_ai_quota
from services.ai_service import (
    generate_report, coach_chat, analyze_goal, suggest_goals,
    get_user_reports, get_unanalyzed_count,
    estimate_tokens, estimate_cost, seed_plan_defaults,
    get_chat_sessions, get_chat_messages, delete_chat_session
)
from datetime import datetime, date, timedelta

ai_bp = Blueprint('ai', __name__, url_prefix='/ai')
limiter.limit("10 per minute", key_func=ai_rate_limit_key)(ai_bp)

# ═══════════════════════════════════════════════════════════
# 🏠 AI REPORTS PAGE
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/reports')
@login_required
def ai_reports():
    """Main AI Reports page"""
    reports = get_user_reports(current_user.id, limit=10)
    unanalyzed = get_unanalyzed_count(current_user)
    token_limit = current_user.get_token_limit()
    tokens_used = current_user.get_used_tokens()
    tokens_remaining = current_user.get_remaining_tokens()
    can_use, ai_message = current_user.can_use_ai()
    is_banned = current_user.is_ai_banned()
    can_chat = current_user.subscription_tier in ['pro', 'elite']
    can_coach = current_user.can_access_coach()
    can_goals = current_user.can_access_goals()
    weekly_queries = 0
    weekly_limit = 0
    if current_user.subscription_tier == 'free':
        weekly_queries = current_user.get_queries_used_this_week()
        weekly_limit = current_user.get_queries_per_week()
    
    return render_template('user/ai_reports.html',
        reports=reports, unanalyzed_count=unanalyzed,
        token_limit=token_limit, tokens_used=tokens_used, tokens_remaining=tokens_remaining,
        can_use_ai=can_use, ai_message=ai_message, is_banned=is_banned,
        can_chat=can_chat, can_coach=can_coach, can_goals=can_goals,
        weekly_queries=weekly_queries, weekly_limit=weekly_limit)


# ═══════════════════════════════════════════════════════════
# 📊 GENERATE REPORT
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/generate-report', methods=['POST'])
@login_required
@require_daily_ai_quota
def api_generate_report():
    account_id = None
    active_account = current_user.get_active_account()
    if active_account: account_id = active_account.id
    
    can_use, message = current_user.can_use_ai()
    if not can_use: return jsonify({'success': False, 'message': message})
    
    if current_user.subscription_tier == 'free':
        weekly_used = current_user.get_queries_used_this_week()
        weekly_max = current_user.get_queries_per_week()
        if weekly_max and weekly_used >= weekly_max:
            return jsonify({'success': False, 'message': f'Free plan limit: {weekly_max} reports/week. Used: {weekly_used}. Upgrade for unlimited.'})
    
    unanalyzed = get_unanalyzed_count(current_user)
    estimated = estimate_tokens(unanalyzed, 0, 0, 'report')
    remaining = current_user.get_remaining_tokens()
    
    if request.args.get('confirm') != 'true':
        return jsonify({'success': True, 'estimating': True, 'unanalyzed_trades': unanalyzed, 'estimated_tokens': estimated, 'remaining_tokens': remaining, 'estimated_cost': estimate_cost(estimated), 'message': f'Found {unanalyzed} unanalyzed trades. Estimated: {estimated:,} tokens.'})
    
    result = generate_report(current_user, account_id)
    if result['success']: return jsonify(result)
    return jsonify({'success': False, 'message': result.get('message', 'Report generation failed.')})


# ═══════════════════════════════════════════════════════════
# 📋 GET REPORT DETAIL
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/report/<int:report_id>')
@login_required
def get_report(report_id):
    report = AIReport.query.filter_by(id=report_id, user_id=current_user.id).first_or_404()
    return jsonify({'id': report.id, 'date': report.report_date.isoformat(), 'period_start': report.period_start.isoformat(), 'period_end': report.period_end.isoformat(), 'trades_analyzed': report.trades_analyzed, 'diary_entries': report.diary_entries_analyzed, 'checklist_days': report.checklist_days_analyzed, 'summary': report.user_summary, 'strengths': report.strengths, 'warnings': report.warnings, 'action_items': report.action_items, 'score': report.performance_score, 'tokens_used': report.total_tokens, 'cost': report.api_cost})


# ═══════════════════════════════════════════════════════════
# 📜 LIST REPORTS
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/reports-list')
@login_required
def api_reports_list():
    reports = get_user_reports(current_user.id, limit=50)
    return jsonify({'reports': [{'id': r.id, 'date': r.report_date.isoformat(), 'trades_analyzed': r.trades_analyzed, 'summary': r.user_summary[:200] if r.user_summary else '', 'score': r.performance_score, 'tokens_used': r.total_tokens} for r in reports]})


# ═══════════════════════════════════════════════════════════
# 💬 COACH CHAT (Legacy - widget uses this)
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/coach-chat', methods=['POST'])
@login_required
@require_daily_ai_quota
def api_coach_chat():
    data = request.get_json()
    question = data.get('question', '').strip()
    session_id = data.get('session_id', None)
    
    if not question: return jsonify({'success': False, 'message': 'Please ask a question.'})
    if len(question) > 500: return jsonify({'success': False, 'message': 'Question too long. Keep it under 500 characters.'})
    
    result = coach_chat(current_user, question, session_id)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
# 💬 CHAT SESSIONS (New - for history & multi-chat)
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/chat/sessions')
@login_required
def api_chat_sessions():
    """Get all chat sessions for the user"""
    sessions = get_chat_sessions(current_user)
    return jsonify({'sessions': [{'id': s.id, 'title': s.title, 'created_at': s.created_at.isoformat(), 'updated_at': s.updated_at.isoformat() if s.updated_at else None, 'message_count': len(s.messages)} for s in sessions]})


@ai_bp.route('/chat/sessions/<int:session_id>/messages')
@login_required
def api_chat_messages(session_id):
    """Get all messages for a session"""
    messages = get_chat_messages(session_id, current_user.id)
    return jsonify({'messages': [{'id': m.id, 'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat() if m.created_at else None} for m in messages]})


@ai_bp.route('/chat/sessions/<int:session_id>/delete', methods=['POST'])
@login_required
def api_delete_session(session_id):
    """Delete a chat session"""
    success = delete_chat_session(session_id, current_user.id)
    return jsonify({'success': success})


@ai_bp.route('/chat/send', methods=['POST'])
@login_required
@require_daily_ai_quota
def api_chat_send():
    """Send a message in a chat session"""
    data = request.get_json()
    question = data.get('question', '').strip()
    session_id = data.get('session_id', None)
    
    if not question:
        return jsonify({'success': False, 'message': 'Please ask a question.'})
    
    if len(question) > 500:
        return jsonify({'success': False, 'message': 'Question too long.'})
    
    result = coach_chat(current_user, question, session_id)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
# 🎯 GOALS
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 🎯 GOALS
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/goals')
@login_required
def goals_page():
    if not current_user.can_access_goals():
        flash('Goals & Planner requires Elite plan.', 'warning')
        return redirect(url_for('ai.ai_reports'))
    account_id = current_user.get_active_account().id if current_user.get_active_account() else None
    goals = TradingGoal.query.filter_by(user_id=current_user.id, account_id=account_id)\
        .order_by(TradingGoal.created_at.desc()).all()
    return render_template('user/goals_planner.html', goals=goals)


@ai_bp.route('/goals/create', methods=['POST'])
@login_required
def api_create_goal():
    if not current_user.can_access_goals():
        return jsonify({'success': False, 'message': 'Elite plan required.'})
    data = request.get_json()
    active_account = current_user.get_active_account()
    goal = TradingGoal(
        user_id=current_user.id,
        account_id=active_account.id if active_account else None,
        goal_type=data.get('goal_type', 'custom'),
        target_value=float(data.get('target_value', 0)),
        timeframe=data.get('timeframe', 'monthly'),
        start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date(),
        end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date()
    )
    db.session.add(goal)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Goal created!', 'goal_id': goal.id})


@ai_bp.route('/goals/<int:goal_id>/update', methods=['POST'])
@login_required
def api_update_goal(goal_id):
    goal = TradingGoal.query.filter_by(id=goal_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    goal.current_value = float(data.get('current_value', goal.current_value))
    if goal.current_value >= goal.target_value:
        goal.is_completed = True
        goal.is_achieved = True
    db.session.commit()
    return jsonify({'success': True, 'message': 'Goal updated!'})


@ai_bp.route('/goals/<int:goal_id>/analyze', methods=['POST'])
@login_required
@require_daily_ai_quota
def api_analyze_goal(goal_id):
    result = analyze_goal(current_user, goal_id)
    return jsonify(result)


@ai_bp.route('/goals/<int:goal_id>/delete', methods=['POST'])
@login_required
def api_delete_goal(goal_id):
    goal = TradingGoal.query.filter_by(id=goal_id, user_id=current_user.id).first_or_404()
    db.session.delete(goal)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Goal deleted.'})


@ai_bp.route('/goals/suggest', methods=['POST'])
@login_required
@require_daily_ai_quota
def api_suggest_goals():
    if not current_user.can_access_goals():
        return jsonify({'success': False, 'message': 'Elite plan required.'})
    result = suggest_goals(current_user)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
# 🧠 COACH PAGE
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/coach')
@login_required
def coach_page():
    if not current_user.can_access_coach():
        flash('AI Coach requires Elite plan.', 'warning')
        return redirect(url_for('ai.ai_reports'))
    insights = CoachInsight.query.filter_by(user_id=current_user.id).order_by(CoachInsight.created_at.desc()).limit(10).all()
    return render_template('user/ai_coach.html', insights=insights)


# ═══════════════════════════════════════════════════════════
# 💎 TOKEN INFO
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/token-info')
@login_required
def api_token_info():
    """Return token info - paid users see 'unlimited', free users see actual count"""
    tier = current_user.subscription_tier
    is_paid = tier in ['pro', 'elite', 'enterprise']
    
    return jsonify({
        'limit': current_user.get_token_limit(),
        'used': current_user.get_used_tokens(),
        'remaining': current_user.get_remaining_tokens(),
        'plan': tier,
        'is_banned': current_user.is_ai_banned(),
        'is_paid': is_paid,  # 🆕 tells frontend to show "unlimited"
        'weekly_queries': current_user.get_queries_used_this_week(),
        'weekly_limit': current_user.get_queries_per_week()
    })


# ═══════════════════════════════════════════════════════════
# 📊 ESTIMATE TOKENS
# ═══════════════════════════════════════════════════════════

@ai_bp.route('/estimate', methods=['POST'])
@login_required
def api_estimate():
    data = request.get_json()
    estimated = estimate_tokens(data.get('trade_count', 0), data.get('diary_count', 0), data.get('checklist_days', 0), data.get('analysis_type', 'report'))
    remaining = current_user.get_remaining_tokens()
    return jsonify({'estimated_tokens': estimated, 'remaining_tokens': remaining, 'sufficient': estimated <= remaining, 'estimated_cost': estimate_cost(estimated), 'message': f'Estimated: {estimated:,} tokens. Remaining: {remaining:,}.'})






@ai_bp.route('/chat')
@login_required
def chat_page():
    """Full page AI chat"""
    return render_template('user/ai_chat.html')
