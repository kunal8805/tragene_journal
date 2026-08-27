"""
Coupon System Routes
Admin: Full CRUD + Analytics
User: View available coupons + apply at checkout
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Coupon, CouponUsage, CouponUser, User, Payment
from datetime import datetime, date, timedelta
from sqlalchemy import func
from functools import wraps

coupon_bp = Blueprint('coupon', __name__, url_prefix='/coupon')


# ═══════════════════════════════════════════════════════════
# 🛡️ ADMIN HELPER
# ═══════════════════════════════════════════════════════════

def admin_or_moderator_required(f):
    """Allow both super admin and moderators with coupon permission"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import session
        if current_user.is_authenticated and current_user.is_admin:
            return f(*args, **kwargs)
        if session.get('is_moderator') and session.get('moderator_id'):
            allowed = session.get('moderator_permissions', [])
            if 'coupons' in allowed:
                return f(*args, **kwargs)
        flash('Access denied.', 'danger')
        return redirect(url_for('auth.login'))
    return decorated


# ═══════════════════════════════════════════════════════════
# 📊 ADMIN - COUPON DASHBOARD
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons')
@login_required
def admin_coupons():
    """Admin coupon management dashboard"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    
    # Stats
    total = len(coupons)
    active = len([c for c in coupons if c.is_active and not c.is_expired() and not c.is_exhausted()])
    expired = len([c for c in coupons if c.is_expired()])
    exhausted = len([c for c in coupons if c.is_exhausted() and not c.is_expired()])
    
    # Total discount given
    total_discount = db.session.query(func.sum(CouponUsage.discount_applied)).scalar() or 0
    total_usages = CouponUsage.query.count()
    
    return render_template('admin/coupons/dashboard.html',
        coupons=coupons,
        total=total,
        active=active,
        expired=expired,
        exhausted=exhausted,
        total_discount=round(total_discount, 2),
        total_usages=total_usages
    )


# ═══════════════════════════════════════════════════════════
# 📝 ADMIN - CREATE COUPON
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/create', methods=['GET', 'POST'])
@login_required
def admin_create_coupon():
    """Create a new coupon"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        description = request.form.get('description', '').strip()
        discount_type = request.form.get('discount_type', 'percentage')
        discount_value = float(request.form.get('discount_value', 0))
        coupon_type = request.form.get('coupon_type', 'universal')
        max_uses = request.form.get('max_uses', '').strip()
        expires_at_str = request.form.get('expires_at', '').strip()
        min_order = float(request.form.get('min_order', 0))
        
        # Influencer fields
        influencer_name = request.form.get('influencer_name', '').strip()
        influencer_notes = request.form.get('influencer_notes', '').strip()
        
        # Specific users
        user_emails = request.form.get('user_emails', '').strip()
        
        # Validation
        if not code:
            flash('Coupon code is required.', 'danger')
            return render_template('admin/coupons/create.html')
        
        if len(code) < 3 or len(code) > 20:
            flash('Coupon code must be 3-20 characters.', 'danger')
            return render_template('admin/coupons/create.html')
        
        existing = Coupon.query.filter_by(code=code).first()
        if existing:
            flash(f'Coupon code "{code}" already exists.', 'danger')
            return render_template('admin/coupons/create.html')
        
        if discount_value <= 0:
            flash('Discount value must be positive.', 'danger')
            return render_template('admin/coupons/create.html')
        
        if discount_type == 'percentage' and discount_value > 100:
            flash('Percentage discount cannot exceed 100%.', 'danger')
            return render_template('admin/coupons/create.html')
        
        # Create coupon
        coupon = Coupon(
            code=code,
            description=description or None,
            discount_type=discount_type,
            discount_value=discount_value,
            coupon_type=coupon_type,
            max_uses=int(max_uses) if max_uses else None,
            min_order_amount=min_order,
            influencer_name=influencer_name or None,
            influencer_notes=influencer_notes or None,
            created_by_admin_id=current_user.id
        )
        
        # Parse expiration date
        if expires_at_str:
            try:
                coupon.expires_at = datetime.strptime(expires_at_str, '%Y-%m-%dT%H:%M')
            except:
                try:
                    coupon.expires_at = datetime.strptime(expires_at_str, '%Y-%m-%d')
                except:
                    flash('Invalid expiration date format.', 'danger')
                    return render_template('admin/coupons/create.html')
        
        db.session.add(coupon)
        db.session.flush()
        
        # Add specific users if coupon is 'specific' type
        if coupon_type == 'specific' and user_emails:
            emails = [e.strip() for e in user_emails.split(',') if e.strip()]
            added_count = 0
            for email in emails:
                user = User.query.filter_by(email=email).first()
                if user:
                    cu = CouponUser(
                        coupon_id=coupon.id,
                        user_id=user.id,
                        user_email=user.email,
                        user_phone=user.phone_number
                    )
                    db.session.add(cu)
                    added_count += 1
            
            if added_count > 0:
                flash(f'Coupon created and assigned to {added_count} user(s).', 'success')
            else:
                flash('Coupon created but no valid users found for the provided emails.', 'warning')
        else:
            flash(f'Coupon "{code}" created successfully!', 'success')
        
        db.session.commit()
        return redirect(url_for('coupon.admin_coupons'))
    
    # GET - show form with all users for specific coupon assignment
    all_users = User.query.order_by(User.email).all()
    return render_template('admin/coupons/create.html', users=all_users)


# ═══════════════════════════════════════════════════════════
# 👁️ ADMIN - COUPON DETAIL
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/<int:coupon_id>')
@login_required
def admin_coupon_detail(coupon_id):
    """View coupon details and usage history"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    coupon = Coupon.query.get_or_404(coupon_id)
    usages = CouponUsage.query.filter_by(coupon_id=coupon.id).order_by(CouponUsage.used_at.desc()).all()
    allowed_users = CouponUser.query.filter_by(coupon_id=coupon.id).all() if coupon.coupon_type == 'specific' else []
    
    return render_template('admin/coupons/detail.html',
        coupon=coupon,
        usages=usages,
        allowed_users=allowed_users
    )


# ═══════════════════════════════════════════════════════════
# ✏️ ADMIN - EDIT COUPON
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/<int:coupon_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_coupon(coupon_id):
    """Edit an existing coupon"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    coupon = Coupon.query.get_or_404(coupon_id)
    
    if request.method == 'POST':
        coupon.description = request.form.get('description', '').strip() or None
        coupon.discount_type = request.form.get('discount_type', coupon.discount_type)
        coupon.discount_value = float(request.form.get('discount_value', coupon.discount_value))
        coupon.max_uses = int(request.form.get('max_uses')) if request.form.get('max_uses', '').strip() else None
        coupon.min_order_amount = float(request.form.get('min_order', 0))
        coupon.is_active = request.form.get('is_active') == 'on'
        
        expires_at_str = request.form.get('expires_at', '').strip()
        if expires_at_str:
            try:
                coupon.expires_at = datetime.strptime(expires_at_str, '%Y-%m-%dT%H:%M')
            except:
                try:
                    coupon.expires_at = datetime.strptime(expires_at_str, '%Y-%m-%d')
                except:
                    pass
        else:
            coupon.expires_at = None
        
        # Influencer fields
        coupon.influencer_name = request.form.get('influencer_name', '').strip() or None
        coupon.influencer_notes = request.form.get('influencer_notes', '').strip() or None
        
        db.session.commit()
        flash(f'Coupon "{coupon.code}" updated!', 'success')
        return redirect(url_for('coupon.admin_coupon_detail', coupon_id=coupon.id))
    
    return render_template('admin/coupons/edit.html', coupon=coupon)


# ═══════════════════════════════════════════════════════════
# 🗑️ ADMIN - DELETE COUPON
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/<int:coupon_id>/delete', methods=['POST'])
@login_required
def admin_delete_coupon(coupon_id):
    """Delete a coupon and its usage records"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Access denied.'})
    
    coupon = Coupon.query.get_or_404(coupon_id)
    code = coupon.code
    
    try:
        # Delete related records
        CouponUsage.query.filter_by(coupon_id=coupon.id).delete()
        CouponUser.query.filter_by(coupon_id=coupon.id).delete()
        db.session.delete(coupon)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Coupon "{code}" deleted!'})
    except Exception as e:
        db.session.rollback()
        print(f"Delete coupon error: {e}")
        return jsonify({'success': False, 'message': str(e)})


# ═══════════════════════════════════════════════════════════
# 📊 ADMIN - COUPON ANALYTICS
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/analytics')
@login_required
def admin_coupon_analytics():
    """Full coupon analytics page"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    # Global stats
    total_coupons = Coupon.query.count()
    active_coupons = Coupon.query.filter_by(is_active=True).count()
    total_usages = CouponUsage.query.count()
    total_discount = db.session.query(func.sum(CouponUsage.discount_applied)).scalar() or 0
    total_revenue_with_coupons = db.session.query(func.sum(CouponUsage.final_amount)).scalar() or 0
    
    # Coupon type breakdown
    universal_count = Coupon.query.filter_by(coupon_type='universal').count()
    specific_count = Coupon.query.filter_by(coupon_type='specific').count()
    influencer_count = Coupon.query.filter_by(coupon_type='influencer').count()
    
    # Influencer stats
    influencer_coupons = Coupon.query.filter_by(coupon_type='influencer').all()
    influencer_stats = []
    for c in influencer_coupons:
        usage_count = CouponUsage.query.filter_by(coupon_id=c.id).count()
        total_disc = db.session.query(func.sum(CouponUsage.discount_applied)).filter_by(coupon_id=c.id).scalar() or 0
        influencer_stats.append({
            'coupon': c,
            'usage_count': usage_count,
            'total_discount': round(total_disc, 2)
        })
    
    # 🆕 FIXED: Top coupons - Convert Row objects to serializable dicts
    top_coupons_raw = db.session.query(
        Coupon.id,
        Coupon.code, 
        Coupon.discount_type, 
        Coupon.discount_value, 
        Coupon.coupon_type,
        Coupon.max_uses,
        Coupon.expires_at,
        Coupon.is_active,
        func.count(CouponUsage.id).label('usage_count'),
        func.coalesce(func.sum(CouponUsage.discount_applied), 0).label('total_discount')
    ).join(CouponUsage, Coupon.id == CouponUsage.coupon_id)\
        .group_by(Coupon.id)\
        .order_by(func.count(CouponUsage.id).desc()).limit(10).all()
    
    top_coupons = []
    for row in top_coupons_raw:
        top_coupons.append({
            'id': row.id,
            'code': row.code,
            'discount_type': row.discount_type,
            'discount_value': row.discount_value,
            'coupon_type': row.coupon_type,
            'max_uses': row.max_uses,
            'expires_at': row.expires_at.isoformat() if row.expires_at else None,
            'is_active': row.is_active,
            'usage_count': row.usage_count,
            'total_discount': float(row.total_discount)
        })
    
    # Monthly trend (last 12 months)
    monthly_trend = []
    for i in range(12):
        month_start = date.today().replace(day=1) - timedelta(days=30 * i)
        month_start = month_start.replace(day=1)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1, day=1) - timedelta(days=1)
        
        count = CouponUsage.query.filter(
            func.date(CouponUsage.used_at) >= month_start,
            func.date(CouponUsage.used_at) <= month_end
        ).count()
        
        disc = db.session.query(func.sum(CouponUsage.discount_applied)).filter(
            func.date(CouponUsage.used_at) >= month_start,
            func.date(CouponUsage.used_at) <= month_end
        ).scalar() or 0
        
        monthly_trend.append({
            'month': month_start.strftime('%b %Y'),
            'count': count,
            'discount': round(disc, 2)
        })
    monthly_trend.reverse()
    
    return render_template('admin/coupons/analytics.html',
        total_coupons=total_coupons,
        active_coupons=active_coupons,
        total_usages=total_usages,
        total_discount=round(total_discount, 2),
        total_revenue=round(total_revenue_with_coupons, 2),
        universal_count=universal_count,
        specific_count=specific_count,
        influencer_count=influencer_count,
        influencer_stats=influencer_stats,
        top_coupons=top_coupons,
        monthly_trend=monthly_trend
    )


# ═══════════════════════════════════════════════════════════
# 📊 ADMIN - INFLUENCER DETAIL
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/admin/coupons/influencer/<int:coupon_id>')
@login_required
def admin_influencer_detail(coupon_id):
    """Detailed analytics for an influencer coupon"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('user.dashboard'))
    
    coupon = Coupon.query.get_or_404(coupon_id)
    
    if coupon.coupon_type != 'influencer':
        flash('This is not an influencer coupon.', 'warning')
        return redirect(url_for('coupon.admin_coupons'))
    
    usages = CouponUsage.query.filter_by(coupon_id=coupon.id).order_by(CouponUsage.used_at.desc()).all()
    
    # Stats
    total_uses = len(usages)
    total_discount = sum(u.discount_applied for u in usages)
    total_revenue = sum(u.final_amount for u in usages)
    unique_users = len(set(u.user_id for u in usages))
    
    return render_template('admin/coupons/influencer_detail.html',
        coupon=coupon,
        usages=usages,
        total_uses=total_uses,
        total_discount=round(total_discount, 2),
        total_revenue=round(total_revenue, 2),
        unique_users=unique_users
    )


# ═══════════════════════════════════════════════════════════
# 👤 USER - MY COUPONS
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/my-coupons')
@login_required
def my_coupons():
    """Show user's available coupons"""
    
    # Get universal active coupons
    universal_coupons = Coupon.query.filter_by(
        coupon_type='universal',
        is_active=True
    ).all()
    
    # Filter valid universal coupons
    valid_universal = []
    for c in universal_coupons:
        can_use, _ = c.can_be_used_by(current_user)
        if can_use:
            valid_universal.append(c)
    
    # Get specific coupons assigned to this user
    specific_coupons = Coupon.query.join(CouponUser).filter(
        CouponUser.user_id == current_user.id,
        Coupon.is_active == True
    ).all()
    
    valid_specific = []
    for c in specific_coupons:
        can_use, _ = c.can_be_used_by(current_user)
        if can_use:
            valid_specific.append(c)
    
    # Get usage history
    usages = CouponUsage.query.filter_by(user_id=current_user.id)\
        .order_by(CouponUsage.used_at.desc()).limit(20).all()
    
    return render_template('user/coupon/my_coupons.html',
        universal_coupons=valid_universal,
        specific_coupons=valid_specific,
        usages=usages
    )


# ═══════════════════════════════════════════════════════════
# 👤 USER - COUPON DETAIL
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/coupon/<int:coupon_id>')
@login_required
def coupon_detail(coupon_id):
    """View a single coupon's details"""
    coupon = Coupon.query.get_or_404(coupon_id)
    
    # Check if user can see this coupon
    if coupon.coupon_type == 'influencer':
        flash('Coupon not found.', 'danger')
        return redirect(url_for('coupon.my_coupons'))
    
    if coupon.coupon_type == 'specific':
        allowed = CouponUser.query.filter_by(
            coupon_id=coupon.id,
            user_id=current_user.id
        ).first()
        if not allowed:
            flash('Coupon not found.', 'danger')
            return redirect(url_for('coupon.my_coupons'))
    
    can_use, message = coupon.can_be_used_by(current_user)
    
    # Get user's usage of this coupon
    user_usage = CouponUsage.query.filter_by(
        coupon_id=coupon.id,
        user_id=current_user.id
    ).first()
    
    return render_template('user/coupon/coupon_detail.html',
        coupon=coupon,
        can_use=can_use,
        message=message,
        user_usage=user_usage
    )


# ═══════════════════════════════════════════════════════════
# 🔌 API - VALIDATE COUPON (for checkout AJAX)
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/api/validate', methods=['POST'])
@login_required
def api_validate_coupon():
    """Validate a coupon code and return discount info"""
    data = request.get_json()
    code = data.get('code', '').strip().upper()
    order_amount = float(data.get('order_amount', 0))
    
    if not code:
        return jsonify({'success': False, 'message': 'Please enter a coupon code.'})
    
    coupon = Coupon.query.filter_by(code=code).first()
    
    if not coupon:
        return jsonify({'success': False, 'message': 'Invalid coupon code.'})
    
    can_use, message = coupon.can_be_used_by(current_user)
    if not can_use:
        return jsonify({'success': False, 'message': message})
    
    if order_amount < coupon.min_order_amount:
        return jsonify({
            'success': False,
            'message': f'Minimum order amount of ₹{coupon.min_order_amount} required.'
        })
    
    discount = coupon.calculate_discount(order_amount)
    final_amount = round(order_amount - discount, 2)
    
    return jsonify({
        'success': True,
        'message': f'Coupon applied! {coupon.get_discount_display()}',
        'coupon_id': coupon.id,
        'code': coupon.code,
        'discount_type': coupon.discount_type,
        'discount_value': coupon.discount_value,
        'discount_amount': round(discount, 2),
        'original_amount': order_amount,
        'final_amount': final_amount,
        'discount_display': coupon.get_discount_display()
    })


# ═══════════════════════════════════════════════════════════
# 🔌 API - GET USER'S AVAILABLE COUPONS (for checkout page)
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/api/my-available')
@login_required
def api_my_available_coupons():
    """Get user's available coupons for quick apply on checkout page"""
    
    # Get universal active coupons
    universal = Coupon.query.filter_by(
        coupon_type='universal',
        is_active=True
    ).all()
    
    # Get specific coupons assigned to this user
    specific = Coupon.query.join(CouponUser).filter(
        CouponUser.user_id == current_user.id,
        Coupon.is_active == True
    ).all()
    
    all_coupons = universal + specific
    
    result = []
    for c in all_coupons:
        can_use, _ = c.can_be_used_by(current_user)
        if can_use:
            result.append({
                'id': c.id,
                'code': c.code,
                'discount_type': c.discount_type,
                'discount_value': c.discount_value,
                'discount_display': c.get_discount_display(),
                'coupon_type': c.coupon_type,
                'expires_at': c.expires_at.isoformat() if c.expires_at else None
            })
    
    return jsonify({'coupons': result})


# ═══════════════════════════════════════════════════════════
# 🔌 API - USER SEARCH (for admin coupon creation)
# ═══════════════════════════════════════════════════════════

@coupon_bp.route('/api/search-users')
@login_required
def api_search_users():
    """Search users for specific coupon assignment"""
    if not current_user.is_admin:
        return jsonify({'users': []})
    
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify({'users': []})
    
    users = User.query.filter(
        db.or_(
            User.email.ilike(f'%{query}%'),
            User.username.ilike(f'%{query}%'),
            User.phone_number.ilike(f'%{query}%')
        )
    ).limit(20).all()
    
    return jsonify({
        'users': [{
            'id': u.id,
            'email': u.email,
            'username': u.username,
            'phone': u.phone_number or '',
            'full_name': u.full_name
        } for u in users]
    })