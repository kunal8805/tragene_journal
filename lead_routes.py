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
    all_statuses = LeadStatus.query.filter_by(is_active=True).order_by(LeadStatus.sort_order).all()
    return render_template('admin/leads/leads.html', all_statuses=all_statuses)


# ═══════════════════════════════════════════════════════════
# 📋 LEAD CRM — DETAIL PAGE
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/<int:user_id>')
@admin_required
def lead_detail(user_id):
    """Lead detail - view user, add notes, change status"""
    lead = User.query.get_or_404(user_id)
    all_statuses = LeadStatus.query.filter_by(is_active=True).order_by(LeadStatus.sort_order).all()
    
    # Get lead's current status
    lead_status = lead.lead_status if lead.lead_status_id else None
    if not lead_status:
        if lead.subscription_tier != 'free':
            lead_status = LeadStatus.query.filter_by(name='Purchased', is_default=True).first()
        elif lead.email_verified:
            lead_status = LeadStatus.query.filter_by(name='Verified', is_default=True).first()
        else:
            lead_status = LeadStatus.query.filter_by(name='New Lead', is_default=True).first()
    
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
    all_statuses = LeadStatus.query.filter_by(is_active=True).order_by(LeadStatus.sort_order).all()
    
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
    all_statuses = LeadStatus.query.filter_by(is_active=True).order_by(LeadStatus.sort_order).all()
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
        status_obj = LeadStatus.query.filter_by(name=status).first()
        if status_obj:
            if status == 'Purchased':
                query = query.filter(User.subscription_tier != 'free')
            elif status == 'Verified':
                query = query.filter(User.email_verified == True, User.subscription_tier == 'free')
            elif status == 'New Lead':
                query = query.filter(User.email_verified == False, User.subscription_tier == 'free')
            else:
                # Custom statuses - filter by lead_status_id
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
        # Determine status - REAL from DB
        if u.lead_status_id:
            status_obj = LeadStatus.query.get(u.lead_status_id)
            if status_obj:
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
    
    # Count by lead_status_id (REAL counts from DB)
    all_statuses = LeadStatus.query.filter_by(is_active=True).all()
    for s in all_statuses:
        count = User.query.filter_by(lead_status_id=s.id).count()
        stats[s.name] = count
    
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
        status = LeadStatus.query.get(int(status_id))
        if status:
            user.lead_status_id = status.id
            db.session.commit()
            return jsonify({'success': True, 'message': f'Status changed to {status.name}'})
    
    return jsonify({'success': False, 'error': 'Invalid status'})


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
    
    # POST - add note
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
    
    # POST - schedule follow-up
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
        if not status:
            return jsonify({'success': False, 'error': 'Invalid status'})
        
        for lid in lead_ids:
            user = User.query.get(int(lid))
            if user:
                user.lead_status_id = status.id
        
        db.session.commit()
        return jsonify({'success': True, 'message': f'Status changed to {status.name} for {len(lead_ids)} leads'})
    
    return jsonify({'success': False, 'error': 'Invalid action'})


# ═══════════════════════════════════════════════════════════
# 📊 API — STATUS CRUD
# ═══════════════════════════════════════════════════════════

@lead_bp.route('/leads/api/statuses')
@admin_required
def api_get_statuses():
    """Get all status categories"""
    statuses = LeadStatus.query.filter_by(is_active=True).order_by(LeadStatus.sort_order).all()
    return jsonify({
        'success': True,
        'statuses': [{
            'id': s.id,
            'name': s.name,
            'color': s.color,
            'is_default': s.is_default
        } for s in statuses]
    })


@lead_bp.route('/leads/api/statuses/custom', methods=['POST'])
@admin_required
def api_create_status():
    """Create a custom status"""
    data = request.get_json()
    name = data.get('name', '').strip()
    color = data.get('color', '#4F46E5')
    
    if not name:
        return jsonify({'success': False, 'error': 'Name required'})
    
    existing = LeadStatus.query.filter_by(name=name).first()
    if existing:
        return jsonify({'success': False, 'error': 'Status already exists'})
    
    status = LeadStatus(
        name=name,
        color=color,
        is_default=False,
        is_active=True,
        sort_order=99,
        created_by_admin_id=current_user.id if current_user.is_authenticated else None
    )
    db.session.add(status)
    db.session.commit()
    
    return jsonify({'success': True, 'status': {'id': status.id, 'name': status.name, 'color': status.color}})


@lead_bp.route('/leads/api/statuses/<int:status_id>', methods=['PUT', 'DELETE'])
@admin_required
def api_update_delete_status(status_id):
    """Update or delete a status"""
    status = LeadStatus.query.get_or_404(status_id)
    
    if request.method == 'DELETE':
        if status.is_default:
            return jsonify({'success': False, 'error': 'Cannot delete default status'})
        
        Influencer.query.filter_by(status_id=status.id).update({Influencer.status_id: None})
        User.query.filter_by(lead_status_id=status.id).update({User.lead_status_id: None})
        db.session.delete(status)
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
    per_page = 25
    
    query = Influencer.query
    
    if search:
        query = query.filter(
            db.or_(
                Influencer.name.ilike(f'%{search}%'),
                Influencer.email.ilike(f'%{search}%'),
                Influencer.phone.ilike(f'%{search}%'),
                Influencer.social_handle.ilike(f'%{search}%')
            )
        )
    
    if response_filter != 'all':
        query = query.filter(Influencer.response_status == response_filter)
    
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    
    influencers = query.order_by(Influencer.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    data = [{
        'id': inf.id,
        'name': inf.name,
        'email': inf.email,
        'phone': inf.phone or '',
        'platform': inf.platform or '',
        'social_handle': inf.social_handle or '',
        'follower_count': inf.follower_count or 0,
        'niche': inf.niche or '',
        'response_status': inf.response_status,
        'status_name': inf.status.name if inf.status else 'Unassigned',
        'status_color': inf.status.color if inf.status else '#6B7280',
        'created_at': inf.created_at.strftime('%d %b %Y') if inf.created_at else ''
    } for inf in influencers]
    
    return jsonify({
        'success': True,
        'influencers': data,
        'total': total,
        'pages': pages
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
    platform = data.get('platform', '').strip() or None
    social_handle = data.get('social_handle', '').strip() or None
    follower_count = data.get('follower_count') or None
    niche = data.get('niche', '').strip() or None
    
    if not name or not email:
        return jsonify({'success': False, 'error': 'Name and email required'})
    
    existing = Influencer.query.filter_by(email=email).first()
    if existing:
        return jsonify({'success': False, 'error': 'Email already exists'})
    
    try:
        influencer = Influencer(
            name=name,
            email=email,
            phone=phone,
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
        errors = []
        
        for row in reader:
            name = (row.get('name') or '').strip()
            email = (row.get('email') or '').strip()
            
            if not name or not email:
                failed += 1
                errors.append(f'Row skipped: missing name or email')
                continue
            
            existing = Influencer.query.filter_by(email=email).first()
            if existing:
                skipped += 1
                continue
            
            phone = (row.get('phone') or '').strip() or None
            platform = (row.get('platform') or '').strip() or None
            social_handle = (row.get('social_handle') or '').strip() or None
            niche = (row.get('niche') or '').strip() or None
            
            follower_count = None
            fc_raw = (row.get('follower_count') or '').strip()
            if fc_raw:
                try:
                    follower_count = int(fc_raw.replace(',', ''))
                except:
                    follower_count = None
            
            try:
                influencer = Influencer(
                    name=name,
                    email=email,
                    phone=phone,
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
                errors.append(f'{email}: {str(e)}')
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Import complete: {added} added, {skipped} skipped (duplicates), {failed} failed',
            'added': added,
            'skipped': skipped,
            'failed': failed,
            'errors': errors[:10]
        })
        
    except Exception as e:
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
    influencer.status_id = int(status_id) if status_id else None
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