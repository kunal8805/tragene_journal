from datetime import datetime, timedelta
from functools import wraps
from math import ceil

from flask import jsonify, request
from flask_limiter.util import get_remote_address
from flask_login import current_user

from extensions import db
from models import AIPlanDefaults, AIUsageLog


def login_rate_limit_key():
    email = (request.form.get('email') or '').strip().lower()
    return f"{get_remote_address()}:{email or 'missing-email'}"


def ai_rate_limit_key():
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return f"ip:{get_remote_address()}"


def format_retry_after(until):
    seconds = max(1, ceil((until - datetime.utcnow()).total_seconds()))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = ceil(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def lock_duration_for_attempts(failed_attempts):
    if failed_attempts >= 15:
        return timedelta(minutes=30)
    if failed_attempts >= 10:
        return timedelta(minutes=5)
    if failed_attempts >= 5:
        return timedelta(minutes=1)
    return None


def is_user_locked(user):
    if not user.locked_until:
        return False, None
    if user.locked_until <= datetime.utcnow():
        user.locked_until = None
        db.session.commit()
        return False, None
    return True, format_retry_after(user.locked_until)


def record_failed_login(user):
    user.failed_attempts = (user.failed_attempts or 0) + 1
    duration = lock_duration_for_attempts(user.failed_attempts)
    if duration:
        user.locked_until = datetime.utcnow() + duration
    db.session.commit()
    return format_retry_after(user.locked_until) if user.locked_until else None


def reset_failed_login(user):
    user.failed_attempts = 0
    user.locked_until = None


def get_daily_ai_limit(user):
    plan_default = AIPlanDefaults.query.filter_by(
        plan_tier=user.subscription_tier,
        is_active=True
    ).first()
    if plan_default and plan_default.daily_requests is not None:
        return plan_default.daily_requests
    fallback_limits = {
        'free': 2,
        'pro': 50,
        'elite': 150,
        'enterprise': None,
    }
    return fallback_limits.get(user.subscription_tier, 2)


def get_daily_ai_usage(user):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    return AIUsageLog.query.filter(
        AIUsageLog.user_id == user.id,
        AIUsageLog.status == 'success',
        AIUsageLog.created_at >= today_start,
        AIUsageLog.created_at < tomorrow_start
    ).count()


def check_daily_ai_quota(user):
    daily_limit = get_daily_ai_limit(user)
    if daily_limit is None:
        return True, None
    used = get_daily_ai_usage(user)
    if used >= daily_limit:
        return False, (
            f"You've used today's {daily_limit} AI request limit for your "
            f"{user.subscription_tier.title()} plan. Upgrade for more AI requests."
        )
    return True, None


def require_daily_ai_quota(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if current_user.is_authenticated:
            allowed, message = check_daily_ai_quota(current_user)
            if not allowed:
                if request.is_json or request.path.startswith('/api/') or request.method == 'POST':
                    return jsonify({'success': False, 'message': message}), 429
                from flask import flash, redirect, url_for
                flash(message, 'warning')
                return redirect(url_for('ai.ai_reports'))
        return view_func(*args, **kwargs)

    return wrapped
