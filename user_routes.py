from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response
from flask_login import login_required, current_user
from extensions import db
from models import Trade, ImportHistory, DayNote, DiaryEntry, DiaryImage, TradingAccount, User, FAQ, SupportTicket, TicketReply
from datetime import datetime, date, timedelta
import csv
import io
import os
from werkzeug.utils import secure_filename
from sqlalchemy import func

user_bp = Blueprint('user', __name__, url_prefix='/user')

# ═══════════════════════════════════════════════════════════
# 📸 UNIVERSAL IMAGE COMPRESSION
# ═══════════════════════════════════════════════════════════

def compress_image(file, upload_dir, filename_prefix, max_size=1920, quality=80):
    """Compress and save any uploaded image. Returns (filename, filepath)."""
    from PIL import Image
    
    original_filename = secure_filename(file.filename)
    filename = f"{filename_prefix}_{original_filename}"
    
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    
    img = Image.open(file)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    if img.width > max_size or img.height > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    img.save(filepath, quality=quality, optimize=True)
    
    return filename, filepath.replace('\\', '/')

# ─── Helper: Get Active Account ───
def get_active_account_id():
    """Get current active account ID, returns None if no account"""
    account = current_user.get_active_account()
    return account.id if account else None

# ─── Account Management Routes ───
@user_bp.route('/switch-account/<int:account_id>', methods=['POST'])
@login_required
def switch_account(account_id):
    """Switch to a different trading account"""
    if current_user.switch_account(account_id):
        account = current_user.get_active_account()
        flash(f'Switched to: {account.name}', 'success')
    else:
        flash('Invalid account.', 'danger')
    return redirect(request.referrer or url_for('user.dashboard'))

@user_bp.route('/api/accounts')
@login_required
def api_get_accounts():
    """Get all accounts for the current user"""
    accounts = TradingAccount.query.filter_by(
        user_id=current_user.id, 
        is_active=True
    ).order_by(TradingAccount.created_at).all()
    
    active_id = current_user.current_account_id
    
    return jsonify([{
        'id': acc.id,
        'name': acc.name,
        'broker': acc.broker or '',
        'account_type': acc.account_type,
        'currency': acc.currency,
        'is_current': acc.id == active_id,
        'trade_count': acc.trade_count,
        'total_pnl': acc.total_pnl,
        'stats': acc.get_stats()
    } for acc in accounts])

@user_bp.route('/api/accounts/create', methods=['POST'])
@login_required
def api_create_account():
    """Create a new trading account"""
    if not current_user.can_create_account():
        max_acc = current_user.get_max_accounts()
        return jsonify({
            'success': False, 
            'message': f'Account limit reached. You can have max {max_acc} accounts on your {current_user.subscription_tier} plan.'
        })
    
    try:
        data = request.get_json()
        name = data.get('name', 'New Account').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Account name is required.'})
        
        account = TradingAccount(
            user_id=current_user.id,
            name=name,
            broker=data.get('broker', ''),
            account_type=data.get('account_type', 'live'),
            currency=data.get('currency', 'USD')
        )
        db.session.add(account)
        db.session.commit()
        
        # Auto-switch to new account
        current_user.switch_account(account.id)
        
        return jsonify({
            'success': True, 
            'message': f'Account "{name}" created!',
            'account_id': account.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@user_bp.route('/api/accounts/<int:account_id>/update', methods=['POST'])
@login_required
def api_update_account(account_id):
    """Update account details"""
    account = TradingAccount.query.filter_by(
        id=account_id, 
        user_id=current_user.id
    ).first_or_404()
    
    try:
        data = request.get_json()
        account.name = data.get('name', account.name).strip()
        account.broker = data.get('broker', account.broker)
        account.account_type = data.get('account_type', account.account_type)
        account.currency = data.get('currency', account.currency)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Account updated!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@user_bp.route('/api/accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def api_delete_account(account_id):
    """Delete an account and all its data"""
    account = TradingAccount.query.filter_by(
        id=account_id, 
        user_id=current_user.id
    ).first_or_404()
    
    # Don't allow deleting the last account
    account_count = current_user.get_account_count()
    if account_count <= 1:
        return jsonify({'success': False, 'message': 'Cannot delete your only account.'})
    
    try:
        # Delete all associated data
        Trade.query.filter_by(account_id=account.id).delete()
        DayNote.query.filter_by(account_id=account.id).delete()
        DiaryEntry.query.filter_by(account_id=account.id).delete()
        ImportHistory.query.filter_by(account_id=account.id).delete()
        
        # If this is the active account, switch to another
        if current_user.current_account_id == account.id:
            other_account = TradingAccount.query.filter(
                TradingAccount.user_id == current_user.id,
                TradingAccount.id != account.id,
                TradingAccount.is_active == True
            ).first()
            if other_account:
                current_user.current_account_id = other_account.id
        
        # Soft delete (set inactive)
        account.is_active = False
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Account deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# ─── Dashboard ───
@user_bp.route('/dashboard')
@login_required
def dashboard():
    from sqlalchemy import func
    from datetime import datetime, date, timedelta
    
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    # Get all trades with profit/loss
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).filter(
        Trade.profit_loss.isnot(None)
    ).order_by(Trade.entry_date.desc()).all()
    
    # Get diary entries
    diary_entries = DiaryEntry.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(DiaryEntry.entry_date.desc()).all()
    
    # Get day notes
    day_notes = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(DayNote.created_at.desc()).all()
    
    # ── Calculate All Analytics ──
    total_trades = len(trades)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win and t.profit_loss is not None]
    total_wins = len(wins)
    total_losses = len(losses)
    
    win_rate = round((total_wins / total_trades) * 100, 1) if total_trades > 0 else 0
    net_pnl = round(sum(t.profit_loss for t in trades), 2)
    gross_profit = round(sum(t.profit_loss for t in wins), 2)
    gross_loss = round(abs(sum(t.profit_loss for t in losses)), 2)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999999 if gross_profit > 0 else 0)
    avg_pnl_per_trade = round(net_pnl / total_trades, 2) if total_trades > 0 else 0
    
    # Streaks (properly calculated from sorted trades)
    sorted_trades = sorted(trades, key=lambda x: x.entry_date)
    max_win_streak = 0
    current_win_streak = 0
    max_loss_streak = 0
    current_loss_streak = 0
    
    for t in sorted_trades:
        if t.is_win:
            current_win_streak += 1
            current_loss_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_loss_streak += 1
            current_win_streak = 0
            max_loss_streak = max(max_loss_streak, current_loss_streak)
    
    # Trading days calculation
    if trades:
        all_dates = [t.entry_date.date() for t in trades]
        unique_dates = list(set(all_dates))
        trading_days = len(unique_dates)
        first_date = min(all_dates)
        last_date = max(all_dates)
        total_days = (last_date - first_date).days + 1
        avg_trades_per_day = round(total_trades / trading_days, 1) if trading_days > 0 else 0
    else:
        trading_days = 0
        total_days = 0
        avg_trades_per_day = 0
    
    # Best/worst trade
    best_trade = max(trades, key=lambda t: t.profit_loss) if trades else None
    worst_trade = min(trades, key=lambda t: t.profit_loss) if trades else None
    
    # Max drawdown (properly calculated)
    peak = 0
    running_pnl = 0
    max_drawdown = 0
    for t in sorted_trades:
        running_pnl += (t.profit_loss or 0)
        if running_pnl > peak:
            peak = running_pnl
        drawdown = peak - running_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    max_drawdown = round(max_drawdown, 2)
    
    # Symbol analysis
    symbol_stats = {}
    for t in trades:
        sym = t.symbol or 'Unknown'
        if sym not in symbol_stats:
            symbol_stats[sym] = {'count': 0, 'wins': 0, 'pnl': 0}
        symbol_stats[sym]['count'] += 1
        symbol_stats[sym]['pnl'] += (t.profit_loss or 0)
        if t.is_win:
            symbol_stats[sym]['wins'] += 1
    
    top_symbols = sorted([
        {
            'symbol': sym,
            'trade_count': stats['count'],
            'pnl': round(stats['pnl'], 2),
            'win_rate': round((stats['wins'] / stats['count']) * 100, 1) if stats['count'] > 0 else 0
        }
        for sym, stats in symbol_stats.items()
    ], key=lambda x: x['pnl'], reverse=True)
    
    best_symbol = top_symbols[0]['symbol'] if top_symbols else '-'
    most_traded_symbol = max(symbol_stats, key=lambda x: symbol_stats[x]['count']) if symbol_stats else '-'
    
    # Avg R:R
    trades_with_rr = [t for t in trades if t.risk_reward_ratio and t.risk_reward_ratio > 0]
    avg_rr = round(sum(t.risk_reward_ratio for t in trades_with_rr) / len(trades_with_rr), 1) if trades_with_rr else 0
    
    # Recent trades (last 5)
    recent_trades = trades[:5] if trades else []
    
    # Daily activity (last 30 days)
    today = date.today()
    daily_activity = []
    for i in range(30):
        day = today - timedelta(days=29 - i)
        day_trades = [t for t in trades if t.entry_date.date() == day]
        daily_activity.append({
            'date': day.strftime('%Y-%m-%d'),
            'has_trades': len(day_trades) > 0,
            'trade_count': len(day_trades),
            'pnl': round(sum(t.profit_loss or 0 for t in day_trades), 2)
        })
    
    # Session breakdown
    session_stats = {}
    for t in trades:
        session = t.session or 'Unknown'
        if session not in session_stats:
            session_stats[session] = {'count': 0, 'pnl': 0}
        session_stats[session]['count'] += 1
        session_stats[session]['pnl'] += (t.profit_loss or 0)
    
    session_breakdown = [
        {'name': name, 'trade_count': stats['count'], 'pnl': round(stats['pnl'], 2)}
        for name, stats in sorted(session_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    ]
    
    # Monthly breakdown
    monthly_stats = {}
    for t in trades:
        month_key = t.entry_date.strftime('%B %Y')
        if month_key not in monthly_stats:
            monthly_stats[month_key] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
        monthly_stats[month_key]['trades'] += 1
        monthly_stats[month_key]['pnl'] += (t.profit_loss or 0)
        if t.is_win:
            monthly_stats[month_key]['wins'] += 1
        else:
            monthly_stats[month_key]['losses'] += 1
    
    monthly_breakdown = [
        {
            'month': month,
            'trade_count': stats['trades'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': round((stats['wins'] / stats['trades']) * 100, 1) if stats['trades'] > 0 else 0,
            'pnl': round(stats['pnl'], 2)
        }
        for month, stats in sorted(monthly_stats.items(), reverse=True)
    ]
    
    # Get active account
    account = current_user.get_active_account()
    
    # Package all analytics
    analytics = {
        'total_trades': total_trades,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'win_rate': win_rate,
        'net_pnl': net_pnl,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'profit_factor': profit_factor,
        'avg_pnl_per_trade': avg_pnl_per_trade,
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'trading_days': trading_days,
        'total_days': total_days,
        'avg_trades_per_day': avg_trades_per_day,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'best_symbol': best_symbol,
        'most_traded_symbol': most_traded_symbol,
        'avg_rr': avg_rr,
        'max_drawdown': max_drawdown,
        'recent_trades': recent_trades,
        'daily_activity': daily_activity,
        'top_symbols': top_symbols,
        'session_breakdown': session_breakdown,
        'monthly_breakdown': monthly_breakdown,
        'diary_count': len(diary_entries),
        'notes_count': len(day_notes),
        'has_diary': len(diary_entries) > 0,
        'has_notes': len(day_notes) > 0,
        'last_diary': diary_entries[0].entry_date if diary_entries else None,
        'last_note': day_notes[0].note_date if day_notes else None
    }
    
    return render_template('user/dashboard.html', analytics=analytics, account=account)

# ─── Journal ───
@user_bp.route('/journal')
@login_required
def journal():
    account_id = get_active_account_id()
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).order_by(Trade.entry_date.desc()).all()
    return render_template('user/journal.html', trades=trades)

# ─── Import Selection Page ───
@user_bp.route('/import')
@login_required
def import_trades():
    csv_label, csv_enabled = current_user.csv_upload_label()
    return render_template('user/import.html', csv_label=csv_label, csv_enabled=csv_enabled)

# ─── Manual Add Trade ───
@user_bp.route('/trade/add', methods=['GET', 'POST'])
@login_required
def add_trade():
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    if request.method == 'POST':
        trade = Trade(
            user_id=current_user.id,
            account_id=account_id,
            symbol=request.form.get('symbol','').upper(),
            trade_type=request.form.get('trade_type','buy'),
            entry_price=float(request.form.get('entry_price',0)),
            exit_price=float(request.form.get('exit_price')) if request.form.get('exit_price') else None,
            stop_loss=float(request.form.get('stop_loss')) if request.form.get('stop_loss') else None,
            take_profit=float(request.form.get('take_profit')) if request.form.get('take_profit') else None,
            lot_size=float(request.form.get('lot_size',1.0)),
            entry_date=datetime.strptime(request.form.get('entry_date',datetime.utcnow().strftime('%Y-%m-%dT%H:%M')),'%Y-%m-%dT%H:%M'),
            exit_date=datetime.strptime(request.form.get('exit_date'),'%Y-%m-%dT%H:%M') if request.form.get('exit_date') else None,
            session=request.form.get('session',''),
            notes=request.form.get('notes',''),
            tags=request.form.get('tags',''),
            import_source='manual'
        )
        trade.calculate_pnl()
        db.session.add(trade)
        db.session.add(ImportHistory(
            user_id=current_user.id, 
            account_id=account_id,
            import_type='manual', 
            trades_imported=1
        ))
        db.session.commit()
        flash('Trade added!', 'success')
        return redirect(url_for('user.journal'))
    return render_template('user/add_trade.html')

# ─── Trade Detail ───
@user_bp.route('/trade/<int:trade_id>')
@login_required
def trade_detail(trade_id):
    account_id = get_active_account_id()
    trade = Trade.query.filter_by(
        id=trade_id, 
        user_id=current_user.id,
        account_id=account_id
    ).first_or_404()
    return render_template('user/trade_detail.html', trade=trade)

# ─── Edit Trade ───
@user_bp.route('/trade/<int:trade_id>/edit', methods=['GET','POST'])
@login_required
def edit_trade(trade_id):
    account_id = get_active_account_id()
    trade = Trade.query.filter_by(
        id=trade_id, 
        user_id=current_user.id,
        account_id=account_id
    ).first_or_404()
    
    if request.method == 'POST':
        trade.symbol = request.form.get('symbol',trade.symbol).upper()
        trade.trade_type = request.form.get('trade_type',trade.trade_type)
        trade.entry_price = float(request.form.get('entry_price',trade.entry_price))
        trade.exit_price = float(request.form.get('exit_price')) if request.form.get('exit_price') else None
        trade.stop_loss = float(request.form.get('stop_loss')) if request.form.get('stop_loss') else None
        trade.take_profit = float(request.form.get('take_profit')) if request.form.get('take_profit') else None
        trade.lot_size = float(request.form.get('lot_size',trade.lot_size))
        trade.session = request.form.get('session',trade.session)
        trade.notes = request.form.get('notes',trade.notes)
        trade.tags = request.form.get('tags',trade.tags)
        if request.form.get('entry_date'):
            trade.entry_date = datetime.strptime(request.form.get('entry_date'),'%Y-%m-%dT%H:%M')
        if request.form.get('exit_date'):
            trade.exit_date = datetime.strptime(request.form.get('exit_date'),'%Y-%m-%dT%H:%M')
        trade.calculate_pnl()
        db.session.commit()
        flash('Trade updated!','success')
        return redirect(url_for('user.trade_detail', trade_id=trade.id))
    return render_template('user/edit_trade.html', trade=trade)

# ─── Delete Trade ───
@user_bp.route('/trade/<int:trade_id>/delete', methods=['POST'])
@login_required
def delete_trade(trade_id):
    account_id = get_active_account_id()
    trade = Trade.query.filter_by(
        id=trade_id, 
        user_id=current_user.id,
        account_id=account_id
    ).first_or_404()
    db.session.delete(trade)
    db.session.commit()
    flash('Trade deleted.','info')
    return redirect(url_for('user.journal'))

# ─── CSV Import ───
@user_bp.route('/import/csv', methods=['GET','POST'])
@login_required
def import_csv():
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    can_upload, days_left = current_user.can_upload_csv()
    if not can_upload:
        flash(f'CSV upload available in {days_left} day{"s" if days_left>1 else ""}.','warning')
        return redirect(url_for('user.import_trades'))
    
    if request.method == 'POST':
        file = request.files.get('csv_file')
        if not file or file.filename == '':
            flash('No file selected.','danger')
            return render_template('user/import_csv.html')
        if not file.filename.endswith('.csv'):
            flash('Please upload a CSV file.','danger')
            return render_template('user/import_csv.html')
        
        try:
            # Read raw content
            raw_content = file.read()
            print(f"\n=== CSV IMPORT DEBUG ===")
            print(f"File size: {len(raw_content)} bytes")
            
            # Try different encodings
            content = None
            for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
                try:
                    content = raw_content.decode(encoding)
                    print(f"Decoded with {encoding}")
                    break
                except:
                    continue
            
            if content is None:
                flash('Cannot read file encoding.', 'danger')
                return render_template('user/import_csv.html')
            
            # Print first 500 chars
            print(f"First 500 chars:\n{content[:500]}")
            
            stream = io.StringIO(content, newline=None)
            csv_reader = csv.DictReader(stream)
            
            print(f"Detected columns: {csv_reader.fieldnames}")
            
            trades_added, errors = 0, []
            
            for row_num, row in enumerate(csv_reader, start=2):
                print(f"\nRow {row_num}: {row}")
                
                try:
                    # Symbol - try ALL possible column names
                    symbol = None
                    for key in row.keys():
                        if key and 'symbol' in key.lower():
                            symbol = row[key]
                            break
                    if not symbol:
                        symbol = row.get(list(row.keys())[0]) if row.keys() else None
                    
                    if not symbol or str(symbol).strip() == '':
                        errors.append(f'Row {row_num}: Missing symbol')
                        print(f"  -> SKIP: No symbol found")
                        continue
                    
                    symbol = str(symbol).strip().upper()
                    print(f"  Symbol: {symbol}")
                    
                    # Type
                    type_raw = None
                    for key in row.keys():
                        if key and ('type' in key.lower() or 'side' in key.lower() or 'direction' in key.lower()):
                            type_raw = row[key]
                            break
                    if not type_raw:
                        type_raw = 'buy'
                    type_raw = str(type_raw).strip().lower()
                    trade_type = 'sell' if type_raw in ['sell','short','s','-1'] else 'buy'
                    print(f"  Type: {trade_type}")
                    
                    # Entry Price
                    entry_price = None
                    for key in row.keys():
                        if key and ('entry' in key.lower() or 'open' in key.lower() or 'price' in key.lower()):
                            try:
                                entry_price = float(str(row[key]).strip().replace(',',''))
                                break
                            except:
                                continue
                    
                    if entry_price is None:
                        # Try second column as entry
                        cols = list(row.values())
                        if len(cols) >= 3:
                            try:
                                entry_price = float(str(cols[2]).strip().replace(',',''))
                            except:
                                pass
                    
                    if entry_price is None:
                        errors.append(f'Row {row_num}: Missing entry price')
                        print(f"  -> SKIP: No entry price")
                        continue
                    print(f"  Entry: {entry_price}")
                    
                    # Exit Price
                    exit_price = None
                    for key in row.keys():
                        if key and ('exit' in key.lower() or 'close' in key.lower()):
                            try:
                                exit_price = float(str(row[key]).strip().replace(',',''))
                                break
                            except:
                                continue
                    print(f"  Exit: {exit_price}")
                    
                    # SL
                    stop_loss = None
                    for key in row.keys():
                        if key and ('sl' in key.lower() or 'stop' in key.lower()):
                            try:
                                stop_loss = float(str(row[key]).strip().replace(',',''))
                                break
                            except:
                                continue
                    
                    # TP
                    take_profit = None
                    for key in row.keys():
                        if key and ('tp' in key.lower() or 'take' in key.lower() or 'limit' in key.lower() or 'target' in key.lower()):
                            try:
                                take_profit = float(str(row[key]).strip().replace(',',''))
                                break
                            except:
                                continue
                    
                    # Lot size
                    lot_size = 1.0
                    for key in row.keys():
                        if key and ('lot' in key.lower() or 'volume' in key.lower() or 'size' in key.lower()):
                            try:
                                lot_size = float(str(row[key]).strip().replace(',',''))
                                break
                            except:
                                continue
                    
                    # Date
                    entry_date = datetime.utcnow()
                    for key in row.keys():
                        if key and ('date' in key.lower() or 'time' in key.lower()):
                            val = str(row[key]).strip()
                            for fmt in ['%Y-%m-%d','%Y/%m/%d','%d/%m/%Y','%Y.%m.%d','%Y-%m-%d %H:%M:%S','%Y.%m.%d %H:%M:%S']:
                                try:
                                    entry_date = datetime.strptime(val, fmt)
                                    break
                                except:
                                    continue
                            break
                    
                    # Notes
                    notes = ''
                    for key in row.keys():
                        if key and ('note' in key.lower() or 'comment' in key.lower()):
                            notes = str(row[key]).strip()
                            break
                    
                    trade = Trade(
                        user_id=current_user.id,
                        account_id=account_id,
                        symbol=symbol, trade_type=trade_type,
                        entry_price=entry_price, exit_price=exit_price, stop_loss=stop_loss,
                        take_profit=take_profit, lot_size=lot_size, entry_date=entry_date,
                        notes=notes, import_source='csv'
                    )
                    trade.calculate_pnl()
                    db.session.add(trade)
                    trades_added += 1
                    print(f"  -> ADDED: {symbol} {trade_type} @ {entry_price}")
                    
                except Exception as e:
                    errors.append(f'Row {row_num}: {str(e)}')
                    print(f"  -> ERROR: {str(e)}")
            
            current_user.last_csv_import = datetime.utcnow()
            db.session.add(ImportHistory(
                user_id=current_user.id,
                account_id=account_id,
                import_type='csv', 
                file_name=file.filename, 
                trades_imported=trades_added
            ))
            db.session.commit()
            
            print(f"\n=== RESULT: {trades_added} added, {len(errors)} errors ===")
            if errors:
                for e in errors:
                    print(f"  {e}")
            
            if errors and trades_added == 0:
                flash(f'Import failed. {len(errors)} rows had issues. Check console for details.','danger')
            elif errors:
                flash(f'{trades_added} trades imported. {len(errors)} rows skipped.','warning')
            elif trades_added > 0:
                flash(f'{trades_added} trades imported successfully!','success')
            else:
                flash('No trades imported.','danger')
            
            return redirect(url_for('user.journal'))
            
        except Exception as e:
            db.session.rollback()
            print(f"FATAL CSV ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            flash(f'Error: {str(e)}','danger')
    
    return render_template('user/import_csv.html')

# ─── Analytics ───
@user_bp.route('/analytics')
@login_required
def analytics():
    from sqlalchemy import func, extract
    
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).filter(
        Trade.profit_loss.isnot(None)
    ).order_by(Trade.entry_date.asc()).all()

    # ── Stats ──────────────────────────────────────────────
    total_trades = len(trades)
    wins   = [t for t in trades if t.is_win]
    losses = [t for t in trades if t.is_win is False]
    total_wins   = len(wins)
    total_losses = len(losses)

    win_rate     = round((total_wins / (total_wins + total_losses)) * 100, 1) if (total_wins + total_losses) > 0 else 0
    net_profit   = round(sum(t.profit_loss for t in trades), 2)
    gross_profit = round(sum(t.profit_loss for t in wins), 2)
    gross_loss   = round(abs(sum(t.profit_loss for t in losses)), 2)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999 if gross_profit > 0 else 0)

    trades_with_rr = [t for t in trades if t.risk_reward_ratio and t.risk_reward_ratio > 0]
    avg_rr = round(sum(t.risk_reward_ratio for t in trades_with_rr) / len(trades_with_rr), 2) if trades_with_rr else 0

    # ── Equity Curve ───────────────────────────────────────
    equity = 0.0
    equity_labels = []
    equity_data   = []
    for i, t in enumerate(trades, 1):
        equity += t.profit_loss
        equity_labels.append(f"#{i}")
        equity_data.append(round(equity, 2))

    # ── Drawdown Curve ─────────────────────────────────────
    peak = 0.0
    drawdown_data = []
    for val in equity_data:
        if val > peak:
            peak = val
        dd = round(val - peak, 2) if peak > 0 else 0.0
        drawdown_data.append(dd)

    max_drawdown = round(min(drawdown_data), 2) if drawdown_data else 0

    # ── Profit by Symbol ───────────────────────────────────
    symbol_pnl = {}
    for t in trades:
        symbol_pnl[t.symbol] = round(symbol_pnl.get(t.symbol, 0) + t.profit_loss, 2)
    symbol_pnl = dict(sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True))
    best_symbol  = max(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "-"
    worst_symbol = min(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "-"

    # ── Monthly Profit ─────────────────────────────────────
    monthly = {}
    for t in trades:
        key = t.entry_date.strftime('%b %Y')
        monthly[key] = round(monthly.get(key, 0) + t.profit_loss, 2)

    # ── Session Breakdown ──────────────────────────────────
    session_pnl = {}
    for t in trades:
        s = t.session or 'unknown'
        session_pnl[s] = round(session_pnl.get(s, 0) + t.profit_loss, 2)

    # ── Day of Week ────────────────────────────────────────
    days_order = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
    day_pnl = {d: 0.0 for d in days_order}
    for t in trades:
        day_name = t.entry_date.strftime('%A')
        if day_name in day_pnl:
            day_pnl[day_name] = round(day_pnl[day_name] + t.profit_loss, 2)

    return render_template('user/analytics.html',
        # stats
        total_trades=total_trades,
        total_wins=total_wins,
        total_losses=total_losses,
        win_rate=win_rate,
        net_profit=net_profit,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        avg_rr=avg_rr,
        best_symbol=best_symbol,
        worst_symbol=worst_symbol,
        max_drawdown=max_drawdown,
        # chart data
        equity_labels=equity_labels,
        equity_data=equity_data,
        drawdown_data=drawdown_data,
        symbol_labels=list(symbol_pnl.keys()),
        symbol_data=list(symbol_pnl.values()),
        monthly_labels=list(monthly.keys()),
        monthly_data=list(monthly.values()),
        session_labels=list(session_pnl.keys()),
        session_data=list(session_pnl.values()),
        day_labels=days_order,
        day_data=list(day_pnl.values()),
    )

# ─── Calendar ───
@user_bp.route('/calendar')
@login_required
def calendar():
    return render_template('user/calendar.html')

@user_bp.route('/api/trade-dates')
@login_required
def api_trade_dates():
    from sqlalchemy import func
    account_id = get_active_account_id()
    
    trades = db.session.query(
        func.date(Trade.entry_date).label('trade_date'),
        func.sum(Trade.profit_loss).label('total_pnl'),
        func.count(Trade.id).label('trade_count')
    ).filter(
        Trade.user_id==current_user.id,
        Trade.account_id==account_id,
        Trade.profit_loss.isnot(None)
    ).group_by(func.date(Trade.entry_date)).all()
    
    result = {}
    for row in trades:
        pnl = round(row.total_pnl, 2) if row.total_pnl else 0
        result[str(row.trade_date)] = {'pnl': pnl, 'count': row.trade_count}
    return jsonify(result)

@user_bp.route('/calendar/day/<date_str>', methods=['GET'])
@login_required
def calendar_day(date_str):
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return "Invalid date", 400
    
    account_id = get_active_account_id()
    
    # GET - render the day detail
    day_trades = Trade.query.filter(
        Trade.user_id==current_user.id,
        Trade.account_id==account_id,
        db.func.date(Trade.entry_date)==target_date
    ).order_by(Trade.entry_date.desc()).all()
    
    day_note = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id,
        note_date=target_date
    ).first()
    
    total_trades = len(day_trades)
    wins = [t for t in day_trades if t.is_win]
    losses = [t for t in day_trades if t.is_win is False and t.profit_loss is not None]
    day_pnl = round(sum(t.profit_loss for t in day_trades if t.profit_loss is not None), 2)
    win_count = len(wins)
    loss_count = len(losses)
    day_win_rate = round((win_count/(win_count+loss_count))*100,1) if (win_count+loss_count)>0 else 0
    best_trade = max(day_trades, key=lambda t: t.profit_loss or -999999, default=None)
    worst_trade = min(day_trades, key=lambda t: t.profit_loss or 999999, default=None)
    
    return render_template('user/calendar_day.html',
        date_str=date_str, target_date=target_date, trades=day_trades,
        total_trades=total_trades, day_pnl=day_pnl, win_count=win_count,
        loss_count=loss_count, day_win_rate=day_win_rate,
        best_trade=best_trade, worst_trade=worst_trade, day_note=day_note)

# ─── AI Report ───
@user_bp.route('/ai-report')
@login_required
def ai_report():
    return render_template('user/ai_report.html')

# ═══════════════════════════════════════════════════════════
# 🤖 PER-PAGE AI ANALYSIS (NEW)
# ═══════════════════════════════════════════════════════════

@user_bp.route('/analyse/<page_key>')
@login_required
def analyse_page(page_key):
    """View or trigger per-page AI analysis"""
    from ai_service import analyse_page as run_analysis
    from models import AIPageAnalysis
    
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    # Validate page_key
    valid_pages = ['journal', 'analytics', 'calendar_day', 'insights', 'diary', 'goals', 'dashboard']
    if page_key not in valid_pages:
        flash('Invalid analysis page.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    # Optional sub-params
    sub_id = request.args.get('sub_id', None)  # calendar date, goal_id
    force = request.args.get('force') == '1'   # Re-analyse even if cached
    
    # Check if cached result exists (unless forcing re-analysis)
    if not force:
        cached = AIPageAnalysis.query.filter_by(
            user_id=current_user.id,
            account_id=account_id,
            page_key=page_key,
            sub_id=sub_id
        ).order_by(AIPageAnalysis.created_at.desc()).first()
        
        if cached:
            return render_template('user/analyse_result.html',
                analysis=cached,
                page_key=page_key,
                page_title=cached.page_title,
                from_cache=True
            )
    
    # Run analysis
    result = run_analysis(current_user, page_key, account_id, 
                         extra_params={'sub_id': sub_id} if sub_id else None)
    
    if not result.get('success'):
        flash(result.get('message', 'Analysis failed. Please try again.'), 'danger')
        return redirect(request.referrer or url_for('user.dashboard'))
    
    # Get the saved analysis for display
    analysis = AIPageAnalysis.query.get(result['analysis_id'])
    
    if not analysis:
        flash('Analysis saved but could not be retrieved.', 'warning')
        return redirect(request.referrer or url_for('user.dashboard'))
    
    return render_template('user/analyse_result.html',
        analysis=analysis,
        page_key=page_key,
        page_title=analysis.page_title,
        from_cache=False
    )

# ─── Settings ───
@user_bp.route('/settings')
@login_required
def settings():
    accounts = TradingAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).order_by(TradingAccount.created_at).all()
    
    max_accounts = current_user.get_max_accounts()
    current_count = len(accounts)
    
    return render_template('user/settings.html',
        accounts=accounts,
        max_accounts=max_accounts,
        current_count=current_count,
        can_create=current_user.can_create_account(),
        email_verified=current_user.email_verified,
        user_email=current_user.email)

# ─── Subscription ───
@user_bp.route('/subscription')
@login_required
def subscription():
    from payment_routes import get_pricing_for_user
    
    max_accounts = current_user.get_max_accounts()
    current_count = current_user.get_account_count()
    
    pricing, country = get_pricing_for_user()
    
    return render_template('user/subscription.html',
        max_accounts=max_accounts,
        current_count=current_count,
        pricing=pricing,
        country=country,
        symbol=pricing['symbol'],
        currency=pricing['currency'],
        pro_monthly=pricing['pro']['monthly'],
        elite_monthly=pricing['elite']['monthly'],
        gateway_name=pricing['gateway_name']
    )

# ═══════════════════════════════════════════════════════════
# 💡 INSIGHTS
# ═══════════════════════════════════════════════════════════

@user_bp.route('/insights')
@login_required
def insights():
    from sqlalchemy import func
    
    account_id = get_active_account_id()
    if not account_id:
        flash('Please create an account first.', 'warning')
        return redirect(url_for('user.settings'))
    
    # Get all trades
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).filter(
        Trade.profit_loss.isnot(None)
    ).order_by(Trade.entry_date.asc()).all()
    
    # Get diary entries count
    diary_count = DiaryEntry.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).count()
    
    # Get notes count
    notes_count = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).count()
    
    # ── Basic Stats ──────────────────────────────
    total_trades = len(trades)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    total_wins = len(wins)
    total_losses = len(losses)
    win_rate = round((total_wins/total_trades)*100, 1) if total_trades > 0 else 0
    
    total_pnl = round(sum(t.profit_loss for t in trades), 2)
    gross_profit = round(sum(t.profit_loss for t in wins), 2)
    gross_loss = round(abs(sum(t.profit_loss for t in losses)), 2)
    profit_factor = round(gross_profit/gross_loss, 2) if gross_loss > 0 else 999
    
    # Best/worst trade
    best_trade = max(trades, key=lambda t: t.profit_loss) if trades else None
    worst_trade = min(trades, key=lambda t: t.profit_loss) if trades else None
    
    # Biggest win streak
    max_win_streak = 0
    current_win_streak = 0
    for t in trades:
        if t.is_win:
            current_win_streak += 1
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_win_streak = 0
    
    # Biggest loss streak
    max_loss_streak = 0
    current_loss_streak = 0
    for t in trades:
        if not t.is_win:
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0
    
    # Symbol analysis
    symbol_stats = {}
    for t in trades:
        sym = t.symbol
        if sym not in symbol_stats:
            symbol_stats[sym] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0, 'volume': 0}
        symbol_stats[sym]['count'] += 1
        symbol_stats[sym]['pnl'] += t.profit_loss
        if t.is_win:
            symbol_stats[sym]['wins'] += 1
        else:
            symbol_stats[sym]['losses'] += 1
    
    # Best symbol (most profit)
    best_symbol = max(symbol_stats, key=lambda x: symbol_stats[x]['pnl']) if symbol_stats else '-'
    
    # Most traded symbol
    most_traded = max(symbol_stats, key=lambda x: symbol_stats[x]['count']) if symbol_stats else '-'
    
    # Worst symbol (most losses)
    worst_symbol = max(symbol_stats, key=lambda x: symbol_stats[x]['losses']) if symbol_stats else '-'
    
    # Trading days and inactivity
    trade_dates = set()
    for t in trades:
        trade_dates.add(t.entry_date.date())
    
    if trades:
        first_trade_date = trades[0].entry_date.date()
        last_trade_date = trades[-1].entry_date.date()
        total_days = (last_trade_date - first_trade_date).days + 1
        trading_days = len(trade_dates)
        inactive_days = total_days - trading_days
    else:
        trading_days = 0
        inactive_days = 0
    
    # Average trades per day
    avg_trades_per_day = round(total_trades/trading_days, 1) if trading_days > 0 else 0
    
    # ── Weekly Data ──────────────────────────────
    weekly_data = {}
    for t in trades:
        week = t.entry_date.strftime('%Y-W%W')
        if week not in weekly_data:
            weekly_data[week] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
        weekly_data[week]['trades'] += 1
        weekly_data[week]['pnl'] += t.profit_loss
        if t.is_win:
            weekly_data[week]['wins'] += 1
        else:
            weekly_data[week]['losses'] += 1
    
    # ── Monthly Data ─────────────────────────────
    monthly_data = {}
    for t in trades:
        month = t.entry_date.strftime('%Y-%m')
        if month not in monthly_data:
            monthly_data[month] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
        monthly_data[month]['trades'] += 1
        monthly_data[month]['pnl'] += t.profit_loss
        if t.is_win:
            monthly_data[month]['wins'] += 1
        else:
            monthly_data[month]['losses'] += 1
    
    # ── Yearly Contribution Data ─────────────────
    today = date.today()
    year_start = date(today.year, 1, 1)
    
    # Get daily trade counts for the year
    daily_trades = db.session.query(
        func.date(Trade.entry_date).label('trade_date'),
        func.count(Trade.id).label('trade_count'),
        func.sum(Trade.profit_loss).label('total_pnl')
    ).filter(
        Trade.user_id==current_user.id,
        Trade.account_id==account_id,
        Trade.profit_loss.isnot(None),
        func.date(Trade.entry_date) >= year_start
    ).group_by(func.date(Trade.entry_date)).all()
    
    # Convert to dict
    daily_data = {}
    for row in daily_trades:
        daily_data[str(row.trade_date)] = {
            'count': row.trade_count,
            'pnl': round(row.total_pnl, 2) if row.total_pnl else 0
        }
    
    # Build contribution grid
    contribution_data = []
    current_date = year_start
    while current_date <= today:
        date_str = current_date.strftime('%Y-%m-%d')
        day_data = daily_data.get(date_str, {'count': 0, 'pnl': 0})
        contribution_data.append({
            'date': date_str,
            'day': current_date.strftime('%a'),
            'count': day_data['count'],
            'pnl': day_data['pnl']
        })
        current_date += timedelta(days=1)
    
    return render_template('user/insights.html',
        # Basic stats
        total_trades=total_trades,
        total_wins=total_wins,
        total_losses=total_losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        best_trade=best_trade,
        worst_trade=worst_trade,
        max_win_streak=max_win_streak,
        max_loss_streak=max_loss_streak,
        best_symbol=best_symbol,
        most_traded=most_traded,
        worst_symbol=worst_symbol,
        symbol_stats=symbol_stats,
        trading_days=trading_days,
        inactive_days=inactive_days,
        avg_trades_per_day=avg_trades_per_day,
        diary_count=diary_count,
        notes_count=notes_count,
        # Chart data
        weekly_data=weekly_data,
        monthly_data=monthly_data,
        contribution_data=contribution_data,
        today=today
    )

@user_bp.route('/insights/report/<period>')
@login_required
def generate_report(period):
    """Generate downloadable report"""
    from io import StringIO
    
    account_id = get_active_account_id()
    
    trades = Trade.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).filter(
        Trade.profit_loss.isnot(None)
    ).order_by(Trade.entry_date.desc()).all()
    
    now = datetime.utcnow()
    
    # Filter by period
    if period == 'week':
        start_date = now - timedelta(days=7)
        report_title = 'Weekly Trading Report'
    elif period == 'month':
        start_date = now - timedelta(days=30)
        report_title = 'Monthly Trading Report'
    elif period == 'year':
        start_date = now - timedelta(days=365)
        report_title = 'Yearly Trading Report'
    else:
        start_date = None
        report_title = 'All-Time Trading Report'
    
    if start_date:
        filtered_trades = [t for t in trades if t.entry_date >= start_date]
    else:
        filtered_trades = trades
    
    # Calculate stats
    total = len(filtered_trades)
    wins = [t for t in filtered_trades if t.is_win]
    losses = [t for t in filtered_trades if not t.is_win]
    total_pnl = sum(t.profit_loss for t in filtered_trades)
    win_rate = (len(wins)/total*100) if total > 0 else 0
    
    # Get account name
    account = current_user.get_active_account()
    account_name = account.name if account else 'Default'
    
    # Build report
    report = f"""
╔══════════════════════════════════════════════════════════╗
║           Tragene Journal - {report_title.upper()}           ║
╚══════════════════════════════════════════════════════════╝

📊 Generated: {now.strftime('%d %B %Y, %H:%M')}
👤 Trader: {current_user.username}
📧 Email: {current_user.email}
📁 Account: {account_name}

{'='*60}

📈 PERFORMANCE SUMMARY
{'='*60}
  Total Trades:      {total}
  Winning Trades:    {len(wins)}
  Losing Trades:     {len(losses)}
  Win Rate:          {win_rate:.1f}%
  Net P&L:           ${total_pnl:,.2f}
  Gross Profit:      ${sum(t.profit_loss for t in wins):,.2f}
  Gross Loss:        ${abs(sum(t.profit_loss for t in losses)):,.2f}
  Profit Factor:     {(sum(t.profit_loss for t in wins)/abs(sum(t.profit_loss for t in losses)) if losses else 999):.2f}

{'='*60}

🏆 TOP PERFORMERS
{'='*60}
"""
    
    # Symbol analysis
    symbols = {}
    for t in filtered_trades:
        s = t.symbol
        if s not in symbols:
            symbols[s] = {'count': 0, 'pnl': 0}
        symbols[s]['count'] += 1
        symbols[s]['pnl'] += t.profit_loss
    
    sorted_symbols = sorted(symbols.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for sym, stats in sorted_symbols[:5]:
        report += f"  {sym:<15} {stats['count']:>4} trades | P&L: ${stats['pnl']:,.2f}\n"
    
    report += f"""
{'='*60}

📋 RECENT TRADES
{'='*60}
"""
    for t in filtered_trades[:10]:
        report += f"  {t.entry_date.strftime('%d/%m/%y')} | {t.symbol:<10} | {'WIN' if t.is_win else 'LOSS'} | ${t.profit_loss:,.2f}\n"
    
    report += f"""
{'='*60}

💡 Generated by Tragene Journal
"""
    
    # Create downloadable file
    output = StringIO()
    output.write(report)
    output.seek(0)
    
    return Response(
        output.getvalue(),
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment;filename=trade_report_{period}_{now.strftime("%Y%m%d")}.txt'}
    )

# ═══════════════════════════════════════════════════════════
# 📖 TRADING DIARY ROUTES
# ═══════════════════════════════════════════════════════════

@user_bp.route('/diary')
@login_required
def diary():
    return render_template('user/diary.html', today=date.today().isoformat())

@user_bp.route('/diary/save', methods=['POST'])
@login_required
def save_diary():
    try:
        entry_date = request.form.get('date')
        title = request.form.get('title', '')
        content = request.form.get('content', '')
        mood = request.form.get('mood', '')
        
        account_id = get_active_account_id()
        
        # Create entry
        entry = DiaryEntry(
            user_id=current_user.id,
            account_id=account_id,
            entry_date=datetime.strptime(entry_date, '%Y-%m-%d').date(),
            title=title,
            content=content,
            mood=mood
        )
        db.session.add(entry)
        db.session.flush()  # Get entry ID
        
        # Handle image uploads
        if 'images' in request.files:
            files = request.files.getlist('images')
            for i, file in enumerate(files[:3]):  # Max 3 images
                if file and file.filename and file.filename != '':
                    # Compress and save
                    upload_dir_full = os.path.join('static', 'uploads', 'diary', str(current_user.id))
                    filename, filepath = compress_image(file, upload_dir_full, f"diary_{entry.id}_{i}")
                    
                    # Save to database
                    image = DiaryImage(
                        entry_id=entry.id,
                        filename=filename,
                        filepath=filepath.replace('\\', '/')
                    )
                    db.session.add(image)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Diary entry saved successfully! 📖'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error saving diary entry: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@user_bp.route('/diary/entries')
@login_required
def get_diary_entries():
    try:
        account_id = get_active_account_id()
        
        entries = DiaryEntry.query.filter_by(
            user_id=current_user.id,
            account_id=account_id
        ).order_by(DiaryEntry.entry_date.desc()).all()
        
        result = []
        for e in entries:
            result.append({
                'id': e.id,
                'date': e.entry_date.strftime('%B %d, %Y'),
                'title': e.title or 'Untitled Entry',
                'content': e.content[:200] if e.content else '',
                'mood': e.mood or '',
                'image_count': len(e.images)
            })
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error loading diary entries: {str(e)}")
        return jsonify([])

@user_bp.route('/diary/entry/<int:entry_id>')
@login_required
def get_diary_entry(entry_id):
    try:
        account_id = get_active_account_id()
        
        entry = DiaryEntry.query.filter_by(
            id=entry_id, 
            user_id=current_user.id,
            account_id=account_id
        ).first_or_404()
        
        return jsonify({
            'id': entry.id,
            'date': entry.entry_date.strftime('%B %d, %Y'),
            'title': entry.title or '',
            'content': entry.content or '',
            'mood': entry.mood or '',
            'images': [f"/{img.filepath}" for img in entry.images]
        })
        
    except Exception as e:
        print(f"Error loading diary entry: {str(e)}")
        return jsonify({'error': 'Entry not found'}), 404

@user_bp.route('/diary/entry/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_diary_entry(entry_id):
    try:
        account_id = get_active_account_id()
        
        entry = DiaryEntry.query.filter_by(
            id=entry_id, 
            user_id=current_user.id,
            account_id=account_id
        ).first_or_404()
        
        # Delete associated image files
        for image in entry.images:
            try:
                if os.path.exists(image.filepath):
                    os.remove(image.filepath)
            except:
                pass
        
        # Delete entry (cascade will delete images from db)
        db.session.delete(entry)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Entry deleted!'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})



@user_bp.route('/diary/auto-write', methods=['POST'])
@login_required
def auto_write_diary():
    """Auto-generate a diary entry using AI based on today's trading activity"""
    # Elite check
    if not current_user.can_access_goals():  # same elite gate
        return jsonify({
            'success': False, 
            'message': '🔒 Auto-Write Diary is an Elite feature. Upgrade to Elite (₹799/mo) to unlock.'
        })
    
    account_id = get_active_account_id()
    if not account_id:
        return jsonify({'success': False, 'message': 'No active account.'})
    
    from ai_service import auto_write_diary as generate_diary
    result = generate_diary(current_user, account_id)
    
    return jsonify(result)
    

# ═══════════════════════════════════════════════════════════
# 📖 END DIARY ROUTES
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 📧 EMAIL SETTINGS
# ═══════════════════════════════════════════════════════════

@user_bp.route('/change-email', methods=['POST'])
@login_required
def change_email():
    """Change email instantly if not verified"""
    
    if current_user.email_verified:
        flash('Your email is already verified and cannot be changed.', 'warning')
        return redirect(url_for('user.settings'))
    
    new_email = request.form.get('new_email', '').strip()
    
    if not new_email:
        flash('Please enter a new email address.', 'danger')
        return redirect(url_for('user.settings'))
    
    existing = User.query.filter_by(email=new_email).first()
    if existing and existing.id != current_user.id:
        flash('This email is already registered.', 'danger')
        return redirect(url_for('user.settings'))
    
    # Update email instantly
    old_email = current_user.email
    current_user.email = new_email
    db.session.commit()
    
    flash(f'Email changed from {old_email} to {new_email}.', 'success')
    return redirect(url_for('user.settings'))

@user_bp.route('/resend-verification')
@login_required
def resend_verification_from_settings():
    """Resend verification email from settings"""
    if current_user.email_verified:
        flash('Your email is already verified! ✅', 'success')
        return redirect(url_for('user.settings'))
    
    from verify_email import send_verification_email
    result = send_verification_email(current_user)
    
    if result['success']:
        flash(result['message'], 'success')
    else:
        flash(result['message'], 'danger')
    
    return redirect(url_for('user.settings'))

# ═══════════════════════════════════════════════════════════
# 📧 END EMAIL SETTINGS
# ═══════════════════════════════════════════════════════════

# ─── Helpers ───
def parse_float(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip()!='':
            try: return float(str(v).strip().replace(',',''))
            except: continue
    return None

def parse_date(row, names):
    formats = ['%Y-%m-%d','%Y/%m/%d','%d/%m/%Y','%m/%d/%Y','%Y-%m-%d %H:%M:%S','%Y/%m/%d %H:%M:%S','%Y.%m.%d','%Y.%m.%d %H:%M:%S','%d-%m-%Y','%d/%m/%Y %H:%M:%S','%Y-%m-%dT%H:%M:%S','%Y-%m-%dT%H:%M']
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip()!='':
            s = str(v).strip()
            for f in formats:
                try: return datetime.strptime(s, f)
                except: continue
            if ' ' in s:
                for f in ['%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%d/%m/%Y','%d-%m-%Y']:
                    try: return datetime.strptime(s.split(' ')[0], f)
                    except: continue
    return None

def detect_session(dt):
    if dt is None: return ''
    h = dt.hour
    if 0 <= h < 8: return 'asian'
    elif 8 <= h < 16: return 'london'
    else: return 'newyork'


# ─── Day Notes API Routes ───

@user_bp.route('/api/day-notes')
@login_required
def api_day_notes():
    """Get all dates that have notes"""
    account_id = get_active_account_id()
    notes = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id
    ).all()
    result = {}
    for note in notes:
        result[str(note.note_date)] = True
    return jsonify(result)

@user_bp.route('/api/day-notes/<date_str>')
@login_required
def api_get_day_notes(date_str):
    """Get all notes for a specific date"""
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify([])
    
    account_id = get_active_account_id()
    
    notes = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id,
        note_date=target_date
    ).order_by(DayNote.created_at.desc()).all()
    
    return jsonify([{
        'id': note.id,
        'note': note.note,
        'created_at': note.created_at.strftime('%H:%M %d %b %Y') if note.created_at else '',
        'updated_at': note.updated_at.strftime('%H:%M %d %b %Y') if note.updated_at else ''
    } for note in notes])

@user_bp.route('/api/day-notes/<date_str>/count')
@login_required
def api_day_notes_count(date_str):
    """Get note count for a specific date"""
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'count': 0})
    
    account_id = get_active_account_id()
    
    count = DayNote.query.filter_by(
        user_id=current_user.id,
        account_id=account_id,
        note_date=target_date
    ).count()
    
    return jsonify({'count': count})

@user_bp.route('/api/day-notes/save', methods=['POST'])
@login_required
def api_save_day_note():
    """Save a new note for a specific date"""
    try:
        data = request.get_json()
        date_str = data.get('date')
        note_text = data.get('note', '').strip()
        
        if not date_str or not note_text:
            return jsonify({'success': False, 'message': 'Date and note are required'})
        
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        account_id = get_active_account_id()
        
        # Create new note (allows multiple notes per day)
        day_note = DayNote(
            user_id=current_user.id,
            account_id=account_id,
            note_date=target_date,
            note=note_text
        )
        db.session.add(day_note)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Note saved!'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@user_bp.route('/api/day-notes/delete/<int:note_id>', methods=['POST'])
@login_required
def api_delete_day_note(note_id):
    """Delete a specific note"""
    try:
        account_id = get_active_account_id()
        note = DayNote.query.filter_by(
            id=note_id, 
            user_id=current_user.id,
            account_id=account_id
        ).first_or_404()
        db.session.delete(note)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Note deleted!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@user_bp.route('/api/day-notes/edit/<int:note_id>', methods=['POST'])
@login_required
def api_edit_day_note(note_id):
    """Edit a specific note"""
    try:
        data = request.get_json()
        note_text = data.get('note', '').strip()
        
        if not note_text:
            return jsonify({'success': False, 'message': 'Note content is required'})
        
        account_id = get_active_account_id()
        note = DayNote.query.filter_by(
            id=note_id, 
            user_id=current_user.id,
            account_id=account_id
        ).first_or_404()
        note.note = note_text
        note.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Note updated!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# ═══════════════════════════════════════════════════════════
# 📚 FAQ & SUPPORT
# ═══════════════════════════════════════════════════════════

@user_bp.route('/support')
@login_required
def support():
    """Help & Support page with FAQ and tickets"""
    # Get FAQs grouped by category
    faqs = FAQ.query.filter_by(is_active=True).order_by(FAQ.category, FAQ.display_order).all()
    faq_categories = {}
    for faq in faqs:
        if faq.category not in faq_categories:
            faq_categories[faq.category] = []
        faq_categories[faq.category].append(faq)
    
    # Get user's tickets
    tickets = SupportTicket.query.filter_by(user_id=current_user.id)\
        .order_by(SupportTicket.created_at.desc()).limit(20).all()
    
    return render_template('user/support.html',
        faq_categories=faq_categories,
        tickets=tickets)


@user_bp.route('/support/create', methods=['GET', 'POST'])
@login_required
def support_create():
    """Create a new support ticket"""
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        category = request.form.get('category', 'other')
        message = request.form.get('message', '').strip()
        
        if not subject or not message:
            flash('Please fill in all required fields.', 'danger')
            return render_template('user/support_create.html')
        
        # Generate ticket number
        count = SupportTicket.query.count() + 1
        ticket_number = f'TK-{count:04d}'
        
        ticket = SupportTicket(
            user_id=current_user.id,
            ticket_number=ticket_number,
            subject=subject,
            category=category,
            status='open',
            priority='medium'
        )
        db.session.add(ticket)
        db.session.flush()
        
        # Add first message as reply
        reply = TicketReply(
            ticket_id=ticket.id,
            user_id=current_user.id,
            message=message,
            is_admin_reply=False
        )
        
        # Handle attachment
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename and file.filename != '':
                upload_dir = os.path.join('static', 'uploads', 'tickets', str(ticket.id))
                filename, filepath = compress_image(file, upload_dir, f"ticket_{ticket.id}_0")
                reply.attachment_url = filepath
        
        db.session.add(reply)
        db.session.commit()
        
        flash(f'Ticket #{ticket_number} created! We\'ll respond soon.', 'success')
        return redirect(url_for('user.support_ticket', ticket_number=ticket_number))
    
    return render_template('user/support_create.html')


@user_bp.route('/support/<string:ticket_number>')
@login_required
def support_ticket(ticket_number):
    """View a single support ticket"""
    ticket = SupportTicket.query.filter_by(
        ticket_number=ticket_number,
        user_id=current_user.id
    ).first_or_404()
    
    return render_template('user/support_ticket.html', ticket=ticket)


@user_bp.route('/api/support/<string:ticket_number>/reply', methods=['POST'])
@login_required
def api_ticket_reply(ticket_number):
    """User reply to ticket"""
    ticket = SupportTicket.query.filter_by(
        ticket_number=ticket_number,
        user_id=current_user.id
    ).first_or_404()
    
    if ticket.status == 'closed':
        return jsonify({'success': False, 'message': 'Cannot reply to a closed ticket.'})
    
    data = request.get_json()
    reply = TicketReply(
        ticket_id=ticket.id,
        user_id=current_user.id,
        message=data['message'],
        is_admin_reply=False
    )
    db.session.add(reply)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Reply sent!'})


@user_bp.route('/api/support/<string:ticket_number>/close', methods=['POST'])
@login_required
def api_ticket_close(ticket_number):
    """User closes their ticket"""
    ticket = SupportTicket.query.filter_by(
        ticket_number=ticket_number,
        user_id=current_user.id
    ).first_or_404()
    
    ticket.status = 'closed'
    db.session.commit()
    return jsonify({'success': True, 'message': 'Ticket closed.'})





# ═══════════════════════════════════════════════════════════
# 💳 PAYMENT HISTORY
# ═══════════════════════════════════════════════════════════

@user_bp.route('/payment-history')
@login_required
def payment_history():
    """User payment and subscription history"""
    from models import Payment, Subscription
    
    # Get all payments for this user
    payments = Payment.query.filter_by(user_id=current_user.id)\
        .order_by(Payment.created_at.desc()).all()
    
    # Get current subscription
    subscription = Subscription.query.filter_by(user_id=current_user.id).first()
    
    # Build payment history with details
    payment_history = []
    for payment in payments:
        # Format amount from paise to rupees/dollars
        amount = payment.total_amount / 100 if payment.total_amount else 0
        base_amount = payment.base_amount / 100 if payment.base_amount else 0
        gateway_fee = payment.gateway_fee / 100 if payment.gateway_fee else 0
        
        payment_history.append({
            'id': payment.id,
            'order_id': payment.cashfree_order_id,
            'payment_id': payment.cashfree_payment_id,
            'plan_tier': payment.plan_tier,
            'plan_type': payment.plan_type,
            'amount': amount,
            'base_amount': base_amount,
            'gateway_fee': gateway_fee,
            'currency': payment.currency or 'INR',
            'status': payment.status,
            'created_at': payment.created_at,
            'completed_at': payment.payment_completed_at,
            'error_message': payment.error_message
        })
    
    # Subscription info
    sub_info = None
    if subscription:
        today = date.today()
        days_left = None
        if subscription.end_date:
            if hasattr(subscription.end_date, 'date'):
                days_left = (subscription.end_date.date() - today).days
            else:
                days_left = (subscription.end_date - today).days
        
        sub_info = {
            'plan_tier': subscription.plan_tier,
            'plan_type': subscription.plan_type,
            'is_active': subscription.is_active,
            'auto_renew': subscription.auto_renew,
            'start_date': subscription.start_date,
            'end_date': subscription.end_date,
            'days_left': days_left,
            'cancelled_at': subscription.cancelled_at,
            'cancel_reason': subscription.cancel_reason
        }
    
    # Stats
    total_spent = sum(p['amount'] for p in payment_history if p['status'] == 'SUCCESS')
    total_payments = len(payment_history)
    successful_payments = len([p for p in payment_history if p['status'] == 'SUCCESS'])
    failed_payments = len([p for p in payment_history if p['status'] == 'FAILED'])
    pending_payments = len([p for p in payment_history if p['status'] == 'PENDING'])
    
    return render_template('user/payment_history.html',
        payments=payment_history,
        subscription=sub_info,
        total_spent=total_spent,
        total_payments=total_payments,
        successful_payments=successful_payments,
        failed_payments=failed_payments,
        pending_payments=pending_payments
    )