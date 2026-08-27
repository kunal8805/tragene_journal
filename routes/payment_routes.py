"""
Payment Routes - India Only (Cashfree Integration)
Handles: Checkout page, order creation, webhook verification, payment status
Supports: India (₹ INR via Cashfree)
Security: Blocks unverified users from purchasing
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import User, Subscription, Payment, PlanPrice, Coupon, CouponUsage, CouponUser
from datetime import datetime, timedelta
import json
import os
import hashlib
import hmac
import base64
import requests

payment_bp = Blueprint('payment', __name__, url_prefix='/payment')

# ═══════════════════════════════════════════════════════════
# 💰 PLAN PRICING (India Only - INR)
# ═══════════════════════════════════════════════════════════

PRICING = {
    'pro': {'monthly': 399, 'yearly': 3990},
    'elite': {'monthly': 799, 'yearly': 7990},
    'currency': 'INR', 'symbol': '₹'
}

GATEWAY_FEE_PERCENT = 2

# Cashfree config
CASHFREE_APP_ID = os.getenv('CASHFREE_APP_ID', '')
CASHFREE_SECRET_KEY = os.getenv('CASHFREE_SECRET_KEY', '')
CASHFREE_API_URL = 'https://api.cashfree.com/pg'


# ═══════════════════════════════════════════════════════════
# 💰 GET PRICING
# ═══════════════════════════════════════════════════════════

def get_pricing():
    """Get pricing from database or fallback to hardcoded"""
    try:
        plans = PlanPrice.query.filter_by(is_active=True, currency='INR').order_by(PlanPrice.sort_order).all()
        if plans:
            pricing = {'currency': 'INR', 'symbol': '₹', 'gateway': 'cashfree', 'gateway_name': 'Cashfree'}
            for plan in plans:
                if plan.plan_tier not in pricing:
                    pricing[plan.plan_tier] = {}
                pricing[plan.plan_tier][plan.plan_type] = plan.total_price
                pricing[plan.plan_tier][plan.plan_type + '_base'] = plan.price
            return pricing
    except Exception as e:
        print(f"⚠️ Could not load prices from DB: {e}")
    
    return PRICING


# ═══════════════════════════════════════════════════════════
# 🛒 CHECKOUT PAGE
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/checkout/<plan_tier>/<plan_type>')
@login_required
def checkout(plan_tier, plan_type):
    """Render checkout page"""
    if not current_user.email_verified:
        flash('Please verify your email before purchasing a plan. Check your inbox or go to Settings to resend the verification email.', 'warning')
        return redirect(url_for('user.settings'))
    
    pricing = get_pricing()
    
    # Get valid plan types from pricing
    valid_types = {k for k in pricing.get(plan_tier, {}).keys() if not k.endswith('_base')}
    if plan_tier not in pricing or plan_type not in valid_types:
        flash('Invalid plan selected.', 'danger')
        return redirect(url_for('user.subscription'))
    
    if current_user.subscription_tier == plan_tier and current_user.subscription_active:
        flash(f'You are already on the {plan_tier.upper()} plan.', 'info')
        return redirect(url_for('user.subscription'))
    
    # Get plan details from database if available
    plan = PlanPrice.query.filter_by(plan_tier=plan_tier, plan_type=plan_type, is_active=True, currency='INR').first()
    
    if plan:
        base_price = plan.price
        gateway_fee_percent = plan.gateway_fee_percent
        gateway_fee = round(base_price * gateway_fee_percent / 100, 2)
        total_price = plan.total_price
        plan_name = plan.plan_name or plan_tier.upper()
    else:
        base_price = pricing[plan_tier][plan_type]
        gateway_fee = round(base_price * GATEWAY_FEE_PERCENT / 100, 2)
        total_price = round(base_price + gateway_fee, 2)
        plan_name = plan_tier.upper()
        gateway_fee_percent = GATEWAY_FEE_PERCENT
    
    plan_label = plan_type.replace('_', ' ').title()
    if plan_type == 'yearly':
        plan_label = 'Yearly (2 Months Free!)'
    elif plan_type == 'quarterly':
        plan_label = 'Quarterly'
    elif plan_type == 'half_yearly':
        plan_label = '6 Months'
    
    return render_template('user/payment/checkout.html',
        plan_tier=plan_tier, plan_type=plan_type,
        plan_name=plan_name, plan_label=plan_label,
        base_price=base_price, gateway_fee=gateway_fee,
        total_price=total_price, total_paise=int(total_price * 100),
        currency='INR', symbol='₹',
        is_india=True, gateway='cashfree',
        gateway_name='Cashfree', country='IN'
    )


# ═══════════════════════════════════════════════════════════
# 📦 CREATE ORDER
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/create-order', methods=['POST'])
@login_required
def create_order():
    """Create a payment order with Cashfree"""
    if not current_user.email_verified:
        return jsonify({'success': False, 'message': 'Please verify your email before purchasing. Go to Settings to verify.'})
    
    data = request.get_json()
    plan_tier = data.get('plan_tier')
    plan_type = data.get('plan_type')
    coupon_code = data.get('coupon_code', '').strip().upper()
    coupon_id = data.get('coupon_id')
    
    # 🆕 Check if purchases are blocked (maintenance mode)
    from models import check_purchase_blocked
    is_blocked, block_msg = check_purchase_blocked(current_user, plan_tier)
    if is_blocked:
        return jsonify({'success': False, 'message': block_msg or 'Purchases are temporarily unavailable. Please try again later.'})
    
    pricing = get_pricing()
    
    valid_types = {k for k in pricing.get(plan_tier, {}).keys() if not k.endswith('_base')}
    if plan_tier not in pricing or plan_type not in valid_types:
        return jsonify({'success': False, 'message': 'Invalid plan.'})
    
    # Check for existing pending payments (cancel them)
    existing_pending = Payment.query.filter_by(
        user_id=current_user.id,
        status='PENDING'
    ).filter(
        Payment.created_at >= datetime.utcnow() - timedelta(minutes=30)
    ).all()
    
    for old_payment in existing_pending:
        if old_payment.cashfree_session_id:
            status_data = _get_cashfree_order_status(old_payment.cashfree_order_id)
            if status_data and status_data.get('order_status') == 'PAID':
                old_payment.status = 'SUCCESS'
                old_payment.payment_completed_at = datetime.utcnow()
                db.session.commit()
                _activate_subscription(old_payment.user_id, old_payment.plan_tier, old_payment.plan_type)
                # Record coupon if any
                if old_payment.coupon_id:
                    _finalize_coupon_usage(old_payment)
                return jsonify({
                    'success': True,
                    'message': 'Payment already completed!',
                    'redirect': url_for('payment.success', order_id=old_payment.cashfree_order_id)
                })
        
        # Rollback any coupon usage on cancelled pending payments
        if old_payment.coupon_id:
            _rollback_coupon_usage(old_payment)
        
        old_payment.status = 'CANCELLED'
        old_payment.error_message = 'User initiated new payment.'
    
    if existing_pending:
        db.session.commit()
    
    # Get price from database
    plan = PlanPrice.query.filter_by(plan_tier=plan_tier, plan_type=plan_type, is_active=True, currency='INR').first()
    
    if plan:
        base_price = plan.price
        gateway_fee_percent = plan.gateway_fee_percent
        gateway_fee = round(base_price * gateway_fee_percent / 100, 2)
        total_price = plan.total_price
    else:
        base_price = pricing[plan_tier][plan_type]
        gateway_fee = round(base_price * GATEWAY_FEE_PERCENT / 100, 2)
        total_price = round(base_price + gateway_fee, 2)
    
    # ═══════════════════════════════════════════════════════
    # 🎟️ COUPON VALIDATION (No usage recording yet!)
    # ═══════════════════════════════════════════════════════
    coupon_discount = 0
    applied_coupon = None
    original_total = total_price
    
    if coupon_code and coupon_id:
        try:
            applied_coupon = Coupon.query.get(int(coupon_id))
            
            if applied_coupon and applied_coupon.code.upper() == coupon_code.upper():
                # Validate coupon
                can_use, message = applied_coupon.can_be_used_by(current_user)
                
                if not can_use:
                    return jsonify({'success': False, 'message': f'Coupon error: {message}'})
                
                # Check minimum order
                if total_price < applied_coupon.min_order_amount:
                    return jsonify({
                        'success': False,
                        'message': f'Minimum order of ₹{applied_coupon.min_order_amount} required for this coupon.'
                    })
                
                # Calculate discount (but DON'T record usage yet!)
                coupon_discount = applied_coupon.calculate_discount(total_price)
                total_price = round(total_price - coupon_discount, 2)
                
                if total_price < 0:
                    total_price = 0
                    coupon_discount = original_total
                
                print(f"🎟️ Coupon validated: {applied_coupon.code} | Discount: ₹{coupon_discount} | Final: ₹{total_price}")
            else:
                return jsonify({'success': False, 'message': 'Invalid coupon.'})
                
        except Exception as e:
            print(f"❌ Coupon validation error: {e}")
            return jsonify({'success': False, 'message': 'Error validating coupon. Please try again.'})
    
    total_paise = int(total_price * 100)
    
    order_id = f"TRADEJ_{current_user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    # 🆕 Store coupon info in payment record (for later use after payment success)
    payment = Payment(
        user_id=current_user.id,
        cashfree_order_id=order_id,
        base_amount=int(base_price * 100),
        gateway_fee=int(gateway_fee * 100),
        total_amount=total_paise,
        currency='INR',
        plan_tier=plan_tier,
        plan_type=plan_type,
        status='PENDING',
        coupon_id=applied_coupon.id if applied_coupon else None,
        coupon_code=coupon_code if applied_coupon else None,
        coupon_discount=coupon_discount if applied_coupon else 0
    )
    db.session.add(payment)
    db.session.commit()
    
    # Create Cashfree order
    result = _create_cashfree_order(order_id, total_paise, current_user)
    if result and result.get('payment_session_id'):
        payment.cashfree_session_id = result['payment_session_id']
        db.session.commit()
        
        response_data = {
            'success': True,
            'payment_session_id': result['payment_session_id'],
            'order_id': order_id,
            'gateway': 'cashfree'
        }
        
        # Include coupon info in response
        if applied_coupon and coupon_discount > 0:
            response_data['coupon_applied'] = True
            response_data['coupon_code'] = applied_coupon.code
            response_data['discount_amount'] = coupon_discount
            response_data['original_amount'] = original_total
            response_data['final_amount'] = total_price
        
        return jsonify(response_data)
    
    # Payment creation failed - no coupon to rollback since we didn't record it
    payment.status = 'FAILED'
    payment.error_message = 'Failed to create payment session.'
    db.session.commit()
    return jsonify({'success': False, 'message': 'Failed to create payment session. Try again.'})


# ═══════════════════════════════════════════════════════════
# 🔄 CASHFREE WEBHOOK
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/webhook', methods=['POST'])
def webhook():
    """Handle Cashfree webhook notifications"""
    raw_body = request.get_data(as_text=True)
    signature = request.headers.get('x-webhook-signature')
    timestamp = request.headers.get('x-webhook-timestamp')

    if not _verify_webhook_signature(raw_body, timestamp, signature):
        print("❌ Webhook: Invalid signature")
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401

    webhook_data = request.get_json()
    print(f"📨 Webhook received: {json.dumps(webhook_data, indent=2)}")
    
    order_id = webhook_data.get('data', {}).get('order', {}).get('order_id')
    payment_status = webhook_data.get('data', {}).get('payment', {}).get('payment_status')
    cf_payment_id = webhook_data.get('data', {}).get('payment', {}).get('cf_payment_id')
    
    if not order_id:
        return jsonify({'status': 'error', 'message': 'No order_id in webhook'}), 400
    
    payment = Payment.query.filter_by(cashfree_order_id=order_id).first()
    if not payment:
        return jsonify({'status': 'error', 'message': 'Order not found'}), 404
    
    if payment.status == 'SUCCESS':
        return jsonify({'status': 'ok', 'message': 'Already processed'})
    
    payment.cashfree_payment_id = cf_payment_id
    payment.webhook_response = json.dumps(webhook_data)
    
    if payment_status == 'SUCCESS':
        payment.status = 'SUCCESS'
        payment.payment_completed_at = datetime.utcnow()
        db.session.commit()
        
        # 🆕 NOW record coupon usage - payment is confirmed!
        if payment.coupon_id:
            _finalize_coupon_usage(payment)
        
        _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
        print(f"✅ Payment SUCCESS via webhook: {order_id}")
        
    elif payment_status == 'FAILED':
        payment.status = 'FAILED'
        payment.error_message = 'Payment failed at gateway.'
        # 🆕 Rollback coupon if payment failed
        if payment.coupon_id:
            _rollback_coupon_usage(payment)
        db.session.commit()
        print(f"❌ Payment FAILED via webhook: {order_id}")
        
    elif payment_status == 'USER_DROPPED':
        payment.status = 'CANCELLED'
        payment.error_message = 'User abandoned payment.'
        # 🆕 Rollback coupon if user cancelled
        if payment.coupon_id:
            _rollback_coupon_usage(payment)
        db.session.commit()
        print(f"🚫 Payment abandoned via webhook: {order_id}")
    else:
        payment.status = payment_status or 'UNKNOWN'
        db.session.commit()
        print(f"❓ Payment status: {payment_status}")
    
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════
# ✅ SUCCESS / ❌ CANCEL / FAILED
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/success')
@login_required
def success():
    """Handle payment success page - VERIFY PAYMENT BEFORE SHOWING SUCCESS"""
    order_id = request.args.get('order_id', '')
    
    if not order_id:
        flash('Invalid payment session. Please try again.', 'danger')
        return redirect(url_for('user.subscription'))
    
    payment = Payment.query.filter_by(
        user_id=current_user.id,
        cashfree_order_id=order_id
    ).first()
    
    if not payment:
        flash('Payment record not found. Please try again.', 'danger')
        return redirect(url_for('user.subscription'))
    
    if payment.status != 'SUCCESS':
        status_verified = False
        
        if payment.cashfree_session_id and not payment.cashfree_session_id.startswith('mock_'):
            status_data = _get_cashfree_order_status(order_id)
            
            if status_data:
                order_status = status_data.get('order_status')
                
                if order_status == 'PAID':
                    payment.status = 'SUCCESS'
                    payment.payment_completed_at = datetime.utcnow()
                    if 'cf_payment_id' in status_data:
                        payment.cashfree_payment_id = status_data['cf_payment_id']
                    db.session.commit()
                    
                    # 🆕 Record coupon usage on verified payment
                    if payment.coupon_id:
                        _finalize_coupon_usage(payment)
                    
                    _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
                    status_verified = True
                    print(f"✅ Payment verified on success page: {order_id}")
                
                elif order_status == 'ACTIVE':
                    payment.status = 'PENDING'
                    db.session.commit()
                    flash('Payment is still being processed. Please complete the payment or try again.', 'warning')
                    return redirect(url_for('user.subscription'))
                
                else:
                    payment.status = 'FAILED'
                    payment.error_message = f'Payment status: {order_status}'
                    # Rollback coupon
                    if payment.coupon_id:
                        _rollback_coupon_usage(payment)
                    db.session.commit()
                    flash(f'Payment was not completed. Status: {order_status}. Please try again.', 'danger')
                    return redirect(url_for('user.subscription'))
        
        if not status_verified and payment.cashfree_session_id and payment.cashfree_session_id.startswith('mock_'):
            payment.status = 'SUCCESS'
            payment.payment_completed_at = datetime.utcnow()
            db.session.commit()
            
            # 🆕 Record coupon usage for mock payments too
            if payment.coupon_id:
                _finalize_coupon_usage(payment)
            
            _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
            status_verified = True
            print(f"✅ Mock payment verified: {order_id}")
        
        if not status_verified:
            flash('Could not verify payment status. Please contact support if you were charged.', 'warning')
            return redirect(url_for('user.subscription'))
    
    return render_template('user/payment/success.html',
        payment=payment,
        plan_name=payment.plan_tier.upper())


@payment_bp.route('/cancel')
@login_required
def cancel():
    return render_template('user/payment/cancel.html')


@payment_bp.route('/failed')
@login_required
def failed():
    return render_template('user/payment/failed.html')


# ═══════════════════════════════════════════════════════════
# 📊 PAYMENT STATUS CHECK (AJAX)
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/check-status/<order_id>')
@login_required
def check_payment_status(order_id):
    payment = Payment.query.filter_by(
        user_id=current_user.id,
        cashfree_order_id=order_id
    ).first()
    
    if not payment:
        return jsonify({'status': 'NOT_FOUND'})
    
    if payment.status == 'PENDING' and payment.cashfree_session_id and not payment.cashfree_session_id.startswith('mock_'):
        status_data = _get_cashfree_order_status(order_id)
        if status_data:
            order_status = status_data.get('order_status')
            
            if order_status == 'PAID':
                payment.status = 'SUCCESS'
                payment.payment_completed_at = datetime.utcnow()
                if 'cf_payment_id' in status_data:
                    payment.cashfree_payment_id = status_data['cf_payment_id']
                db.session.commit()
                
                # 🆕 Record coupon on status check success
                if payment.coupon_id:
                    _finalize_coupon_usage(payment)
                
                _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
                return jsonify({'status': 'SUCCESS'})
            
            elif order_status != 'ACTIVE':
                payment.status = 'FAILED'
                payment.error_message = f'Payment status: {order_status}'
                # Rollback coupon on failure
                if payment.coupon_id:
                    _rollback_coupon_usage(payment)
                db.session.commit()
                return jsonify({'status': 'FAILED', 'message': f'Payment {order_status.lower()}'})
    
    return jsonify({'status': payment.status})


# ═══════════════════════════════════════════════════════════
# 🛡️ PAYMENT VERIFICATION (Manual)
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/verify-payment/<order_id>')
@login_required
def verify_payment(order_id):
    payment = Payment.query.filter_by(
        user_id=current_user.id,
        cashfree_order_id=order_id
    ).first()
    
    if not payment:
        return jsonify({'success': False, 'message': 'Payment not found'})
    
    if payment.status == 'SUCCESS':
        return jsonify({'success': True, 'message': 'Payment already confirmed'})
    
    if payment.cashfree_session_id and not payment.cashfree_session_id.startswith('mock_'):
        status_data = _get_cashfree_order_status(order_id)
        if status_data:
            order_status = status_data.get('order_status')
            
            if order_status == 'PAID':
                payment.status = 'SUCCESS'
                payment.payment_completed_at = datetime.utcnow()
                if 'cf_payment_id' in status_data:
                    payment.cashfree_payment_id = status_data['cf_payment_id']
                db.session.commit()
                
                # 🆕 Record coupon
                if payment.coupon_id:
                    _finalize_coupon_usage(payment)
                
                _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
                return jsonify({'success': True, 'message': 'Payment verified and activated!'})
            
            elif order_status == 'ACTIVE':
                return jsonify({'success': False, 'message': 'Payment still pending. Complete the payment first.'})
            
            else:
                payment.status = 'FAILED'
                payment.error_message = f'Payment status: {order_status}'
                if payment.coupon_id:
                    _rollback_coupon_usage(payment)
                db.session.commit()
                return jsonify({'success': False, 'message': f'Payment {order_status.lower()}'})
    
    return jsonify({'success': False, 'message': 'Could not verify payment. Contact support.'})


# ═══════════════════════════════════════════════════════════
# 🎟️ COUPON HELPERS
# ═══════════════════════════════════════════════════════════

def _finalize_coupon_usage(payment):
    """Record coupon usage AFTER successful payment"""
    if not payment.coupon_id:
        return
    
    try:
        coupon = Coupon.query.get(payment.coupon_id)
        if not coupon:
            print(f"⚠️ Coupon not found: {payment.coupon_id}")
            return
        
        # Check if usage already recorded
        existing = CouponUsage.query.filter_by(payment_id=payment.id).first()
        if existing:
            print(f"⚠️ Coupon usage already recorded for payment {payment.id}")
            return
        
        # Create usage record
        usage = CouponUsage(
            coupon_id=coupon.id,
            user_id=payment.user_id,
            payment_id=payment.id,
            order_amount=payment.base_amount / 100 if payment.base_amount else 0,
            discount_applied=payment.coupon_discount or 0,
            final_amount=payment.total_amount / 100 if payment.total_amount else 0,
            plan_purchased=f"{payment.plan_tier}_{payment.plan_type}" if payment.plan_tier else None,
            ip_address=request.headers.get('X-Forwarded-For', request.remote_addr) if request else None
        )
        db.session.add(usage)
        
        # Update coupon usage count
        coupon.used_count = (coupon.used_count or 0) + 1
        
        # Mark specific coupon as used for this user
        if coupon.coupon_type == 'specific':
            cu = CouponUser.query.filter_by(
                coupon_id=coupon.id,
                user_id=payment.user_id
            ).first()
            if cu:
                cu.is_used = True
        
        db.session.commit()
        print(f"✅ Coupon '{coupon.code}' usage finalized for payment {payment.id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Failed to finalize coupon usage: {e}")


def _rollback_coupon_usage(payment):
    """Rollback coupon usage when payment fails/cancels"""
    if not payment.coupon_id:
        return
    
    try:
        coupon = Coupon.query.get(payment.coupon_id)
        if not coupon:
            return
        
        # Remove usage record if it exists
        usage = CouponUsage.query.filter_by(payment_id=payment.id).first()
        if usage:
            db.session.delete(usage)
        
        # Decrement usage count
        if coupon.used_count and coupon.used_count > 0:
            coupon.used_count -= 1
        
        # Unmark specific coupon
        if coupon.coupon_type == 'specific':
            cu = CouponUser.query.filter_by(
                coupon_id=coupon.id,
                user_id=payment.user_id
            ).first()
            if cu:
                cu.is_used = False
        
        db.session.commit()
        print(f"🔄 Coupon '{coupon.code}' usage rolled back for payment {payment.id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Failed to rollback coupon usage: {e}")


# ═══════════════════════════════════════════════════════════
# 🔧 CASHFREE HELPERS
# ═══════════════════════════════════════════════════════════

def _create_cashfree_order(order_id, amount_paise, user):
    """Create Cashfree order - INR only"""
    if not CASHFREE_APP_ID or not CASHFREE_SECRET_KEY:
        print("⚠️ Cashfree not configured. Using mock.")
        return {'payment_session_id': f'mock_{order_id}'}
    
    headers = {
        'Content-Type': 'application/json',
        'x-api-version': '2023-08-01',
        'x-client-id': CASHFREE_APP_ID,
        'x-client-secret': CASHFREE_SECRET_KEY
    }
    
    payload = {
        'order_id': order_id,
        'order_amount': amount_paise / 100,
        'order_currency': 'INR',
        'customer_details': {
            'customer_id': str(user.id),
            'customer_name': user.full_name or user.username or 'User',
            'customer_email': user.email,
            'customer_phone': '9999999999'
        },
        'order_meta': {
            'return_url': f"{request.host_url}payment/success?order_id={order_id}",
            'notify_url': f"{request.host_url}payment/webhook"
        }
    }
    
    try:
        print(f"🔄 Creating Cashfree order: {order_id} (₹{amount_paise/100})")
        response = requests.post(f"{CASHFREE_API_URL}/orders", headers=headers, json=payload, timeout=10)
        response_data = response.json()
        print(f"📦 Cashfree response: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ Cashfree error: {response_data}")
            return None
        
        return response_data
    except Exception as e:
        print(f"❌ Cashfree Error: {e}")
        return None


def _verify_webhook_signature(raw_body, timestamp, signature):
    """Verify Cashfree webhook signature"""
    if not CASHFREE_SECRET_KEY:
        print("⚠️ Webhook verification skipped - no secret key configured")
        return True
    if not signature or not timestamp:
        print("❌ Missing signature or timestamp in webhook")
        return False
    
    try:
        payload = timestamp + raw_body
        computed = hmac.new(CASHFREE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
        computed_b64 = base64.b64encode(computed).decode()
        is_valid = hmac.compare_digest(computed_b64, signature)
        
        if not is_valid:
            print("❌ Webhook signature verification FAILED")
        
        return is_valid
    except Exception as e:
        print(f"❌ Webhook signature verification error: {e}")
        return False


def _get_cashfree_order_status(order_id):
    """Check Cashfree order status"""
    if not CASHFREE_APP_ID or not CASHFREE_SECRET_KEY:
        print("⚠️ Cashfree not configured - cannot check order status")
        return None
    
    headers = {
        'x-api-version': '2023-08-01',
        'x-client-id': CASHFREE_APP_ID,
        'x-client-secret': CASHFREE_SECRET_KEY
    }
    
    try:
        response = requests.get(f"{CASHFREE_API_URL}/orders/{order_id}", headers=headers, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Cashfree status check failed: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Cashfree Order Status Error: {e}")
        return None


def _activate_subscription(user_id, plan_tier, plan_type):
    """Activate user's subscription after successful payment"""
    try:
        user = User.query.get(user_id)
        if not user:
            print(f"❌ User not found: {user_id}")
            return False
        
        # Calculate end date based on plan type
        duration_map = {
            'monthly': 30,
            'quarterly': 90,
            'half_yearly': 180,
            'yearly': 365,
            'lifetime': 36500
        }
        days = duration_map.get(plan_type, 30)
        end_date = datetime.utcnow() + timedelta(days=days)
        
        sub = Subscription.query.filter_by(user_id=user_id).first()
        if not sub:
            sub = Subscription(user_id=user_id)
            db.session.add(sub)
            db.session.flush()
        
        sub.plan_tier = plan_tier
        sub.plan_type = plan_type
        sub.start_date = datetime.utcnow()
        sub.end_date = end_date
        sub.is_active = True
        sub.auto_renew = True
        
        user.subscription_tier = plan_tier
        user.subscription_active = True
        
        db.session.commit()
        print(f"✅ Subscription activated: User {user_id} → {plan_tier} ({plan_type})")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Subscription activation error: {e}")
        return False