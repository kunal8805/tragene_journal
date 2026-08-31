from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, make_response
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db, limiter
from models import User, TradingAccount, Moderator, LoginDevice
from rate_limits import (
    is_user_locked,
    login_rate_limit_key,
    record_failed_login,
    reset_failed_login,
)
from verify_email import send_verification_email, verify_email_token
from datetime import datetime
import uuid
import hashlib

auth_bp = Blueprint('auth', __name__)

# ==================== DEVICE LIMIT FUNCTIONS ====================

def get_device_id():
    """Get or create unique device ID from cookie"""
    device_id = request.cookies.get('device_id')
    
    if not device_id:
        raw = f"{request.user_agent}{request.remote_addr}{datetime.utcnow()}"
        device_id = hashlib.md5(raw.encode()).hexdigest()
    
    return device_id


def get_device_name():
    """Get device name from user agent"""
    ua = request.user_agent.string.lower()
    
    if 'iphone' in ua:
        return 'iPhone'
    elif 'ipad' in ua:
        return 'iPad'
    elif 'android' in ua:
        return 'Android Phone'
    elif 'windows' in ua:
        return 'Windows PC'
    elif 'macintosh' in ua or 'mac os' in ua:
        return 'Mac'
    elif 'linux' in ua:
        return 'Linux'
    else:
        return 'Unknown Device'


def get_max_devices(user):
    """Get max devices allowed for user's tier"""
    if user.is_admin:
        return float('inf')
    
    tier_limits = {
        'free': 2,
        'pro': 3,
        'elite': 5,
        'enterprise': float('inf')
    }
    return tier_limits.get(user.subscription_tier, 2)


def check_device_limit(user):
    """Check if user has exceeded device limit. Returns (can_login, message, device_id)"""
    device_id = get_device_id()
    max_devices = get_max_devices(user)
    
    # Admin - unlimited devices
    if user.is_admin or max_devices == float('inf'):
        existing_device = LoginDevice.query.filter_by(
            user_id=user.id,
            device_id=device_id
        ).first()
        
        if not existing_device:
            new_device = LoginDevice(
                user_id=user.id,
                device_id=device_id,
                device_name=get_device_name(),
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string[:500]
            )
            db.session.add(new_device)
            db.session.commit()
        else:
            existing_device.last_active = datetime.utcnow()
            existing_device.is_active = True
            db.session.commit()
        
        return True, None, device_id
    
    # Check if this device already registered
    existing_device = LoginDevice.query.filter_by(
        user_id=user.id,
        device_id=device_id
    ).first()
    
    if existing_device:
        existing_device.last_active = datetime.utcnow()
        existing_device.is_active = True
        db.session.commit()
        return True, None, device_id
    
    # Count active devices
    active_devices = LoginDevice.query.filter_by(
        user_id=user.id,
        is_active=True
    ).count()
    
    # Check limit
    if active_devices >= max_devices:
        tier_name = user.subscription_tier.capitalize()
        return False, f"Maximum {max_devices} devices allowed on {tier_name} plan. Log out from another device or upgrade.", None
    
    # Register new device
    new_device = LoginDevice(
        user_id=user.id,
        device_id=device_id,
        device_name=get_device_name(),
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string[:500]
    )
    db.session.add(new_device)
    db.session.commit()
    
    return True, None, device_id


def remove_device(user_id, device_id):
    """Remove/logout a specific device"""
    device = LoginDevice.query.filter_by(
        user_id=user_id,
        device_id=device_id
    ).first()
    
    if device:
        device.is_active = False
        db.session.commit()
        return True
    return False


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # If already authenticated, redirect
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))
    
    # Clean stale session data
    if session and not session.get('is_moderator'):
        session.clear()
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        phone_number = request.form.get('phone', '').strip()
        date_of_birth_str = request.form.get('dob')
        country = request.form.get('country')
        state = request.form.get('state')
        
        terms_accepted = request.form.get('terms') == 'on'
        
        if not username or not email or not password:
            flash('All required fields must be filled.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if not first_name or not last_name:
            flash('First name and last name are required.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
            flash('Password must contain both letters and numbers.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if not terms_accepted:
            flash('You must agree to the Terms & Conditions and Privacy Policy.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose another.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please login or use another email.', 'danger')
            return render_template('register.html', form_data=request.form)
        
        date_of_birth = None
        if date_of_birth_str:
            try:
                date_of_birth = datetime.strptime(date_of_birth_str, '%Y-%m-%d').date()
                age = (datetime.now().date() - date_of_birth).days // 365
                if age < 18:
                    flash('You must be at least 18 years old to register.', 'danger')
                    return render_template('register.html', form_data=request.form)
            except ValueError:
                flash('Invalid date format. Please select a valid date.', 'danger')
                return render_template('register.html', form_data=request.form)
        
        if phone_number:
            if len(phone_number) < 10:
                flash('Please enter a valid phone number.', 'danger')
                return render_template('register.html', form_data=request.form)
        
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number if phone_number else None,
            date_of_birth=date_of_birth,
            country=country,
            state=state,
            subscription_tier='free',
            email_verified=False
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.flush()
        
        account = TradingAccount(
            user_id=user.id,
            name='My First Account',
            account_type='live',
            currency='USD'
        )
        db.session.add(account)
        db.session.flush()
        
        user.current_account_id = account.id
        db.session.commit()
        
        # Clear any existing session and set new one
        session.clear()
        session.permanent = True
        session['session_version'] = user.session_version or 0
        login_user(user, remember=True)
        send_verification_email(user)
        
        flash('Registration successful! Please check your email to verify your account. 📧', 'success')
        return redirect(url_for('user.dashboard'))
    
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit(
    "5 per minute",
    key_func=login_rate_limit_key,
    methods=["POST"],
    error_message="Too many login attempts for this email from your network. Please try again in a minute."
)
def login():
    # If moderator session exists, go to admin
    if session.get('is_moderator'):
        return redirect(url_for('admin.dashboard'))
    
    # If regular user is logged in
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))
    
    # Clean stale session data (but keep moderator session)
    if session and not session.get('is_moderator'):
        session.clear()
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        
        if not email or not password:
            flash('Please enter email and password.', 'danger')
            return render_template('login.html')
        
        # 1. Try moderator login FIRST
        moderator = Moderator.query.filter_by(email=email).first()
        
        if moderator and moderator.check_password(password):
            if moderator.is_banned:
                flash(f'Account banned. Reason: {moderator.ban_reason}', 'danger')
                return render_template('login.html')
            
            if not moderator.is_active:
                flash('Account is inactive. Contact admin.', 'danger')
                return render_template('login.html')
            
            # Clear Flask-Login session
            if current_user.is_authenticated:
                logout_user()
            
            # Clear everything and set moderator session
            session.clear()
            session['moderator_id'] = moderator.id
            session['is_moderator'] = True
            session.permanent = True
            
            moderator.last_login_at = datetime.utcnow()
            moderator.last_login_ip = request.remote_addr
            
            from routes.moderator_routes import log_activity
            log_activity(moderator.id, 'login', 'moderator', moderator.id, 
                        f'Logged in from {request.remote_addr}')
            
            db.session.commit()
            
            flash(f'Welcome back, {moderator.full_name}! 🛡', 'success')
            return redirect(url_for('admin.dashboard'))
        
        # 2. Try regular user login (admin or normal user)
        user = User.query.filter_by(email=email).first()
        if user:
            locked, retry_after = is_user_locked(user)
            if locked:
                flash(f'Too many failed attempts. Try again in {retry_after}.', 'danger')
                return render_template('login.html')
        
        if user and user.check_password(password):
            # Clear moderator session
            session.clear()
            session.permanent = True
            
            # Check device limit
            can_login, device_message, device_id = check_device_limit(user)
            if not can_login:
                flash(device_message, 'danger')
                return render_template('login.html')
            
            if not user.get_active_account():
                account = TradingAccount(
                    user_id=user.id,
                    name='My First Account',
                    account_type='live',
                    currency=user.account_currency or 'USD'
                )
                db.session.add(account)
                db.session.flush()
                user.current_account_id = account.id
                db.session.commit()
            
            reset_failed_login(user)
            session['session_version'] = user.session_version or 0
            login_user(user, remember=True)
            db.session.commit()
            flash(f'Welcome back, {user.full_name}! 👋', 'success')
            
            next_page = request.args.get('next')
            
            # Set device cookie
            if device_id:
                response = redirect(next_page if next_page else (url_for('admin.dashboard') if user.is_admin else url_for('user.dashboard')))
                response.set_cookie('device_id', device_id, max_age=60*60*24*365)
                return response
            
            if next_page:
                return redirect(next_page)
            
            if user.is_admin:
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('user.dashboard'))
        
        if user:
            retry_after = record_failed_login(user)
            if retry_after:
                flash(f'Too many failed attempts. Try again in {retry_after}.', 'danger')
                return render_template('login.html')
        
        flash('Invalid email or password. Please try again.', 'danger')
    
    return render_template('login.html')


@auth_bp.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    if request.method == 'GET':
        return render_template('logout.html')
    
    logout_scope = request.form.get('logout_scope', 'device')
    username = current_user.full_name or current_user.username
    
    if logout_scope == 'all':
        current_user.session_version = (current_user.session_version or 0) + 1
        db.session.commit()
        logout_user()
        session.clear()
        flash('You have been logged out of all devices.', 'success')
        return redirect(url_for('auth.login'))
    
    # Remove device on logout
    device_id = request.cookies.get('device_id')
    if device_id:
        remove_device(current_user.id, device_id)
    
    logout_user()
    session.clear()
    flash(f'Logged out successfully. See you soon, {username}! 👋', 'success')
    
    response = redirect(url_for('auth.login'))
    response.delete_cookie('device_id')
    return response


@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    result = verify_email_token(token)
    
    if result['success']:
        flash(result['message'], 'success')
    else:
        flash(result['message'], 'danger')
    
    if current_user.is_authenticated:
        return redirect(url_for('user.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/resend-verification')
@login_required
def resend_verification():
    if current_user.email_verified:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Your email is already verified! ✅'})
        flash('Your email is already verified! ✅', 'success')
        return redirect(url_for('user.dashboard'))
    
    result = send_verification_email(current_user)
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result)
    
    if result['success']:
        flash(result['message'], 'success')
    else:
        flash(result['message'], 'danger')
    
    return redirect(url_for('user.dashboard'))


# ==================== DEVICE MANAGEMENT ROUTES ====================

@auth_bp.route('/my-devices')
@login_required
def my_devices():
    """Show user's logged-in devices"""
    devices = LoginDevice.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).order_by(LoginDevice.last_active.desc()).all()
    
    max_devices = get_max_devices(current_user)
    current_device_id = request.cookies.get('device_id')
    
    # Check if AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('json') == 'true':
        return jsonify({
            'success': True,
            'devices': [{
                'id': d.id,
                'device_id': d.device_id,
                'device_name': d.device_name,
                'icon': d.get_device_icon(),
                'ip_address': d.ip_address,
                'last_active_display': d.get_last_active_display()
            } for d in devices],
            'max_devices': 'unlimited' if max_devices == float('inf') else max_devices,
            'current_device_id': current_device_id,
            'device_count': len(devices)
        })
    
    return render_template('user/devices.html',
        devices=devices,
        max_devices=max_devices,
        current_device_id=current_device_id,
        device_count=len(devices)
    )


@auth_bp.route('/api/logout-device/<int:device_id>', methods=['POST'])
@login_required
def api_logout_device(device_id):
    """Logout a specific device"""
    device = LoginDevice.query.get(device_id)
    
    if not device or device.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
    
    device.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Device logged out successfully'})


@auth_bp.route('/api/logout-all-devices', methods=['POST'])
@login_required
def api_logout_all_devices():
    """Logout from all devices except current"""
    current_device_id = request.cookies.get('device_id')
    
    # Deactivate all other devices
    LoginDevice.query.filter(
        LoginDevice.user_id == current_user.id,
        LoginDevice.device_id != current_device_id,
        LoginDevice.is_active == True
    ).update({'is_active': False})
    
    # Increment session version
    current_user.session_version = (current_user.session_version or 0) + 1
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Logged out from all other devices'})