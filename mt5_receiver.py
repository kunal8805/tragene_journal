from flask import Blueprint, request, jsonify
from extensions import db
from models import Trade, SyncConnection
from datetime import datetime
import os
import hmac
import secrets

mt5_receiver_bp = Blueprint('mt5_receiver', __name__)

# ═══════════════════════════════════════════════════════════
# 🔐 SIMPLE SECURITY - JUST API KEY
# ═══════════════════════════════════════════════════════════

VPS_API_KEY = os.environ.get('VPS_API_KEY', 'Hg7kLm9pQr2xYw4zNc8vBd5fJh3nMs6tUy1aXe0i')

def verify_api_key(api_key):
    """Simple API key verification"""
    if not api_key:
        return False
    return hmac.compare_digest(api_key, VPS_API_KEY)

# ═══════════════════════════════════════════════════════════
# 📥 MT5 TRADE RECEIVER ENDPOINT
# ═══════════════════════════════════════════════════════════

@mt5_receiver_bp.route('/api/sync/receive-trades', methods=['POST'])
def receive_trades_from_vps():
    """Endpoint where VPS sends MT5 trades back to main site"""

    api_key = request.headers.get('X-API-Key')

    if not verify_api_key(api_key):
        print(f"🚫 Blocked: Invalid API key from {request.remote_addr}")
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401

    try:
        data = request.get_json()

        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        sync_id = data.get('sync_id')
        trades = data.get('closed_trades', [])

        if not sync_id:
            return jsonify({'success': False, 'error': 'No sync_id provided'}), 400

        print(f"\n📥 RECEIVED TRADES FROM VPS:")
        print(f"   Sync ID: {sync_id}")
        print(f"   Trade Count: {len(trades)}")

        connection = SyncConnection.query.filter_by(
            sync_id=sync_id,
            is_active=True
        ).first()

        if not connection:
            print(f"   ❌ Connection not found or inactive: {sync_id}")
            return jsonify({'success': False, 'error': 'Connection not found'}), 404

        trades_added = 0
        trades_skipped = 0

        for trade_data in trades:
            try:
                ticket = str(trade_data.get('ticket', trade_data.get('order_id', '')))

                if not ticket:
                    continue

                existing = Trade.query.filter_by(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    platform_ticket=ticket
                ).first()

                if existing:
                    trades_skipped += 1
                    print(f"   ⏭️  Skipped duplicate: {ticket}")
                    continue

                entry_time = trade_data.get('entry_time') or trade_data.get('open_time')
                exit_time = trade_data.get('exit_time') or trade_data.get('close_time')

                if entry_time:
                    try:
                        entry_date = datetime.strptime(entry_time, '%Y-%m-%d %H:%M:%S')
                    except:
                        try:
                            entry_date = datetime.fromtimestamp(int(entry_time))
                        except:
                            entry_date = datetime.utcnow()
                else:
                    entry_date = datetime.utcnow()

                if exit_time:
                    try:
                        exit_date = datetime.strptime(exit_time, '%Y-%m-%d %H:%M:%S')
                    except:
                        try:
                            exit_date = datetime.fromtimestamp(int(exit_time))
                        except:
                            exit_date = None
                else:
                    exit_date = None

                trade = Trade(
                    user_id=connection.user_id,
                    account_id=connection.account_id,
                    symbol=str(trade_data.get('symbol', ''))[:20],
                    trade_type=trade_data.get('type', trade_data.get('trade_type', 'buy')),
                    entry_price=float(trade_data.get('entry_price', trade_data.get('open_price', 0))),
                    exit_price=float(trade_data.get('exit_price', trade_data.get('close_price', 0))) if trade_data.get('exit_price') or trade_data.get('close_price') else None,
                    quantity=float(trade_data.get('volume', trade_data.get('lots', 1.0))),
                    entry_date=entry_date,
                    exit_date=exit_date,
                    profit_loss=float(trade_data.get('profit', trade_data.get('pnl', 0))),
                    import_source='mt5_vps',
                    platform_ticket=ticket,
                    notes=None,
                    market='forex',
                    broker=connection.server_name or connection.platform.upper()
                )

                # Only auto-calculate P&L if MT5 didn't already give us a real profit value
                if trade.profit_loss == 0 and trade.exit_price:
                    trade.calculate_pnl()

                db.session.add(trade)
                trades_added += 1
                print(f"   ✅ Added: {trade.symbol} - ${trade.profit_loss}")

            except Exception as e:
                print(f"   ❌ Error adding trade: {str(e)}")
                continue

        # ✅ FIXED: Use last_synced_at (not last_sync)
        connection.last_synced_at = datetime.utcnow()
        connection.sync_status = 'active'
        connection.last_error = None
        connection.sync_count = (connection.sync_count or 0) + 1
        connection.total_trades_fetched = (connection.total_trades_fetched or 0) + trades_added

        db.session.commit()

        print(f"   ✅ Added: {trades_added}, Skipped: {trades_skipped}")
        print(f"   ✅ Connection updated: last_synced_at={connection.last_synced_at}")

        return jsonify({
            'success': True,
            'trades_added': trades_added,
            'trades_skipped': trades_skipped
        })

    except Exception as e:
        db.session.rollback()
        print(f"❌ Receive trades error: {str(e)}")
        return jsonify({'success': False, 'error': 'Server error'}), 500


@mt5_receiver_bp.route('/api/sync/health', methods=['GET'])
def health_check():
    """Health check for VPS - doesn't need API key"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })