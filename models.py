from extensions import db
from flask_login import UserMixin
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
import json


class TradingAccount(db.Model):
    """Separate trading accounts for different markets/styles"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False, default='Default Account')
    broker = db.Column(db.String(100))
    account_type = db.Column(db.String(20), default='live')
    currency = db.Column(db.String(10), default='USD')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    trades = db.relationship('Trade', backref='account', lazy=True, foreign_keys='Trade.account_id')
    day_notes = db.relationship('DayNote', backref='account', lazy=True, foreign_keys='DayNote.account_id')
    diary_entries = db.relationship('DiaryEntry', backref='account', lazy=True, foreign_keys='DiaryEntry.account_id')
    import_history = db.relationship('ImportHistory', backref='account', lazy=True, foreign_keys='ImportHistory.account_id')
    
    def __repr__(self):
        return f'<TradingAccount {self.name}>'
    
    @property
    def trade_count(self):
        return len(self.trades)
    
    @property
    def total_pnl(self):
        return sum(t.profit_loss for t in self.trades if t.profit_loss is not None)
    
    def get_stats(self):
        trades = self.trades
        wins = [t for t in trades if t.is_win]
        total = len(trades)
        win_rate = round((len(wins) / total) * 100, 1) if total > 0 else 0
        pnl = self.total_pnl
        return {'count': total, 'wins': len(wins), 'win_rate': win_rate, 'pnl': pnl}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    
    # ===== NEW REGISTRATION FIELDS =====
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    country = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    # ===================================
    
    is_admin = db.Column(db.Boolean, default=False)
    subscription_tier = db.Column(db.String(20), default='free')
    subscription_active = db.Column(db.Boolean, default=True)
    current_account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    trading_style = db.Column(db.String(50))
    preferred_session = db.Column(db.String(20))
    account_currency = db.Column(db.String(10), default='USD')
    last_csv_import = db.Column(db.DateTime, nullable=True)
    last_analyzed_date = db.Column(db.Date, nullable=True)
    email_verified = db.Column(db.Boolean, default=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    lead_status_id = db.Column(db.Integer, db.ForeignKey('lead_statuses.id'), nullable=True)
    failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    lead_status = db.relationship('LeadStatus', backref='users_with_status', foreign_keys=[lead_status_id])
    
    trades = db.relationship('Trade', backref='trader', lazy=True, foreign_keys='Trade.user_id')
    imports = db.relationship('ImportHistory', backref='importer', lazy=True, foreign_keys='ImportHistory.user_id')
    day_notes = db.relationship('DayNote', backref='author', lazy=True, foreign_keys='DayNote.user_id')
    accounts = db.relationship('TradingAccount', backref='owner', lazy=True, foreign_keys='TradingAccount.user_id')
    diary_entries = db.relationship('DiaryEntry', backref='author_ref', lazy=True, foreign_keys='DiaryEntry.user_id')
    
    def get_id(self):
        return f"{self.id}:{self.session_version or 0}"
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def full_name(self):
        """Return full name"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        return self.username
    
    def get_max_accounts(self):
        tier_limits = {'free': 2, 'pro': 10, 'elite': 999}
        return tier_limits.get(self.subscription_tier, 2)
    
    def get_account_count(self):
        return TradingAccount.query.filter_by(user_id=self.id).count()
    
    def can_create_account(self):
        return self.get_account_count() < self.get_max_accounts()
    
    def get_active_account(self):
        if self.current_account_id:
            account = TradingAccount.query.get(self.current_account_id)
            if account and account.user_id == self.id and account.is_active:
                return account
        first_account = TradingAccount.query.filter_by(user_id=self.id, is_active=True).order_by(TradingAccount.created_at).first()
        if first_account:
            self.current_account_id = first_account.id
            db.session.commit()
        return first_account
    
    def switch_account(self, account_id):
        account = TradingAccount.query.filter_by(id=account_id, user_id=self.id, is_active=True).first()
        if account:
            self.current_account_id = account.id
            db.session.commit()
            return True
        return False
    
    def create_default_account(self):
        if self.get_account_count() == 0:
            account = TradingAccount(user_id=self.id, name='My First Account', account_type='live', currency=self.account_currency)
            db.session.add(account)
            db.session.flush()
            self.current_account_id = account.id
            db.session.commit()
            return account
        return None
    
    def can_upload_csv(self):
        if self.subscription_tier in ['pro', 'elite']:
            return True, None
        if self.last_csv_import is None:
            return True, None
        days_since = (datetime.utcnow() - self.last_csv_import).days
        if days_since >= 7:
            return True, None
        return False, 7 - days_since
    
    def csv_upload_label(self):
        if self.subscription_tier in ['pro', 'elite']:
            return "Upload CSV", True
        can, days = self.can_upload_csv()
        if can:
            return "Upload CSV", True
        return f"Available in {days} day{'s' if days > 1 else ''}", False
    
    def get_token_limit(self):
        override = AIUserOverride.query.filter_by(user_id=self.id).first()
        if override and override.override_tokens is not None:
            return override.override_tokens
        plan_default = AIPlanDefaults.query.filter_by(plan_tier=self.subscription_tier, is_active=True).first()
        if plan_default:
            return plan_default.monthly_tokens
        tier_limits = {'free': 2000, 'pro': 50000, 'elite': 150000}
        return tier_limits.get(self.subscription_tier, 2000)
    
    def get_used_tokens(self):
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return db.session.query(db.func.sum(AIUsageLog.total_tokens)).filter(AIUsageLog.user_id == self.id, AIUsageLog.created_at >= month_start).scalar() or 0
    
    def get_remaining_tokens(self):
        return self.get_token_limit() - self.get_used_tokens()
    
    def get_queries_per_week(self):
        override = AIUserOverride.query.filter_by(user_id=self.id).first()
        if override and override.override_queries_per_week is not None:
            return override.override_queries_per_week
        plan_default = AIPlanDefaults.query.filter_by(plan_tier=self.subscription_tier).first()
        if plan_default:
            return plan_default.queries_per_week
        return 2
    
    def get_queries_used_this_week(self):
        today = datetime.utcnow().date()
        week_start = today - timedelta(days=today.weekday())
        return AIUsageLog.query.filter_by(user_id=self.id).filter(db.func.date(AIUsageLog.created_at) >= week_start).count()
    
    def can_use_ai(self):
        override = AIUserOverride.query.filter_by(user_id=self.id).first()
        if override and override.is_banned:
            return False, "AI access has been restricted. Contact support."
        if self.subscription_tier == 'free':
            queries_used = self.get_queries_used_this_week()
            max_queries = self.get_queries_per_week()
            if max_queries and queries_used >= max_queries:
                return False, f"Weekly limit reached ({queries_used}/{max_queries}). Upgrade for more."
        remaining = self.get_remaining_tokens()
        if remaining <= 0:
            return False, "Monthly token limit reached. Resets next month."
        return True, f"🪙 {remaining:,} tokens remaining"
    
    def is_ai_banned(self):
        override = AIUserOverride.query.filter_by(user_id=self.id).first()
        return override.is_banned if override else False
    
    def can_access_coach(self):
        return self.subscription_tier in ['elite']

    def can_access_goals(self):
        return self.subscription_tier in ['elite']


class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    
    # ── Market Type ──
    market = db.Column(db.String(20), default='forex')  # forex, crypto, indian_stock
    
    # ── Core Fields (All Markets) ──
    symbol = db.Column(db.String(20), nullable=False)
    trade_type = db.Column(db.String(10), nullable=False)  # buy, sell
    entry_price = db.Column(db.Float, nullable=False)
    exit_price = db.Column(db.Float)
    stop_loss = db.Column(db.Float)
    take_profit = db.Column(db.Float)
    quantity = db.Column(db.Float, default=1.0)  # Lots / Coins / Shares
    profit_loss = db.Column(db.Float)
    profit_loss_pips = db.Column(db.Float)
    risk_reward_ratio = db.Column(db.Float)
    is_win = db.Column(db.Boolean)
    
    # ── Dates ──
    entry_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    exit_date = db.Column(db.DateTime)
    
    # ── Forex Specific ──
    session = db.Column(db.String(20))  # asian, london, newyork
    leverage = db.Column(db.Integer)  # 1:100, 1:500
    
    # ── Crypto Specific ──
    exchange = db.Column(db.String(50))  # Binance, Coinbase, Bybit (also used by Indian)
    crypto_segment = db.Column(db.String(20))  # spot, futures, perpetual
    
    # ── Indian Market Specific ──
    segment = db.Column(db.String(30))  # equity, futures, options, intraday, commodity, currency
    instrument = db.Column(db.String(20))  # stock, index, etf, commodity
    expiry_date = db.Column(db.Date)  # F&O expiry
    strike_price = db.Column(db.Float)  # Options strike
    option_type = db.Column(db.String(2))  # CE, PE
    brokerage = db.Column(db.Float, default=0)  # ₹
    taxes = db.Column(db.Float, default=0)  # STT, GST, etc
    other_charges = db.Column(db.Float, default=0)
    net_pnl_after_charges = db.Column(db.Float)
    margin_used = db.Column(db.Float)  # ₹
    capital_at_risk_pct = db.Column(db.Float)  # %
    
    # ── Common ──
    setup_type = db.Column(db.String(50))  # breakout, trend, scalping, etc
    notes = db.Column(db.Text)
    tags = db.Column(db.String(200))
    screenshot_path = db.Column(db.String(500))
    import_source = db.Column(db.String(20), default='manual')
    broker = db.Column(db.String(100))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Screenshots relationship defined in TradeScreenshot class only
    
    def calculate_pnl(self):
        """Calculate P&L based on market type"""
        if self.exit_price and self.entry_price:
            symbol_upper = self.symbol.upper() if self.symbol else ''
            market_type = detect_market(self.symbol)
            
            if market_type == 'indian_stock':
                pip_multiplier, value_per_pip = 1, 1
            elif market_type == 'crypto':
                pip_multiplier, value_per_pip = 1, 1
            elif 'XAU' in symbol_upper or 'GOLD' in symbol_upper:
                pip_multiplier, value_per_pip = 100, 10
            elif any(x in symbol_upper for x in ['US30', 'NAS100', 'NAS', 'SPX', 'DJI', 'DAX', 'GER30', 'UK100']):
                pip_multiplier, value_per_pip = 1, 10
            elif 'JPY' in symbol_upper:
                pip_multiplier, value_per_pip = 100, 10
            else:
                pip_multiplier, value_per_pip = 10000, 10
            
            if self.trade_type == 'buy':
                self.profit_loss_pips = round((self.exit_price - self.entry_price) * pip_multiplier, 2)
            else:
                self.profit_loss_pips = round((self.entry_price - self.exit_price) * pip_multiplier, 2)
            
            self.profit_loss = round(self.profit_loss_pips * self.quantity * value_per_pip, 2)
            self.is_win = self.profit_loss > 0
            
            # Net P&L after charges (for Indian market)
            if market_type == 'indian_stock':
                total_charges = (self.brokerage or 0) + (self.taxes or 0) + (self.other_charges or 0)
                self.net_pnl_after_charges = round(self.profit_loss - total_charges, 2)
            
            # Risk:Reward
            if self.stop_loss and self.take_profit and self.entry_price:
                if self.trade_type == 'buy':
                    risk = self.entry_price - self.stop_loss
                    reward = self.take_profit - self.entry_price
                else:
                    risk = self.stop_loss - self.entry_price
                    reward = self.entry_price - self.take_profit
                if risk > 0 and reward > 0:
                    self.risk_reward_ratio = round(reward / risk, 2)


class TradeScreenshot(db.Model):
    """Screenshots attached to trades"""
    __tablename__ = 'trade_screenshots'
    
    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.Integer, db.ForeignKey('trade.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    trade = db.relationship('Trade', backref='screenshots')





class SyncConnection(db.Model):
    """User's sync connections to external brokers/exchanges"""
    __tablename__ = 'sync_connections'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    
    # Market & Platform
    market = db.Column(db.String(20), nullable=False)  # crypto, forex, indian_stock
    platform = db.Column(db.String(50), nullable=False)  # binance, mt4, mt5, zerodha, etc
    method = db.Column(db.String(20), default='api')  # api, csv, investor_password
    label = db.Column(db.String(100))  # User-given name like "My Binance Spot"
    
    # Encrypted credentials
    api_key_encrypted = db.Column(db.Text)
    api_secret_encrypted = db.Column(db.Text)
    server_name = db.Column(db.String(200))  # MT4/MT5 server
    mt_account_number = db.Column(db.String(50))  # MT4/MT5 login ID
    investor_password_encrypted = db.Column(db.Text)  # MT4/MT5 investor password
    passphrase_encrypted = db.Column(db.Text)  # Some exchanges need passphrase

    # VPS Sync Tracking
    sync_id = db.Column(db.String(80), unique=True, nullable=True)  # TGF_SYNC_{user}_{account}_{random}
    username = db.Column(db.String(80), nullable=True)  # Denormalized username for VPS display
    
    # Status & Stats
    is_active = db.Column(db.Boolean, default=True)
    sync_status = db.Column(db.String(20), default='active')  # active, error, paused, expired, stopped
    last_synced_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    last_error_at = db.Column(db.DateTime)
    total_trades_fetched = db.Column(db.Integer, default=0)
    sync_count = db.Column(db.Integer, default=0)  # How many times synced
    
    # Admin controls
    admin_stopped = db.Column(db.Boolean, default=False)
    stop_reason = db.Column(db.Text)
    stopped_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='sync_connections', foreign_keys=[user_id])



# ═══════════════════════════════════════════════════════════
# 🔍 MARKET DETECTION HELPER
# ═══════════════════════════════════════════════════════════

def detect_market(symbol):
    """Auto-detect market type from symbol"""
    if not symbol:
        return 'other'
    
    symbol = symbol.upper().strip()
    
    # Crypto
    crypto_symbols = ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE', 'MATIC', 'DOT', 
                      'AVAX', 'LINK', 'UNI', 'ATOM', 'LTC', 'BCH', 'FIL', 'ICP',
                      'SAND', 'MANA', 'GALA', 'SHIB', 'PEPE', 'ARB', 'OP', 'APT',
                      'SUI', 'SEI', 'TIA', 'INJ', 'RUNE', 'FET', 'AGIX', 'OCEAN',
                      'USDT', 'USDC', 'BUSD', 'DAI']
    if any(symbol.startswith(c) or symbol.endswith(c) for c in crypto_symbols):
        return 'crypto'
    if 'USDT' in symbol or 'USD' in symbol and any(c in symbol for c in crypto_symbols):
        return 'crypto'
    
    # Indian Stocks
    indian_patterns = ['.NS', '.BO', 'NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY',
                       'RELIANCE', 'TCS', 'INFY', 'HDFC', 'ICICI', 'WIPRO', 'ITC',
                       'SBIN', 'BHARTIARTL', 'KOTAKBANK', 'LT', 'HCLTECH', 'SUNPHARMA',
                       'TITAN', 'MARUTI', 'AXISBANK', 'BAJFINANCE', 'ADANIENT',
                       'ADANIPORTS', 'POWERGRID', 'NTPC', 'ONGC', 'COALINDIA', 'IOC',
                       'BPCL', 'HINDUNILVR', 'ASIANPAINT', 'ULTRACEMCO', 'JSWSTEEL',
                       'TATASTEEL', 'TATAMOTORS', 'M&M', 'BAJAJ-AUTO', 'EICHERMOT']
    if any(symbol.startswith(p) or p in symbol for p in indian_patterns):
        return 'indian_stock'
    if symbol.endswith('.NS') or symbol.endswith('.BO'):
        return 'indian_stock'
    
    # Forex
    forex_pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'GBPJPY', 'AUDUSD', 'USDCAD',
                   'USDCHF', 'NZDUSD', 'EURGBP', 'EURJPY', 'XAUUSD', 'XAGUSD',
                   'US30', 'NAS100', 'SPX500', 'DAX', 'GER30', 'UK100', 'USOIL', 'UKOIL',
                   'EURCHF', 'EURAUD', 'GBPAUD', 'GBPCAD', 'GBPCHF', 'AUDCAD',
                   'AUDCHF', 'AUDJPY', 'CADJPY', 'CHFJPY', 'NZDJPY', 'EURNZD',
                   'GBPNZD', 'AUDNZD', 'XAU', 'XAG', 'WTI', 'BRENT']
    if symbol in forex_pairs:
        return 'forex'
    
    return 'other'


def get_market_info(symbol):
    """Get market display info: icon, color, label, currency"""
    market = detect_market(symbol)
    return {
        'forex': {'icon': '💱', 'color': '#4f8ef7', 'label': 'Forex', 'currency': '$', 'currency_name': 'USD'},
        'crypto': {'icon': '₿', 'color': '#ff6b6b', 'label': 'Crypto', 'currency': '$', 'currency_name': 'USD'},
        'indian_stock': {'icon': '📈', 'color': '#ffb300', 'label': 'Indian', 'currency': '₹', 'currency_name': 'INR'},
        'other': {'icon': '📊', 'color': '#8b5cf6', 'label': 'Other', 'currency': '$', 'currency_name': 'USD'},
    }.get(market, {'icon': '📊', 'color': '#8b5cf6', 'label': 'Other', 'currency': '$', 'currency_name': 'USD'})


def get_currency_for_market(market):
    """Get currency symbol for a market type"""
    return '₹' if market == 'indian_stock' else '$'





class ImportHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    import_type = db.Column(db.String(20), nullable=False)
    file_name = db.Column(db.String(200))
    trades_imported = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DayNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    note_date = db.Column(db.Date, nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DiaryEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    entry_date = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(200))
    content = db.Column(db.Text)
    mood = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    images = db.relationship('DiaryImage', backref='entry', lazy=True, cascade='all, delete-orphan')


class DiaryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey('diary_entry.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════
# 🛠️ TOOLS MODELS
# ═══════════════════════════════════════════════════════════

class TradingRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    rule_type = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    config_json = db.Column(db.Text)
    is_template = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    checks = db.relationship('TradeRuleCheck', backref='rule', lazy=True, cascade='all, delete-orphan')


class TradeRuleCheck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    trade_id = db.Column(db.Integer, db.ForeignKey('trade.id'), nullable=False)
    rule_id = db.Column(db.Integer, db.ForeignKey('trading_rule.id'), nullable=False)
    passed = db.Column(db.Boolean, default=True)
    details = db.Column(db.Text)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    trade = db.relationship('Trade', backref='rule_checks')


class Checklist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    checklist_type = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_editable = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tasks = db.relationship('ChecklistTask', backref='checklist', lazy=True, cascade='all, delete-orphan', order_by='ChecklistTask.task_order')
    completions = db.relationship('ChecklistCompletion', backref='checklist', lazy=True, cascade='all, delete-orphan')


class ChecklistTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    checklist_id = db.Column(db.Integer, db.ForeignKey('checklist.id'), nullable=False)
    task_name = db.Column(db.String(200), nullable=False)
    task_order = db.Column(db.Integer, default=1)
    applies_to = db.Column(db.String(20), default='all_days')


class ChecklistCompletion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    checklist_id = db.Column(db.Integer, db.ForeignKey('checklist.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('checklist_task.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    completed = db.Column(db.Boolean, default=False)
    skipped_reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    task = db.relationship('ChecklistTask', backref='completions')


class TragenePoints(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    points = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(50), nullable=False)
    source_id = db.Column(db.Integer)
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='tragene_points')


# ═══════════════════════════════════════════════════════════
# 🤖 AI MODELS
# ═══════════════════════════════════════════════════════════

class AIChatSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    title = db.Column(db.String(100), default='New Chat')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    messages = db.relationship('AIChatMessage', backref='session', lazy=True, cascade='all, delete-orphan', order_by='AIChatMessage.created_at')
    user = db.relationship('User', backref='ai_chat_sessions')


class AIChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('ai_chat_session.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    tokens_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AIReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    report_date = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    trades_analyzed = db.Column(db.Integer, default=0)
    diary_entries_analyzed = db.Column(db.Integer, default=0)
    checklist_days_analyzed = db.Column(db.Integer, default=0)
    user_summary = db.Column(db.Text)
    strengths = db.Column(db.Text)
    warnings = db.Column(db.Text)
    action_items = db.Column(db.Text)
    performance_score = db.Column(db.Integer)
    raw_prompt = db.Column(db.Text)
    raw_response = db.Column(db.Text)
    model_used = db.Column(db.String(50))
    prompt_tokens = db.Column(db.Integer)
    completion_tokens = db.Column(db.Integer)
    total_tokens = db.Column(db.Integer)
    api_cost = db.Column(db.Float)
    ai_context = db.Column(db.Text)
    report_type = db.Column(db.String(30), default='manual')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='ai_reports')


class AIUsageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    report_id = db.Column(db.Integer, db.ForeignKey('ai_report.id'), nullable=True)
    analysis_type = db.Column(db.String(50))
    model_used = db.Column(db.String(50))
    prompt_tokens = db.Column(db.Integer)
    completion_tokens = db.Column(db.Integer)
    total_tokens = db.Column(db.Integer)
    api_cost = db.Column(db.Float)
    api_latency_ms = db.Column(db.Integer)
    status = db.Column(db.String(20))
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='ai_usage_logs')


class AIPlanDefaults(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_tier = db.Column(db.String(20), unique=True, nullable=False)
    monthly_tokens = db.Column(db.Integer, default=2000)
    daily_requests = db.Column(db.Integer, nullable=True)
    queries_per_week = db.Column(db.Integer, nullable=True)
    reports_per_week = db.Column(db.Integer, default=2)
    is_active = db.Column(db.Boolean, default=True)
    updated_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIUserOverride(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    override_tokens = db.Column(db.Integer, nullable=True)
    override_queries_per_week = db.Column(db.Integer, nullable=True)
    override_reports_per_week = db.Column(db.Integer, nullable=True)
    is_banned = db.Column(db.Boolean, default=False)
    is_rate_limited = db.Column(db.Boolean, default=False)
    rate_limit_per_hour = db.Column(db.Integer, default=10)
    reason = db.Column(db.Text)
    set_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', backref='ai_override', foreign_keys=[user_id])


class TradingGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    goal_type = db.Column(db.String(50), nullable=False)
    target_value = db.Column(db.Float, nullable=False)
    current_value = db.Column(db.Float, default=0)
    timeframe = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    is_achieved = db.Column(db.Boolean, nullable=True)
    ai_insight = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', backref='trading_goals')


class CoachInsight(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    insight_type = db.Column(db.String(50))
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    severity = db.Column(db.String(20))
    is_read = db.Column(db.Boolean, default=False)
    related_report_id = db.Column(db.Integer, db.ForeignKey('ai_report.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='coach_insights')


# ═══════════════════════════════════════════════════════════
# 📄 PER-PAGE AI ANALYSIS MODEL
# ═══════════════════════════════════════════════════════════

class AIPageAnalysis(db.Model):
    """Cached per-page AI analyses — one per user/account/page.
    Caches results so users can re-view without re-spending tokens."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('trading_account.id'), nullable=True)
    
    # What was analyzed
    page_key = db.Column(db.String(30), nullable=False)  # journal, analytics, calendar_day, insights, diary, goals, dashboard
    sub_id = db.Column(db.String(100), nullable=True)     # calendar date string, goal_id, etc.
    
    # Date range analyzed
    date_range_start = db.Column(db.Date, nullable=True)
    date_range_end = db.Column(db.Date, nullable=True)
    data_window_note = db.Column(db.String(200), nullable=True)  # "Showing last 15 days — you have more data..."
    
    # Content sections
    content = db.Column(db.Text)           # Full AI response (cleaned)
    summary = db.Column(db.Text)           # 1-2 line summary
    standout_wins = db.Column(db.Text)     # JSON array of strings
    standout_losses = db.Column(db.Text)   # JSON array of strings
    money_leaks = db.Column(db.Text)       # JSON array: unprotected losses, oversized risk
    suggestions = db.Column(db.Text)       # JSON array: action items
    score = db.Column(db.Integer, nullable=True)  # 1-10, NULL for calendar/diary pages
    
    # Token tracking
    tokens_used = db.Column(db.Integer)
    api_cost = db.Column(db.Float)
    model_used = db.Column(db.String(50))
    
    # Data counts analyzed
    trades_analyzed = db.Column(db.Integer, default=0)
    entries_analyzed = db.Column(db.Integer, default=0)  # For diary entries
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='page_analyses')
    
    def get_standout_wins(self):
        """Parse JSON back to list"""
        try:
            return json.loads(self.standout_wins) if self.standout_wins else []
        except:
            return []
    
    def get_standout_losses(self):
        """Parse JSON back to list"""
        try:
            return json.loads(self.standout_losses) if self.standout_losses else []
        except:
            return []
    
    def get_money_leaks(self):
        """Parse JSON back to list"""
        try:
            return json.loads(self.money_leaks) if self.money_leaks else []
        except:
            return []
    
    def get_suggestions(self):
        """Parse JSON back to list"""
        try:
            return json.loads(self.suggestions) if self.suggestions else []
        except:
            return []
    
    @property
    def page_title(self):
        """Human-readable page title"""
        titles = {
            'journal': '📊 Journal Analysis',
            'analytics': '📈 Analytics Deep-Dive',
            'calendar_day': '📅 Day Analysis',
            'insights': '💡 Insights Analysis',
            'diary': '📖 Diary Analysis',
            'goals': '🎯 Goals Analysis',
            'dashboard': '🏠 Dashboard Snapshot',
        }
        return titles.get(self.page_key, 'Analysis')


# ═══════════════════════════════════════════════════════════
# 💳 SUBSCRIPTION & PAYMENT MODELS
# ═══════════════════════════════════════════════════════════

class Subscription(db.Model):
    """User subscription plans"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    plan_tier = db.Column(db.String(20), default='free')  # free, pro, elite
    plan_type = db.Column(db.String(20), default='monthly')  # monthly, yearly
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    auto_renew = db.Column(db.Boolean, default=True)
    cancel_reason = db.Column(db.Text, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='subscription', foreign_keys=[user_id])
    payments = db.relationship('Payment', backref='subscription', lazy=True)


class Payment(db.Model):
    """Payment records for subscriptions"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), nullable=True)
    
    # Cashfree details
    cashfree_order_id = db.Column(db.String(100), unique=True)
    cashfree_payment_id = db.Column(db.String(100), nullable=True)
    cashfree_session_id = db.Column(db.String(200), nullable=True)
    cashfree_signature = db.Column(db.String(500), nullable=True)
    
    # 🆕 Coupon tracking
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=True)
    coupon_code = db.Column(db.String(50), nullable=True)
    coupon_discount = db.Column(db.Float, default=0)
    
    # Amount details (in paise)
    base_amount = db.Column(db.Integer, nullable=False)  # Plan price in paise
    gateway_fee = db.Column(db.Integer, default=0)  # 2% fee in paise
    total_amount = db.Column(db.Integer, nullable=False)  # base + fee in paise
    currency = db.Column(db.String(10), default='INR')
    
    # Plan info
    plan_tier = db.Column(db.String(20), nullable=False)  # pro, elite
    plan_type = db.Column(db.String(20), nullable=False)  # monthly, yearly
    
    # Status
    status = db.Column(db.String(30), default='PENDING')  # PENDING, SUCCESS, FAILED, CANCELLED, REFUNDED
    webhook_response = db.Column(db.Text, nullable=True)  # Full webhook JSON
    error_message = db.Column(db.Text, nullable=True)
    
    # Timestamps
    payment_completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='payments', foreign_keys=[user_id])
    coupon = db.relationship('Coupon', backref='payments', foreign_keys=[coupon_id])


# ═══════════════════════════════════════════════════════════
# 📧 EMAIL VERIFICATION
# ═══════════════════════════════════════════════════════════

class EmailVerification(db.Model):
    """Email verification tokens"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    new_email = db.Column(db.String(120), nullable=True)  # For email change requests
    type = db.Column(db.String(20), default='verify')  # 'verify' or 'change'
    is_used = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='email_verifications')


# ═══════════════════════════════════════════════════════════
# 📚 FAQ & SUPPORT MODELS
# ═══════════════════════════════════════════════════════════

class FAQ(db.Model):
    """Frequently Asked Questions - managed by admin"""
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(300), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default='General')  # General, Account, Trading, Billing, AI, Technical
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportTicket(db.Model):
    """User support tickets"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ticket_number = db.Column(db.String(20), unique=True, nullable=False)  # TK-001
    subject = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # bug, feature, account, billing, ai, trading, other
    status = db.Column(db.String(20), default='open')  # open, in_progress, resolved, closed
    priority = db.Column(db.String(20), default='medium')  # low, medium, high
    admin_note = db.Column(db.Text, nullable=True)  # Private admin notes
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='support_tickets')
    replies = db.relationship('TicketReply', backref='ticket', lazy=True, cascade='all, delete-orphan', order_by='TicketReply.created_at')


class TicketReply(db.Model):
    """Replies in a support ticket thread"""
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # NULL = admin reply
    message = db.Column(db.Text, nullable=False)
    attachment_url = db.Column(db.String(500), nullable=True)
    is_admin_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='ticket_replies')


# ═══════════════════════════════════════════════════════════
# 📝 CONTACT MESSAGES (NEW)
# ═══════════════════════════════════════════════════════════

class ContactMessage(db.Model):
    """Contact form submissions"""
    __tablename__ = 'contact_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f"<ContactMessage {self.id}: {self.subject[:50]}>"


# ═══════════════════════════════════════════════════════════
# 📝 BLOG & CMS MODELS
# ═══════════════════════════════════════════════════════════

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    blogs = db.relationship('Blog', backref='category', lazy=True)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)


# Association table for Blog and Tag
blog_tag = db.Table('blog_tag',
    db.Column('blog_id', db.Integer, db.ForeignKey('blog.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)


class Blog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)
    markdown_content = db.Column(db.Text, nullable=True)
    excerpt = db.Column(db.Text, nullable=True)
    
    # SEO Fields
    meta_title = db.Column(db.String(255), nullable=True)
    meta_description = db.Column(db.String(300), nullable=True)
    focus_keyword = db.Column(db.String(100), nullable=True)
    canonical_url = db.Column(db.String(255), nullable=True)
    
    # Relations
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Media
    featured_image = db.Column(db.String(255), nullable=True)
    
    # Status & Dates
    status = db.Column(db.String(20), default='draft') # draft, published, scheduled, archived
    published_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Metrics
    views = db.Column(db.Integer, default=0)
    estimated_reading_time = db.Column(db.Integer, default=1) # in minutes
    is_featured = db.Column(db.Boolean, default=False)
    
    author = db.relationship('User', backref='blogs')
    tags = db.relationship('Tag', secondary=blog_tag, lazy='subquery',
        backref=db.backref('blogs', lazy=True))


# ═══════════════════════════════════════════════════════════
# 🚀 SEO & SITE SETTINGS MODELS
# ═══════════════════════════════════════════════════════════

class SEOSettings(db.Model):
    """Global site-wide SEO settings"""
    id = db.Column(db.Integer, primary_key=True)
    site_title = db.Column(db.String(255), default="Tragene Journal")
    default_meta_description = db.Column(db.String(300), nullable=True)
    default_keywords = db.Column(db.String(300), nullable=True)
    
    site_logo = db.Column(db.String(255), nullable=True)
    favicon = db.Column(db.String(255), nullable=True)
    default_og_image = db.Column(db.String(255), nullable=True)
    default_twitter_image = db.Column(db.String(255), nullable=True)
    
    robots_txt_content = db.Column(db.Text, nullable=True)
    
    # Verifications
    google_verification = db.Column(db.String(100), nullable=True)
    bing_verification = db.Column(db.String(100), nullable=True)
    yandex_verification = db.Column(db.String(100), nullable=True)
    
    # Analytics
    google_analytics_id = db.Column(db.String(50), nullable=True)
    google_tag_manager_id = db.Column(db.String(50), nullable=True)
    microsoft_clarity_id = db.Column(db.String(50), nullable=True)
    facebook_pixel_id = db.Column(db.String(50), nullable=True)


class PageMetadata(db.Model):
    """SEO overrides for custom Landing Pages"""
    id = db.Column(db.Integer, primary_key=True)
    page_route = db.Column(db.String(255), unique=True, nullable=False) # e.g. 'index', 'pricing', 'features'
    title = db.Column(db.String(255), nullable=True)
    description = db.Column(db.String(300), nullable=True)
    keywords = db.Column(db.String(300), nullable=True)
    canonical_url = db.Column(db.String(255), nullable=True)
    robots = db.Column(db.String(100), default='index, follow')
    og_image = db.Column(db.String(255), nullable=True)
    schema_type = db.Column(db.String(100), nullable=True) # e.g. SoftwareApplication, WebSite
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Redirect(db.Model):
    """301/302 Redirect Manager"""
    id = db.Column(db.Integer, primary_key=True)
    old_path = db.Column(db.String(255), unique=True, nullable=False)
    new_path = db.Column(db.String(255), nullable=False)
    status_code = db.Column(db.Integer, default=301)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class NewsletterSubscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)


class MediaLibrary(db.Model):
    """Track uploaded images (for WebP conversion and Alt text)"""
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    alt_text = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(50), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)



class PlanPrice(db.Model):
    """Admin-managed plan pricing"""
    id = db.Column(db.Integer, primary_key=True)
    plan_tier = db.Column(db.String(20), nullable=False)
    plan_type = db.Column(db.String(20), nullable=False)
    plan_name = db.Column(db.String(100), nullable=True)
    currency = db.Column(db.String(10), default='INR')
    price = db.Column(db.Float, nullable=False, default=0)
    gateway_fee_percent = db.Column(db.Float, default=2.0)
    total_price = db.Column(db.Float, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_featured = db.Column(db.Boolean, default=False)
    discount_percent = db.Column(db.Float, default=0)
    description = db.Column(db.Text, nullable=True)
    features_json = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    def get_features(self):
        try:
            return json.loads(self.features_json) if self.features_json else []
        except:
            return []
    
    def set_features(self, features_list):
        self.features_json = json.dumps(features_list)
    
    def calculate_total(self):
        discounted = round(self.price * (1 - self.discount_percent / 100), 2)
        fee = round(discounted * self.gateway_fee_percent / 100, 2)
        self.total_price = round(discounted + fee, 2)
        return self.total_price




# ═══════════════════════════════════════════════════════════
# 👥 MODERATOR SYSTEM
# ═══════════════════════════════════════════════════════════

class PermissionRegistry(db.Model):
    """Master list of all available permissions in the system"""
    __tablename__ = 'permission_registry'
    
    id = db.Column(db.Integer, primary_key=True)
    permission_key = db.Column(db.String(50), unique=True, nullable=False)
    section = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200))
    category = db.Column(db.String(20), default='write')  # read, write, delete, manage
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Moderator(db.Model):
    """Sub-admins with granular permissions"""
    __tablename__ = 'moderators'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_banned = db.Column(db.Boolean, default=False)
    ban_reason = db.Column(db.Text)
    banned_until = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    permissions = db.relationship('ModeratorPermission', backref='moderator', lazy=True, cascade='all, delete-orphan')
    activities = db.relationship('ModeratorActivityLog', backref='moderator', lazy=True)
    
    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)
    
    def has_permission(self, key):
        """Check if moderator has a specific permission"""
        for p in self.permissions:
            if p.permission_key == key and p.is_granted:
                return True
        return False
    
    def get_allowed_permissions(self):
        """Get list of granted permission keys"""
        return [p.permission_key for p in self.permissions if p.is_granted]


class ModeratorPermission(db.Model):
    """Individual permission grants for moderators"""
    __tablename__ = 'moderator_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    moderator_id = db.Column(db.Integer, db.ForeignKey('moderators.id'), nullable=False)
    permission_key = db.Column(db.String(50), nullable=False)
    is_granted = db.Column(db.Boolean, default=False)
    granted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow)


class ModeratorActivityLog(db.Model):
    """Tracks every action performed by moderators"""
    __tablename__ = 'moderator_activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    moderator_id = db.Column(db.Integer, db.ForeignKey('moderators.id'), nullable=False)
    action_type = db.Column(db.String(30), nullable=False)  # create, update, delete, ban, reply, login
    target_type = db.Column(db.String(50))  # user, blog, ticket, subscription, ai_tokens
    target_id = db.Column(db.Integer)
    description = db.Column(db.Text, nullable=False)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



# ═══════════════════════════════════════════════════════════
# 🎟️ COUPON SYSTEM
# ═══════════════════════════════════════════════════════════

class Coupon(db.Model):
    """Discount coupon/promo codes"""
    __tablename__ = 'coupons'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    
    # Discount
    discount_type = db.Column(db.String(20), nullable=False)  # 'percentage' or 'fixed'
    discount_value = db.Column(db.Float, nullable=False)  # 20 = 20% or ₹20
    
    # Coupon type
    coupon_type = db.Column(db.String(20), nullable=False)  # 'universal', 'specific', 'influencer'
    
    # Limits
    max_uses = db.Column(db.Integer, nullable=True)  # NULL = unlimited
    used_count = db.Column(db.Integer, default=0)
    min_order_amount = db.Column(db.Float, default=0)  # Minimum cart value
    
    # Influencer tracking
    influencer_name = db.Column(db.String(100), nullable=True)
    influencer_notes = db.Column(db.Text, nullable=True)
    
    # Validity
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    
    # Audit
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    usages = db.relationship('CouponUsage', backref='coupon', lazy=True, cascade='all, delete-orphan')
    allowed_users = db.relationship('CouponUser', backref='coupon', lazy=True, cascade='all, delete-orphan')
    creator = db.relationship('User', backref='created_coupons', foreign_keys=[created_by_admin_id])
    
    def is_expired(self):
        """Check if coupon has expired"""
        if self.expires_at:
            return datetime.utcnow() > self.expires_at
        return False
    
    def is_exhausted(self):
        """Check if coupon usage limit reached"""
        if self.max_uses is not None:
            return self.used_count >= self.max_uses
        return False
    
    def can_be_used_by(self, user):
        """Check if a specific user can use this coupon"""
        if not self.is_active:
            return False, "Coupon is inactive."
        
        if self.is_expired():
            return False, "Coupon has expired."
        
        if self.is_exhausted():
            return False, "Coupon usage limit reached."
        
        # Check if user already used this coupon
        existing_usage = CouponUsage.query.filter_by(
            coupon_id=self.id, 
            user_id=user.id
        ).first()
        if existing_usage:
            return False, "You have already used this coupon."
        
        # For specific coupons, check if user is in allowed list
        if self.coupon_type == 'specific':
            allowed = CouponUser.query.filter_by(
                coupon_id=self.id, 
                user_id=user.id
            ).first()
            if not allowed:
                return False, "This coupon is not available for your account."
            if allowed.is_used:
                return False, "You have already used this coupon."
        
        return True, "Valid"
    
    def calculate_discount(self, order_amount):
        """Calculate discount amount"""
        if self.discount_type == 'percentage':
            discount = order_amount * (self.discount_value / 100)
        else:  # fixed
            discount = self.discount_value
        
        # Discount cannot exceed order amount
        return min(discount, order_amount)
    
    def get_discount_display(self):
        """Human-readable discount string"""
        if self.discount_type == 'percentage':
            return f"{self.discount_value}% OFF"
        else:
            return f"₹{self.discount_value} OFF"
    
    def __repr__(self):
        return f'<Coupon {self.code} - {self.get_discount_display()}>'


class CouponUsage(db.Model):
    """Records each time a coupon is used"""
    __tablename__ = 'coupon_usages'
    
    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)
    
    # Order details
    order_amount = db.Column(db.Float, nullable=False)  # Original amount
    discount_applied = db.Column(db.Float, nullable=False)  # Discount given
    final_amount = db.Column(db.Float, nullable=False)  # After discount
    plan_purchased = db.Column(db.String(50), nullable=True)  # Which plan
    
    # Tracking
    used_at = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50), nullable=True)
    
    user = db.relationship('User', backref='coupon_usages', foreign_keys=[user_id])
    payment = db.relationship('Payment', backref='coupon_usage', foreign_keys=[payment_id])
    
    def __repr__(self):
        return f'<CouponUsage coupon={self.coupon_id} user={self.user_id} discount=₹{self.discount_applied}>'


class CouponUser(db.Model):
    """Links specific coupons to allowed users"""
    __tablename__ = 'coupon_users'
    
    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Denormalized for quick display
    user_email = db.Column(db.String(120), nullable=True)
    user_phone = db.Column(db.String(20), nullable=True)
    
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='assigned_coupons', foreign_keys=[user_id])
    
    def __repr__(self):
        return f'<CouponUser coupon={self.coupon_id} user={self.user_id}>'




# ═══════════════════════════════════════════════════════════
# 🛡️ PURCHASE CONTROL SYSTEM
# ═══════════════════════════════════════════════════════════

class PurchaseControl(db.Model):
    """Admin-controlled purchase blocks for maintenance"""
    __tablename__ = 'purchase_controls'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Block type
    block_type = db.Column(db.String(20), nullable=False)  # 'all', 'specific_tier', 'specific_users'
    
    # What's blocked
    blocked_tier = db.Column(db.String(20), nullable=True)  # 'pro', 'elite' etc (if specific_tier)
    
    # Users affected (comma-separated user IDs as string, or 'all')
    blocked_user_ids = db.Column(db.Text, nullable=True)  # "1,5,23" or "all"
    
    # Reason shown to users
    reason = db.Column(db.Text, nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)  # Private notes
    
    # Timing
    starts_at = db.Column(db.DateTime, default=datetime.utcnow)
    ends_at = db.Column(db.DateTime, nullable=True)  # NULL = permanent until turned off
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    
    # Audit
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    creator = db.relationship('User', backref='purchase_controls', foreign_keys=[created_by_admin_id])
    
    def is_expired(self):
        """Check if the block has expired"""
        if self.ends_at and datetime.utcnow() > self.ends_at:
            return True
        return False
    
    def get_blocked_users_list(self):
        """Parse blocked_user_ids string to list of integers"""
        if not self.blocked_user_ids or self.blocked_user_ids == 'all':
            return []
        try:
            return [int(uid.strip()) for uid in self.blocked_user_ids.split(',') if uid.strip()]
        except:
            return []
    
    def get_time_remaining(self):
        """Get human-readable time remaining"""
        if not self.ends_at:
            return "Permanent"
        now = datetime.utcnow()
        if now > self.ends_at:
            return "Expired"
        diff = self.ends_at - now
        if diff.days > 0:
            return f"{diff.days}d {diff.seconds // 3600}h remaining"
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m remaining"
        return f"{minutes}m remaining"
    
    def blocks_user(self, user):
        """Check if this control blocks a specific user"""
        if not self.is_active:
            return False
        if self.is_expired():
            return False
        
        if self.block_type == 'all':
            return True
        
        if self.block_type == 'specific_tier':
            return user.subscription_tier == self.blocked_tier or True  # Blocks anyone buying this tier
        
        if self.block_type == 'specific_users':
            blocked_ids = self.get_blocked_users_list()
            return user.id in blocked_ids
        
        return False
    
    def __repr__(self):
        return f'<PurchaseControl {self.block_type} - {"Active" if self.is_active else "Inactive"}>'


def check_purchase_blocked(user, plan_tier=None):
    """
    Check if a user is blocked from purchasing.
    Returns (is_blocked, message) tuple.
    """
    active_controls = PurchaseControl.query.filter_by(is_active=True).all()
    
    for control in active_controls:
        if control.is_expired():
            continue
        
        if control.block_type == 'all':
            msg = control.reason or "Purchases are temporarily disabled for maintenance."
            if control.ends_at:
                remaining = control.get_time_remaining()
                msg += f" Expected to resume in: {remaining}"
            return True, msg
        
        if control.block_type == 'specific_tier' and control.blocked_tier == plan_tier:
            msg = control.reason or f"Purchases for {plan_tier.upper()} plan are temporarily paused."
            if control.ends_at:
                remaining = control.get_time_remaining()
                msg += f" Expected to resume in: {remaining}"
            return True, msg
        
        if control.block_type == 'specific_users' and control.blocks_user(user):
            msg = control.reason or "Your account is temporarily restricted from making purchases."
            if control.ends_at:
                remaining = control.get_time_remaining()
                msg += f" Expected to resume in: {remaining}"
            return True, msg
    
    return False, None

# ═══════════════════════════════════════════════════════════
# 📋 LEAD CRM SYSTEM
# ═══════════════════════════════════════════════════════════

class LeadStatus(db.Model):
    """Custom status categories for leads and influencers"""
    __tablename__ = 'lead_statuses'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#4F46E5')  # Hex color for badge
    is_default = db.Column(db.Boolean, default=False)  # System default vs custom
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    status_type = db.Column(db.String(20), default='lead')  # 'lead' or 'influencer'
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    creator = db.relationship('User', backref='created_lead_statuses', foreign_keys=[created_by_admin_id])
    
    def __repr__(self):
        return f'<LeadStatus {self.name} ({self.status_type})>'


class LeadNote(db.Model):
    """Notes for both user leads and influencers"""
    __tablename__ = 'lead_notes'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_type = db.Column(db.String(20), nullable=False)  # 'user' or 'influencer'
    lead_id = db.Column(db.Integer, nullable=False)  # user.id or influencer.id
    content = db.Column(db.Text, nullable=False)
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    creator = db.relationship('User', backref='created_lead_notes', foreign_keys=[created_by_admin_id])
    
    def __repr__(self):
        return f'<LeadNote {self.lead_type}:{self.lead_id}>'


class LeadFollowUp(db.Model):
    """Scheduled follow-ups for leads and influencers"""
    __tablename__ = 'lead_followups'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_type = db.Column(db.String(20), nullable=False)  # 'user' or 'influencer'
    lead_id = db.Column(db.Integer, nullable=False)
    followup_date = db.Column(db.DateTime, nullable=False)
    followup_type = db.Column(db.String(20), default='call')  # call, whatsapp, email
    notes = db.Column(db.Text, nullable=True)
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    creator = db.relationship('User', backref='created_followups', foreign_keys=[created_by_admin_id])
    
    def __repr__(self):
        return f'<LeadFollowUp {self.lead_type}:{self.lead_id}>'


class Influencer(db.Model):
    """Influencer CRM - track collaborators"""
    __tablename__ = 'influencers'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    
    # Contact details
    email = db.Column(db.String(120), unique=True, nullable=True)
    phone = db.Column(db.String(100), nullable=True)  # Increased from 50 to 100 for multiple numbers
    location = db.Column(db.String(200), nullable=True)  # State/City/Country
    
    # Social details
    platform = db.Column(db.String(50), nullable=True)  # Increased from 30 to 50
    social_handle = db.Column(db.String(100), nullable=True)  # @username
    follower_count = db.Column(db.Integer, nullable=True)
    niche = db.Column(db.String(100), nullable=True)  # Increased from 50 to 100
    
    # Status tracking
    status_id = db.Column(db.Integer, db.ForeignKey('lead_statuses.id'), nullable=True)  # Lead status category
    response_status = db.Column(db.String(30), default='not_contacted')  # not_contacted, contacted, responded, negotiating, agreed, declined
    
    # Source
    source = db.Column(db.String(20), default='manual')  # manual, csv_import
    
    # Quick info
    tags = db.Column(db.String(200), nullable=True)  # comma-separated
    notes = db.Column(db.Text, nullable=True)  # Quick summary note
    last_contacted_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    status = db.relationship('LeadStatus', backref='influencers', foreign_keys=[status_id])
    
    def get_notes(self):
        """Get all notes for this influencer"""
        return LeadNote.query.filter_by(lead_type='influencer', lead_id=self.id).order_by(LeadNote.created_at.desc()).all()
    
    def get_followups(self):
        """Get all follow-ups for this influencer"""
        return LeadFollowUp.query.filter_by(lead_type='influencer', lead_id=self.id).order_by(LeadFollowUp.followup_date.desc()).all()
    
    def __repr__(self):
        return f'<Influencer {self.name}>'


class InfluencerCampaign(db.Model):
    """Track influencer coupon campaigns"""
    __tablename__ = 'influencer_campaigns'
    
    id = db.Column(db.Integer, primary_key=True)
    influencer_id = db.Column(db.Integer, db.ForeignKey('influencers.id'), nullable=False)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=True)
    
    campaign_name = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(20), default='active')  # active, completed, paused
    
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    influencer = db.relationship('Influencer', backref='campaigns', foreign_keys=[influencer_id])
    coupon = db.relationship('Coupon', backref='campaigns', foreign_keys=[coupon_id])
    
    def get_total_uses(self):
        """Get total coupon uses for this campaign"""
        if not self.coupon_id:
            return 0
        from sqlalchemy import func
        return db.session.query(func.count(CouponUsage.id)).filter_by(coupon_id=self.coupon_id).scalar() or 0
    
    def get_total_revenue(self):
        """Get total revenue generated by this campaign"""
        if not self.coupon_id:
            return 0
        from sqlalchemy import func
        return db.session.query(func.sum(CouponUsage.final_amount)).filter_by(coupon_id=self.coupon_id).scalar() or 0
    
    def get_total_discount(self):
        """Get total discount given"""
        if not self.coupon_id:
            return 0
        from sqlalchemy import func
        return db.session.query(func.sum(CouponUsage.discount_applied)).filter_by(coupon_id=self.coupon_id).scalar() or 0
    
    def __repr__(self):
        return f'<InfluencerCampaign influencer={self.influencer_id}>'


# ═══════════════════════════════════════════════════════════
# 🔧 SEED DEFAULT LEAD STATUSES
# ═══════════════════════════════════════════════════════════

def seed_lead_statuses():
    """Seed default lead status categories for both leads and influencers"""
    
    # Lead statuses
    lead_defaults = [
        {'name': 'New Lead', 'color': '#6B7280', 'is_default': True, 'sort_order': 1, 'status_type': 'lead'},
        {'name': 'Verified', 'color': '#10B981', 'is_default': True, 'sort_order': 2, 'status_type': 'lead'},
        {'name': 'Interested', 'color': '#F59E0B', 'is_default': True, 'sort_order': 3, 'status_type': 'lead'},
        {'name': 'Call Follow-up', 'color': '#3B82F6', 'is_default': True, 'sort_order': 4, 'status_type': 'lead'},
        {'name': 'WhatsApp Follow-up', 'color': '#22C55E', 'is_default': True, 'sort_order': 5, 'status_type': 'lead'},
        {'name': 'Email Follow-up', 'color': '#8B5CF6', 'is_default': True, 'sort_order': 6, 'status_type': 'lead'},
        {'name': 'Purchased', 'color': '#06B6D4', 'is_default': True, 'sort_order': 7, 'status_type': 'lead'},
        {'name': 'Dead Lead', 'color': '#EF4444', 'is_default': True, 'sort_order': 8, 'status_type': 'lead'},
    ]
    
    # Influencer statuses
    influencer_defaults = [
        {'name': 'New Influencer', 'color': '#6B7280', 'is_default': True, 'sort_order': 1, 'status_type': 'influencer'},
        {'name': 'Top Priority', 'color': '#F59E0B', 'is_default': True, 'sort_order': 2, 'status_type': 'influencer'},
        {'name': 'VIP Influencer', 'color': '#8B5CF6', 'is_default': True, 'sort_order': 3, 'status_type': 'influencer'},
        {'name': 'In Discussion', 'color': '#3B82F6', 'is_default': True, 'sort_order': 4, 'status_type': 'influencer'},
        {'name': 'Partnered', 'color': '#00C853', 'is_default': True, 'sort_order': 5, 'status_type': 'influencer'},
    ]
    
    for d in lead_defaults + influencer_defaults:
        if not LeadStatus.query.filter_by(name=d['name'], status_type=d['status_type']).first():
            db.session.add(LeadStatus(**d))
    
    db.session.commit()
    print("✅ Lead and Influencer statuses seeded!")
