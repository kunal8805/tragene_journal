from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db
from models import User, TradingAccount, Moderator
from verify_email import send_verification_email, verify_email_token
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))
    
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
        
        login_user(user)
        send_verification_email(user)
        
        flash('Registration successful! Please check your email to verify your account. 📧', 'success')
        return redirect(url_for('user.dashboard'))
    
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # If moderator session exists, go to admin
    if session.get('is_moderator'):
        return redirect(url_for('admin.dashboard'))
    
    # If regular user is logged in
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        remember = request.form.get('remember') == 'on'
        
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
            
            from moderator_routes import log_activity
            log_activity(moderator.id, 'login', 'moderator', moderator.id, 
                        f'Logged in from {request.remote_addr}')
            
            db.session.commit()
            
            flash(f'Welcome back, {moderator.full_name}! 🛡', 'success')
            return redirect(url_for('admin.dashboard'))
        
        # 2. Try regular user login (admin or normal user)
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            # Clear moderator session
            session.clear()
            
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
            
            login_user(user, remember=remember)
            flash(f'Welcome back, {user.full_name}! 👋', 'success')
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            
            if user.is_admin:
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('user.dashboard'))
        
        flash('Invalid email or password. Please try again.', 'danger')
    
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    if current_user.is_authenticated:
        username = current_user.full_name or current_user.username
        logout_user()
        flash(f'Logged out successfully. See you soon, {username}! 👋', 'success')
    
    # Clear everything
    session.clear()
    
    return redirect(url_for('auth.login'))


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