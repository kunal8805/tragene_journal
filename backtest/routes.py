from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import login_required, current_user
from extensions import db
from models import User, BacktestStrategy, BacktestRun, HistoricalData, BacktestUsage
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import json
import os
from typing import Optional
from .engine import BacktestEngine
from .indicators import IndicatorCalculator

backtest_bp = Blueprint('backtest', __name__, url_prefix='/backtest')

# ==================== CONSTANTS ====================
TIER_LIMITS = {
    'free': 10,          # 10 backtests per month
    'pro': 150,          # 150 backtests per month (₹399)
    'elite': float('inf') # Unlimited (₹799)
}

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'historical')

# ==================== RATE LIMITER ====================
import time
from collections import defaultdict

class SimpleRateLimiter:
    def __init__(self):
        self.attempts = defaultdict(list)
        self.banned_until = {}
    
    def check(self, user_id):
        now = time.time()
        
        if user_id in self.banned_until:
            if now < self.banned_until[user_id]:
                remaining = int(self.banned_until[user_id] - now)
                hours = remaining // 3600
                minutes = (remaining % 3600) // 60
                return False, f"Rate limit applied. Try again in {hours}h {minutes}m."
            else:
                del self.banned_until[user_id]
                self.attempts[user_id] = []
        
        self.attempts[user_id] = [t for t in self.attempts[user_id] if t > now - 86400]
        
        last_minute = [t for t in self.attempts[user_id] if t > now - 60]
        if len(last_minute) >= 1:
            wait = 60 - int(now - last_minute[0])
            return False, f"Please wait {wait} seconds before next backtest."
        
        if len(self.attempts[user_id]) >= 60:
            self.banned_until[user_id] = now + 86400
            return False, "Too many backtests in 24 hours. Rate limit applied for 24 hours."
        
        return True, None
    
    def record(self, user_id):
        self.attempts[user_id].append(time.time())

rate_limiter = SimpleRateLimiter()

# ==================== USER ROUTES ====================

@backtest_bp.route('/builder')
@login_required
def builder():
    """Strategy builder page"""
    return render_template('user/backtest/builder.html')


@backtest_bp.route('/my-strategies')
@login_required
def my_strategies():
    """User's saved strategies"""
    return render_template('user/backtest/my_strategies.html')


@backtest_bp.route('/results/<int:run_id>')
@login_required
def results(run_id):
    """Backtest results page"""
    return render_template('user/backtest/results.html', run_id=run_id)


# ==================== USER API ENDPOINTS ====================

@backtest_bp.route('/api/symbols', methods=['GET'])
@login_required
def get_symbols():
    """Get available symbols"""
    symbols = get_all_symbols()
    return jsonify({'success': True, 'symbols': symbols})


@backtest_bp.route('/api/symbol-status/<path:symbol>', methods=['GET'])
@login_required
def symbol_status(symbol):
    """Check if specific symbol data is available"""
    from urllib.parse import unquote
    symbol = unquote(symbol)
    
    print(f"DEBUG: Checking symbol status for: {symbol}")
    
    data = HistoricalData.query.filter_by(symbol=symbol).first()
    
    if data and data.row_count > 0:
        print(f"DEBUG: Found data for {symbol}: {data.row_count} rows")
        return jsonify({
            'success': True,
            'available': True,
            'date_range': f"{data.date_range_start} to {data.date_range_end}",
            'row_count': data.row_count
        })
    
    # Check universal fallback
    universal = HistoricalData.query.filter_by(symbol='UNIVERSAL/EURUSD-DEMO').first()
    if universal and universal.row_count > 0:
        print(f"DEBUG: No data for {symbol}, using universal fallback")
        return jsonify({
            'success': True,
            'available': False,
            'message': 'Using universal fallback dataset',
            'date_range': f"{universal.date_range_start} to {universal.date_range_end}"
        })
    
    return jsonify({
        'success': False,
        'available': False,
        'message': 'No data available'
    })


@backtest_bp.route('/api/run', methods=['POST'])
@login_required
def run_backtest():
    """Run a backtest"""
    try:
        # Check quota
        can_run, message = check_quota(current_user)
        if not can_run:
            return jsonify({'success': False, 'message': message}), 403
        
        # Check rate limit
        can_run, rate_message = rate_limiter.check(current_user.id)
        if not can_run:
            return jsonify({'success': False, 'message': rate_message}), 429
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        # Validate required fields
        if not data.get('name'):
            return jsonify({'success': False, 'message': 'Strategy name required'}), 400
        
        if not data.get('symbol'):
            return jsonify({'success': False, 'message': 'Symbol required'}), 400
        
        if not data.get('entry_rules') or len(data.get('entry_rules', [])) == 0:
            return jsonify({'success': False, 'message': 'At least one entry rule required'}), 400
        
        # Load historical data
        symbol = data['symbol']
        timeframe = data.get('timeframe', '1h')
        
        historical_data = load_historical_data(symbol, timeframe)
        
        if historical_data is None or len(historical_data) == 0:
            return jsonify({
                'success': False, 
                'message': f'No historical data available for {symbol}. Please try another symbol or use universal dataset.'
            }), 404
        
        # Filter by date range if provided
        date_range = data.get('date_range', {})
        if date_range.get('start'):
            start_date = pd.to_datetime(date_range['start']).tz_localize(None)
            if hasattr(historical_data.index, 'tz') and historical_data.index.tz is not None:
                historical_data.index = historical_data.index.tz_localize(None)
            historical_data = historical_data[historical_data.index >= start_date]
        
        if date_range.get('end'):
            end_date = pd.to_datetime(date_range['end']).tz_localize(None)
            if hasattr(historical_data.index, 'tz') and historical_data.index.tz is not None:
                historical_data.index = historical_data.index.tz_localize(None)
            historical_data = historical_data[historical_data.index <= end_date]
        
        # Sample data to max 5000 bars (~92% accuracy)
        if len(historical_data) > 5000:
            sample_step = len(historical_data) // 5000
            historical_data = historical_data.iloc[::sample_step]
            print(f'DEBUG: Sampled data to {len(historical_data)} bars')
        
        if len(historical_data) < 20:
            return jsonify({'success': False, 'message': 'Insufficient data for backtest (need at least 20 bars)'}), 400
        
        # Run backtest
        engine = BacktestEngine(
            data=historical_data,
            strategy_config=data,
            symbol=symbol,
            timeframe=timeframe
        )
        
        results = engine.run()
        
        # Increment usage count
        increment_usage(current_user)
        
        # Record rate limiter attempt
        rate_limiter.record(current_user.id)
        
        # Save backtest run to database
        # Convert date strings to Python date objects
        if date_range.get('start'):
            start_date = pd.to_datetime(date_range['start']).date()
        else:
            start_date = historical_data.index[0].date()
        
        if date_range.get('end'):
            end_date = pd.to_datetime(date_range['end']).date()
        else:
            end_date = historical_data.index[-1].date()
        
        run = BacktestRun(
            user_id=current_user.id,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            config_json=json.dumps(data),
            status='completed',
            created_at=datetime.utcnow()
        )
        run.set_results(results)
        db.session.add(run)
        
        # Save strategy if requested
        if data.get('save_strategy'):
            strategy = BacktestStrategy(
                user_id=current_user.id,
                name=data['name'],
                description=data.get('description', ''),
                symbol=symbol,
                timeframe=timeframe,
                is_active=True
            )
            strategy.set_config(data)
            db.session.add(strategy)
            db.session.flush()
            run.strategy_id = strategy.id
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'run_id': run.id,
            'message': 'Backtest completed successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Backtest error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error running backtest: {str(e)[:200]}'}), 500


@backtest_bp.route('/api/my-usage', methods=['GET'])
@login_required
def my_usage():
    """Get user's backtest usage stats"""
    try:
        user = current_user
        tier = user.subscription_tier if user.subscription_tier in TIER_LIMITS else 'free'
        
        today = datetime.utcnow().date()
        month_start = today.replace(day=1)
        
        today_count = BacktestRun.query.filter_by(user_id=user.id).filter(
            db.func.date(BacktestRun.created_at) == today
        ).count()
        
        month_count = BacktestRun.query.filter_by(user_id=user.id).filter(
            db.func.date(BacktestRun.created_at) >= month_start
        ).count()
        
        total_count = BacktestRun.query.filter_by(user_id=user.id).count()
        
        tier_labels = {'free': 'Free', 'pro': 'Pro', 'elite': 'Elite'}
        monthly_limit = TIER_LIMITS.get(tier, 10)
        
        if monthly_limit == float('inf'):
            remaining = 'Unlimited'
            usage_percent = 0
        else:
            remaining = max(0, monthly_limit - month_count)
            usage_percent = round((month_count / monthly_limit) * 100, 1) if monthly_limit > 0 else 0
        
        return jsonify({
            'success': True,
            'usage': {
                'tier': tier_labels.get(tier, 'Free'),
                'today_count': today_count,
                'month_count': month_count,
                'total_count': total_count,
                'monthly_limit': monthly_limit if monthly_limit != float('inf') else 'Unlimited',
                'remaining': remaining,
                'usage_percent': usage_percent
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@backtest_bp.route('/api/results/<int:run_id>', methods=['GET'])
@login_required
def get_results(run_id):
    """Get backtest results"""
    run = BacktestRun.query.get(run_id)
    
    if not run:
        return jsonify({'success': False, 'message': 'Backtest not found'}), 404
    
    # Check if user owns this backtest or is admin
    if run.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    return jsonify({
        'success': True,
        'results': run.get_results()
    })


@backtest_bp.route('/api/save_strategy', methods=['POST'])
@login_required
def save_strategy():
    """Save a strategy"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        if not data.get('name'):
            return jsonify({'success': False, 'message': 'Strategy name required'}), 400
        
        strategy = BacktestStrategy(
            user_id=current_user.id,
            name=data['name'],
            description=data.get('description', ''),
            symbol=data.get('symbol', ''),
            timeframe=data.get('timeframe', '1h'),
            is_active=True
        )
        strategy.set_config(data)
        
        db.session.add(strategy)
        db.session.commit()
        
        return jsonify({'success': True, 'strategy_id': strategy.id, 'message': 'Strategy saved'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error saving strategy: {str(e)}'}), 500


@backtest_bp.route('/api/strategies', methods=['GET'])
@login_required
def get_strategies():
    """Get user's saved strategies"""
    strategies = BacktestStrategy.query.filter_by(user_id=current_user.id).order_by(
        BacktestStrategy.created_at.desc()
    ).all()
    
    strategy_list = []
    for strategy in strategies:
        # Get last run results if available
        last_run = BacktestRun.query.filter_by(strategy_id=strategy.id).order_by(
            BacktestRun.created_at.desc()
        ).first()
        
        last_run_stats = None
        if last_run:
            results = last_run.get_results()
            if results and 'stats' in results:
                last_run_stats = results['stats']
        
        strategy_list.append({
            'id': strategy.id,
            'name': strategy.name,
            'description': strategy.description,
            'symbol': strategy.symbol,
            'timeframe': strategy.timeframe,
            'is_active': strategy.is_active,
            'created_at': strategy.created_at.isoformat(),
            'updated_at': strategy.updated_at.isoformat(),
            'last_run': last_run_stats
        })
    
    return jsonify({'success': True, 'strategies': strategy_list})


@backtest_bp.route('/api/run_strategy/<int:strategy_id>', methods=['POST'])
@login_required
def run_saved_strategy(strategy_id):
    """Run a saved strategy"""
    strategy = BacktestStrategy.query.get(strategy_id)
    
    if not strategy or strategy.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Strategy not found'}), 404
    
    # Check quota
    can_run, message = check_quota(current_user)
    if not can_run:
        return jsonify({'success': False, 'message': message}), 403
    
    config = strategy.get_config()
    config['name'] = strategy.name
    
    # Load data and run backtest
    historical_data = load_historical_data(strategy.symbol, strategy.timeframe)
    
    if historical_data is None or len(historical_data) == 0:
        return jsonify({'success': False, 'message': 'No data available for this strategy'}), 404
    
    engine = BacktestEngine(
        data=historical_data,
        strategy_config=config,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe
    )
    
    results = engine.run()
    
    # Save run
    run = BacktestRun(
        user_id=current_user.id,
        strategy_id=strategy.id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        start_date=historical_data.index[0].date(),
        end_date=historical_data.index[-1].date(),
        config_json=json.dumps(config),
        status='completed',
        created_at=datetime.utcnow()
    )
    run.set_results(results)
    
    db.session.add(run)
    increment_usage(current_user)
    db.session.commit()
    
    return jsonify({'success': True, 'run_id': run.id})


@backtest_bp.route('/api/clone_strategy/<int:strategy_id>', methods=['POST'])
@login_required
def clone_strategy(strategy_id):
    """Clone a strategy"""
    strategy = BacktestStrategy.query.get(strategy_id)
    
    if not strategy or strategy.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Strategy not found'}), 404
    
    new_strategy = BacktestStrategy(
        user_id=current_user.id,
        name=f"{strategy.name} (Copy)",
        description=strategy.description,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        is_active=True
    )
    new_strategy.set_config(strategy.get_config())
    
    db.session.add(new_strategy)
    db.session.commit()
    
    return jsonify({'success': True, 'strategy_id': new_strategy.id})


@backtest_bp.route('/api/delete_strategy/<int:strategy_id>', methods=['DELETE'])
@login_required
def delete_strategy(strategy_id):
    """Delete a strategy"""
    strategy = BacktestStrategy.query.get(strategy_id)
    
    if not strategy or strategy.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Strategy not found'}), 404
    
    db.session.delete(strategy)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Strategy deleted'})


# ==================== ADMIN ROUTES ====================

@backtest_bp.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """Admin dashboard page"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('user.dashboard'))
    
    return render_template('admin/backtest/dashboard.html')


@backtest_bp.route('/admin/data-manager')
@login_required
def admin_data_manager():
    """Admin data manager page"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('user.dashboard'))
    
    return render_template('admin/backtest/data_manager.html')


@backtest_bp.route('/admin/analytics')
@login_required
def admin_analytics():
    """Admin analytics page"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('user.dashboard'))
    
    return render_template('admin/backtest/backtest_analytics.html')


# ==================== ADMIN API ENDPOINTS ====================

@backtest_bp.route('/api/admin/dashboard', methods=['GET'])
@login_required
def admin_dashboard_data():
    """Get admin dashboard data"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    
    # Stats
    total_backtests = BacktestRun.query.count()
    today_backtests = BacktestRun.query.filter(
        db.func.date(BacktestRun.created_at) == today
    ).count()
    month_backtests = BacktestRun.query.filter(
        db.func.date(BacktestRun.created_at) >= month_start
    ).count()
    last_month_backtests = BacktestRun.query.filter(
        db.func.date(BacktestRun.created_at) >= last_month_start,
        db.func.date(BacktestRun.created_at) < month_start
    ).count()
    
    active_users = db.session.query(db.func.count(db.func.distinct(BacktestRun.user_id))).filter(
        db.func.date(BacktestRun.created_at) >= month_start
    ).scalar() or 0
    
    total_strategies = BacktestStrategy.query.count()
    data_files = HistoricalData.query.count()
    
    # Calculate average return
    all_runs = BacktestRun.query.filter_by(status='completed').all()
    avg_return = 0
    if all_runs:
        total_return_sum = 0
        return_count = 0
        for run in all_runs:
            results = run.get_results()
            if results and 'stats' in results:
                total_return_sum += results['stats'].get('total_return', 0)
                return_count += 1
        if return_count > 0:
            avg_return = total_return_sum / return_count
    
    # Daily counts for last 7 days
    daily_labels = []
    daily_counts = []
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        daily_labels.append(date.strftime('%a'))
        count = BacktestRun.query.filter(
            db.func.date(BacktestRun.created_at) == date
        ).count()
        daily_counts.append(count)
    
    # Top users
    top_users_query = db.session.query(
        User.username,
        db.func.count(BacktestRun.id).label('count')
    ).join(BacktestRun, BacktestRun.user_id == User.id).group_by(
        User.username
    ).order_by(db.desc('count')).limit(10).all()
    
    top_users = [{'username': u[0], 'count': u[1]} for u in top_users_query]
    
    # Recent activity
    recent_runs = BacktestRun.query.order_by(BacktestRun.created_at.desc()).limit(20).all()
    recent_activity = []
    for run in recent_runs:
        user = User.query.get(run.user_id)
        strategy_name = None
        if run.strategy_id:
            strategy = BacktestStrategy.query.get(run.strategy_id)
            if strategy:
                strategy_name = strategy.name
        
        recent_activity.append({
            'id': run.id,
            'username': user.username if user else 'Unknown',
            'strategy_name': strategy_name,
            'symbol': run.symbol,
            'timeframe': run.timeframe,
            'status': run.status,
            'created_at': run.created_at.isoformat()
        })
    
    # Top strategies
    top_strategies = get_top_strategies(10)
    
    return jsonify({
        'success': True,
        'stats': {
            'total_backtests': total_backtests,
            'today_backtests': today_backtests,
            'month_backtests': month_backtests,
            'last_month_backtests': last_month_backtests,
            'active_users': active_users,
            'total_strategies': total_strategies,
            'data_files': data_files,
            'avg_return': round(avg_return, 2)
        },
        'charts': {
            'daily_labels': daily_labels,
            'daily_counts': daily_counts,
            'top_users': top_users
        },
        'recent_activity': recent_activity,
        'top_strategies': top_strategies
    })


@backtest_bp.route('/api/admin/data-status', methods=['GET'])
@login_required
def admin_data_status():
    """Get data status for all symbols"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    all_data = {}
    all_symbols = get_all_symbols()
    
    for symbol in all_symbols:
        data = HistoricalData.query.filter_by(symbol=symbol).first()
        if data and data.row_count > 0:
            all_data[symbol] = {
                'row_count': data.row_count,
                'date_start': str(data.date_range_start) if data.date_range_start else 'N/A',
                'date_end': str(data.date_range_end) if data.date_range_end else 'N/A',
                'timeframes': {data.timeframe: True}
            }
    
    # Universal dataset
    universal = HistoricalData.query.filter_by(symbol='UNIVERSAL/EURUSD-DEMO').first()
    universal_data = None
    if universal and universal.row_count > 0:
        universal_data = {
            'row_count': universal.row_count,
            'date_start': str(universal.date_range_start),
            'date_end': str(universal.date_range_end),
            'timeframes': {universal.timeframe: True}
        }
    
    total_loaded = len([s for s in all_symbols if s in all_data])
    total_symbols = len(all_symbols)
    
    return jsonify({
        'success': True,
        'data': all_data,
        'universal': universal_data,
        'stats': {
            'total_symbols': total_symbols,
            'total_loaded': total_loaded,
            'total_missing': total_symbols - total_loaded,
            'universal': universal_data is not None
        }
    })


@backtest_bp.route('/api/admin/upload-csv', methods=['POST'])
@login_required
def admin_upload_csv():
    """Upload CSV data for a symbol - accepts any format with smart column matching"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    try:
        symbol = request.form.get('symbol')
        file = request.files.get('file')
        
        if not symbol or not file:
            return jsonify({'success': False, 'message': 'Symbol and file required'}), 400
        
        if not file.filename.endswith('.csv'):
            return jsonify({'success': False, 'message': 'Only CSV files allowed'}), 400
        
        # Read CSV content
        file_content = file.read()
        
        # Check if semicolon-separated (no header)
        if b';' in file_content[:1000]:
            # Semicolon separated, no header
            df = pd.read_csv(pd.io.common.BytesIO(file_content), sep=';', header=None)
            df.columns = ['date', 'open', 'high', 'low', 'close', 'volume'][:len(df.columns)]
        else:
            # Try comma-separated with header
            try:
                df = pd.read_csv(pd.io.common.BytesIO(file_content))
            except:
                # Comma-separated without header
                df = pd.read_csv(pd.io.common.BytesIO(file_content), header=None)
                df.columns = ['date', 'open', 'high', 'low', 'close', 'volume'][:len(df.columns)]
        
        # Smart column matching - accepts ANY CSV format
        column_aliases = {
            'date': [
                'date', 'datetime', 'timestamp', 'time', 'local time', 'open time', 
                'open_time', 'datetime_utc', 'date_time', 'etc/utc', 'etc_utc', 
                'utc', 'gmt', 'time (utc)', 'time_utc', 'time (etc/utc)',
                'timezone', 'time_zone', 'tz', 'datetime_local', 'local_time',
                'date_time_utc', 'datetime_utc+00:00', 'date_time_utc+00:00'
            ],
            'open': [
                'open', 'o', 'open_price', 'open price', 'price_open', 
                'opening price', 'opening_price', 'open_value'
            ],
            'high': [
                'high', 'h', 'high_price', 'high price', 'price_high', 
                'highest', 'highest_price', 'high_value'
            ],
            'low': [
                'low', 'l', 'low_price', 'low price', 'price_low', 
                'lowest', 'lowest_price', 'low_value'
            ],
            'close': [
                'close', 'c', 'close_price', 'close price', 'price_close', 
                'adj close', 'adj_close', 'adjusted close', 'adjusted_close', 
                'closing price', 'closing_price', 'price', 'close_value'
            ],
            'volume': [
                'volume', 'vol', 'v', 'trade_volume', 'base_volume', 
                'quote_volume', 'volume_base', 'volume_quote', 'vol_base', 
                'vol_quote', 'total_volume', 'traded_volume'
            ]
        }
        
        # Normalize column names for matching
        df.columns = [str(c).strip() for c in df.columns]
        
        # Find matching columns
        matched_columns = {}
        used_columns = set()
        
        for canonical, aliases in column_aliases.items():
            match = None
            for col in df.columns:
                if col in used_columns:
                    continue
                col_lower = col.lower().strip()
                if col_lower in [a.lower() for a in aliases]:
                    match = col
                    break
            
            if match:
                matched_columns[canonical] = match
                used_columns.add(match)
        
        # FALLBACK: If no date column matched, use the FIRST column as date
        # This handles unusual timezone column names like "Etc/UTC"
        if 'date' not in matched_columns and len(df.columns) > 0:
            first_col = df.columns[0]
            if first_col not in used_columns:
                matched_columns['date'] = first_col
                used_columns.add(first_col)
                print(f"Fallback: Using first column '{first_col}' as date")
        
        # Check required fields
        required_fields = ['date', 'open', 'high', 'low', 'close']
        missing_fields = [f for f in required_fields if f not in matched_columns]
        
        if missing_fields:
            return jsonify({
                'success': False,
                'message': f'Missing required columns: {", ".join(missing_fields)}. Your CSV has: {", ".join(df.columns)}'
            }), 400
        
        # Rename matched columns to canonical names
        rename_map = {v: k for k, v in matched_columns.items()}
        df.rename(columns=rename_map, inplace=True)
        
        # Keep only canonical columns
        keep_columns = ['date', 'open', 'high', 'low', 'close']
        if 'volume' in matched_columns:
            keep_columns.append('volume')
        
        df = df[keep_columns]
        
        # Handle date conversion - try multiple formats for YOUR specific CSV
        date_str = df['date'].astype(str).str.strip()
        
        # Try YYYYMMDD HHMMSS format first (e.g., 20250101 180000)
        df['date'] = pd.to_datetime(date_str.str.replace(' ', ''), format='%Y%m%d%H%M%S', errors='coerce')
        
        # If that failed, try standard formats
        if df['date'].isna().any():
            df['date'] = pd.to_datetime(date_str, errors='coerce', utc=False)
        
        # Remove timezone if present (ALWAYS timezone-naive)
        df['date'] = df['date'].dt.tz_localize(None)
        
        # Drop rows where date couldn't be parsed
        df = df.dropna(subset=['date'])
        
        # Print what format was detected for debugging
        print(f"Date format detected: first date = {df['date'].iloc[0] if len(df) > 0 else 'NONE'}")
        
        # Convert numeric columns
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Add volume if missing
        if 'volume' not in df.columns:
            df['volume'] = 0
        else:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        
        # Drop rows with null OHLC values
        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        
        # Sort chronologically
        df = df.sort_values('date')
        df.set_index('date', inplace=True)
        
        # Check for duplicates
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='first')]
        
        # Check minimum rows
        if len(df) < 10:
            return jsonify({'success': False, 'message': f'Insufficient data: only {len(df)} valid rows found. Need at least 10.'}), 400
        
        # Save to file
        os.makedirs(DATA_DIR, exist_ok=True)
        filename = f"{symbol.replace('/', '_')}.csv"
        filepath = os.path.join(DATA_DIR, filename)
        df.to_csv(filepath)
        
        # Auto-detect timeframe from data
        if len(df) > 1:
            time_diff = df.index[1] - df.index[0]
            minutes = time_diff.total_seconds() / 60
            if minutes < 5:
                detected_tf = '1m'
            elif minutes < 15:
                detected_tf = '5m'
            elif minutes < 30:
                detected_tf = '15m'
            elif minutes < 60:
                detected_tf = '30m'
            elif minutes < 240:
                detected_tf = '1h'
            elif minutes < 1440:
                detected_tf = '4h'
            else:
                detected_tf = '1d'
        else:
            detected_tf = '1d'
        
        # Update database
        existing = HistoricalData.query.filter_by(
            symbol=symbol,
            timeframe=detected_tf
        ).first()
        
        if not existing:
            existing = HistoricalData(
                symbol=symbol,
                timeframe=detected_tf,
                filename=filename,
                uploaded_by=current_user.id
            )
        
        existing.filename = filename
        existing.date_range_start = df.index[0].date()
        existing.date_range_end = df.index[-1].date()
        existing.row_count = len(df)
        existing.file_size = os.path.getsize(filepath)
        existing.updated_at = datetime.utcnow()
        
        db.session.add(existing)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Loaded {len(df)} rows from {df.index[0].date()} to {df.index[-1].date()}',
            'row_count': len(df),
            'date_start': str(df.index[0].date()),
            'date_end': str(df.index[-1].date()),
            'matched_columns': {k: v for k, v in matched_columns.items()},
            'timeframe': detected_tf
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Upload error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error uploading CSV: {str(e)[:200]}'}), 500


@backtest_bp.route('/api/admin/delete-csv/<path:symbol>', methods=['DELETE'])
@login_required
def admin_delete_csv(symbol):
    """Delete CSV data for a symbol"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    try:
        data = HistoricalData.query.filter_by(symbol=symbol).first()
        
        if data:
            # Delete file
            filepath = os.path.join(DATA_DIR, data.filename)
            if os.path.exists(filepath):
                os.remove(filepath)
            
            # Delete from database
            db.session.delete(data)
            db.session.commit()
            
            return jsonify({'success': True, 'message': f'Data deleted for {symbol}'})
        
        return jsonify({'success': False, 'message': 'No data found for this symbol'}), 404
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting data: {str(e)}'}), 500


@backtest_bp.route('/api/admin/analytics', methods=['GET'])
@login_required
def admin_analytics_data():
    """Get analytics data"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    # Monthly volume (always return 12 months)
    monthly_labels = []
    monthly_values = []
    for i in range(11, -1, -1):
        month_date = datetime.utcnow().replace(day=1) - timedelta(days=i*30)
        month_date = month_date.replace(day=1)
        month_start = month_date
        month_end = (month_start + timedelta(days=32)).replace(day=1)
        
        count = BacktestRun.query.filter(
            BacktestRun.created_at >= month_start,
            BacktestRun.created_at < month_end
        ).count()
        
        monthly_labels.append(month_start.strftime('%b'))
        monthly_values.append(count)
    
    # Success rate
    all_completed = BacktestRun.query.filter_by(status='completed').all()
    profitable = 0
    loss_making = 0
    for run in all_completed:
        results = run.get_results()
        if results and 'stats' in results:
            if results['stats'].get('total_return', 0) >= 0:
                profitable += 1
            else:
                loss_making += 1
    
    # Top symbols (always return list)
    symbol_counts = db.session.query(
        BacktestRun.symbol,
        db.func.count(BacktestRun.id).label('count')
    ).group_by(BacktestRun.symbol).order_by(db.desc('count')).limit(10).all()
    
    top_symbols = [{'symbol': s[0], 'count': s[1]} for s in symbol_counts] if symbol_counts else []
    
    # Timeframe stats (always return data)
    timeframe_counts = db.session.query(
        BacktestRun.timeframe,
        db.func.count(BacktestRun.id).label('count')
    ).group_by(BacktestRun.timeframe).all()
    
    if timeframe_counts:
        timeframe_labels = [t[0] for t in timeframe_counts]
        timeframe_values = [t[1] for t in timeframe_counts]
    else:
        timeframe_labels = ['1m', '5m', '15m', '1h', '4h', '1d']
        timeframe_values = [0, 0, 0, 0, 0, 0]
    
    # User stats (always return list)
    users = User.query.all()
    user_stats = []
    for user in users:
        today = datetime.utcnow().date()
        month_start = today.replace(day=1)
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)
        
        today_count = BacktestRun.query.filter_by(user_id=user.id).filter(
            db.func.date(BacktestRun.created_at) == today
        ).count()
        
        month_count = BacktestRun.query.filter_by(user_id=user.id).filter(
            db.func.date(BacktestRun.created_at) >= month_start
        ).count()
        
        last_month_count = BacktestRun.query.filter_by(user_id=user.id).filter(
            db.func.date(BacktestRun.created_at) >= last_month_start,
            db.func.date(BacktestRun.created_at) < month_start
        ).count()
        
        total_count = BacktestRun.query.filter_by(user_id=user.id).count()
        strategy_count = BacktestStrategy.query.filter_by(user_id=user.id).count()
        
        best_return = None
        user_runs = BacktestRun.query.filter_by(user_id=user.id, status='completed').all()
        for run in user_runs:
            results = run.get_results()
            if results and 'stats' in results:
                return_val = results['stats'].get('total_return', 0)
                if best_return is None or return_val > best_return:
                    best_return = return_val
        
        user_stats.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'tier': user.subscription_tier,
            'today_count': today_count,
            'month_count': month_count,
            'last_month_count': last_month_count,
            'total_count': total_count,
            'strategy_count': strategy_count,
            'best_return': best_return
        })
    
    user_stats.sort(key=lambda x: x['total_count'], reverse=True)
    
    # Symbol statistics (always return list)
    symbol_stats = []
    for symbol in get_all_symbols():
        symbol_runs = BacktestRun.query.filter_by(symbol=symbol, status='completed').all()
        if symbol_runs:
            returns = []
            win_rates = []
            for run in symbol_runs:
                results = run.get_results()
                if results and 'stats' in results:
                    returns.append(results['stats'].get('total_return', 0))
                    win_rates.append(results['stats'].get('win_rate', 0))
            
            avg_return = float(np.mean(returns)) if returns else 0.0
            best_return = float(max(returns)) if returns else 0.0
            avg_win_rate = float(np.mean(win_rates)) if win_rates else 0.0
            
            category = 'forex' if symbol in ['EUR/USD', 'GBP/USD', 'USD/JPY'] else \
                      'crypto' if '/USDT' in symbol else 'indian'
            
            symbol_stats.append({
                'symbol': symbol,
                'category': category,
                'times_tested': len(symbol_runs),
                'avg_return': avg_return,
                'best_return': best_return,
                'win_rate': avg_win_rate
            })
    
    # Key metrics
    key_metrics = [
        {'label': 'Total Backtests', 'value': str(BacktestRun.query.count()), 'change': 'All time', 'change_type': 'info', 'color': 'var(--text-primary)'},
        {'label': 'Total Users', 'value': str(User.query.count()), 'change': 'Registered', 'change_type': 'info', 'color': 'var(--text-primary)'},
        {'label': 'Total Strategies', 'value': str(BacktestStrategy.query.count()), 'change': 'Saved', 'change_type': 'info', 'color': 'var(--text-primary)'},
        {'label': 'Data Files', 'value': str(HistoricalData.query.count()), 'change': 'Uploaded', 'change_type': 'info', 'color': 'var(--text-primary)'},
    ]
    
    # Symbol distribution
    forex_count = sum(1 for s in symbol_stats if s['category'] == 'forex')
    crypto_count = sum(1 for s in symbol_stats if s['category'] == 'crypto')
    indian_count = sum(1 for s in symbol_stats if s['category'] == 'indian')
    
    # Timeframe performance
    win_rates_list = []
    returns_list = []
    for tf in timeframe_labels:
        tf_runs = BacktestRun.query.filter_by(timeframe=tf, status='completed').all()
        tf_returns = []
        tf_wins = []
        for run in tf_runs:
            results = run.get_results()
            if results and 'stats' in results:
                tf_returns.append(results['stats'].get('total_return', 0))
                tf_wins.append(results['stats'].get('win_rate', 0))
        
        returns_list.append(float(np.mean(tf_returns)) if tf_returns else 0.0)
        win_rates_list.append(float(np.mean(tf_wins)) if tf_wins else 0.0)
    
    return jsonify({
        'success': True,
        'key_metrics': key_metrics,
        'monthly_volume': {'labels': monthly_labels, 'values': monthly_values},
        'success_rate': {'profitable': profitable, 'loss_making': loss_making},
        'top_symbols': top_symbols,
        'timeframe_stats': {'labels': timeframe_labels, 'values': timeframe_values},
        'users': user_stats,
        'top_strategies': get_top_strategies(20),
        'symbol_stats': symbol_stats,
        'symbol_distribution': {
            'forex': forex_count,
            'crypto': crypto_count,
            'indian': indian_count
        },
        'symbol_performance': symbol_stats[:10],
        'timeframe_popularity': {'labels': timeframe_labels, 'values': timeframe_values},
        'timeframe_performance': {
            'labels': timeframe_labels,
            'win_rates': win_rates_list,
            'returns': returns_list
        }
    })


# ==================== HELPER FUNCTIONS ====================

def get_all_symbols():
    """Get list of all supported symbols"""
    return [
        'UNIVERSAL/EURUSD-DEMO',
        # Forex Majors
        'EUR/USD', 'USD/JPY', 'GBP/USD', 'USD/CHF', 'USD/CAD', 'AUD/USD', 'NZD/USD',
        # Forex Crosses
        'EUR/GBP', 'EUR/JPY', 'GBP/JPY', 'EUR/CHF', 'AUD/JPY', 'EUR/AUD', 'GBP/AUD',
        'CHF/JPY', 'CAD/JPY', 'NZD/JPY',
        # Commodities
        'XAU/USD', 'XAG/USD', 'USOIL', 'UKOIL',
        # Extra
        'USD/INR',
        # Crypto
        'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
        'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'MATIC/USDT', 'DOT/USDT',
        # Indian Indices
        'NIFTY 50', 'BANK NIFTY', 'SENSEX',
        # Indian Stocks
        'RELIANCE', 'HDFC BANK', 'ICICI BANK', 'TCS', 'INFOSYS', 'SBIN', 'TATA MOTORS'
    ]


def load_historical_data(symbol: str, timeframe: str = '1d') -> Optional[pd.DataFrame]:
    """
    Load historical data for a symbol.
    Falls back to universal dataset if specific symbol not found.
    """
    # Try to load specific symbol data with matching timeframe
    data = HistoricalData.query.filter_by(symbol=symbol, timeframe=timeframe).first()
    
    # If not found, try ANY timeframe for this symbol
    if not data:
        data = HistoricalData.query.filter_by(symbol=symbol).first()
    
    if data:
        filepath = os.path.join(DATA_DIR, data.filename)
        if os.path.exists(filepath):
            try:
                df = pd.read_csv(filepath)
                df['date'] = pd.to_datetime(df['date'], errors='coerce', utc=False)
                df['date'] = df['date'].dt.tz_localize(None)
                df = df.dropna(subset=['date'])
                df.set_index('date', inplace=True)
                df = df.sort_index()
                return df
            except Exception as e:
                print(f"Error loading {symbol} data: {e}")
    
    # Try universal fallback
    universal = HistoricalData.query.filter_by(
        symbol='UNIVERSAL/EURUSD-DEMO', 
        timeframe=timeframe
    ).first()
    
    if not universal:
        # Try any timeframe
        universal = HistoricalData.query.filter_by(symbol='UNIVERSAL/EURUSD-DEMO').first()
    
    if universal:
        filepath = os.path.join(DATA_DIR, universal.filename)
        if os.path.exists(filepath):
            try:
                df = pd.read_csv(filepath)
                df['date'] = pd.to_datetime(df['date'], errors='coerce', utc=False)
                df['date'] = df['date'].dt.tz_localize(None)
                df = df.dropna(subset=['date'])
                df.set_index('date', inplace=True)
                df = df.sort_index()
                return df
            except Exception as e:
                print(f"Error loading universal data: {e}")
    
    return None


def check_quota(user: User) -> tuple:
    """Check if user can run a backtest"""
    limit = TIER_LIMITS.get(user.subscription_tier, TIER_LIMITS['free'])
    
    if limit == float('inf'):
        return True, None
    
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    
    # Count backtests this month
    month_count = BacktestRun.query.filter_by(user_id=user.id).filter(
        db.func.date(BacktestRun.created_at) >= month_start
    ).count()
    
    if month_count >= limit:
        remaining = 0
        message = f'Monthly limit reached ({limit}/{limit}). Upgrade for more backtests.'
        return False, message
    
    remaining = limit - month_count
    return True, f'{remaining} backtests remaining this month'


def increment_usage(user: User):
    """Increment user's backtest usage count"""
    today = datetime.utcnow().date()
    
    usage = BacktestUsage.query.filter_by(user_id=user.id, run_date=today).first()
    
    if usage:
        usage.run_count += 1
    else:
        usage = BacktestUsage(user_id=user.id, run_date=today, run_count=1)
        db.session.add(usage)


def get_top_strategies(limit: int = 10) -> list:
    """Get top performing strategies"""
    all_runs = BacktestRun.query.filter_by(status='completed').all()
    
    strategy_performance = {}
    
    for run in all_runs:
        results = run.get_results()
        if results and 'stats' in results:
            user = User.query.get(run.user_id)
            username = user.username if user else 'Unknown'
            
            key = f"{run.user_id}_{run.symbol}_{run.strategy_id or run.id}"
            
            stats = results['stats']
            return_val = stats.get('total_return', 0)
            
            if key not in strategy_performance:
                strategy_performance[key] = {
                    'username': username,
                    'strategy_name': results.get('strategy_name', 'Unnamed'),
                    'symbol': run.symbol,
                    'total_return': return_val,
                    'win_rate': stats.get('win_rate', 0),
                    'profit_factor': stats.get('profit_factor', 0),
                    'sharpe_ratio': stats.get('sharpe_ratio', 0),
                    'total_trades': stats.get('total_trades', 0),
                    'run_id': run.id
                }
            else:
                # Keep the best performing run
                if return_val > strategy_performance[key]['total_return']:
                    strategy_performance[key].update({
                        'total_return': return_val,
                        'win_rate': stats.get('win_rate', 0),
                        'profit_factor': stats.get('profit_factor', 0),
                        'sharpe_ratio': stats.get('sharpe_ratio', 0),
                        'total_trades': stats.get('total_trades', 0),
                        'run_id': run.id
                    })
    
    # Sort by total return
    sorted_strategies = sorted(
        strategy_performance.values(),
        key=lambda x: x['total_return'],
        reverse=True
    )
    
    return sorted_strategies[:limit]



@backtest_bp.route('/my-results')
@login_required
def my_results():
    """User's saved backtest results"""
    return render_template('user/backtest/my_results.html')


@backtest_bp.route('/api/my-results', methods=['GET'])
@login_required
def get_my_results():
    """Get user's saved backtest results"""
    try:
        runs = BacktestRun.query.filter_by(user_id=current_user.id).order_by(
            BacktestRun.created_at.desc()
        ).limit(50).all()
        
        results_list = []
        for run in runs:
            results = run.get_results()
            stats = results.get('stats', {})
            
            results_list.append({
                'id': run.id,
                'strategy_name': results.get('strategy_name', 'Unnamed'),
                'symbol': run.symbol,
                'timeframe': run.timeframe,
                'status': run.status,
                'created_at': run.created_at.isoformat(),
                'total_return': stats.get('total_return', 0),
                'win_rate': stats.get('win_rate', 0),
                'total_trades': stats.get('total_trades', 0),
                'profit_factor': stats.get('profit_factor', 0),
                'max_drawdown': stats.get('max_drawdown', 0)
            })
        
        return jsonify({'success': True, 'results': results_list})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



@backtest_bp.route('/api/delete-result/<int:result_id>', methods=['DELETE'])
@login_required
def delete_result(result_id):
    """Delete a user's backtest result"""
    try:
        run = BacktestRun.query.get(result_id)
        
        if not run:
            return jsonify({'success': False, 'message': 'Result not found'}), 404
        
        # Check ownership
        if run.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        db.session.delete(run)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Result deleted'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



@backtest_bp.route('/api/bulk-delete-results', methods=['POST'])
@login_required
def bulk_delete_results():
    """Bulk delete user's backtest results"""
    try:
        data = request.get_json()
        result_ids = data.get('result_ids', [])
        
        if not result_ids:
            return jsonify({'success': False, 'message': 'No results selected'}), 400
        
        deleted_count = 0
        for result_id in result_ids:
            run = BacktestRun.query.get(result_id)
            if run and run.user_id == current_user.id:
                db.session.delete(run)
                deleted_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} result(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500





@backtest_bp.route('/api/view-strategy-config/<int:run_id>', methods=['GET'])
@login_required
def view_strategy_config(run_id):
    """Get strategy config for a backtest run - owner or admin only"""
    try:
        run = BacktestRun.query.get(run_id)
        if not run:
            return jsonify({'success': False, 'message': 'Result not found'}), 404
        
        # Check if user owns this OR is admin
        if run.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        config = json.loads(run.config_json) if run.config_json else {}
        results = run.get_results()
        stats = results.get('stats', {})
        
        user = User.query.get(run.user_id)
        
        return jsonify({
            'success': True,
            'config': config,
            'stats': stats,
            'symbol': run.symbol,
            'timeframe': run.timeframe,
            'strategy_name': config.get('name', 'Unnamed'),
            'created_at': run.created_at.isoformat(),
            'username': user.username if user else 'Unknown',
            'is_admin_view': current_user.is_admin and run.user_id != current_user.id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

