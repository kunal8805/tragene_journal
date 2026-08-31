"""
Trial System Routes - API Endpoints
"""

from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from trial import (
    TrialClaim,
    claim_trial,
    get_trial_status,
    should_show_popup,
    dismiss_popup,
    record_popup_shown,
    mark_converted,
    get_trial_analytics,
    get_trial_summary,
    get_claims_chart_data,
    get_conversion_chart_data,
    extend_trial,
    revoke_trial,
    check_all_trials_expiry,
    check_trial_expiry
)
from models import User
from datetime import datetime

trial_bp = Blueprint('trial', __name__, url_prefix='/trial')


# ==================== USER ROUTES ====================

@trial_bp.route('/api/status', methods=['GET'])
@login_required
def api_trial_status():
    """Get current user's trial status"""
    result = get_trial_status(current_user)
    return jsonify(result)


@trial_bp.route('/api/claim', methods=['POST'])
@login_required
def api_claim_trial():
    """Claim free trial"""
    result = claim_trial(current_user)
    
    if not result['success']:
        # Check if email not verified
        if not current_user.email_verified:
            result['need_verification'] = True
            result['message'] = 'Please verify your email first to claim the trial.'
        
        return jsonify(result), 400
    
    return jsonify(result)


@trial_bp.route('/api/dismiss-popup', methods=['POST'])
@login_required
def api_dismiss_popup():
    """Dismiss trial popup"""
    result = dismiss_popup(current_user)
    return jsonify(result)


@trial_bp.route('/api/dismiss-banner', methods=['POST'])
@login_required
def api_dismiss_banner():
    """Dismiss trial banner"""
    # Banner dismissal is just client-side for now
    return jsonify({'success': True})


@trial_bp.route('/api/should-show-popup', methods=['GET'])
@login_required
def api_should_show_popup():
    """Check if popup should be shown on login"""
    should_show, popup_data = should_show_popup(current_user)
    
    result = {
        'success': True,
        'should_show': should_show,
        'popup_data': popup_data
    }
    
    if should_show:
        record_popup_shown(current_user)
    
    return jsonify(result)


# ==================== ADMIN ROUTES ====================

@trial_bp.route('/admin/analytics')
@login_required
def admin_trial_analytics_page():
    """Admin trial analytics page"""
    if not current_user.is_admin:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('admin.dashboard'))
    
    return render_template('admin/trial_analytics.html')


@trial_bp.route('/api/admin/analytics', methods=['GET'])
@login_required
def api_admin_analytics():
    """Get trial analytics data for admin"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    # Get filters
    status_filter = request.args.get('status', 'all')
    date_filter = request.args.get('date_range', 'all')
    search = request.args.get('search', '')
    
    # Get summary
    summary = get_trial_summary()
    
    # Get charts data
    claims_chart = get_claims_chart_data()
    conversion_chart = get_conversion_chart_data()
    
    # Get users list
    users = get_trial_analytics(status_filter, date_filter, search)
    
    # Build charts data
    charts = {
        'claims_labels': claims_chart['labels'],
        'claims_data': claims_chart['data'],
        'funnel_registered': summary['total_users'],
        'funnel_verified': summary['verified_users'],
        'funnel_claimed': summary['total_claims'],
        'funnel_active': summary['active_trials'],
        'funnel_converted': summary['converted_count'],
        'status_active': summary['active_trials'],
        'status_expired': summary['expired_trials'],
        'status_converted': summary['converted_count'],
        'status_not_claimed': summary['total_users'] - summary['total_claims'],
        'conversion_labels': conversion_chart['labels'],
        'conversion_data': conversion_chart['data']
    }
    
    return jsonify({
        'success': True,
        'summary': summary,
        'charts': charts,
        'users': users
    })


@trial_bp.route('/api/admin/extend', methods=['POST'])
@login_required
def api_admin_extend_trial():
    """Extend trial for a user (admin)"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    user_id = data.get('user_id')
    days = data.get('days', 7)
    
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required'}), 400
    
    result = extend_trial(user_id, days)
    return jsonify(result)


@trial_bp.route('/api/admin/revoke', methods=['POST'])
@login_required
def api_admin_revoke_trial():
    """Revoke trial for a user (admin)"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required'}), 400
    
    result = revoke_trial(user_id)
    return jsonify(result)


@trial_bp.route('/api/admin/export', methods=['GET'])
@login_required
def api_admin_export_csv():
    """Export trial analytics as CSV"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    import csv
    from io import StringIO
    
    # Get all trial data
    users = get_trial_analytics('all', 'all', '')
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'User ID', 'Username', 'Email', 'Email Verified', 
        'Trial Status', 'Claimed Date', 'Trial End', 
        'Days Left', 'Converted', 'Converted Plan'
    ])
    
    # Data rows
    for user in users:
        writer.writerow([
            user['user_id'],
            user['username'],
            user['email'],
            'Yes' if user['email_verified'] else 'No',
            user['status_display'],
            user['claimed_date'] or '-',
            user['trial_end'] or '-',
            user['days_left'] if user['days_left'] is not None else '-',
            'Yes' if user['converted'] else 'No',
            user['converted_plan'] or '-'
        ])
    
    # Return CSV
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=trial_analytics.csv'}
    )


# ==================== WEBHOOK / CALLBACK ====================

@trial_bp.route('/api/convert', methods=['POST'])
@login_required
def api_convert_to_paid():
    """Mark user as converted (called after successful payment)"""
    data = request.get_json()
    plan = data.get('plan', 'pro')
    
    result = mark_converted(current_user, plan)
    return jsonify(result)


# ==================== CRON JOB (Internal) ====================

@trial_bp.route('/api/cron/check-expiry', methods=['GET'])
def cron_check_expiry():
    """Check all trials for expiry (protected by internal key)"""
    api_key = request.headers.get('X-API-Key') or request.args.get('key')
    
    # Simple protection - use a secret key
    if api_key != 'your-secret-cron-key':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    expired_count = check_all_trials_expiry()
    
    return jsonify({
        'success': True,
        'expired_count': expired_count,
        'message': f'{expired_count} trials expired'
    })