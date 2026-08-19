from flask import Flask, render_template, flash, redirect, url_for, request, session, Response, jsonify
from extensions import db, login_manager, migrate
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

def create_app():
    app = Flask(__name__)
    
    basedir = os.path.abspath(os.path.dirname(__file__))
    
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(basedir, 'trading_journal.db'))
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'
    
    from models import User, Trade, ImportHistory, DayNote, TradingAccount, Category, Tag, Blog, SEOSettings, PageMetadata, Redirect, NewsletterSubscriber, MediaLibrary, ContactMessage, Coupon, CouponUsage, CouponUser, LeadStatus, LeadNote, LeadFollowUp, Influencer, InfluencerCampaign, seed_lead_statuses
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    from auth import auth_bp
    from user_routes import user_bp
    from admin_routes import admin_bp
    from tools import tools_bp
    from ai_routes import ai_bp
    from payment_routes import payment_bp
    from blog_routes import blog_bp
    from seo_routes import seo_bp
    from moderator_routes import moderator_bp
    from coupon_routes import coupon_bp
    from lead_routes import lead_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(tools_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(blog_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(moderator_bp)
    app.register_blueprint(coupon_bp)
    app.register_blueprint(lead_bp)
    
    with app.app_context():
        db_path = os.path.join(basedir, 'trading_journal.db')
        if not os.path.exists(db_path):
            db.create_all()
            create_admin()
            seed_template_rules()
            seed_ai_plan_defaults()
            seed_moderator_permissions()
            seed_lead_statuses()
            print("✅ Database created with all tables!")
        else:
            db.create_all()
            seed_template_rules()
            seed_ai_plan_defaults()
            seed_moderator_permissions()
            seed_lead_statuses()
            migrate_existing_data()

    @app.context_processor
    def inject_seo():
        from models import SEOSettings, PageMetadata
        settings = SEOSettings.query.first()
        from flask import request
        path = request.path
        page_meta = PageMetadata.query.filter_by(page_route=path).first()
        if not page_meta and path == '/':
            page_meta = PageMetadata.query.filter_by(page_route='index').first()
        return dict(seo_settings=settings, page_meta=page_meta)

    @app.context_processor
    def inject_market_helpers():
        from models import detect_market, get_market_info, get_currency_for_market
        return dict(
            detect_market=detect_market,
            get_market_info=get_market_info,
            get_currency_for_market=get_currency_for_market
        )

    @app.context_processor
    def inject_moderator_permissions():
        """Make moderator permissions available in all templates"""
        from models import Moderator
        
        mod_id = session.get('moderator_id')
        if mod_id:
            moderator = Moderator.query.get(mod_id)
            if moderator:
                allowed = moderator.get_allowed_permissions()
                return {
                    'is_moderator': True,
                    'moderator': moderator,
                    'can_access': lambda key: key in allowed
                }
        
        return {
            'is_moderator': False,
            'moderator': None,
            'can_access': lambda key: True  # Admin sees everything
        }

    @app.before_request
    def check_redirects():
        from flask import request, redirect
        from models import Redirect
        if request.path.startswith('/static/'):
            return
        r = Redirect.query.filter_by(old_path=request.path).first()
        if r:
            return redirect(r.new_path, code=r.status_code)

    @app.errorhandler(404)
    def page_not_found(e):
        try:
            from models import Blog
            from flask import render_template
            popular_blogs = Blog.query.filter_by(status='published').order_by(Blog.views.desc()).limit(3).all()
            return render_template('404.html', popular_blogs=popular_blogs), 404
        except:
            return "Page not found", 404
            
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response
    
    # ===== MAIN ROUTES =====
    @app.route('/')
    @app.route('/home')
    def home():
        return render_template('index.html')
    
    # ===== ADS.TXT FOR GOOGLE ADSENSE =====
    @app.route('/ads.txt')
    def ads_txt():
        content = "google.com, pub-4811775453229832, DIRECT, f08c47fec0942fa0"
        return Response(content, mimetype='text/plain')
    
    # ===== LEGAL AND STATIC PAGES =====
    @app.route('/terms')
    def terms():
        return render_template('terms.html', now=datetime.now())

    @app.route('/privacy')
    def privacy():
        return render_template('privacy.html', now=datetime.now())

    @app.route('/refund-policy')
    @app.route('/refund')
    def refund_policy():
        return render_template('refund.html', now=datetime.now())

    @app.route('/faq')
    def faq():
        return render_template('faq.html', now=datetime.now())

    @app.route('/contact')
    def contact():
        return render_template('contact.html', now=datetime.now())

    @app.route('/contact/submit', methods=['POST'])
    def contact_submit():
        try:
            full_name = request.form.get('full_name', '').strip()
            email = request.form.get('email', '').strip()
            subject = request.form.get('subject', '').strip()
            category = request.form.get('category', '').strip()
            message = request.form.get('message', '').strip()
            
            if not all([full_name, email, subject, category, message]):
                flash('Please fill in all required fields.', 'error')
                return redirect(url_for('contact'))
            
            contact = ContactMessage(
                full_name=full_name,
                email=email,
                subject=subject,
                category=category,
                message=message,
                ip_address=request.headers.get('X-Forwarded-For', request.remote_addr),
                user_agent=request.headers.get('User-Agent')
            )
            db.session.add(contact)
            db.session.commit()
            
            flash('Your message has been sent successfully! Our support team will get back to you soon.', 'success')
            return redirect(url_for('contact'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Contact form error: {e}")
            flash('There was an error sending your message. Please try again.', 'error')
            return redirect(url_for('contact'))

    @app.route('/about')
    def about():
        return render_template('about.html')
    
    # ═══════════════════════════════════════════════════════════
    # 🔄 MT5 VPS SYNC WEBHOOK (NEW)
    # ═══════════════════════════════════════════════════════════
    
    @app.route('/api/mt5/sync', methods=['POST'])
    def mt5_sync_webhook():
        """Receive MT5 trade data from VPS sync server"""
        from sync_service import process_mt5_trade_data
        
        # Verify internal key
        api_key = request.headers.get('X-Internal-Key')
        expected_key = os.environ.get('VPS_INTERNAL_KEY', 'TGF_INT_xK92mQ27pL38nR4')
        
        if api_key != expected_key:
            print(f"❌ MT5 Webhook: Invalid internal key from {request.remote_addr}")
            return jsonify({'status': 'error', 'message': 'Invalid internal key'}), 401
        
        try:
            data = request.get_json()
            if not data:
                return jsonify({'status': 'error', 'message': 'No JSON data received'}), 400
            
            sync_id = data.get('sync_id')
            if not sync_id:
                return jsonify({'status': 'error', 'message': 'No sync_id in payload'}), 400
            
            print(f"📥 MT5 Webhook received: sync_id={sync_id}, trades={len(data.get('closed_trades', []))}")
            
            result = process_mt5_trade_data(data)
            
            if result.get('success'):
                print(f"✅ MT5 Webhook processed: {result.get('trades_added', 0)} new trades saved")
                return jsonify({
                    'status': 'ok',
                    'message': f"Processed {result.get('trades_added', 0)} trades",
                    'trades_added': result.get('trades_added', 0)
                }), 200
            else:
                print(f"⚠️ MT5 Webhook error: {result.get('error', 'Unknown error')}")
                return jsonify({
                    'status': 'error',
                    'message': result.get('error', 'Processing failed')
                }), 400
                
        except Exception as e:
            print(f"❌ MT5 Webhook exception: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({'status': 'error', 'message': f'Server error: {str(e)[:200]}'}), 500
    
    # ═══════════════════════════════════════════════════════════
    # END MT5 VPS SYNC WEBHOOK
    # ═══════════════════════════════════════════════════════════
    
    # Start sync scheduler
    from sync_service import start_scheduler
    start_scheduler()
    
    return app

def create_admin():
    from models import User, TradingAccount
    
    admin_email = os.environ.get('ADMIN_EMAIL', 'kunaldhade@tragene.com')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'Kunal_8805')
    
    existing_admin = User.query.filter_by(email=admin_email).first()
    
    if not existing_admin:
        admin = User(
            username='admin',
            email=admin_email,
            is_admin=True,
            subscription_tier='enterprise'
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.flush()
        
        admin_account = TradingAccount(
            user_id=admin.id,
            name='Admin Trading Account',
            account_type='live',
            currency='USD'
        )
        db.session.add(admin_account)
        db.session.flush()
        
        admin.current_account_id = admin_account.id
        db.session.commit()
        print(f"✅ Admin created: {admin_email}")
        print(f"✅ Default account created for admin")
    else:
        if not existing_admin.get_active_account():
            account = TradingAccount(
                user_id=existing_admin.id,
                name='Admin Trading Account',
                account_type='live',
                currency='USD'
            )
            db.session.add(account)
            db.session.flush()
            existing_admin.current_account_id = account.id
        
        if not existing_admin.is_admin:
            existing_admin.is_admin = True
        
        if existing_admin.subscription_tier != 'enterprise':
            existing_admin.subscription_tier = 'enterprise'
        
        db.session.commit()
        print(f"✅ Admin verified: {admin_email}")

def seed_template_rules():
    from models import TradingRule
    import json
    
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

def seed_ai_plan_defaults():
    try:
        from ai_service import seed_plan_defaults
        seed_plan_defaults()
    except ImportError:
        print("⚠️ AI service not available - skipping AI plan defaults")

def seed_moderator_permissions():
    """Seed default permissions for moderator system"""
    try:
        from moderator_routes import seed_default_permissions
        seed_default_permissions()
        print("✅ Moderator permissions seeded!")
    except Exception as e:
        print(f"⚠️ Could not seed moderator permissions: {e}")

def migrate_existing_data():
    from models import User, TradingAccount, Trade, DayNote, DiaryEntry, ImportHistory
    
    print("\n📦 Checking database for migration needs...")
    
    try:
        users_without_accounts = User.query.filter(
            ~User.accounts.any()
        ).all()
    except Exception as e:
        print(f"⚠️  Could not check accounts (this is normal if tables just created): {e}")
        users_without_accounts = []
    
    try:
        orphan_trades = Trade.query.filter(Trade.account_id == None).count()
    except:
        orphan_trades = 0
    
    try:
        orphan_notes = DayNote.query.filter(DayNote.account_id == None).count()
    except:
        orphan_notes = 0
    
    if users_without_accounts or orphan_trades > 0 or orphan_notes > 0:
        print(f"\n⚠️  Migration required:")
        print(f"   Users without accounts: {len(users_without_accounts)}")
        print(f"   Trades without account: {orphan_trades}")
        print(f"   Notes without account: {orphan_notes}")
        print("   Creating default accounts and assigning data...\n")
        
        try:
            for user in users_without_accounts:
                account = TradingAccount(
                    user_id=user.id,
                    name='My First Account',
                    account_type='live',
                    currency=user.account_currency or 'USD'
                )
                db.session.add(account)
                db.session.flush()
                
                user.current_account_id = account.id
                
                Trade.query.filter_by(user_id=user.id, account_id=None).update(
                    {Trade.account_id: account.id}
                )
                
                DayNote.query.filter_by(user_id=user.id, account_id=None).update(
                    {DayNote.account_id: account.id}
                )
                
                DiaryEntry.query.filter_by(user_id=user.id, account_id=None).update(
                    {DiaryEntry.account_id: account.id}
                )
                
                ImportHistory.query.filter_by(user_id=user.id, account_id=None).update(
                    {ImportHistory.account_id: account.id}
                )
                
                print(f"   ✅ Migrated: {user.username}")
            
            db.session.commit()
            print(f"\n✅ Migration complete! {len(users_without_accounts)} users updated.\n")
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration error: {str(e)}")
            print("   Check the error above for details.\n")
    else:
        print("✅ No migration needed - database is up to date.\n")
    
    try:
        users_without_current = User.query.filter(User.current_account_id == None).all()
        if users_without_current:
            print(f"🔧 Fixing {len(users_without_current)} users missing active account...")
            for user in users_without_current:
                account = user.get_active_account()
                if account:
                    user.current_account_id = account.id
            db.session.commit()
            print("   ✅ Fixed.\n")
    except:
        pass


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)