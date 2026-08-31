# trial.py - FIXED VERSION

from datetime import datetime, timedelta, date
from extensions import db

# ==================== CONSTANTS ====================
TRIAL_DURATION_DAYS = 7
TRIAL_PLAN_VALUE = 799
POPUP_COOLDOWN_DAYS = 2

# ==================== MODEL ====================

class TrialClaim(db.Model):
    """Free trial claim record"""
    __tablename__ = 'trial_claims'
    
    id = db.Column(db.Integer, primary_key=True)
    # FIX: Use string reference to avoid circular import
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    
    claimed_at = db.Column(db.DateTime, default=datetime.utcnow)
    trial_start_date = db.Column(db.Date, nullable=False)
    trial_end_date = db.Column(db.Date, nullable=False)
    
    is_active = db.Column(db.Boolean, default=True)
    has_expired = db.Column(db.Boolean, default=False)
    
    converted_to_paid = db.Column(db.Boolean, default=False)
    converted_at = db.Column(db.DateTime, nullable=True)
    converted_plan = db.Column(db.String(20), nullable=True)
    
    popup_dismissed_count = db.Column(db.Integer, default=0)
    last_popup_shown_at = db.Column(db.DateTime, nullable=True)
    last_popup_dismissed_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship - use string to avoid circular import
    user = db.relationship('User', backref=db.backref('trial_claim', uselist=False, lazy=True))
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None,
            'trial_start_date': str(self.trial_start_date) if self.trial_start_date else None,
            'trial_end_date': str(self.trial_end_date) if self.trial_end_date else None,
            'is_active': self.is_active,
            'has_expired': self.has_expired,
            'converted_to_paid': self.converted_to_paid,
            'converted_at': self.converted_at.isoformat() if self.converted_at else None,
            'converted_plan': self.converted_plan,
            'days_left': self.get_days_left(),
            'popup_dismissed_count': self.popup_dismissed_count
        }
    
    def get_days_left(self):
        if not self.is_active or self.has_expired:
            return 0
        today = date.today()
        if today > self.trial_end_date:
            return 0
        return (self.trial_end_date - today).days
    
    def get_status(self):
        if self.converted_to_paid:
            return 'converted'
        elif self.is_active and not self.has_expired:
            return 'active'
        elif self.has_expired:
            return 'expired'
        else:
            return 'unknown'
    
    def __repr__(self):
        return f'<TrialClaim user_id={self.user_id} status={self.get_status()}>'


# ==================== HELPER FUNCTIONS ====================

def get_trial_claim(user_id):
    return TrialClaim.query.filter_by(user_id=user_id).first()


def is_email_verified(user):
    return getattr(user, 'email_verified', False)


def can_claim_trial(user):
    if not is_email_verified(user):
        return False, 'Email not verified. Please verify your email first.'
    
    existing = get_trial_claim(user.id)
    if existing:
        return False, 'Trial already claimed.'
    
    if user.subscription_tier in ['pro', 'elite', 'enterprise']:
        return False, 'You already have a premium plan.'
    
    return True, None


def claim_trial(user):
    can_claim, error_message = can_claim_trial(user)
    if not can_claim:
        return {'success': False, 'message': error_message}
    
    start_date = date.today()
    end_date = start_date + timedelta(days=TRIAL_DURATION_DAYS)
    
    trial = TrialClaim(
        user_id=user.id,
        trial_start_date=start_date,
        trial_end_date=end_date,
        is_active=True,
        has_expired=False,
        claimed_at=datetime.utcnow()
    )
    
    db.session.add(trial)
    
    # Update user fields (using setattr to avoid model changes)
    setattr(user, 'has_claimed_trial', True)
    setattr(user, 'trial_claimed_at', datetime.utcnow())
    setattr(user, 'trial_end_date', end_date)
    setattr(user, 'is_trial_active', True)
    setattr(user, 'trial_expired', False)
    
    # Temporarily give pro access
    user.subscription_tier = 'pro'
    
    db.session.commit()
    
    return {
        'success': True,
        'message': 'Trial claimed successfully! 7 days of Pro features unlocked.',
        'trial_end_date': str(end_date),
        'days_left': TRIAL_DURATION_DAYS
    }


def check_trial_expiry(user):
    trial = get_trial_claim(user.id)
    
    if not trial:
        return {'success': True, 'status': 'no_trial'}
    
    if trial.converted_to_paid:
        return {'success': True, 'status': 'converted'}
    
    today = date.today()
    
    if today > trial.trial_end_date and trial.is_active:
        trial.is_active = False
        trial.has_expired = True
        
        setattr(user, 'is_trial_active', False)
        setattr(user, 'trial_expired', True)
        user.subscription_tier = 'free'
        
        db.session.commit()
        
        return {
            'success': True,
            'status': 'expired',
            'message': 'Your trial has expired. Upgrade to continue using premium features.'
        }
    
    days_left = trial.get_days_left()
    
    if days_left <= 3 and days_left > 0:
        return {
            'success': True,
            'status': 'active',
            'days_left': days_left,
            'expiring_soon': True,
            'message': f'Your trial ends in {days_left} days.'
        }
    
    return {
        'success': True,
        'status': 'active',
        'days_left': days_left,
        'expiring_soon': False
    }


def should_show_popup(user):
    if user.subscription_tier in ['pro', 'elite', 'enterprise'] and not getattr(user, 'is_trial_active', False):
        return False, None
    
    if getattr(user, 'is_trial_active', False):
        return False, None
    
    trial = get_trial_claim(user.id)
    
    if not trial:
        if is_email_verified(user):
            return True, {'type': 'claim'}
        else:
            return True, {'type': 'verify'}
    
    if trial.has_expired:
        if trial.last_popup_shown_at:
            cooldown_end = trial.last_popup_shown_at + timedelta(days=POPUP_COOLDOWN_DAYS)
            if datetime.utcnow() < cooldown_end:
                return False, None
        
        return True, {'type': 'upgrade'}
    
    return False, None


def record_popup_shown(user):
    trial = get_trial_claim(user.id)
    if trial:
        trial.last_popup_shown_at = datetime.utcnow()
        db.session.commit()


def dismiss_popup(user):
    trial = get_trial_claim(user.id)
    
    if trial:
        trial.popup_dismissed_count = (trial.popup_dismissed_count or 0) + 1
        trial.last_popup_dismissed_at = datetime.utcnow()
        trial.last_popup_shown_at = datetime.utcnow()
        db.session.commit()
    else:
        # If no trial claim yet, create a dummy record for popup tracking
        trial = TrialClaim(
            user_id=user.id,
            trial_start_date=date.today(),
            trial_end_date=date.today(),
            is_active=False,
            has_expired=True,
            popup_dismissed_count=1,
            last_popup_dismissed_at=datetime.utcnow(),
            last_popup_shown_at=datetime.utcnow()
        )
        db.session.add(trial)
        db.session.commit()
    
    return {'success': True}


def get_trial_status(user):
    trial = get_trial_claim(user.id)
    
    result = {
        'success': True,
        'has_claimed': False,
        'is_active': False,
        'has_expired': False,
        'converted': False,
        'email_verified': is_email_verified(user),
        'can_claim': False,
        'days_left': 0,
        'trial_end_date': None,
        'status': 'not_claimed'
    }
    
    if is_email_verified(user) and not trial:
        result['can_claim'] = True
    
    if trial:
        result['has_claimed'] = True
        result['trial_end_date'] = str(trial.trial_end_date)
        result['days_left'] = trial.get_days_left()
        result['converted'] = trial.converted_to_paid
        
        status = trial.get_status()
        result['status'] = status
        
        if status == 'active':
            result['is_active'] = True
        elif status == 'expired':
            result['has_expired'] = True
    
    return result


def mark_converted(user, plan='pro'):
    trial = get_trial_claim(user.id)
    
    if trial:
        trial.converted_to_paid = True
        trial.converted_at = datetime.utcnow()
        trial.converted_plan = plan
        trial.is_active = False
        trial.has_expired = True
        
        setattr(user, 'converted_from_trial', True)
        setattr(user, 'converted_at', datetime.utcnow())
        user.subscription_tier = plan
        setattr(user, 'is_trial_active', False)
        
        db.session.commit()
        
        return {'success': True, 'message': f'Conversion to {plan} recorded.'}
    
    return {'success': False, 'message': 'No trial found for this user.'}


def get_trial_analytics(status_filter='all', date_filter='all', search=''):
    query = TrialClaim.query
    
    if status_filter != 'all':
        if status_filter == 'active':
            query = query.filter(TrialClaim.is_active == True, TrialClaim.has_expired == False)
        elif status_filter == 'expired':
            query = query.filter(TrialClaim.has_expired == True, TrialClaim.converted_to_paid == False)
        elif status_filter == 'converted':
            query = query.filter(TrialClaim.converted_to_paid == True)
    
    if date_filter != 'all':
        days = int(date_filter)
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(TrialClaim.claimed_at >= cutoff)
    
    trials = query.all()
    
    users_data = []
    for trial in trials:
        user = trial.user
        
        if search and search.lower() not in user.username.lower() and search.lower() not in user.email.lower():
            continue
        
        users_data.append({
            'user_id': user.id,
            'username': user.username,
            'email': user.email,
            'email_verified': user.email_verified,
            'status': trial.get_status(),
            'status_display': trial.get_status().replace('_', ' ').title(),
            'claimed_date': str(trial.trial_start_date) if trial.trial_start_date else None,
            'trial_end': str(trial.trial_end_date) if trial.trial_end_date else None,
            'days_left': trial.get_days_left() if trial.is_active else None,
            'converted': trial.converted_to_paid,
            'converted_plan': trial.converted_plan
        })
    
    return users_data


def get_trial_summary():
    from models import User
    
    total_users = User.query.count()
    verified_users = User.query.filter_by(email_verified=True).count()
    
    total_claims = TrialClaim.query.count()
    active_trials = TrialClaim.query.filter_by(is_active=True, has_expired=False).count()
    expired_trials = TrialClaim.query.filter_by(has_expired=True, converted_to_paid=False).count()
    converted_count = TrialClaim.query.filter_by(converted_to_paid=True).count()
    
    conversion_rate = round((converted_count / total_claims * 100), 1) if total_claims > 0 else 0
    revenue = converted_count * TRIAL_PLAN_VALUE
    
    return {
        'total_users': total_users,
        'verified_users': verified_users,
        'total_claims': total_claims,
        'active_trials': active_trials,
        'expired_trials': expired_trials,
        'converted_count': converted_count,
        'conversion_rate': conversion_rate,
        'revenue': revenue
    }


def get_claims_chart_data():
    labels = []
    data = []
    
    for i in range(29, -1, -1):
        day = date.today() - timedelta(days=i)
        labels.append(day.strftime('%d %b'))
        
        count = TrialClaim.query.filter(
            db.func.date(TrialClaim.claimed_at) == day
        ).count()
        
        data.append(count)
    
    return {'labels': labels, 'data': data}


def get_conversion_chart_data():
    pro_count = TrialClaim.query.filter_by(converted_to_paid=True, converted_plan='pro').count()
    elite_count = TrialClaim.query.filter_by(converted_to_paid=True, converted_plan='elite').count()
    
    return {
        'labels': ['Pro (₹799)', 'Elite (₹1499)'],
        'data': [pro_count, elite_count]
    }


def extend_trial(user_id, days=7):
    trial = get_trial_claim(user_id)
    
    if not trial:
        return {'success': False, 'message': 'No trial found for this user.'}
    
    from models import User
    user = User.query.get(user_id)
    
    if not user:
        return {'success': False, 'message': 'User not found.'}
    
    new_end_date = trial.trial_end_date + timedelta(days=days)
    trial.trial_end_date = new_end_date
    trial.is_active = True
    trial.has_expired = False
    
    setattr(user, 'trial_end_date', new_end_date)
    setattr(user, 'is_trial_active', True)
    setattr(user, 'trial_expired', False)
    user.subscription_tier = 'pro'
    
    db.session.commit()
    
    return {'success': True, 'message': f'Trial extended by {days} days.'}


def revoke_trial(user_id):
    trial = get_trial_claim(user_id)
    
    if not trial:
        return {'success': False, 'message': 'No trial found for this user.'}
    
    from models import User
    user = User.query.get(user_id)
    
    if not user:
        return {'success': False, 'message': 'User not found.'}
    
    trial.is_active = False
    trial.has_expired = True
    
    setattr(user, 'is_trial_active', False)
    setattr(user, 'trial_expired', True)
    user.subscription_tier = 'free'
    
    db.session.commit()
    
    return {'success': True, 'message': 'Trial revoked.'}


def check_all_trials_expiry():
    from models import User
    
    active_trials = TrialClaim.query.filter_by(is_active=True, has_expired=False).all()
    expired_count = 0
    
    for trial in active_trials:
        if date.today() > trial.trial_end_date:
            trial.is_active = False
            trial.has_expired = True
            
            user = User.query.get(trial.user_id)
            if user:
                setattr(user, 'is_trial_active', False)
                setattr(user, 'trial_expired', True)
                user.subscription_tier = 'free'
            
            expired_count += 1
    
    if expired_count > 0:
        db.session.commit()
    
    return expired_count