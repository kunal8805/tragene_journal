from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_required, current_user, logout_user
from functools import wraps
from extensions import db
from models import (
    User, Trade, TradingAccount,
    AIReport, AIUsageLog, AIPlanDefaults, AIUserOverride,
    FAQ, SupportTicket, TicketReply,
    Blog, Category, Tag, SEOSettings, Redirect, NewsletterSubscriber, PageMetadata,
    Subscription, Payment, DiaryEntry, SyncConnection,
    Moderator, ModeratorPermission
)
from services.ai_service import estimate_tokens, estimate_cost
from datetime import datetime, date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Super admin via Flask-Login
        if current_user.is_authenticated and current_user.is_admin:
            # Ensure no moderator session exists
            session.pop('moderator_id', None)
            session.pop('is_moderator', None)
            return f(*args, **kwargs)
        
        # Moderator via session
        if session.get('is_moderator'):
            mod_id = session.get('moderator_id')
            if mod_id:
                moderator = Moderator.query.get(mod_id)
                if moderator and moderator.is_active and not moderator.is_banned:
                    # Ensure no Flask-Login user
                    if current_user.is_authenticated:
                        logout_user()
                    return f(*args, **kwargs)
                else:
                    # Invalid moderator — clear session
                    session.clear()
                    flash('Session expired.', 'warning')
                    return redirect(url_for('auth.login'))
        
        flash('Access denied.', 'danger')
        return redirect(url_for('auth.login'))
    return decorated_function

# ═══════════════════════════════════════════════════════════
# 📊 DASHBOARD
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    from models import FAQ, SupportTicket, Blog, NewsletterSubscriber, ContactMessage
    
    # User stats
    total_users = User.query.count()
    total_trades = Trade.query.count()
    total_accounts = TradingAccount.query.count()
    
    free_users = User.query.filter_by(subscription_tier='free').count()
    basic_users = User.query.filter_by(subscription_tier='basic').count()
    pro_users = User.query.filter_by(subscription_tier='pro').count()
    elite_users = User.query.filter_by(subscription_tier='elite').count()
    enterprise_users = User.query.filter_by(subscription_tier='enterprise').count()
    
    active_subs = basic_users + pro_users + elite_users + enterprise_users
    
    # Revenue
    revenue = (basic_users * 499) + (pro_users * 999) + (elite_users * 799) + (enterprise_users * 799)
    
    # New users today
    today = date.today()
    new_users_today = User.query.filter(func.date(User.created_at) == today).count()
    
    # Trades today
    trades_today = Trade.query.filter(func.date(Trade.entry_date) == today).count()
    
    # AI stats
    total_ai_reports = AIReport.query.count()
    total_ai_cost = db.session.query(func.sum(AIUsageLog.api_cost)).scalar() or 0
    
    # Support tickets
    open_tickets = SupportTicket.query.filter_by(status='open').count()
    in_progress_tickets = SupportTicket.query.filter_by(status='in_progress').count()
    resolved_tickets = SupportTicket.query.filter_by(status='resolved').count()
    closed_tickets = SupportTicket.query.filter_by(status='closed').count()
    total_tickets = open_tickets + in_progress_tickets + resolved_tickets + closed_tickets
    
    # Blog stats
    total_blogs = Blog.query.count()
    published_blogs = Blog.query.filter_by(status='published').count()
    
    # FAQ count
    total_faqs = FAQ.query.filter_by(is_active=True).count()
    
    # Newsletter
    newsletter_count = NewsletterSubscriber.query.count()
    
    # Contact messages
    contact_messages = ContactMessage.query.count()
    
    # Recent users (last 10)
    recent_users_list = []
    recent_users_query = User.query.order_by(User.created_at.desc()).limit(10).all()
    for user in recent_users_query:
        trade_count = Trade.query.filter_by(user_id=user.id).count()
        recent_users_list.append({
            'username': user.username,
            'email': user.email,
            'subscription_tier': user.subscription_tier,
            'trade_count': trade_count,
            'created_at': user.created_at
        })
    
    # Recent trades (last 10) - build manually with username
    recent_trades_data = []
    raw_trades = Trade.query.order_by(Trade.entry_date.desc()).limit(10).all()
    for trade in raw_trades:
        trade_user = User.query.get(trade.user_id)
        recent_trades_data.append({
            'username': trade_user.username if trade_user else 'Unknown',
            'symbol': trade.symbol or 'N/A',
            'trade_type': trade.trade_type or 'buy',
            'profit_loss': trade.profit_loss,
            'entry_date': trade.entry_date
        })
    
    return render_template('admin/dashboard.html',
        total_users=total_users,
        total_trades=total_trades,
        total_accounts=total_accounts,
        free_users=free_users,
        basic_users=basic_users,
        pro_users=pro_users,
        elite_users=elite_users,
        enterprise_users=enterprise_users,
        active_subs=active_subs,
        revenue=revenue,
        new_users_today=new_users_today,
        trades_today=trades_today,
        total_ai_reports=total_ai_reports,
        total_ai_cost=round(total_ai_cost, 2),
        open_tickets=open_tickets,
        in_progress_tickets=in_progress_tickets,
        resolved_tickets=resolved_tickets,
        closed_tickets=closed_tickets,
        total_tickets=total_tickets,
        total_blogs=total_blogs,
        published_blogs=published_blogs,
        total_faqs=total_faqs,
        newsletter_count=newsletter_count,
        contact_messages=contact_messages,
        recent_users=recent_users_list,
        recent_trades=recent_trades_data
    )


# ═══════════════════════════════════════════════════════════
# 👥 USERS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/users')
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=all_users)


# ═══════════════════════════════════════════════════════════
# 👥 USER DETAIL API
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/api/user-detail/<int:user_id>')
@admin_required
def api_user_detail(user_id):
    """Get full user details for modal"""
    user = User.query.get_or_404(user_id)
    
    # Get subscription info
    sub = Subscription.query.filter_by(user_id=user.id).first()
    
    # Get payment stats
    payment_count = Payment.query.filter_by(user_id=user.id).count()
    successful_payments = Payment.query.filter_by(user_id=user.id, status='SUCCESS').count()
    total_spent = db.session.query(func.sum(Payment.total_amount)).filter(
        Payment.user_id == user.id, 
        Payment.status == 'SUCCESS'
    ).scalar() or 0
    
    # Trading stats
    trade_count = Trade.query.filter_by(user_id=user.id).count()
    account_count = TradingAccount.query.filter_by(user_id=user.id, is_active=True).count()
    diary_count = DiaryEntry.query.filter_by(user_id=user.id).count()
    ai_report_count = AIReport.query.filter_by(user_id=user.id).count()
    ticket_count = SupportTicket.query.filter_by(user_id=user.id).count()
    
    # AI tokens
    ai_tokens_used = user.get_used_tokens()
    
    # Ban status
    is_banned = False
    override = AIUserOverride.query.filter_by(user_id=user.id).first()
    if override:
        is_banned = override.is_banned
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'phone_number': user.phone_number or '',
            'date_of_birth': user.date_of_birth.strftime('%d %b %Y') if user.date_of_birth else '',
            'country': user.country or '',
            'state': user.state or '',
            'is_admin': user.is_admin,
            'email_verified': user.email_verified,
            'email_verified_at': user.email_verified_at.strftime('%d %b %Y %H:%M') if user.email_verified_at else '',
            'subscription_tier': user.subscription_tier,
            'subscription_active': user.subscription_active,
            'created_at': user.created_at.strftime('%d %b %Y'),
            'payment_count': payment_count,
            'successful_payments': successful_payments,
            'total_spent': round(total_spent / 100, 2) if total_spent else 0,
            'sub_start': sub.start_date.strftime('%d %b %Y') if sub and sub.start_date else '',
            'sub_end': sub.end_date.strftime('%d %b %Y') if sub and sub.end_date else '',
            'trade_count': trade_count,
            'account_count': account_count,
            'diary_count': diary_count,
            'ai_report_count': ai_report_count,
            'ai_tokens_used': ai_tokens_used,
            'ticket_count': ticket_count,
            'is_banned': is_banned
        }
    })


@admin_bp.route('/api/user-toggle-ban/<int:user_id>', methods=['POST'])
@admin_required
def api_user_toggle_ban(user_id):
    """Ban or unban a user"""
    user = User.query.get_or_404(user_id)
    
    if user.is_admin:
        return jsonify({'success': False, 'message': 'Cannot ban admin users.'})
    
    data = request.get_json()
    ban = data.get('ban', True)
    
    override = AIUserOverride.query.filter_by(user_id=user.id).first()
    if ban:
        if not override:
            override = AIUserOverride(
                user_id=user.id, 
                is_banned=True, 
                reason='Banned by admin',
                set_by_admin_id=current_user.id
            )
            db.session.add(override)
        else:
            override.is_banned = True
            override.reason = 'Banned by admin'
            override.set_by_admin_id = current_user.id
        message = f'User {user.username} has been banned.'
    else:
        if override:
            override.is_banned = False
            override.reason = None
        message = f'User {user.username} has been unbanned.'
    
    db.session.commit()
    return jsonify({'success': True, 'message': message})


# ═══════════════════════════════════════════════════════════
# 💳 SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/subscriptions')
@admin_required
def subscriptions():
    from datetime import date
    
    today = date.today()
    week_later = today + timedelta(days=7)
    
    # Counts
    free_count = User.query.filter_by(subscription_tier='free').count()
    basic_count = User.query.filter_by(subscription_tier='basic').count()
    pro_count = User.query.filter_by(subscription_tier='pro').count()
    elite_count = User.query.filter_by(subscription_tier='elite').count()
    enterprise_count = User.query.filter_by(subscription_tier='enterprise').count()
    
    total_users = User.query.count()
    total_active = basic_count + pro_count + elite_count + enterprise_count
    
    # Build subscription data
    all_users = User.query.order_by(User.subscription_tier, User.created_at.desc()).all()
    subscriptions = []
    
    monthly_revenue = 0
    monthly_subs = 0
    expiring_soon = 0
    expired_count = 0
    
    for user in all_users:
        sub = Subscription.query.filter_by(user_id=user.id).first()
        
        # Default values
        days_left = None
        status_label = 'free'
        plan_type = '—'
        start_date = '—'
        end_date = '—'
        
        # Only show subscription details if user has a PAID subscription record
        if sub and user.subscription_tier != 'free':
            plan_type = sub.plan_type or 'monthly'
            start_date = sub.start_date.strftime('%d %b %Y') if sub.start_date else '—'
            end_date = sub.end_date.strftime('%d %b %Y') if sub.end_date else '—'
            
            if sub.is_active and sub.end_date:
                if hasattr(sub.end_date, 'date'):
                    days_left = (sub.end_date.date() - today).days
                else:
                    days_left = (sub.end_date - today).days
                
                if days_left > 0:
                    if days_left <= 7:
                        status_label = 'expiring'
                        expiring_soon += 1
                    else:
                        status_label = 'active'
                    
                    # Calculate monthly revenue
                    if plan_type == 'monthly':
                        tier_prices = {'basic': 499, 'pro': 999, 'elite': 799, 'enterprise': 799}
                        monthly_revenue += tier_prices.get(user.subscription_tier, 0)
                        monthly_subs += 1
                else:
                    status_label = 'expired'
                    expired_count += 1
            elif sub.is_active:
                status_label = 'active'
            elif not sub.is_active:
                status_label = 'cancelled'
        
        subscriptions.append({
            'user_id': user.id,
            'username': user.username,
            'email': user.email,
            'tier': user.subscription_tier,
            'plan_type': plan_type,
            'status_label': status_label,
            'start_date': start_date,
            'end_date': end_date,
            'days_left': days_left,
        })
    
    return render_template('admin/subscriptions.html',
        subscriptions=subscriptions,
        free_count=free_count,
        basic_count=basic_count,
        pro_count=pro_count,
        elite_count=elite_count,
        enterprise_count=enterprise_count,
        total_users=total_users,
        total_active=total_active,
        monthly_revenue=monthly_revenue,
        monthly_subs=monthly_subs,
        expiring_soon=expiring_soon,
        expired_count=expired_count
    )




@admin_bp.route('/api/subscription-detail/<int:user_id>')
@admin_required
def api_subscription_detail(user_id):
    """Get subscription details for modal"""
    user = User.query.get_or_404(user_id)
    sub = Subscription.query.filter_by(user_id=user.id).first()
    
    today = date.today()
    days_left = None
    status_label = 'free'
    
    if sub:
        if sub.end_date:
            days_left = (sub.end_date.date() - today).days if hasattr(sub.end_date, 'date') else (sub.end_date - today).days
            if days_left > 0:
                status_label = 'expiring' if days_left <= 7 else 'active'
            else:
                status_label = 'expired'
        if not sub.is_active:
            status_label = 'cancelled'
    
    payment_count = Payment.query.filter_by(user_id=user.id).count()
    successful_payments = Payment.query.filter_by(user_id=user.id, status='SUCCESS').count()
    total_spent = db.session.query(func.sum(Payment.total_amount)).filter(
        Payment.user_id == user.id, Payment.status == 'SUCCESS'
    ).scalar() or 0
    
    return jsonify({
        'success': True,
        'subscription': {
            'username': user.username,
            'email': user.email,
            'joined': user.created_at.strftime('%d %b %Y'),
            'tier': user.subscription_tier,
            'plan_type': sub.plan_type if sub else 'monthly',
            'status_label': status_label,
            'start_date': sub.start_date.strftime('%d %b %Y') if sub and sub.start_date else '—',
            'end_date': sub.end_date.strftime('%d %b %Y') if sub and sub.end_date else '—',
            'days_left': days_left if days_left is not None else 0,
            'auto_renew': sub.auto_renew if sub else False,
            'cancelled_at': sub.cancelled_at.strftime('%d %b %Y') if sub and sub.cancelled_at else None,
            'cancel_reason': sub.cancel_reason if sub else None,
            'payment_count': payment_count,
            'successful_payments': successful_payments,
            'total_spent': round(total_spent / 100, 2) if total_spent else 0
        }
    })


# ═══════════════════════════════════════════════════════════
# 📈 ANALYTICS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/analytics')
@admin_required
def analytics():
    total_users = User.query.count()
    total_trades = Trade.query.count()
    
    return render_template('admin/analytics.html',
        total_users=total_users,
        total_trades=total_trades
    )


# ═══════════════════════════════════════════════════════════
# ⚙️ SETTINGS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/settings')
@admin_required
def settings():
    """Redirect to SEO settings as the main settings page"""
    return redirect(url_for('admin.seo_settings'))


# ═══════════════════════════════════════════════════════════
# 🤖 AI CONTROL CENTER
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/ai-control')
@admin_required
def ai_control():
    total_ai_users = db.session.query(func.count(func.distinct(AIUsageLog.user_id))).scalar() or 0
    today_queries = AIUsageLog.query.filter(func.date(AIUsageLog.created_at) == date.today()).count()
    
    month_start = date.today().replace(day=1)
    month_tokens = db.session.query(func.sum(AIUsageLog.total_tokens)).filter(func.date(AIUsageLog.created_at) >= month_start).scalar() or 0
    month_cost = db.session.query(func.sum(AIUsageLog.api_cost)).filter(func.date(AIUsageLog.created_at) >= month_start).scalar() or 0
    
    banned_count = AIUserOverride.query.filter_by(is_banned=True).count()
    plan_defaults = AIPlanDefaults.query.order_by(AIPlanDefaults.plan_tier).all()
    
    top_spenders = db.session.query(
        User.username, User.email, User.subscription_tier,
        func.sum(AIUsageLog.total_tokens).label('total_tokens'),
        func.sum(AIUsageLog.api_cost).label('total_cost')
    ).join(AIUsageLog, User.id == AIUsageLog.user_id)\
        .filter(func.date(AIUsageLog.created_at) >= month_start)\
        .group_by(User.id)\
        .order_by(func.sum(AIUsageLog.api_cost).desc()).limit(10).all()
    
    return render_template('admin/ai_control.html',
        total_ai_users=total_ai_users, today_queries=today_queries,
        month_tokens=month_tokens, month_cost=round(month_cost, 2),
        banned_count=banned_count, plan_defaults=plan_defaults, top_spenders=top_spenders
    )


# ═══════════════════════════════════════════════════════════
# 🔍 USER AI MANAGEMENT
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/api/ai-users')
@admin_required
def api_ai_users():
    users = User.query.order_by(User.created_at.desc()).all()
    result = []
    for user in users:
        token_limit = user.get_token_limit()
        tokens_used = user.get_used_tokens()
        month_start = date.today().replace(day=1)
        user_cost = db.session.query(func.sum(AIUsageLog.api_cost)).filter(AIUsageLog.user_id == user.id, func.date(AIUsageLog.created_at) >= month_start).scalar() or 0
        override = AIUserOverride.query.filter_by(user_id=user.id).first()
        result.append({
            'id': user.id, 'username': user.username, 'email': user.email,
            'plan': user.subscription_tier, 'token_limit': token_limit,
            'tokens_used': tokens_used, 'tokens_remaining': token_limit - tokens_used,
            'monthly_cost': round(user_cost, 4),
            'is_banned': override.is_banned if override else False,
            'has_override': override is not None and override.override_tokens is not None,
            'override_tokens': override.override_tokens if override else None
        })
    return jsonify({'users': result})


@admin_bp.route('/api/ai-users/<int:user_id>/ban', methods=['POST'])
@admin_required
def api_ban_user_ai(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    ban = data.get('ban', True)
    reason = data.get('reason', '')
    override = AIUserOverride.query.filter_by(user_id=user.id).first()
    if not override:
        override = AIUserOverride(user_id=user.id, is_banned=ban, reason=reason, set_by_admin_id=current_user.id)
        db.session.add(override)
    else:
        override.is_banned = ban
        override.reason = reason
        override.set_by_admin_id = current_user.id
    db.session.commit()
    return jsonify({'success': True, 'message': f'User {user.username} AI access {"banned" if ban else "unbanned"}.'})


@admin_bp.route('/api/ai-users/<int:user_id>/rate-limit', methods=['POST'])
@admin_required
def api_rate_limit_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    override = AIUserOverride.query.filter_by(user_id=user.id).first()
    if not override:
        override = AIUserOverride(user_id=user.id, is_rate_limited=data.get('rate_limited', True), rate_limit_per_hour=data.get('limit_per_hour', 10), set_by_admin_id=current_user.id)
        db.session.add(override)
    else:
        override.is_rate_limited = data.get('rate_limited', True)
        override.rate_limit_per_hour = data.get('limit_per_hour', 10)
        override.set_by_admin_id = current_user.id
    db.session.commit()
    return jsonify({'success': True, 'message': f'Rate limit updated for {user.username}.'})


@admin_bp.route('/api/ai-users/<int:user_id>/override-tokens', methods=['POST'])
@admin_required
def api_override_tokens(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    tokens = data.get('tokens')
    override = AIUserOverride.query.filter_by(user_id=user.id).first()
    if not override:
        override = AIUserOverride(user_id=user.id, override_tokens=tokens, reason=data.get('reason', ''), set_by_admin_id=current_user.id)
        db.session.add(override)
    else:
        override.override_tokens = tokens
        override.reason = data.get('reason', '')
        override.set_by_admin_id = current_user.id
    db.session.commit()
    return jsonify({'success': True, 'message': f'Token override {"set" if tokens else "removed"} for {user.username}.'})


@admin_bp.route('/api/ai-users/<int:user_id>/reset-tokens', methods=['POST'])
@admin_required
def api_reset_tokens(user_id):
    user = User.query.get_or_404(user_id)
    month_start = date.today().replace(day=1)
    AIUsageLog.query.filter(AIUsageLog.user_id == user.id, func.date(AIUsageLog.created_at) >= month_start).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': f'Token usage reset for {user.username}.'})


@admin_bp.route('/api/ai-users/<int:user_id>/logs')
@admin_required
def api_user_ai_logs(user_id):
    user = User.query.get_or_404(user_id)
    logs = AIUsageLog.query.filter_by(user_id=user.id).order_by(AIUsageLog.created_at.desc()).limit(50).all()
    return jsonify({'user': {'id': user.id, 'username': user.username, 'email': user.email}, 'logs': [{'id': l.id, 'type': l.analysis_type, 'model': l.model_used, 'tokens': l.total_tokens, 'cost': l.api_cost, 'status': l.status, 'error': l.error_message, 'date': l.created_at.isoformat() if l.created_at else None} for l in logs]})


@admin_bp.route('/api/ai-users/<int:user_id>/reports')
@admin_required
def api_user_ai_reports(user_id):
    user = User.query.get_or_404(user_id)
    reports = AIReport.query.filter_by(user_id=user.id).order_by(AIReport.created_at.desc()).limit(20).all()
    return jsonify({'user': {'id': user.id, 'username': user.username}, 'reports': [{'id': r.id, 'date': r.report_date.isoformat(), 'trades_analyzed': r.trades_analyzed, 'score': r.performance_score, 'summary': r.user_summary[:300] if r.user_summary else '', 'strengths': r.strengths, 'warnings': r.warnings, 'action_items': r.action_items, 'tokens_used': r.total_tokens, 'cost': r.api_cost, 'model': r.model_used, 'raw_prompt': r.raw_prompt, 'raw_response': r.raw_response} for r in reports]})


@admin_bp.route('/api/ai-plan-defaults', methods=['GET', 'POST'])
@admin_required
def api_plan_defaults():
    if request.method == 'GET':
        defaults = AIPlanDefaults.query.order_by(AIPlanDefaults.plan_tier).all()
        return jsonify({'defaults': [{'plan_tier': d.plan_tier, 'monthly_tokens': d.monthly_tokens, 'daily_requests': d.daily_requests, 'queries_per_week': d.queries_per_week, 'reports_per_week': d.reports_per_week, 'is_active': d.is_active} for d in defaults]})
    data = request.get_json()
    for item in data.get('defaults', []):
        plan_default = AIPlanDefaults.query.filter_by(plan_tier=item['plan_tier']).first()
        if plan_default:
            plan_default.monthly_tokens = item.get('monthly_tokens', plan_default.monthly_tokens)
            plan_default.daily_requests = item.get('daily_requests', plan_default.daily_requests)
            plan_default.queries_per_week = item.get('queries_per_week', plan_default.queries_per_week)
            plan_default.reports_per_week = item.get('reports_per_week', plan_default.reports_per_week)
            plan_default.updated_by_admin_id = current_user.id
            plan_default.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Plan defaults updated!'})


@admin_bp.route('/api/ai-kill-switch', methods=['POST'])
@admin_required
def api_kill_switch():
    data = request.get_json()
    if data.get('disable_all'):
        users = User.query.filter_by(is_admin=False).all()
        for user in users:
            override = AIUserOverride.query.filter_by(user_id=user.id).first()
            if not override:
                db.session.add(AIUserOverride(user_id=user.id, is_banned=True, reason=f'Global Kill Switch: {data.get("reason", "")}', set_by_admin_id=current_user.id))
            else:
                override.is_banned = True
        db.session.commit()
        return jsonify({'success': True, 'message': 'AI disabled for all users.'})
    else:
        AIUserOverride.query.update({AIUserOverride.is_banned: False})
        db.session.commit()
        return jsonify({'success': True, 'message': 'AI re-enabled for all users.'})


@admin_bp.route('/api/ai-cost-analytics')
@admin_required
def api_ai_cost_analytics():
    daily_costs = []
    for i in range(30):
        d = date.today() - timedelta(days=i)
        cost = db.session.query(func.sum(AIUsageLog.api_cost)).filter(func.date(AIUsageLog.created_at) == d).scalar() or 0
        daily_costs.append({'date': d.isoformat(), 'cost': round(cost, 4)})
    month_start = date.today().replace(day=1)
    monthly_cost = db.session.query(func.sum(AIUsageLog.api_cost)).filter(func.date(AIUsageLog.created_at) >= month_start).scalar() or 0
    monthly_tokens = db.session.query(func.sum(AIUsageLog.total_tokens)).filter(func.date(AIUsageLog.created_at) >= month_start).scalar() or 0
    monthly_queries = AIUsageLog.query.filter(func.date(AIUsageLog.created_at) >= month_start).count()
    model_stats = db.session.query(AIUsageLog.model_used, func.count(AIUsageLog.id).label('count'), func.sum(AIUsageLog.total_tokens).label('tokens'), func.sum(AIUsageLog.api_cost).label('cost')).filter(func.date(AIUsageLog.created_at) >= month_start).group_by(AIUsageLog.model_used).all()
    return jsonify({'daily_costs': daily_costs[::-1], 'monthly_summary': {'cost': round(monthly_cost, 2), 'tokens': monthly_tokens, 'queries': monthly_queries}, 'model_breakdown': [{'model': m.model_used or 'unknown', 'count': m.count, 'tokens': m.tokens, 'cost': round(m.cost, 4)} for m in model_stats]})


# ═══════════════════════════════════════════════════════════
# 📚 FAQ MANAGEMENT
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/faq')
@admin_required
def faq_manage():
    faqs = FAQ.query.order_by(FAQ.category, FAQ.display_order).all()
    return render_template('admin/faq_manage.html', faqs=faqs)


@admin_bp.route('/api/faq/create', methods=['POST'])
@admin_required
def api_faq_create():
    data = request.get_json()
    faq = FAQ(question=data['question'], answer=data['answer'], category=data.get('category', 'General'), display_order=data.get('display_order', 0))
    db.session.add(faq)
    db.session.commit()
    return jsonify({'success': True, 'message': 'FAQ created!'})


@admin_bp.route('/api/faq/<int:faq_id>/update', methods=['POST'])
@admin_required
def api_faq_update(faq_id):
    faq = FAQ.query.get_or_404(faq_id)
    data = request.get_json()
    faq.question = data.get('question', faq.question)
    faq.answer = data.get('answer', faq.answer)
    faq.category = data.get('category', faq.category)
    faq.display_order = data.get('display_order', faq.display_order)
    faq.is_active = data.get('is_active', faq.is_active)
    db.session.commit()
    return jsonify({'success': True, 'message': 'FAQ updated!'})


@admin_bp.route('/api/faq/<int:faq_id>/delete', methods=['POST'])
@admin_required
def api_faq_delete(faq_id):
    faq = FAQ.query.get_or_404(faq_id)
    db.session.delete(faq)
    db.session.commit()
    return jsonify({'success': True, 'message': 'FAQ deleted!'})


# ═══════════════════════════════════════════════════════════
# 🎫 SUPPORT TICKETS (Admin)
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/support')
@admin_required
def support_tickets():
    status_filter = request.args.get('status', 'open')
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    if status_filter != 'all':
        tickets = [t for t in tickets if t.status == status_filter]
    return render_template('admin/support.html', tickets=tickets, status_filter=status_filter)


@admin_bp.route('/support/<string:ticket_number>')
@admin_required
def support_ticket_detail(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    return render_template('admin/support_ticket.html', ticket=ticket)


@admin_bp.route('/api/support/<string:ticket_number>/reply', methods=['POST'])
@admin_required
def api_admin_ticket_reply(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    
    if request.is_json:
        data = request.get_json()
        message = data.get('message', '').strip()
    else:
        message = request.form.get('message', '').strip()
    
    if not message:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Message required.'})
        else:
            flash('Message is required.', 'danger')
            return redirect(url_for('admin.support_ticket_detail', ticket_number=ticket_number))
    
    reply = TicketReply(
        ticket_id=ticket.id,
        user_id=None,
        message=message,
        is_admin_reply=True
    )
    db.session.add(reply)
    db.session.flush()
    
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file and file.filename:
            try:
                from routes.user_routes import compress_image
                upload_dir = os.path.join('static', 'uploads', 'tickets', str(ticket.id))
                filename, filepath = compress_image(file, upload_dir, f"ticket_{ticket.id}_admin")
                reply.attachment_url = filepath
            except ImportError:
                pass
    
    ticket.status = 'in_progress'
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    
    if request.is_json:
        return jsonify({'success': True, 'message': 'Reply sent!'})
    else:
        flash('Reply sent successfully!', 'success')
        return redirect(url_for('admin.support_ticket_detail', ticket_number=ticket_number))


@admin_bp.route('/api/support/<string:ticket_number>/status', methods=['POST'])
@admin_required
def api_admin_ticket_status(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    data = request.get_json()
    ticket.status = data['status']
    if data['status'] == 'resolved':
        ticket.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': f'Ticket {data["status"]}!'})


@admin_bp.route('/api/support/<string:ticket_number>/note', methods=['POST'])
@admin_required
def api_admin_ticket_note(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    data = request.get_json()
    ticket.admin_note = data.get('admin_note', '')
    db.session.commit()
    return jsonify({'success': True, 'message': 'Note saved!'})


# ═══════════════════════════════════════════════════════════
# 📝 BLOG & CMS ADMIN
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/blog')
@admin_required
def blog_list():
    blogs = Blog.query.order_by(Blog.created_at.desc()).all()
    return render_template('admin/blog/list.html', blogs=blogs)

@admin_bp.route('/blog/new', methods=['GET', 'POST'])
@admin_required
def blog_new():
    if request.method == 'POST':
        title = request.form.get('title')
        slug = request.form.get('slug')
        content = request.form.get('content')
        status = request.form.get('status', 'draft')
        meta_title = request.form.get('meta_title', '')
        meta_description = request.form.get('meta_description', '')
        
        blog = Blog(
            title=title,
            slug=slug,
            content=content,
            status=status,
            meta_title=meta_title,
            meta_description=meta_description,
            author_id=current_user.id
        )
        db.session.add(blog)
        db.session.commit()
        flash('Blog created successfully', 'success')
        return redirect(url_for('admin.blog_list'))
        
    return render_template('admin/blog/editor.html', blog=None)

@admin_bp.route('/blog/edit/<int:blog_id>', methods=['GET', 'POST'])
@admin_required
def blog_edit(blog_id):
    blog = Blog.query.get_or_404(blog_id)
    if request.method == 'POST':
        blog.title = request.form.get('title')
        blog.slug = request.form.get('slug')
        blog.content = request.form.get('content')
        blog.status = request.form.get('status', 'draft')
        blog.meta_title = request.form.get('meta_title', '')
        blog.meta_description = request.form.get('meta_description', '')
        
        db.session.commit()
        flash('Blog updated successfully', 'success')
        return redirect(url_for('admin.blog_list'))
        
    return render_template('admin/blog/editor.html', blog=blog)

@admin_bp.route('/blog/delete/<int:blog_id>', methods=['POST'])
@admin_required
def blog_delete(blog_id):
    blog = Blog.query.get_or_404(blog_id)
    db.session.delete(blog)
    db.session.commit()
    flash('Blog deleted successfully', 'success')
    return redirect(url_for('admin.blog_list'))

@admin_bp.route('/seo/settings', methods=['GET', 'POST'])
@admin_required
def seo_settings():
    settings = SEOSettings.query.first()
    if not settings:
        settings = SEOSettings()
        db.session.add(settings)
        
    if request.method == 'POST':
        settings.site_title = request.form.get('site_title')
        settings.default_meta_description = request.form.get('default_meta_description')
        settings.robots_txt_content = request.form.get('robots_txt_content')
        db.session.commit()
        flash('SEO Settings updated', 'success')
        return redirect(url_for('admin.seo_settings'))
        
    return render_template('admin/blog/seo_settings.html', settings=settings)



# ═══════════════════════════════════════════════════════════
# 💰 PAYMENT ANALYTICS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/payment-analytics')
@admin_required
def payment_analytics():
    """Complete payment analytics dashboard"""
    from models import Payment, Subscription
    
    # Revenue stats
    total_revenue = db.session.query(func.sum(Payment.total_amount)).filter(
        Payment.status == 'SUCCESS'
    ).scalar() or 0
    total_revenue = round(total_revenue / 100, 2)  # Convert paise to rupees
    
    successful = Payment.query.filter_by(status='SUCCESS').count()
    pending = Payment.query.filter_by(status='PENDING').count()
    failed = Payment.query.filter_by(status='FAILED').count()
    cancelled = Payment.query.filter_by(status='CANCELLED').count()
    total_payments = successful + pending + failed + cancelled
    
    # Unique buyers
    unique_buyers = db.session.query(func.count(func.distinct(Payment.user_id))).filter(
        Payment.status == 'SUCCESS'
    ).scalar() or 0
    
    # Repeat buyers (more than 1 successful payment)
    repeat_buyers_query = db.session.query(
        Payment.user_id, func.count(Payment.id).label('count')
    ).filter(Payment.status == 'SUCCESS').group_by(Payment.user_id).having(func.count(Payment.id) > 1).all()
    repeat_buyers = len(repeat_buyers_query)
    
    # Avg order value
    avg_order = round(total_revenue / successful, 2) if successful > 0 else 0
    
    # Success rate
    success_rate = round((successful / total_payments) * 100, 1) if total_payments > 0 else 0
    
    # Revenue trend (last 30 days)
    revenue_trend = []
    for i in range(30):
        d = date.today() - timedelta(days=29 - i)
        day_total = db.session.query(func.sum(Payment.total_amount)).filter(
            Payment.status == 'SUCCESS',
            func.date(Payment.payment_completed_at) == d
        ).scalar() or 0
        day_count = Payment.query.filter(
            Payment.status == 'SUCCESS',
            func.date(Payment.payment_completed_at) == d
        ).count()
        revenue_trend.append({
            'date': d.strftime('%d %b'),
            'revenue': round(day_total / 100, 2),
            'orders': day_count
        })
    
    # Monthly revenue
    monthly_revenue = []
    for i in range(12):
        month_start = date.today().replace(day=1) - timedelta(days=30 * i)
        month_start = month_start.replace(day=1)
        if i == 0:
            month_end = date.today()
        else:
            if month_start.month == 12:
                month_end = month_start.replace(year=month_start.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = month_start.replace(month=month_start.month + 1, day=1) - timedelta(days=1)
        
        month_total = db.session.query(func.sum(Payment.total_amount)).filter(
            Payment.status == 'SUCCESS',
            func.date(Payment.payment_completed_at) >= month_start,
            func.date(Payment.payment_completed_at) <= month_end
        ).scalar() or 0
        
        monthly_revenue.append({
            'month': month_start.strftime('%b %Y'),
            'revenue': round(month_total / 100, 2)
        })
    monthly_revenue.reverse()
    
    # Top spenders
    top_spenders = db.session.query(
        User.username, User.email,
        func.count(Payment.id).label('orders'),
        func.sum(Payment.total_amount).label('total_spent'),
        func.max(Payment.payment_completed_at).label('last_purchase')
    ).join(Payment, User.id == Payment.user_id)\
        .filter(Payment.status == 'SUCCESS')\
        .group_by(User.id)\
        .order_by(func.sum(Payment.total_amount).desc()).limit(10).all()
    
    # Plan breakdown
    plan_breakdown = db.session.query(
        Payment.plan_tier,
        func.count(Payment.id).label('sales'),
        func.sum(Payment.total_amount).label('revenue')
    ).filter(Payment.status == 'SUCCESS')\
        .group_by(Payment.plan_tier).all()
    
    # Payment status distribution
    status_distribution = {
        'successful': successful,
        'pending': pending,
        'failed': failed,
        'cancelled': cancelled
    }
    
    return render_template('admin/payment_analytics.html',
        total_revenue=total_revenue,
        successful=successful,
        pending=pending,
        failed=failed,
        cancelled=cancelled,
        total_payments=total_payments,
        unique_buyers=unique_buyers,
        repeat_buyers=repeat_buyers,
        avg_order=avg_order,
        success_rate=success_rate,
        revenue_trend=revenue_trend,
        monthly_revenue=monthly_revenue,
        top_spenders=top_spenders,
        plan_breakdown=plan_breakdown,
        status_distribution=status_distribution
    )


@admin_bp.route('/api/payment-transactions')
@admin_required
def api_payment_transactions():
    """API endpoint for transaction log with search and filters"""
    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all')
    plan = request.args.get('plan', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    query = Payment.query
    
    if status != 'all':
        query = query.filter_by(status=status.upper())
    
    if plan != 'all':
        query = query.filter_by(plan_tier=plan)
    
    if search:
        query = query.join(User, Payment.user_id == User.id).filter(
            db.or_(
                User.username.ilike(f'%{search}%'),
                User.email.ilike(f'%{search}%'),
                Payment.cashfree_order_id.ilike(f'%{search}%')
            )
        )
    
    total = query.count()
    payments = query.order_by(Payment.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    transactions = []
    for p in payments:
        user = User.query.get(p.user_id)
        transactions.append({
            'order_id': p.cashfree_order_id,
            'username': user.username if user else 'Unknown',
            'email': user.email if user else '',
            'plan': p.plan_tier.upper() if p.plan_tier else 'N/A',
            'plan_type': p.plan_type or 'monthly',
            'amount': round(p.total_amount / 100, 2) if p.total_amount else 0,
            'currency': p.currency or 'INR',
            'status': p.status,
            'date': p.created_at.strftime('%d %b %Y, %H:%M') if p.created_at else 'N/A',
            'completed': p.payment_completed_at.strftime('%d %b %Y, %H:%M') if p.payment_completed_at else 'N/A',
            'payment_id': p.cashfree_payment_id or 'N/A'
        })
    
    return jsonify({
        'transactions': transactions,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })



# ═══════════════════════════════════════════════════════════
# 💰 SUBSCRIPTION MANAGER
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 🛡️ PURCHASE CONTROL CENTER
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/subscription-manager')
@admin_required
def subscription_manager():
    """Purchase Control Center - Block purchases for maintenance"""
    from models import PurchaseControl
    
    controls = PurchaseControl.query.order_by(PurchaseControl.created_at.desc()).all()
    all_users = User.query.order_by(User.email).all()
    
    # Stats
    active_blocks = len([c for c in controls if c.is_active and not c.is_expired()])
    total_blocks = len(controls)
    
    return render_template('admin/purchase_control.html',
        controls=controls,
        all_users=all_users,
        active_blocks=active_blocks,
        total_blocks=total_blocks
    )


@admin_bp.route('/api/purchase-control/create', methods=['POST'])
@admin_required
def api_create_purchase_control():
    """Create a new purchase block"""
    from models import PurchaseControl
    
    data = request.get_json()
    
    block_type = data.get('block_type', 'all')
    reason = data.get('reason', '').strip()
    admin_notes = data.get('admin_notes', '').strip()
    blocked_tier = data.get('blocked_tier', '')
    blocked_user_ids = data.get('blocked_user_ids', '')
    
    # Duration
    duration_type = data.get('duration_type', 'permanent')  # '1hour', '1day', 'custom', 'permanent'
    custom_minutes = data.get('custom_minutes', 0)
    
    if not reason:
        return jsonify({'success': False, 'message': 'Reason is required.'})
    
    # Calculate end time
    ends_at = None
    if duration_type == '1hour':
        ends_at = datetime.utcnow() + timedelta(hours=1)
    elif duration_type == '1day':
        ends_at = datetime.utcnow() + timedelta(days=1)
    elif duration_type == 'custom' and custom_minutes > 0:
        ends_at = datetime.utcnow() + timedelta(minutes=int(custom_minutes))
    
    control = PurchaseControl(
        block_type=block_type,
        blocked_tier=blocked_tier if block_type == 'specific_tier' else None,
        blocked_user_ids=blocked_user_ids if block_type == 'specific_users' else None,
        reason=reason,
        admin_notes=admin_notes or None,
        starts_at=datetime.utcnow(),
        ends_at=ends_at,
        is_active=True,
        created_by_admin_id=current_user.id
    )
    db.session.add(control)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Purchase block created!', 'id': control.id})


@admin_bp.route('/api/purchase-control/<int:control_id>/toggle', methods=['POST'])
@admin_required
def api_toggle_purchase_control(control_id):
    """Toggle a purchase block on/off"""
    from models import PurchaseControl
    
    control = PurchaseControl.query.get_or_404(control_id)
    control.is_active = not control.is_active
    db.session.commit()
    
    status = 'activated' if control.is_active else 'deactivated'
    return jsonify({'success': True, 'message': f'Block {status}!', 'is_active': control.is_active})


@admin_bp.route('/api/purchase-control/<int:control_id>/delete', methods=['POST'])
@admin_required
def api_delete_purchase_control(control_id):
    """Delete a purchase control"""
    from models import PurchaseControl
    
    control = PurchaseControl.query.get_or_404(control_id)
    db.session.delete(control)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Block deleted!'})


@admin_bp.route('/api/purchase-control/<int:control_id>/extend', methods=['POST'])
@admin_required
def api_extend_purchase_control(control_id):
    """Extend a purchase block's duration"""
    from models import PurchaseControl
    
    control = PurchaseControl.query.get_or_404(control_id)
    data = request.get_json()
    add_minutes = int(data.get('add_minutes', 60))
    
    if control.ends_at:
        control.ends_at = control.ends_at + timedelta(minutes=add_minutes)
    else:
        control.ends_at = datetime.utcnow() + timedelta(minutes=add_minutes)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Block extended by {add_minutes} minutes!',
        'ends_at': control.ends_at.strftime('%d %b %Y, %H:%M') if control.ends_at else 'Permanent'
    })



# ═══════════════════════════════════════════════════════════
# 🔄 SYNC ADMIN ROUTES
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/sync')
@admin_required
def admin_sync():
    """Admin sync management panel"""
    from models import SyncConnection, User
    from services.sync_service import get_all_connections_stats
    
    stats = get_all_connections_stats()
    
    connections = db.session.query(SyncConnection, User).join(
        User, SyncConnection.user_id == User.id
    ).order_by(SyncConnection.created_at.desc()).all()
    
    return render_template('admin/sync/sync_admin.html',
        stats=stats,
        connections=connections
    )


@admin_bp.route('/sync/<int:connection_id>/stop', methods=['POST'])
@admin_required
def admin_stop_sync(connection_id):
    """Admin stops a sync connection"""
    from services.sync_service import admin_stop_connection
    
    # Get admin_id - works for both super admin and moderator
    admin_id = current_user.id if current_user.is_authenticated else session.get('moderator_id')
    
    reason = request.form.get('reason', 'Stopped by admin')
    success = admin_stop_connection(connection_id, admin_id, reason)
    
    if success:
        flash('✅ Sync connection stopped.', 'success')
    else:
        flash('❌ Connection not found.', 'danger')
    
    return redirect(url_for('admin.admin_sync'))


@admin_bp.route('/sync/<int:connection_id>/start', methods=['POST'])
@admin_required
def admin_start_sync(connection_id):
    """Admin re-enables a sync connection"""
    from services.sync_service import admin_start_connection
    
    success = admin_start_connection(connection_id)
    
    if success:
        flash('✅ Sync connection re-enabled.', 'success')
    else:
        flash('❌ Connection not found.', 'danger')
    
    return redirect(url_for('admin.admin_sync'))

@admin_bp.route('/sync/user/<int:user_id>/stop-all', methods=['POST'])
@admin_required
def admin_stop_all_user_sync(user_id):
    """Admin stops all connections for a user"""
    from services.sync_service import admin_stop_all_user_connections
    
    admin_id = current_user.id if current_user.is_authenticated else session.get('moderator_id')
    
    reason = request.form.get('reason', 'Admin stopped all connections')
    count = admin_stop_all_user_connections(user_id, admin_id, reason)
    
    flash(f'✅ Stopped {count} sync connections for user #{user_id}.', 'success')
    return redirect(url_for('admin.admin_sync'))


@admin_bp.route('/sync/user/<int:user_id>/logs')
@admin_required
def admin_user_sync_logs(user_id):
    """Get sync logs for a specific user (JSON)"""
    from services.sync_service import get_user_sync_stats
    
    stats = get_user_sync_stats(user_id)
    return jsonify(stats)


@admin_bp.route('/sync/<int:connection_id>/detail')
@admin_required
def admin_sync_detail(connection_id):
    """Full detail page for one sync connection"""
    from models import SyncConnection, User, TradingAccount
    
    connection = SyncConnection.query.get_or_404(connection_id)
    user = User.query.get(connection.user_id)
    account = TradingAccount.query.get(connection.account_id) if connection.account_id else None
    
    # Get recent trades from this connection
    recent_trades = Trade.query.filter_by(
        user_id=connection.user_id,
        account_id=connection.account_id,
        import_source='auto_sync'
    ).order_by(Trade.entry_date.desc()).limit(20).all()
    
    return render_template('admin/sync/sync_detail.html',
        connection=connection,
        user=user,
        account=account,
        recent_trades=recent_trades
    )
