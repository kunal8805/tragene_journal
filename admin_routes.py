from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from extensions import db
from models import (
    User, Trade, TradingAccount,
    AIReport, AIUsageLog, AIPlanDefaults, AIUserOverride,
    FAQ, SupportTicket, TicketReply,
    Blog, Category, Tag, SEOSettings, Redirect, NewsletterSubscriber, PageMetadata
)
from ai_service import estimate_tokens, estimate_cost
from datetime import datetime, date, timedelta
from sqlalchemy import func
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return "Access Denied", 403
        return f(*args, **kwargs)
    return decorated_function

# ═══════════════════════════════════════════════════════════
# 📊 DASHBOARD
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    total_users = User.query.count()
    total_trades = Trade.query.count()
    total_accounts = TradingAccount.query.count()
    
    free_users = User.query.filter_by(subscription_tier='free').count()
    basic_users = User.query.filter_by(subscription_tier='basic').count()
    pro_users = User.query.filter_by(subscription_tier='pro').count()
    enterprise_users = User.query.filter_by(subscription_tier='enterprise').count()
    
    revenue = (basic_users * 499) + (pro_users * 999) + (enterprise_users * 799)
    
    total_ai_reports = AIReport.query.count()
    total_ai_cost = db.session.query(func.sum(AIUsageLog.api_cost)).scalar() or 0
    
    return render_template('admin/dashboard.html',
        total_users=total_users,
        total_trades=total_trades,
        total_accounts=total_accounts,
        free_users=free_users,
        basic_users=basic_users,
        pro_users=pro_users,
        enterprise_users=enterprise_users,
        revenue=revenue,
        total_ai_reports=total_ai_reports,
        total_ai_cost=round(total_ai_cost, 2)
    )


# ═══════════════════════════════════════════════════════════
# 👥 USERS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=all_users)


# ═══════════════════════════════════════════════════════════
# 💳 SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/subscriptions')
@login_required
@admin_required
def subscriptions():
    all_users = User.query.order_by(User.subscription_tier, User.created_at.desc()).all()
    free_count = User.query.filter_by(subscription_tier='free').count()
    basic_count = User.query.filter_by(subscription_tier='basic').count()
    pro_count = User.query.filter_by(subscription_tier='pro').count()
    enterprise_count = User.query.filter_by(subscription_tier='enterprise').count()
    
    return render_template('admin/subscriptions.html',
        users=all_users,
        free_count=free_count,
        basic_count=basic_count,
        pro_count=pro_count,
        enterprise_count=enterprise_count
    )


# ═══════════════════════════════════════════════════════════
# 📈 ANALYTICS
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/analytics')
@login_required
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
@login_required
@admin_required
def settings():
    """Redirect to SEO settings as the main settings page"""
    return redirect(url_for('admin.seo_settings'))


# ═══════════════════════════════════════════════════════════
# 🤖 AI CONTROL CENTER
# ═══════════════════════════════════════════════════════════

@admin_bp.route('/ai-control')
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
@admin_required
def api_reset_tokens(user_id):
    user = User.query.get_or_404(user_id)
    month_start = date.today().replace(day=1)
    AIUsageLog.query.filter(AIUsageLog.user_id == user.id, func.date(AIUsageLog.created_at) >= month_start).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': f'Token usage reset for {user.username}.'})


@admin_bp.route('/api/ai-users/<int:user_id>/logs')
@login_required
@admin_required
def api_user_ai_logs(user_id):
    user = User.query.get_or_404(user_id)
    logs = AIUsageLog.query.filter_by(user_id=user.id).order_by(AIUsageLog.created_at.desc()).limit(50).all()
    return jsonify({'user': {'id': user.id, 'username': user.username, 'email': user.email}, 'logs': [{'id': l.id, 'type': l.analysis_type, 'model': l.model_used, 'tokens': l.total_tokens, 'cost': l.api_cost, 'status': l.status, 'error': l.error_message, 'date': l.created_at.isoformat() if l.created_at else None} for l in logs]})


@admin_bp.route('/api/ai-users/<int:user_id>/reports')
@login_required
@admin_required
def api_user_ai_reports(user_id):
    user = User.query.get_or_404(user_id)
    reports = AIReport.query.filter_by(user_id=user.id).order_by(AIReport.created_at.desc()).limit(20).all()
    return jsonify({'user': {'id': user.id, 'username': user.username}, 'reports': [{'id': r.id, 'date': r.report_date.isoformat(), 'trades_analyzed': r.trades_analyzed, 'score': r.performance_score, 'summary': r.user_summary[:300] if r.user_summary else '', 'strengths': r.strengths, 'warnings': r.warnings, 'action_items': r.action_items, 'tokens_used': r.total_tokens, 'cost': r.api_cost, 'model': r.model_used, 'raw_prompt': r.raw_prompt, 'raw_response': r.raw_response} for r in reports]})


@admin_bp.route('/api/ai-plan-defaults', methods=['GET', 'POST'])
@login_required
@admin_required
def api_plan_defaults():
    if request.method == 'GET':
        defaults = AIPlanDefaults.query.order_by(AIPlanDefaults.plan_tier).all()
        return jsonify({'defaults': [{'plan_tier': d.plan_tier, 'monthly_tokens': d.monthly_tokens, 'queries_per_week': d.queries_per_week, 'reports_per_week': d.reports_per_week, 'is_active': d.is_active} for d in defaults]})
    data = request.get_json()
    for item in data.get('defaults', []):
        plan_default = AIPlanDefaults.query.filter_by(plan_tier=item['plan_tier']).first()
        if plan_default:
            plan_default.monthly_tokens = item.get('monthly_tokens', plan_default.monthly_tokens)
            plan_default.queries_per_week = item.get('queries_per_week', plan_default.queries_per_week)
            plan_default.reports_per_week = item.get('reports_per_week', plan_default.reports_per_week)
            plan_default.updated_by_admin_id = current_user.id
            plan_default.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Plan defaults updated!'})


@admin_bp.route('/api/ai-kill-switch', methods=['POST'])
@login_required
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
@login_required
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
@login_required
@admin_required
def faq_manage():
    faqs = FAQ.query.order_by(FAQ.category, FAQ.display_order).all()
    return render_template('admin/faq_manage.html', faqs=faqs)


@admin_bp.route('/api/faq/create', methods=['POST'])
@login_required
@admin_required
def api_faq_create():
    data = request.get_json()
    faq = FAQ(question=data['question'], answer=data['answer'], category=data.get('category', 'General'), display_order=data.get('display_order', 0))
    db.session.add(faq)
    db.session.commit()
    return jsonify({'success': True, 'message': 'FAQ created!'})


@admin_bp.route('/api/faq/<int:faq_id>/update', methods=['POST'])
@login_required
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
@login_required
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
@login_required
@admin_required
def support_tickets():
    status_filter = request.args.get('status', 'open')
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    if status_filter != 'all':
        tickets = [t for t in tickets if t.status == status_filter]
    return render_template('admin/support.html', tickets=tickets, status_filter=status_filter)


@admin_bp.route('/support/<string:ticket_number>')
@login_required
@admin_required
def support_ticket_detail(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    return render_template('admin/support_ticket.html', ticket=ticket)


@admin_bp.route('/api/support/<string:ticket_number>/reply', methods=['POST'])
@login_required
@admin_required
def api_admin_ticket_reply(ticket_number):
    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()
    
    # Accept both JSON and form data
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
    
    # Handle attachment
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file and file.filename:
            try:
                from user_routes import compress_image
                upload_dir = os.path.join('static', 'uploads', 'tickets', str(ticket.id))
                filename, filepath = compress_image(file, upload_dir, f"ticket_{ticket.id}_admin")
                reply.attachment_url = filepath
            except ImportError:
                pass  # compress_image not available, skip attachment
    
    ticket.status = 'in_progress'
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    
    if request.is_json:
        return jsonify({'success': True, 'message': 'Reply sent!'})
    else:
        flash('Reply sent successfully!', 'success')
        return redirect(url_for('admin.support_ticket_detail', ticket_number=ticket_number))


@admin_bp.route('/api/support/<string:ticket_number>/status', methods=['POST'])
@login_required
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
@login_required
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
@login_required
@admin_required
def blog_list():
    blogs = Blog.query.order_by(Blog.created_at.desc()).all()
    return render_template('admin/blog/list.html', blogs=blogs)

@admin_bp.route('/blog/new', methods=['GET', 'POST'])
@login_required
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
@login_required
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
@login_required
@admin_required
def blog_delete(blog_id):
    blog = Blog.query.get_or_404(blog_id)
    db.session.delete(blog)
    db.session.commit()
    flash('Blog deleted successfully', 'success')
    return redirect(url_for('admin.blog_list'))

@admin_bp.route('/seo/settings', methods=['GET', 'POST'])
@login_required
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