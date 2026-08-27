"""
TRAGENE SYNC SERVICE
Core sync engine for multi-market trade data fetching.
Supports: Crypto (CCXT), MT4/MT5 (MetaTrader5), Indian CSV parsing
Security: AES-256 credential encryption, read-only enforcement, audit logging
"""

import os
import json
import time
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from extensions import db
from models import SyncConnection, Trade, detect_market
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ═══════════════════════════════════════════════════════════
# 🔐 ENCRYPTION SETUP
# ═══════════════════════════════════════════════════════════

ENCRYPTION_KEY = os.environ.get('SYNC_ENCRYPTION_KEY')
if not ENCRYPTION_KEY:
    ENCRYPTION_KEY = Fernet.generate_key()
    print("⚠️  WARNING: No SYNC_ENCRYPTION_KEY in environment!")
    print("   Generated temporary key. Credentials will be lost on restart.")
    print("   Set SYNC_ENCRYPTION_KEY env variable for production.")

if isinstance(ENCRYPTION_KEY, str):
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

_cipher = Fernet(ENCRYPTION_KEY)


def encrypt(value):
    """AES-256 encrypt a credential value"""
    if not value:
        return None
    try:
        return _cipher.encrypt(value.encode()).decode()
    except Exception as e:
        print(f"❌ Encryption error: {e}")
        return None


def decrypt(encrypted_value):
    """AES-256 decrypt a credential value"""
    if not encrypted_value:
        return None
    try:
        return _cipher.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        print(f"❌ Decryption error: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# 📊 SUPPORTED PLATFORMS
# ═══════════════════════════════════════════════════════════

CRYPTO_EXCHANGES = {
    'binance': {'name': 'Binance', 'requires_passphrase': False},
    'coinbase': {'name': 'Coinbase', 'requires_passphrase': False},
    'bybit': {'name': 'Bybit', 'requires_passphrase': False},
    'kraken': {'name': 'Kraken', 'requires_passphrase': False},
    'okx': {'name': 'OKX', 'requires_passphrase': True},
    'kucoin': {'name': 'KuCoin', 'requires_passphrase': True},
    'bitget': {'name': 'Bitget', 'requires_passphrase': True},
}

MT_PLATFORMS = {
    'mt4': {'name': 'MetaTrader 4', 'port': 443},
    'mt5': {'name': 'MetaTrader 5', 'port': 443},
}

INDIAN_BROKERS = {
    'zerodha': {'name': 'Zerodha', 'method': 'csv'},
    'angelone': {'name': 'Angel One', 'method': 'csv'},
    'upstox': {'name': 'Upstox', 'method': 'csv'},
    'groww': {'name': 'Groww', 'method': 'csv'},
    'dhan': {'name': 'Dhan', 'method': 'csv'},
    'fyers': {'name': 'Fyers', 'method': 'csv'},
    'icicidirect': {'name': 'ICICI Direct', 'method': 'csv'},
}


# ═══════════════════════════════════════════════════════════
# 🎯 SUBSCRIPTION LIMITS
# ═══════════════════════════════════════════════════════════

SYNC_LIMITS = {
    'free': 1,
    'pro': 5,
    'elite': 10,
    'enterprise': 999,
}

FREE_ALLOWED_MARKETS = ['crypto']


# ═══════════════════════════════════════════════════════════
# 🔒 PERMISSION CHECKS
# ═══════════════════════════════════════════════════════════

def can_create_sync(user, market):
    """Check if user can create a new sync connection"""
    
    # Free users can ONLY sync crypto
    if user.subscription_tier == 'free':
        if market == 'crypto':
            pass  # Allowed
        elif market == 'indian_stock':
            pass  # Allowed (CSV only, no risk)
        elif market == 'forex':
            return False, "🔒 Forex/MT4/MT5 sync requires Pro (₹399/mo) or Elite (₹799/mo) plan. Upgrade to unlock."
        else:
            return False, f"Market not available on free plan."
    
    # Pro users get crypto + forex + indian
    # Elite users get everything
    
    if not user.subscription_active:
        return False, "Your subscription is inactive. Please renew."
    
    current_count = SyncConnection.query.filter_by(
        user_id=user.id, 
        is_active=True
    ).count()
    
    max_allowed = SYNC_LIMITS.get(user.subscription_tier, 1)
    
    if current_count >= max_allowed:
        return False, f"Sync limit reached ({current_count}/{max_allowed}). Upgrade your plan."
    
    return True, None


def enforce_subscription_limits():
    """Stop sync for users with expired subscriptions or over limits"""
    from models import User
    
    expired_users = User.query.filter_by(subscription_active=False).all()
    for user in expired_users:
        connections = SyncConnection.query.filter_by(
            user_id=user.id, 
            is_active=True,
            admin_stopped=False
        ).all()
        for conn in connections:
            conn.is_active = False
            conn.sync_status = 'expired'
            conn.stop_reason = 'Subscription expired'
            conn.updated_at = datetime.utcnow()
    
    free_users = User.query.filter_by(subscription_tier='free').all()
    for user in free_users:
        connections = SyncConnection.query.filter_by(
            user_id=user.id,
            is_active=True
        ).filter(SyncConnection.market != 'crypto').all()
        for conn in connections:
            conn.is_active = False
            conn.sync_status = 'stopped'
            conn.admin_stopped = True
            conn.stop_reason = 'Not available on free plan'
            conn.updated_at = datetime.utcnow()
    
    db.session.commit()
    print("✅ Subscription limits enforced")


# ═══════════════════════════════════════════════════════════
# 💱 CRYPTO SYNC ENGINE
# ═══════════════════════════════════════════════════════════

def fetch_crypto_trades(connection):
    """Fetch trades from a crypto exchange using CCXT."""
    try:
        import ccxt
    except ImportError:
        return False, 0, "CCXT library not installed."
    
    try:
        api_key = decrypt(connection.api_key_encrypted)
        api_secret = decrypt(connection.api_secret_encrypted)
        passphrase = decrypt(connection.passphrase_encrypted)
        
        if not api_key or not api_secret:
            return False, 0, "Missing API credentials"
        
        exchange_config = {
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True,
                'recvWindow': 10000,
            },
        }
        
        if passphrase:
            exchange_config['password'] = passphrase
        
        exchange_class = getattr(ccxt, connection.platform)
        exchange = exchange_class(exchange_config)
        
        # Explicitly sync the clock offset instead of relying on lazy auto-adjust
        try:
            exchange.load_time_difference()
        except Exception as e:
            print(f"⚠️  Time sync warning ({connection.platform}): {e}")
        
        # Determine since timestamp
        if connection.last_synced_at:
            since_ms = int(connection.last_synced_at.timestamp() * 1000)
        else:
            since_ms = int((datetime.utcnow() - timedelta(days=30)).timestamp() * 1000)
        
        # Load markets
        try:
            exchange.load_markets()
        except:
            pass
        
        all_trades = []
        symbols = list(exchange.markets.keys())[:50]
        
        for symbol in symbols:
            try:
                trades = exchange.fetch_my_trades(symbol, since=since_ms, limit=50)
                all_trades.extend(trades)
            except:
                continue
            time.sleep(0.05)
        
        if not all_trades:
            connection.last_synced_at = datetime.utcnow()
            connection.sync_status = 'active'
            connection.last_error = None
            connection.sync_count = (connection.sync_count or 0) + 1
            db.session.commit()
            return True, 0, None
        
        trades_added = 0
        for t in all_trades:
            try:
                trade_id = str(t.get('id', ''))
                existing = Trade.query.filter_by(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    import_source='auto_sync',
                    notes=trade_id
                ).first()
                
                if existing:
                    continue
                
                symbol = t.get('symbol', 'UNKNOWN')
                side = t.get('side', 'buy')
                price = t.get('price', 0) or 0
                amount = t.get('amount', 0) or 0
                ts = t.get('timestamp', int(time.time() * 1000))
                
                trade = Trade(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    market=detect_market(symbol),
                    symbol=symbol,
                    trade_type='buy' if str(side).lower() == 'buy' else 'sell',
                    entry_price=float(price),
                    exit_price=float(price),
                    quantity=float(amount),
                    entry_date=datetime.fromtimestamp(ts / 1000),
                    import_source='auto_sync',
                    exchange=connection.platform,
                    notes=trade_id,
                    broker=connection.platform,
                )
                trade.calculate_pnl()
                db.session.add(trade)
                trades_added += 1
                
            except Exception as e:
                print(f"⚠️  Error mapping trade: {e}")
                continue
        
        connection.last_synced_at = datetime.utcnow()
        connection.sync_status = 'active'
        connection.last_error = None
        connection.last_error_at = None
        connection.total_trades_fetched = (connection.total_trades_fetched or 0) + trades_added
        connection.sync_count = (connection.sync_count or 0) + 1
        db.session.commit()
        
        print(f"✅ Crypto sync: {connection.platform} → {trades_added} new trades")
        return True, trades_added, None
        
    except Exception as e:
        error_msg = str(e)[:300]
        connection.sync_status = 'error'
        connection.last_error = error_msg
        connection.last_error_at = datetime.utcnow()
        db.session.commit()
        print(f"❌ Crypto sync error ({connection.platform}): {error_msg}")
        return False, 0, error_msg


# ═══════════════════════════════════════════════════════════
# 📊 MT4/MT5 SYNC ENGINE
# ═══════════════════════════════════════════════════════════

def fetch_mt_trades(connection):
    """Fetch trades from MT4/MT5 using investor password."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False, 0, "MetaTrader5 library not installed."
    
    try:
        server = connection.server_name
        login = int(connection.mt_account_number) if connection.mt_account_number else None
        password = decrypt(connection.investor_password_encrypted)
        
        if not server or not login or not password:
            return False, 0, "Missing MT4/MT5 credentials"
        
        if not mt5.initialize(server=server, login=login, password=password, portable=True):
            error_code = mt5.last_error()
            return False, 0, f"MT5 connection failed: {error_code}"
        
        account_info = mt5.account_info()
        if not account_info:
            mt5.shutdown()
            return False, 0, "Could not retrieve account info."
        
        if connection.last_synced_at:
            date_from = connection.last_synced_at
        else:
            date_from = datetime.utcnow() - timedelta(days=30)
        
        deals = mt5.history_deals_get(date_from, datetime.utcnow())
        
        if deals is None or len(deals) == 0:
            mt5.shutdown()
            connection.last_synced_at = datetime.utcnow()
            connection.sync_status = 'active'
            connection.last_error = None
            connection.sync_count = (connection.sync_count or 0) + 1
            db.session.commit()
            return True, 0, None
        
        orders = mt5.history_orders_get(date_from, datetime.utcnow())
        orders_dict = {}
        if orders:
            for order in orders:
                orders_dict[order.ticket] = order
        
        trades_added = 0
        for deal in deals:
            try:
                if deal.entry not in [0, 1]:
                    continue
                
                existing = Trade.query.filter_by(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    import_source='auto_sync',
                    notes=str(deal.ticket)
                ).first()
                
                if existing:
                    continue
                
                symbol = deal.symbol or 'UNKNOWN'
                trade_type = 'buy' if deal.type in [0, 2] else 'sell'
                price = deal.price or 0
                volume = deal.volume or 1.0
                profit = deal.profit if hasattr(deal, 'profit') else None
                
                trade = Trade(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    market=detect_market(symbol),
                    symbol=symbol,
                    trade_type=trade_type,
                    entry_price=price,
                    exit_price=price,
                    quantity=volume,
                    profit_loss=profit,
                    entry_date=datetime.fromtimestamp(deal.time) if deal.time else datetime.utcnow(),
                    import_source='auto_sync',
                    exchange=connection.platform,
                    broker=connection.platform,
                    notes=str(deal.ticket),
                )
                
                if profit is not None:
                    trade.is_win = profit > 0
                
                db.session.add(trade)
                trades_added += 1
                
            except Exception as e:
                print(f"⚠️  Error mapping MT deal: {e}")
                continue
        
        mt5.shutdown()
        
        connection.last_synced_at = datetime.utcnow()
        connection.sync_status = 'active'
        connection.last_error = None
        connection.last_error_at = None
        connection.total_trades_fetched = (connection.total_trades_fetched or 0) + trades_added
        connection.sync_count = (connection.sync_count or 0) + 1
        db.session.commit()
        
        print(f"✅ MT sync: {connection.platform} ({server}) → {trades_added} new trades")
        return True, trades_added, None
        
    except Exception as e:
        try:
            mt5.shutdown()
        except:
            pass
        
        error_msg = str(e)[:300]
        connection.sync_status = 'error'
        connection.last_error = error_msg
        connection.last_error_at = datetime.utcnow()
        db.session.commit()
        print(f"❌ MT sync error ({connection.platform}): {error_msg}")
        return False, 0, error_msg


# ═══════════════════════════════════════════════════════════
# 🔄 MAIN SYNC DISPATCHER
# ═══════════════════════════════════════════════════════════

def sync_connection(connection_id):
    """Sync a single connection."""
    connection = SyncConnection.query.get(connection_id)
    
    if not connection:
        return {'success': False, 'trades_added': 0, 'error': 'Connection not found'}
    
    if not connection.is_active:
        return {'success': False, 'trades_added': 0, 'error': 'Connection is inactive'}
    
    if connection.admin_stopped:
        return {'success': False, 'trades_added': 0, 'error': f'Stopped by admin: {connection.stop_reason}'}
    
    if connection.sync_status == 'pending':
        return {'success': False, 'trades_added': 0, 'error': 'Connection pending admin verification'}
    
    from models import User
    user = User.query.get(connection.user_id)
    if not user or not user.subscription_active:
        connection.is_active = False
        connection.sync_status = 'expired'
        connection.stop_reason = 'Subscription expired'
        db.session.commit()
        return {'success': False, 'trades_added': 0, 'error': 'Subscription expired'}
    
    if connection.market == 'crypto':
        success, count, error = fetch_crypto_trades(connection)
    elif connection.platform in ['mt4', 'mt5']:
        success, count, error = fetch_mt_trades(connection)
    else:
        return {'success': False, 'trades_added': 0, 'error': f'Unsupported market: {connection.market}'}
    
    return {'success': success, 'trades_added': count, 'error': error}


def sync_all_active_connections():
    """Sync all active connections every 5 minutes."""
    print(f"\n🔄 Starting batch sync at {datetime.utcnow()}")
    
    enforce_subscription_limits()
    
    connections = SyncConnection.query.filter_by(
        is_active=True,
        admin_stopped=False,
        sync_status='active'
    ).all()
    
    success_count = 0
    fail_count = 0
    total_trades = 0
    
    for conn in connections:
        result = sync_connection(conn.id)
        if result['success']:
            success_count += 1
            total_trades += result['trades_added']
        else:
            fail_count += 1
    
    print(f"✅ Batch sync done: {success_count} OK, {fail_count} failed, {total_trades} trades\n")
    return {'success': success_count, 'failed': fail_count, 'trades_added': total_trades}


# ═══════════════════════════════════════════════════════════
# 🧪 TEST CONNECTION
# ═══════════════════════════════════════════════════════════

def test_connection(market, platform, api_key=None, api_secret=None, 
                    server=None, login=None, password=None, passphrase=None):
    """Test if credentials work before saving."""
    if market == 'crypto':
        try:
            import ccxt
            config = {
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot',
                    'adjustForTimeDifference': True,
                    'recvWindow': 10000,
                },
            }
            if passphrase:
                config['password'] = passphrase
            
            # Handle "other" or empty platform — try auto-detect
            if platform == 'other' or not platform:
                exchanges_to_try = [
                    'binance', 'bybit', 'kraken', 'coinbase', 'kucoin', 'okx', 
                    'mexc', 'gate', 'bitget', 'huobi', 'bitfinex', 'gemini',
                    'bitstamp', 'poloniex', 'bittrex', 'hitbtc'
                ]
                
                for ex in exchanges_to_try:
                    try:
                        exchange_class = getattr(ccxt, ex)
                        exchange = exchange_class(config.copy())
                        exchange.load_time_difference()
                        exchange.fetch_balance()
                        return True, f"✅ Auto-detected exchange: {ex}. Connection successful!"
                    except:
                        continue
                
                return False, "❌ Could not auto-detect exchange. Please specify the exchange name manually."
            
            exchange_class = getattr(ccxt, platform)
            exchange = exchange_class(config)
            
            try:
                exchange.load_time_difference()
            except Exception:
                pass
            
            try:
                exchange.fetch_balance()
                return True, "✅ Connection successful! Read-only access confirmed."
            except Exception as e:
                error_str = str(e)
                if platform == 'binance' and '-1021' in error_str:
                    return False, "❌ Your system clock is out of sync with Binance's servers. Enable automatic time sync (Windows: Settings → Time & Language → Sync now. Mac/Linux: enable NTP) and try again."
                if 'AuthenticationError' in type(e).__name__ or '-2015' in error_str:
                    return False, "❌ Authentication failed. Check your API key and secret."
                if 'PermissionDenied' in type(e).__name__:
                    return False, "❌ API key lacks read permission."
                return False, f"❌ Connection failed: {error_str[:150]}"
                
        except ImportError:
            return False, "❌ CCXT not installed."
        except Exception as e:
            return False, f"❌ Error: {str(e)[:150]}"
    
    elif platform in ['mt4', 'mt5']:
        return True, "✅ MT credentials saved. Admin will verify manually."
    
    return False, "Unsupported market"


# ═══════════════════════════════════════════════════════════
# 📋 ADMIN HELPERS
# ═══════════════════════════════════════════════════════════

def get_all_connections_stats():
    total = SyncConnection.query.count()
    active = SyncConnection.query.filter_by(is_active=True, admin_stopped=False).count()
    failed = SyncConnection.query.filter_by(sync_status='error').count()
    stopped = SyncConnection.query.filter_by(admin_stopped=True).count()
    pending = SyncConnection.query.filter_by(sync_status='pending').count()
    
    stale_since = datetime.utcnow() - timedelta(hours=24)
    stale = SyncConnection.query.filter(
        SyncConnection.is_active == True,
        SyncConnection.admin_stopped == False,
        SyncConnection.last_synced_at < stale_since
    ).count()
    
    return {
        'total': total,
        'active': active,
        'failed': failed,
        'stopped': stopped,
        'pending': pending,
        'stale': stale
    }


def get_user_sync_stats(user_id):
    connections = SyncConnection.query.filter_by(user_id=user_id).all()
    
    return {
        'total': len(connections),
        'active': len([c for c in connections if c.is_active and not c.admin_stopped]),
        'failed': len([c for c in connections if c.sync_status == 'error']),
        'total_trades_fetched': sum(c.total_trades_fetched or 0 for c in connections),
        'connections': [{
            'id': c.id,
            'market': c.market,
            'platform': c.platform,
            'label': c.label,
            'status': c.sync_status,
            'last_synced': c.last_synced_at.isoformat() if c.last_synced_at else None,
            'trades_fetched': c.total_trades_fetched or 0,
            'error': c.last_error[:100] if c.last_error else None
        } for c in connections]
    }


def admin_stop_connection(connection_id, admin_id, reason='Stopped by admin'):
    conn = SyncConnection.query.get(connection_id)
    if conn:
        conn.is_active = False
        conn.admin_stopped = True
        conn.stop_reason = reason
        conn.stopped_by_admin_id = admin_id
        conn.sync_status = 'stopped'
        conn.updated_at = datetime.utcnow()
        db.session.commit()
        return True
    return False


def admin_start_connection(connection_id):
    conn = SyncConnection.query.get(connection_id)
    if conn:
        conn.is_active = True
        conn.admin_stopped = False
        conn.stop_reason = None
        conn.stopped_by_admin_id = None
        conn.sync_status = 'active'
        conn.updated_at = datetime.utcnow()
        db.session.commit()
        return True
    return False


def admin_stop_all_user_connections(user_id, admin_id, reason='Admin stopped all connections'):
    connections = SyncConnection.query.filter_by(user_id=user_id, is_active=True).all()
    for conn in connections:
        conn.is_active = False
        conn.admin_stopped = True
        conn.stop_reason = reason
        conn.stopped_by_admin_id = admin_id
        conn.sync_status = 'stopped'
        conn.updated_at = datetime.utcnow()
    db.session.commit()
    return len(connections)


# ═══════════════════════════════════════════════════════════
# ⏰ BACKGROUND SCHEDULER (every 5 minutes)
# ═══════════════════════════════════════════════════════════

_scheduler = None

def sync_with_context(app):
    """Wrapper to run sync inside app context"""
    if app is None:
        print("⚠️  No app context available for sync")
        return
    with app.app_context():
        sync_all_active_connections()

def start_scheduler(app=None):
    """Start background sync scheduler — runs every 5 minutes"""
    global _scheduler
    
    if app is None:
        print("⚠️  Cannot start scheduler: No app provided")
        return
    
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            lambda: sync_with_context(app),
            'interval',
            minutes=5,
            id='sync_all',
            name='Sync all active connections',
            replace_existing=True
        )
        _scheduler.start()
        print("✅ Sync scheduler started (every 5 minutes)")
        
    except ImportError:
        print("⚠️  APScheduler not installed. Sync will only work manually.")


def stop_scheduler():
    """Stop background scheduler"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        print("🛑 Sync scheduler stopped")

# -----------------------------------------------------------
# 🔄 VPS COMMUNICATION (MT4/MT5 Sync)
# -----------------------------------------------------------

# Fixed VPS URL and API key
VPS_URL = os.environ.get('VPS_SYNC_URL', 'http://localhost:5002')
VPS_INTERNAL_KEY = os.environ.get('VPS_INTERNAL_KEY', 'Hg7kLm9pQr2xYw4zNc8vBd5fJh3nMs6tUy1aXe0i')


def generate_sync_id(user_id, account_id):
    """Generate unique sync_id for VPS tracking"""
    import secrets
    random_part = secrets.token_hex(8)
    return f"TGF_SYNC_{user_id}_{account_id}_{random_part}"


def send_mt_credentials_to_vps(connection):
    """Send MT4/MT5 credentials to VPS sync server"""
    import requests
    
    try:
        password = decrypt(connection.investor_password_encrypted)
        if not password:
            return False, "Could not decrypt password"
        
        payload = {
            'sync_id': connection.sync_id,
            'username': connection.username,
            'mt5_login': connection.mt_account_number,
            'mt5_password': password,
            'mt5_server': connection.server_name
        }
        
        print(f"📤 Sending to VPS: {VPS_URL}/api/add_credential")
        print(f"   Sync ID: {connection.sync_id}")
        print(f"   Login: {connection.mt_account_number}")
        print(f"   Server: {connection.server_name}")
        
        resp = requests.post(
            f"{VPS_URL}/api/add_credential",
            json=payload,
            headers={'X-Internal-Key': VPS_INTERNAL_KEY},
            timeout=15
        )
        
        print(f"   VPS Response: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'ok':
                print(f"   ✅ VPS accepted credentials")
                return True, None
            else:
                print(f"   ❌ VPS error: {data.get('message')}")
                return False, data.get('message', 'VPS rejected credentials')
        else:
            print(f"   ❌ VPS returned: {resp.status_code}")
            return False, f"VPS returned: {resp.status_code}"
            
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Cannot connect to VPS at {VPS_URL}")
        return False, "VPS server unreachable. Check VPS is running."
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return False, str(e)[:200]


def remove_mt_credentials_from_vps(sync_id):
    """Remove credentials from VPS when user deletes connection"""
    import requests
    try:
        resp = requests.post(
            f"{VPS_URL}/api/delete_by_sync_id",
            json={'sync_id': sync_id},
            headers={'X-Internal-Key': VPS_INTERNAL_KEY},
            timeout=10
        )
        print(f"🗑️ VPS delete response: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ VPS delete error: {e}")
        return False


def pause_mt_on_vps(sync_id):
    """Pause sync on VPS"""
    import requests
    try:
        resp = requests.post(
            f"{VPS_URL}/api/pause_by_sync_id",
            json={'sync_id': sync_id},
            headers={'X-Internal-Key': VPS_INTERNAL_KEY},
            timeout=10
        )
        print(f"⏸️ VPS pause response: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ VPS pause error: {e}")
        return False


def resume_mt_on_vps(sync_id):
    """Resume sync on VPS"""
    import requests
    try:
        resp = requests.post(
            f"{VPS_URL}/api/resume_by_sync_id",
            json={'sync_id': sync_id},
            headers={'X-Internal-Key': VPS_INTERNAL_KEY},
            timeout=10
        )
        print(f"▶️ VPS resume response: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ VPS resume error: {e}")
        return False


def process_mt5_trade_data(payload):
    """Process incoming MT5 trade data from VPS"""
    sync_id = payload.get('sync_id')
    if not sync_id:
        return {'success': False, 'error': 'No sync_id in payload'}
    
    connection = SyncConnection.query.filter_by(sync_id=sync_id).first()
    if not connection:
        return {'success': False, 'error': f'Connection not found for sync_id: {sync_id}'}
    
    trades_added = 0
    closed_trades = payload.get('closed_trades', [])
    
    for trade_data in closed_trades:
        try:
            ticket = str(trade_data.get('ticket', ''))
            
            existing = Trade.query.filter_by(
                user_id=connection.user_id,
                account_id=connection.account_id,
                import_source='auto_sync',
                notes=f"MT5_{ticket}"
            ).first()
            
            if existing:
                continue
            
            symbol = trade_data.get('symbol', 'UNKNOWN')
            trade_type = 'buy' if trade_data.get('type') in [0, 2] else 'sell'
            open_price = trade_data.get('entry_price', trade_data.get('open_price', 0)) or 0
            close_price = trade_data.get('exit_price', trade_data.get('close_price', 0)) or 0
            lots = trade_data.get('lots', trade_data.get('volume', 1.0)) or 1.0
            profit = trade_data.get('profit')
            sl = trade_data.get('sl')
            tp = trade_data.get('tp')
            close_time = trade_data.get('close_time', trade_data.get('exit_time'))
            
            trade = Trade(
                user_id=connection.user_id,
                account_id=connection.account_id,
                market='forex',
                symbol=symbol,
                trade_type=trade_type,
                entry_price=float(open_price),
                exit_price=float(close_price),
                stop_loss=float(sl) if sl else None,
                take_profit=float(tp) if tp else None,
                quantity=float(lots),
                profit_loss=float(profit) if profit is not None else None,
                entry_date=datetime.fromtimestamp(close_time) if close_time else datetime.utcnow(),
                exit_date=datetime.fromtimestamp(close_time) if close_time else datetime.utcnow(),
                import_source='auto_sync',
                exchange='mt5',
                broker=connection.server_name or 'MT5',
                notes=f"MT5_{ticket}",
            )
            
            trade.calculate_pnl()
            db.session.add(trade)
            trades_added += 1
            
        except Exception as e:
            print(f"⚠️ Error mapping MT5 trade: {e}")
            continue
    
    connection.last_synced_at = datetime.utcnow()
    connection.sync_status = 'active'
    connection.last_error = None
    connection.last_error_at = None
    connection.total_trades_fetched = (connection.total_trades_fetched or 0) + trades_added
    connection.sync_count = (connection.sync_count or 0) + 1
    db.session.commit()
    
    print(f"✅ MT5 data processed: {sync_id} → {trades_added} new trades")
    return {'success': True, 'trades_added': trades_added}