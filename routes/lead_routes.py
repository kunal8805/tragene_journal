"""
Lead CRM + Influencer CRM Routes
Handles: User leads, Influencer leads, Status categories, Notes, Follow-ups, Campaigns
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_required, current_user
from extensions import db
from models import (
    User, LeadStatus, LeadNote, LeadFollowUp, 
    Influencer, InfluencerCampaign, Coupon, CouponUsage, Subscription, Trade,
    Moderator
)
from datetime import datetime, timedelta
from sqlalchemy import func
import csv
import io

lead_bp = Blueprint('lead', __name__, url_prefix='/admin')

# ═══════════════════════════════════════════════════════════
# 🛡️ ADMIN CHECK (Super Admin + Moderator with permissions)
# ═══════════════════════════════════════════════════════════

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        # Super admin via Flask-Login
        if current_user.is_authenticated and current_user.is_admin:
            return f(*args, **kwargs)
        
        # Moderator via session
        if session.get('is_moderator'):
            mod_id = session.get('moderator_id')
            if mod_id:
                moderator = Moderator.query.get(mod_id)
                if moderator and moderator.is_active and not moderator.is_banned:
                    return f(*args, **kwargs)
                else:
                    session.pop('moderator_id', None)
                    session.pop('is_moderator', None)
                    flash('Session expired.', 'danger')
                    return redirect(url_for('auth.login'))
        
        flash('Access denied.', 'danger')
        return redirect(url_for('auth.login'))
    return decorated


# ═══════════════════════════════════════════════════════════
# 📋 LEAD CRM — LIST PAGE
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads')
@admin_required
def leads():
    """Lead CRM - list all users"""
    all_statuses = LeadStatus.query.filter_by(is_active=True, status_type='lead').order_by(LeadStatus.sort_order).all()
    return render_template('admin/leads/leads.html', all_statuses=all_statuses)


# ═══════════════════════════════════════════════════════════
# 📋 LEAD CRM — DETAIL PAGE
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/<int:user_id>')
@admin_required
def lead_detail(user_id):
    """Lead detail - view user, add notes, change status"""
    lead = User.query.get_or_404(user_id)
    # FIXED: Added status_type='lead' filter
    all_statuses = LeadStatus.query.filter_by(is_active=True, status_type='lead').order_by(LeadStatus.sort_order).all()
    
    # Get lead's current status - if no status assigned, show "Unassigned"
    lead_status = lead.lead_status if lead.lead_status_id else None
    if not lead_status:
        if lead.subscription_tier != 'free':
            lead_status = LeadStatus.query.filter_by(name='Purchased', is_default=True, status_type='lead').first()
        elif lead.email_verified:
            lead_status = LeadStatus.query.filter_by(name='Verified', is_default=True, status_type='lead').first()
        else:
            lead_status = LeadStatus.query.filter_by(name='New Lead', is_default=True, status_type='lead').first()
    
    # Get subscription info
    sub = Subscription.query.filter_by(user_id=lead.id).first()
    
    return render_template('admin/leads/lead_detail.html',
        lead=lead,
        all_statuses=all_statuses,
        lead_status=lead_status,
        subscription=sub
    )

# ═══════════════════════════════════════════════════════════
# 🤖 INFLUENCER CRM — LIST PAGE
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers')
@admin_required
def influencers():
    """Influencer CRM - list all influencers"""
    all_statuses = LeadStatus.query.filter_by(is_active=True, status_type='influencer').order_by(LeadStatus.sort_order).all()
    
    # Stats
    total = Influencer.query.count()
    not_contacted = Influencer.query.filter_by(response_status='not_contacted').count()
    contacted = Influencer.query.filter_by(response_status='contacted').count()
    responded = Influencer.query.filter_by(response_status='responded').count()
    agreed = Influencer.query.filter_by(response_status='agreed').count()
    declined = Influencer.query.filter_by(response_status='declined').count()
    
    return render_template('admin/leads/influencers.html',
        all_statuses=all_statuses,
        total=total,
        not_contacted=not_contacted,
        contacted=contacted,
        responded=responded,
        agreed=agreed,
        declined=declined
    )


# ═══════════════════════════════════════════════════════════
# 🤖 INFLUENCER CRM — DETAIL PAGE
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/<int:influencer_id>')
@admin_required
def influencer_detail(influencer_id):
    """Influencer detail page"""
    influencer = Influencer.query.get_or_404(influencer_id)
    all_statuses = LeadStatus.query.filter_by(is_active=True, status_type='influencer').order_by(LeadStatus.sort_order).all()
    all_coupons = Coupon.query.filter_by(is_active=True).order_by(Coupon.created_at.desc()).all()
    campaigns = InfluencerCampaign.query.filter_by(influencer_id=influencer.id).order_by(InfluencerCampaign.created_at.desc()).all()
    
    return render_template('admin/leads/influencer_detail.html',
        influencer=influencer,
        all_statuses=all_statuses,
        all_coupons=all_coupons,
        campaigns=campaigns
    )


# ═══════════════════════════════════════════════════════════
# 📊 API — LEAD LIST (JSON)
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/list')
@admin_required
def api_leads_list():
    """Get paginated leads list with search and filter"""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all')
    per_page = 25
    
    query = User.query
    
    if search:
        query = query.filter(
            db.or_(
                User.email.ilike(f'%{search}%'),
                User.username.ilike(f'%{search}%'),
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.phone_number.ilike(f'%{search}%')
            )
        )
    
    if status != 'all' and status != 'none':
        status_obj = LeadStatus.query.filter_by(name=status, status_type='lead').first()
        if status_obj:
            if status == 'Purchased':
                query = query.filter(User.subscription_tier != 'free')
            elif status == 'Verified':
                query = query.filter(User.email_verified == True, User.subscription_tier == 'free')
            elif status == 'New Lead':
                query = query.filter(User.email_verified == False, User.subscription_tier == 'free')
            else:
                query = query.filter(User.lead_status_id == status_obj.id)
    
    elif status == 'none':
        query = query.filter(
            User.email_verified == False, 
            User.subscription_tier == 'free',
            User.lead_status_id == None
        )
    
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    
    users = query.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    leads_data = []
    for u in users:
        if u.lead_status_id:
            status_obj = LeadStatus.query.get(u.lead_status_id)
            if status_obj and status_obj.status_type == 'lead':
                status_name = status_obj.name
                status_color = status_obj.color
            else:
                status_name = 'New Lead'
                status_color = '#6B7280'
        elif u.subscription_tier != 'free':
            status_name = 'Purchased'
            status_color = '#06B6D4'
        elif u.email_verified:
            status_name = 'Verified'
            status_color = '#10B981'
        else:
            status_name = 'New Lead'
            status_color = '#6B7280'
        
        leads_data.append({
            'id': u.id,
            'name': u.full_name or u.username,
            'email': u.email,
            'phone': u.phone_number or '',
            'status_name': status_name,
            'status_color': status_color,
            'kyc_status': 'not_applied',
            'created_at': u.created_at.strftime('%d %b %Y') if u.created_at else '',
            'subscription_tier': u.subscription_tier,
            'email_verified': u.email_verified
        })
    
    return jsonify({
        'success': True,
        'leads': leads_data,
        'total': total,
        'pages': pages
    })


# ═══════════════════════════════════════════════════════════
# 📊 API — LEAD STATS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/stats')
@admin_required
def api_leads_stats():
    """Get lead stats for dashboard cards"""
    stats = {}
    
    all_statuses = LeadStatus.query.filter_by(is_active=True, status_type='lead').all()
    for s in all_statuses:
        count = User.query.filter_by(lead_status_id=s.id).count()
        stats[s.name] = count
    
    # Add auto-detected statuses
    stats['Purchased'] = User.query.filter(User.subscription_tier != 'free', User.lead_status_id == None).count()
    stats['Verified'] = User.query.filter(User.email_verified == True, User.subscription_tier == 'free', User.lead_status_id == None).count()
    stats['New Lead'] = User.query.filter(User.email_verified == False, User.subscription_tier == 'free', User.lead_status_id == None).count()
    
    return jsonify(stats)


# ═══════════════════════════════════════════════════════════
# 📊 API — LEAD STATUS (REAL UPDATE)
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/<int:user_id>/status', methods=['POST'])
@admin_required
def api_lead_status(user_id):
    """Update lead status category - REAL update"""
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    status_id = data.get('status_id')
    
    if status_id:
        try:
            status_id_int = int(status_id)
            status = LeadStatus.query.get(status_id_int)
            if status and status.status_type == 'lead':
                user.lead_status_id = status.id
                db.session.commit()
                return jsonify({
                    'success': True, 
                    'message': f'Status changed to {status.name}',
                    'status': {
                        'id': status.id,
                        'name': status.name,
                        'color': status.color
                    }
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid lead status'})
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid status ID'})
    
    return jsonify({'success': False, 'error': 'No status ID provided'})


# ═══════════════════════════════════════════════════════════
# 📊 API — LEAD NOTES
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/<int:user_id>/notes', methods=['GET', 'POST'])
@admin_required
def api_lead_notes(user_id):
    """Get or add notes for a user lead"""
    if request.method == 'GET':
        notes = LeadNote.query.filter_by(lead_type='user', lead_id=user_id).order_by(LeadNote.created_at.desc()).all()
        return jsonify({
            'success': True,
            'notes': [{
                'id': n.id,
                'content': n.content,
                'created_at': n.created_at.strftime('%Y-%m-%dT%H:%M:%S') if n.created_at else '',
                'created_by': n.creator.username if n.creator else 'Admin'
            } for n in notes]
        })
    
    data = request.get_json()
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Note cannot be empty'})
    
    note = LeadNote(
        lead_type='user',
        lead_id=user_id,
        content=content,
        created_by_admin_id=current_user.id if current_user.is_authenticated else None
    )
    db.session.add(note)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Note saved'})


@lead_bp.route('/leads/api/notes/<int:note_id>', methods=['DELETE'])
@admin_required
def api_delete_lead_note(note_id):
    """Delete a lead note"""
    note = LeadNote.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    return jsonify({'success': True})


# ═══════════════════════════════════════════════════════════
# 📊 API — LEAD FOLLOW-UPS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/<int:user_id>/followups', methods=['GET', 'POST'])
@admin_required
def api_lead_followups(user_id):
    """Get or schedule follow-ups for a user lead"""
    if request.method == 'GET':
        followups = LeadFollowUp.query.filter_by(lead_type='user', lead_id=user_id).order_by(LeadFollowUp.followup_date.desc()).all()
        return jsonify({
            'success': True,
            'followups': [{
                'id': f.id,
                'followup_date': f.followup_date.strftime('%Y-%m-%dT%H:%M') if f.followup_date else '',
                'followup_type': f.followup_type,
                'notes': f.notes or '',
                'is_completed': f.is_completed
            } for f in followups]
        })
    
    data = request.get_json()
    followup_date = data.get('followup_date')
    followup_type = data.get('followup_type', 'call')
    notes = data.get('notes', '').strip()
    
    if not followup_date:
        return jsonify({'success': False, 'error': 'Date required'})
    
    try:
        fdate = datetime.strptime(followup_date, '%Y-%m-%dT%H:%M')
    except:
        fdate = datetime.utcnow() + timedelta(days=1)
    
    followup = LeadFollowUp(
        lead_type='user',
        lead_id=user_id,
        followup_date=fdate,
        followup_type=followup_type,
        notes=notes or None,
        created_by_admin_id=current_user.id if current_user.is_authenticated else None
    )
    db.session.add(followup)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Follow-up scheduled'})


@lead_bp.route('/leads/api/followups/<int:followup_id>/complete', methods=['POST'])
@admin_required
def api_complete_followup(followup_id):
    """Mark a follow-up as complete"""
    followup = LeadFollowUp.query.get_or_404(followup_id)
    followup.is_completed = True
    followup.completed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


# ═══════════════════════════════════════════════════════════
# 📊 API — BULK ACTIONS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/bulk-action', methods=['POST'])
@admin_required
def api_bulk_action():
    """Bulk actions: add note or move status for multiple leads"""
    data = request.get_json()
    action = data.get('action')
    lead_ids = data.get('lead_ids', [])
    
    if not lead_ids:
        return jsonify({'success': False, 'error': 'No leads selected'})
    
    if action == 'add_note':
        note_content = data.get('note', '').strip()
        if not note_content:
            return jsonify({'success': False, 'error': 'Note cannot be empty'})
        
        for lid in lead_ids:
            note = LeadNote(
                lead_type='user',
                lead_id=int(lid),
                content=note_content,
                created_by_admin_id=current_user.id if current_user.is_authenticated else None
            )
            db.session.add(note)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Note added to {len(lead_ids)} leads'})
    
    if action == 'move_status':
        status_id = data.get('status_id')
        if not status_id:
            return jsonify({'success': False, 'error': 'No status selected'})
        
        status = LeadStatus.query.get(int(status_id))
        if not status or status.status_type != 'lead':
            return jsonify({'success': False, 'error': 'Invalid status'})
        
        for lid in lead_ids:
            user = User.query.get(int(lid))
            if user:
                user.lead_status_id = status.id
        
        db.session.commit()
        return jsonify({'success': True, 'message': f'Status changed to {status.name} for {len(lead_ids)} leads'})
    
    return jsonify({'success': False, 'error': 'Invalid action'})


# ═══════════════════════════════════════════════════════════
# 📊 API — STATUS CRUD (With status_type support)
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/statuses')
@admin_required
def api_get_statuses():
    """Get all status categories - filter by status_type"""
    status_type = request.args.get('status_type', 'lead')
    statuses = LeadStatus.query.filter_by(is_active=True, status_type=status_type).order_by(LeadStatus.sort_order).all()
    return jsonify({
        'success': True,
        'statuses': [{
            'id': s.id,
            'name': s.name,
            'color': s.color,
            'is_default': s.is_default,
            'status_type': s.status_type
        } for s in statuses]
    })


@lead_bp.route('/leads/api/statuses/custom', methods=['POST'])
@admin_required
def api_create_status():
    """Create a custom status - for lead OR influencer"""
    data = request.get_json()
    name = data.get('name', '').strip()
    color = data.get('color', '#4F46E5')
    status_type = data.get('status_type', 'lead')
    
    if not name:
        return jsonify({'success': False, 'error': 'Name required'})
    
    if status_type not in ['lead', 'influencer']:
        return jsonify({'success': False, 'error': 'Invalid status_type'})
    
    existing = LeadStatus.query.filter_by(name=name, status_type=status_type).first()
    if existing:
        return jsonify({'success': False, 'error': 'Status already exists for this type'})
    
    status = LeadStatus(
        name=name,
        color=color,
        status_type=status_type,
        is_default=False,
        is_active=True,
        sort_order=99,
        created_by_admin_id=current_user.id if current_user.is_authenticated else None
    )
    db.session.add(status)
    db.session.commit()
    
    return jsonify({'success': True, 'status': {'id': status.id, 'name': status.name, 'color': status.color, 'status_type': status.status_type}})


@lead_bp.route('/leads/api/statuses/<int:status_id>', methods=['PUT', 'DELETE'])
@admin_required
def api_update_delete_status(status_id):
    """Update or delete a status"""
    status = LeadStatus.query.get_or_404(status_id)
    
    if request.method == 'DELETE':
        # Allow deletion of any status, but reassign leads/influencers first
        Influencer.query.filter_by(status_id=status.id).update({Influencer.status_id: None})
        User.query.filter_by(lead_status_id=status.id).update({User.lead_status_id: None})
        
        # Soft delete - just mark as inactive instead of hard delete
        status.is_active = False
        db.session.commit()
        return jsonify({'success': True, 'message': 'Status deleted'})
    
    # PUT - update
    data = request.get_json()
    status.name = data.get('name', status.name)
    status.color = data.get('color', status.color)
    db.session.commit()
    return jsonify({'success': True})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER LIST
# ═══════════════════════════════════════════════════════════
@lead_bp.route('/influencers/api/list')
@admin_required
def api_influencers_list():
    """Get paginated influencer list with search and filter"""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    response_filter = request.args.get('response', 'all')
    status_filter = request.args.get('status', 'all')  # ADD THIS - filter by status name
    per_page = request.args.get('per_page', 25, type=int)  # ADD THIS - allow custom per_page
    
    query = Influencer.query
    
    if search:
        query = query.filter(
            db.or_(
                Influencer.name.ilike(f'%{search}%'),
                Influencer.email.ilike(f'%{search}%'),
                Influencer.phone.ilike(f'%{search}%'),
                Influencer.social_handle.ilike(f'%{search}%'),
                Influencer.location.ilike(f'%{search}%')
            )
        )
    
    if response_filter != 'all':
        query = query.filter(Influencer.response_status == response_filter)
    
    # ADD THIS - Filter by status name if provided
    if status_filter != 'all':
        status_obj = LeadStatus.query.filter_by(
            name=status_filter, 
            status_type='influencer',
            is_active=True
        ).first()
        if status_obj:
            query = query.filter(Influencer.status_id == status_obj.id)
        else:
            # If status doesn't exist, return empty result
            return jsonify({
                'success': True,
                'influencers': [],
                'total': 0,
                'pages': 0
            })
    
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    
    influencers = query.order_by(Influencer.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    data = [{
        'id': inf.id,
        'name': inf.name,
        'email': inf.email,
        'phone': inf.phone or '',
        'location': inf.location or '',
        'platform': inf.platform or '',
        'social_handle': inf.social_handle or '',
        'follower_count': inf.follower_count or 0,
        'niche': inf.niche or '',
        'response_status': inf.response_status,
        'status_id': inf.status_id,  # ADD THIS
        'status_name': inf.status.name if inf.status and inf.status.status_type == 'influencer' else 'Unassigned',
        'status_color': inf.status.color if inf.status and inf.status.status_type == 'influencer' else '#6B7280',
        'created_at': inf.created_at.strftime('%d %b %Y') if inf.created_at else ''
    } for inf in influencers]
    
    return jsonify({
        'success': True,
        'influencers': data,
        'total': total,
        'pages': pages,
        'current_page': page,  # ADD THIS
        'per_page': per_page  # ADD THIS
    })

# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER ADD (Manual)
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/add', methods=['POST'])
@admin_required
def api_add_influencer():
    """Manually add an influencer"""
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip() or None
    location = data.get('location', '').strip() or None
    platform = data.get('platform', '').strip() or None
    social_handle = data.get('social_handle', '').strip() or None
    follower_count = data.get('follower_count') or None
    niche = data.get('niche', '').strip() or None
    
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'})
    
    if not email and not phone:
        return jsonify({'success': False, 'error': 'Either email or phone is required'})
    
    existing = None
    
    if email:
        existing = Influencer.query.filter_by(email=email).first()
    
    if not existing and phone:
        existing = Influencer.query.filter_by(phone=phone).first()
    
    if existing:
        return jsonify({'success': False, 'error': 'An influencer with this email or phone already exists'})
    
    try:
        influencer = Influencer(
            name=name,
            email=email if email else None,
            phone=phone,
            location=location,
            platform=platform,
            social_handle=social_handle,
            follower_count=int(follower_count) if follower_count else None,
            niche=niche,
            source='manual',
            response_status='not_contacted'
        )
        db.session.add(influencer)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Influencer added!', 'id': influencer.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER CSV IMPORT
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/import-csv', methods=['POST'])
@admin_required
def api_import_influencers_csv():
    """Import influencers from CSV file"""
    if 'csv_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'})
    
    file = request.files['csv_file']
    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'error': 'Please upload a CSV file'})
    
    try:
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        
        added = 0
        skipped = 0
        failed = 0
        skip_reasons = []
        fail_reasons = []
        row_number = 0
        
        def clean_value(value):
            if value is None:
                return None
            value = value.strip()
            if value.upper() in ['NULL', 'NONE', 'N/A', 'NA', '']:
                return None
            return value
        
        for row in reader:
            row_number += 1
            
            name = clean_value(row.get('name'))
            email = clean_value(row.get('email'))
            phone = clean_value(row.get('phone'))
            location = clean_value(row.get('location'))
            platform = clean_value(row.get('platform'))
            social_handle = clean_value(row.get('social_handle'))
            niche = clean_value(row.get('niche'))
            
            if not name:
                failed += 1
                fail_reasons.append(f'Row {row_number}: Missing name')
                continue
            
            if not email and not phone:
                failed += 1
                fail_reasons.append(f'Row {row_number} ({name}): Missing both email and phone')
                continue
            
            existing = None
            duplicate_reason = ''
            
            if email:
                existing = Influencer.query.filter_by(email=email).first()
                if existing:
                    duplicate_reason = f'duplicate email: {email}'
            
            if not existing and phone:
                existing = Influencer.query.filter_by(phone=phone).first()
                if existing:
                    duplicate_reason = f'duplicate phone: {phone}'
            
            if existing:
                skipped += 1
                skip_reasons.append(f'Row {row_number} ({name}): {duplicate_reason}')
                continue
            
            follower_count = None
            fc_raw = clean_value(row.get('follower_count'))
            if fc_raw:
                try:
                    follower_count = int(fc_raw.replace(',', ''))
                except (ValueError, AttributeError):
                    follower_count = None
            
            try:
                influencer = Influencer(
                    name=name,
                    email=email,
                    phone=phone,
                    location=location,
                    platform=platform,
                    social_handle=social_handle,
                    follower_count=follower_count,
                    niche=niche,
                    source='csv_import',
                    response_status='not_contacted'
                )
                db.session.add(influencer)
                added += 1
            except Exception as e:
                failed += 1
                fail_reasons.append(f'Row {row_number} ({name}): {str(e)}')
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Database error during import: {str(e)}'})
        
        return jsonify({
            'success': True,
            'message': f'Import complete: {added} added, {skipped} skipped, {failed} failed',
            'added': added,
            'skipped': skipped,
            'failed': failed,
            'skip_reasons': skip_reasons[:20],
            'fail_reasons': fail_reasons[:20]
        })
    
    except UnicodeDecodeError:
        return jsonify({'success': False, 'error': 'CSV file must be UTF-8 encoded'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER NOTES
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/<int:influencer_id>/notes', methods=['GET', 'POST'])
@admin_required
def api_influencer_notes(influencer_id):
    """Get or add notes for an influencer"""
    influencer = Influencer.query.get_or_404(influencer_id)
    
    if request.method == 'GET':
        notes = LeadNote.query.filter_by(lead_type='influencer', lead_id=influencer_id).order_by(LeadNote.created_at.desc()).all()
        return jsonify({
            'success': True,
            'notes': [{
                'id': n.id,
                'content': n.content,
                'created_at': n.created_at.strftime('%Y-%m-%dT%H:%M:%S') if n.created_at else '',
                'created_by': n.creator.username if n.creator else 'Admin'
            } for n in notes]
        })
    
    data = request.get_json()
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Note cannot be empty'})
    
    note = LeadNote(
        lead_type='influencer',
        lead_id=influencer_id,
        content=content,
        created_by_admin_id=current_user.id if current_user.is_authenticated else None
    )
    db.session.add(note)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Note saved'})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER RESPONSE STATUS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/<int:influencer_id>/response-status', methods=['POST'])
@admin_required
def api_influencer_response_status(influencer_id):
    """Update influencer response status"""
    influencer = Influencer.query.get_or_404(influencer_id)
    data = request.get_json()
    
    status = data.get('response_status', 'not_contacted')
    valid_statuses = ['not_contacted', 'contacted', 'responded', 'negotiating', 'agreed', 'declined']
    
    if status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'})
    
    influencer.response_status = status
    influencer.last_contacted_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Status updated'})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER STATUS CATEGORY
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/<int:influencer_id>/status', methods=['POST'])
@admin_required
def api_influencer_status(influencer_id):
    """Update influencer status category"""
    influencer = Influencer.query.get_or_404(influencer_id)
    data = request.get_json()
    
    status_id = data.get('status_id')
    if status_id:
        status = LeadStatus.query.get(int(status_id))
        if status and status.status_type == 'influencer':
            influencer.status_id = status.id
        else:
            return jsonify({'success': False, 'error': 'Invalid influencer status'})
    else:
        influencer.status_id = None
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Status updated'})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER CAMPAIGN
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/<int:influencer_id>/campaign', methods=['POST'])
@admin_required
def api_create_campaign(influencer_id):
    """Assign a coupon to an influencer (create campaign)"""
    influencer = Influencer.query.get_or_404(influencer_id)
    data = request.get_json()
    
    coupon_id = data.get('coupon_id')
    campaign_name = data.get('campaign_name', '').strip()
    
    if not coupon_id:
        return jsonify({'success': False, 'error': 'Please select a coupon'})
    
    campaign = InfluencerCampaign(
        influencer_id=influencer.id,
        coupon_id=int(coupon_id),
        campaign_name=campaign_name or f"Campaign with {influencer.name}",
        status='active',
        started_at=datetime.utcnow()
    )
    db.session.add(campaign)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Campaign created!', 'campaign_id': campaign.id})


@lead_bp.route('/influencers/api/campaigns/<int:campaign_id>/toggle', methods=['POST'])
@admin_required
def api_toggle_campaign(campaign_id):
    """Toggle campaign status (active/paused/completed)"""
    campaign = InfluencerCampaign.query.get_or_404(campaign_id)
    
    if campaign.status == 'active':
        campaign.status = 'paused'
    elif campaign.status == 'paused':
        campaign.status = 'active'
    
    db.session.commit()
    return jsonify({'success': True, 'status': campaign.status})


# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER STATS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/stats')
@admin_required
def api_influencer_stats():
    """Get influencer stats for dashboard cards"""
    total = Influencer.query.count()
    not_contacted = Influencer.query.filter_by(response_status='not_contacted').count()
    contacted = Influencer.query.filter_by(response_status='contacted').count()
    responded = Influencer.query.filter_by(response_status='responded').count()
    negotiating = Influencer.query.filter_by(response_status='negotiating').count()
    agreed = Influencer.query.filter_by(response_status='agreed').count()
    declined = Influencer.query.filter_by(response_status='declined').count()
    
    return jsonify({
        'success': True,
        'total': total,
        'not_contacted': not_contacted,
        'contacted': contacted,
        'responded': responded,
        'negotiating': negotiating,
        'agreed': agreed,
        'declined': declined
    })



# ═══════════════════════════════════════════════════════════
# 📊 API — INFLUENCER STATUS COUNTS
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/influencers/api/status-counts')
@admin_required
def api_influencer_status_counts():
    """Get counts of influencers per status category"""
    # Get all active influencer statuses
    statuses = LeadStatus.query.filter_by(
        is_active=True, 
        status_type='influencer'
    ).order_by(LeadStatus.sort_order).all()
    
    result = []
    for s in statuses:
        count = Influencer.query.filter_by(status_id=s.id).count()
        result.append({
            'id': s.id,
            'name': s.name,
            'color': s.color,
            'count': count
        })
    
    return jsonify({
        'success': True,
        'statuses': result
    })