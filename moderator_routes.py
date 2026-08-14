"""
Moderator Management Routes
Handles: CRUD moderators, permissions, activity logs
Login is handled in auth.py via session
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_required, current_user
from extensions import db
from models import Moderator, ModeratorPermission, ModeratorActivityLog, PermissionRegistry, User
from datetime import datetime, timedelta
from functools import wraps

moderator_bp = Blueprint('moderator', __name__, url_prefix='/moderator')


# ═══════════════════════════════════════════════════════════
# 🔐 DECORATORS
# ═══════════════════════════════════════════════════════════

def super_admin_required(f):
    """Only main admin can access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Super admin only.', 'danger')
            return redirect(url_for('user.dashboard'))
        return f(*args, **kwargs)
    return decorated


def moderator_required(permission_key=None):
    """Check if moderator has required permission"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Super admin always has access
            if current_user.is_authenticated and current_user.is_admin:
                return f(*args, **kwargs)
            
            # Check moderator session
            mod_id = session.get('moderator_id')
            if not mod_id:
                flash('Access denied. Please login.', 'danger')
                return redirect(url_for('auth.login'))
            
            moderator = Moderator.query.get(mod_id)
            if not moderator or not moderator.is_active or moderator.is_banned:
                session.pop('moderator_id', None)
                session.pop('is_moderator', None)
                flash('Session expired.', 'danger')
                return redirect(url_for('auth.login'))
            
            if permission_key and not moderator.has_permission(permission_key):
                flash('Access denied. Insufficient permissions.', 'danger')
                return redirect(url_for('moderator.manage_moderators'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_moderator():
    """Get moderator from session"""
    mod_id = session.get('moderator_id')
    if mod_id:
        return Moderator.query.get(mod_id)
    return None


def is_moderator():
    return session.get('is_moderator', False)


# ═══════════════════════════════════════════════════════════
# 📋 PERMISSION HELPERS
# ═══════════════════════════════════════════════════════════

def log_activity(moderator_id, action_type, target_type, target_id, description, old_value=None, new_value=None):
    """Log moderator activity"""
    import json
    try:
        log = ModeratorActivityLog(
            moderator_id=moderator_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            description=description,
            old_value=json.dumps(old_value) if old_value else None,
            new_value=json.dumps(new_value) if new_value else None,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"⚠️ Could not log activity: {e}")


def get_permission_groups():
    """Get all permissions grouped by section"""
    perms = PermissionRegistry.query.filter_by(is_active=True).order_by(PermissionRegistry.sort_order).all()
    groups = {}
    for p in perms:
        if p.section not in groups:
            groups[p.section] = []
        groups[p.section].append(p)
    return groups


def seed_default_permissions():
    """Seed the default permissions into PermissionRegistry"""
    defaults = [
        ('Dashboard', 'dashboard', 'View Dashboard', 'Access admin dashboard stats', 'read'),
        ('Users', 'users', 'View Users', 'View user list and details', 'read'),
        ('Users', 'users.edit', 'Edit Users', 'Modify user details', 'write'),
        ('Users', 'users.ban', 'Ban Users', 'Ban or unban users', 'manage'),
        ('Users', 'users.ai_tokens', 'Manage AI Tokens', 'Modify user AI token limits', 'write'),
        ('Subscriptions', 'subscriptions', 'View Subscriptions', 'View subscription data', 'read'),
        ('Subscriptions', 'subscriptions.edit', 'Edit Subscriptions', 'Modify user plans', 'write'),
        ('Subscriptions', 'subscriptions.payment', 'Payment Analytics', 'View payment data', 'read'),
        ('Support', 'support', 'View Tickets', 'View support tickets', 'read'),
        ('Support', 'support.reply', 'Reply to Tickets', 'Reply to support tickets', 'write'),
        ('Support', 'support.close', 'Close Tickets', 'Close/resolve tickets', 'manage'),
        ('FAQ', 'faq', 'Manage FAQ', 'Add/edit/delete FAQ entries', 'write'),
        ('Blog', 'blog', 'View Blog', 'View blog posts', 'read'),
        ('Blog', 'blog.create', 'Create Blog', 'Create new blog posts', 'write'),
        ('Blog', 'blog.edit', 'Edit Blog', 'Edit existing posts', 'write'),
        ('Blog', 'blog.delete', 'Delete Blog', 'Delete blog posts', 'delete'),
        ('AI Control', 'ai_control', 'View AI Stats', 'View AI usage statistics', 'read'),
        ('AI Control', 'ai_control.edit', 'Edit AI Settings', 'Modify AI plan defaults', 'write'),
        ('Analytics', 'analytics', 'View Analytics', 'Access analytics dashboard', 'read'),
        ('SEO', 'seo', 'Manage SEO', 'Edit SEO settings', 'write'),
        ('Settings', 'settings', 'View Settings', 'View site settings', 'read'),
        ('Settings', 'settings.edit', 'Edit Settings', 'Modify site settings', 'write'),
        ('Sync Manager', 'sync', 'View Sync', 'View sync connections', 'read'),
        ('Sync Manager', 'sync.stop', 'Manage Sync', 'Stop/start user sync', 'manage'),
        ('Moderators', 'moderators', 'Manage Moderators', 'Add/edit/delete moderators', 'manage'),
        ('CRM', 'leads', 'View Leads', 'Access Lead CRM', 'read'),
        ('CRM', 'leads.edit', 'Edit Leads', 'Add notes and change lead status', 'write'),
        ('CRM', 'influencers', 'View Influencers', 'Access Influencer CRM', 'read'),
        ('CRM', 'influencers.edit', 'Edit Influencers', 'Add notes and manage influencers', 'write'),
    ]
    
    for section, key, label, desc, cat in defaults:
        existing = PermissionRegistry.query.filter_by(permission_key=key).first()
        if not existing:
            perm = PermissionRegistry(
                permission_key=key,
                section=section,
                label=label,
                description=desc,
                category=cat,
                sort_order=len(defaults)
            )
            db.session.add(perm)
    db.session.commit()


# ═══════════════════════════════════════════════════════════
# 👥 MODERATOR MANAGEMENT (Super Admin Only)
# ═══════════════════════════════════════════════════════════

@moderator_bp.route('/manage')
@login_required
@super_admin_required
def manage_moderators():
    """List all moderators"""
    moderators = Moderator.query.order_by(Moderator.created_at.desc()).all()
    return render_template('admin/moderators/list.html', moderators=moderators)


@moderator_bp.route('/add', methods=['GET', 'POST'])
@login_required
@super_admin_required
def add_moderator():
    """Add a new moderator"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        
        if not email or not password or not full_name:
            flash('All fields are required.', 'danger')
            return redirect(url_for('moderator.add_moderator'))
        
        if Moderator.query.filter_by(email=email).first():
            flash('Email already registered as moderator.', 'danger')
            return redirect(url_for('moderator.add_moderator'))
        
        moderator = Moderator(
            email=email,
            full_name=full_name,
            created_by=current_user.id
        )
        moderator.set_password(password)
        db.session.add(moderator)
        db.session.flush()
        
        permission_keys = request.form.getlist('permissions')
        for key in permission_keys:
            perm = ModeratorPermission(
                moderator_id=moderator.id,
                permission_key=key,
                is_granted=True,
                granted_by=current_user.id
            )
            db.session.add(perm)
        
        db.session.commit()
        
        log_activity(moderator.id, 'create', 'moderator', moderator.id, 
                     f'Moderator created: {email} with {len(permission_keys)} permissions')
        
        flash(f'Moderator {full_name} created!', 'success')
        return redirect(url_for('moderator.manage_moderators'))
    
    permission_groups = get_permission_groups()
    return render_template('admin/moderators/add.html', permission_groups=permission_groups, moderator=None)


@moderator_bp.route('/<int:moderator_id>/edit', methods=['GET', 'POST'])
@login_required
@super_admin_required
def edit_moderator(moderator_id):
    """Edit moderator permissions"""
    moderator = Moderator.query.get_or_404(moderator_id)
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        password = request.form.get('password', '').strip()
        
        moderator.full_name = full_name
        if password:
            moderator.set_password(password)
        
        ModeratorPermission.query.filter_by(moderator_id=moderator.id).delete()
        permission_keys = request.form.getlist('permissions')
        for key in permission_keys:
            perm = ModeratorPermission(
                moderator_id=moderator.id,
                permission_key=key,
                is_granted=True,
                granted_by=current_user.id
            )
            db.session.add(perm)
        
        db.session.commit()
        
        log_activity(moderator.id, 'update', 'moderator', moderator.id,
                     f'Permissions updated: {len(permission_keys)} granted')
        
        flash(f'Moderator {full_name} updated!', 'success')
        return redirect(url_for('moderator.manage_moderators'))
    
    permission_groups = get_permission_groups()
    current_perms = moderator.get_allowed_permissions()
    return render_template('admin/moderators/add.html', 
                           permission_groups=permission_groups, 
                           moderator=moderator,
                           current_perms=current_perms)


@moderator_bp.route('/<int:moderator_id>/toggle', methods=['POST'])
@login_required
@super_admin_required
def toggle_moderator(moderator_id):
    """Ban/Unban moderator"""
    moderator = Moderator.query.get_or_404(moderator_id)
    
    if moderator.is_banned:
        moderator.is_banned = False
        moderator.is_active = True
        moderator.ban_reason = None
        moderator.banned_until = None
        flash(f'{moderator.full_name} unbanned.', 'success')
    else:
        moderator.is_banned = True
        moderator.is_active = False
        moderator.ban_reason = request.form.get('reason', 'Banned by admin')
        days = int(request.form.get('days', 0))
        if days > 0:
            moderator.banned_until = datetime.utcnow() + timedelta(days=days)
        flash(f'{moderator.full_name} banned.', 'warning')
    
    db.session.commit()
    
    log_activity(moderator.id, 'ban', 'moderator', moderator.id,
                 f'{"Banned" if moderator.is_banned else "Unbanned"}: {moderator.ban_reason}')
    
    return redirect(url_for('moderator.manage_moderators'))


@moderator_bp.route('/<int:moderator_id>/delete', methods=['POST'])
@login_required
@super_admin_required
def delete_moderator(moderator_id):
    """Delete a moderator"""
    moderator = Moderator.query.get_or_404(moderator_id)
    
    ModeratorPermission.query.filter_by(moderator_id=moderator.id).delete()
    ModeratorActivityLog.query.filter_by(moderator_id=moderator.id).delete()
    db.session.delete(moderator)
    db.session.commit()
    
    flash(f'Moderator {moderator.full_name} deleted.', 'info')
    return redirect(url_for('moderator.manage_moderators'))


# ═══════════════════════════════════════════════════════════
# 📊 ACTIVITY LOG
# ═══════════════════════════════════════════════════════════

@moderator_bp.route('/activity')
@login_required
@super_admin_required
def activity_log():
    """View all moderator activity"""
    page = request.args.get('page', 1, type=int)
    mod_id = request.args.get('moderator_id', type=int)
    action = request.args.get('action_type', '')
    
    query = ModeratorActivityLog.query.order_by(ModeratorActivityLog.created_at.desc())
    
    if mod_id:
        query = query.filter_by(moderator_id=mod_id)
    if action:
        query = query.filter_by(action_type=action)
    
    logs = query.paginate(page=page, per_page=50)
    moderators = Moderator.query.all()
    
    return render_template('admin/moderators/activity.html', 
                           logs=logs, 
                           moderators=moderators,
                           current_moderator=mod_id,
                           current_action=action)