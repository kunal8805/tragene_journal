"""
Payment Routes - Multi-Currency Cashfree Integration
Handles: Checkout page, order creation, webhook verification, payment status
Supports: India (₹ INR via Cashfree) + International ($ USD via Stripe)
Security: Blocks unverified users from purchasing
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import User, Subscription, Payment
from datetime import datetime, timedelta
import json
import os
import hashlib
import hmac
import base64
import requests

payment_bp = Blueprint('payment', __name__, url_prefix='/payment')

# ═══════════════════════════════════════════════════════════
# 💰 MULTI-CURRENCY PLAN PRICING
# ═══════════════════════════════════════════════════════════

PLAN_PRICES = {
    'IN': {
        'pro': {'monthly': 399, 'yearly': 3999},
        'elite': {'monthly': 799, 'yearly': 7999},
        'currency': 'INR',
        'symbol': '₹',
        'gateway': 'cashfree',
        'gateway_name': 'Cashfree'
    },
    'US': {
        'pro': {'monthly': 5, 'yearly': 49},
        'elite': {'monthly': 10, 'yearly': 99},
        'currency': 'USD',
        'symbol': '$',
        'gateway': 'stripe',
        'gateway_name': 'Stripe'
    },
    'GB': {
        'pro': {'monthly': 4, 'yearly': 39},
        'elite': {'monthly': 8, 'yearly': 79},
        'currency': 'USD',
        'symbol': '$',
        'gateway': 'stripe',
        'gateway_name': 'Stripe'
    },
    'EU': {
        'pro': {'monthly': 5, 'yearly': 49},
        'elite': {'monthly': 10, 'yearly': 99},
        'currency': 'USD',
        'symbol': '$',
        'gateway': 'stripe',
        'gateway_name': 'Stripe'
    },
    'DEFAULT': {
        'pro': {'monthly': 5, 'yearly': 49},
        'elite': {'monthly': 10, 'yearly': 99},
        'currency': 'USD',
        'symbol': '$',
        'gateway': 'stripe',
        'gateway_name': 'Stripe'
    }
}

GATEWAY_FEE_PERCENT = 2

# Cashfree config
CASHFREE_APP_ID = os.getenv('CASHFREE_APP_ID', '')
CASHFREE_SECRET_KEY = os.getenv('CASHFREE_SECRET_KEY', '')
CASHFREE_API_URL = 'https://api.cashfree.com/pg'

# Stripe config
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')

# ═══════════════════════════════════════════════════════════
# 🌍 COUNTRY DETECTION
# ═══════════════════════════════════════════════════════════

def get_user_country():
    country = request.cookies.get('country')
    if country and country in PLAN_PRICES:
        return country
    try:
        country = request.headers.get('CF-IPCountry')
        if country and country in PLAN_PRICES:
            return country
        response = requests.get('https://ipapi.co/json/', timeout=3)
        data = response.json()
        country = data.get('country_code', 'IN')
        return country if country in PLAN_PRICES else 'DEFAULT'
    except:
        return 'IN'


def get_pricing_for_user():
    country = get_user_country()
    pricing = PLAN_PRICES.get(country, PLAN_PRICES['DEFAULT'])
    return pricing, country


# ═══════════════════════════════════════════════════════════
# 🛒 CHECKOUT PAGE
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/checkout/<plan_tier>/<plan_type>')
@login_required
def checkout(plan_tier, plan_type):
    # 🔒 Block unverified users from purchasing
    if not current_user.email_verified:
        flash('Please verify your email before purchasing a plan. Check your inbox or go to Settings to resend the verification email.', 'warning')
        return redirect(url_for('user.settings'))
    
    pricing, country = get_pricing_for_user()
    
    if plan_tier not in pricing or plan_type not in ['monthly', 'yearly']:
        flash('Invalid plan selected.', 'danger')
        return redirect(url_for('user.subscription'))
    
    if current_user.subscription_tier == plan_tier:
        flash(f'You are already on the {plan_tier.upper()} plan.', 'info')
        return redirect(url_for('user.subscription'))
    
    base_price = pricing[plan_tier][plan_type]
    gateway_fee = round(base_price * GATEWAY_FEE_PERCENT / 100, 2)
    total_price = round(base_price + gateway_fee, 2)
    
    plan_name = plan_tier.upper()
    plan_label = 'Monthly' if plan_type == 'monthly' else 'Yearly (Save ~16%)'
    is_india = (country == 'IN')
    
    return render_template('user/payment/checkout.html',
        plan_tier=plan_tier, plan_type=plan_type,
        plan_name=plan_name, plan_label=plan_label,
        base_price=base_price, gateway_fee=gateway_fee,
        total_price=total_price, total_paise=int(total_price * 100),
        currency=pricing['currency'], symbol=pricing['symbol'],
        is_india=is_india, gateway=pricing['gateway'],
        gateway_name=pricing['gateway_name'], country=country
    )


# ═══════════════════════════════════════════════════════════
# 📦 CREATE ORDER
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/create-order', methods=['POST'])
@login_required
def create_order():
    # 🔒 Block unverified users from purchasing
    if not current_user.email_verified:
        return jsonify({'success': False, 'message': 'Please verify your email before purchasing. Go to Settings to verify.'})
    
    data = request.get_json()
    plan_tier = data.get('plan_tier')
    plan_type = data.get('plan_type')
    
    pricing, country = get_pricing_for_user()
    
    if plan_tier not in pricing or plan_type not in ['monthly', 'yearly']:
        return jsonify({'success': False, 'message': 'Invalid plan.'})
    
    base_price = pricing[plan_tier][plan_type]
    gateway_fee = round(base_price * GATEWAY_FEE_PERCENT / 100, 2)
    total_price = round(base_price + gateway_fee, 2)
    total_paise = int(total_price * 100)
    currency = pricing['currency']
    gateway = pricing['gateway']
    
    order_id = f"TRADEJ_{current_user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    payment = Payment(
        user_id=current_user.id, cashfree_order_id=order_id,
        base_amount=int(base_price * 100), gateway_fee=int(gateway_fee * 100),
        total_amount=total_paise, currency=currency,
        plan_tier=plan_tier, plan_type=plan_type, status='PENDING'
    )
    db.session.add(payment)
    db.session.commit()
    
    if gateway == 'cashfree':
        result = _create_cashfree_order(order_id, total_paise, current_user)
        if result and result.get('payment_session_id'):
            payment.cashfree_session_id = result['payment_session_id']
            db.session.commit()
            return jsonify({'success': True, 'payment_session_id': result['payment_session_id'], 'order_id': order_id, 'gateway': 'cashfree'})
    elif gateway == 'stripe':
        result = _create_stripe_session(order_id, total_price, currency, plan_tier, plan_type, current_user)
        if result and result.get('url'):
            return jsonify({'success': True, 'url': result['url'], 'order_id': order_id, 'gateway': 'stripe'})
    
    payment.status = 'FAILED'
    payment.error_message = 'Failed to create payment session.'
    db.session.commit()
    return jsonify({'success': False, 'message': 'Failed to create payment session. Try again.'})


# ═══════════════════════════════════════════════════════════
# 🔄 CASHFREE WEBHOOK
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data(as_text=True)
    signature = request.headers.get('x-webhook-signature')
    timestamp = request.headers.get('x-webhook-timestamp')

    if not _verify_webhook_signature(raw_body, timestamp, signature):
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401

    webhook_data = request.get_json()
    
    order_id = webhook_data.get('data', {}).get('order', {}).get('order_id')
    payment_status = webhook_data.get('data', {}).get('payment', {}).get('payment_status')
    cf_payment_id = webhook_data.get('data', {}).get('payment', {}).get('cf_payment_id')
    
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
        _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
    elif payment_status == 'FAILED':
        payment.status = 'FAILED'
        payment.error_message = 'Payment failed at gateway.'
    else:
        payment.status = payment_status or 'UNKNOWN'
    
    db.session.commit()
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════
# 🔄 STRIPE WEBHOOK
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET', ''))
    except Exception as e:
        print(f"❌ Stripe Webhook Error: {e}")
        return jsonify({'status': 'error'}), 400
    
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        order_id = session.get('client_reference_id')
        payment = Payment.query.filter_by(cashfree_order_id=order_id).first()
        if payment and payment.status != 'SUCCESS':
            payment.status = 'SUCCESS'
            payment.cashfree_payment_id = session.get('id')
            payment.webhook_response = json.dumps(session)
            payment.payment_completed_at = datetime.utcnow()
            _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)
            db.session.commit()
    
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════
# ✅ SUCCESS / ❌ CANCEL / FAILED
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/success')
@login_required
def success():
    order_id = request.args.get('order_id', '')
    payment = Payment.query.filter_by(user_id=current_user.id, cashfree_order_id=order_id).first()

    if payment and payment.status != 'SUCCESS' and payment.cashfree_session_id:
        status_data = _get_cashfree_order_status(order_id)
        if status_data and status_data.get('order_status') == 'PAID':
            payment.status = 'SUCCESS'
            payment.payment_completed_at = datetime.utcnow()
            db.session.commit()
            _activate_subscription(payment.user_id, payment.plan_tier, payment.plan_type)

    return render_template('user/payment/success.html', payment=payment, plan_name=payment.plan_tier.upper() if payment else 'N/A')


@payment_bp.route('/cancel')
@login_required
def cancel():
    return render_template('user/payment/cancel.html')


@payment_bp.route('/failed')
@login_required
def failed():
    return render_template('user/payment/failed.html')


# ═══════════════════════════════════════════════════════════
# 🌍 SET COUNTRY
# ═══════════════════════════════════════════════════════════

@payment_bp.route('/set-country/<country>')
def set_country(country):
    if country in PLAN_PRICES:
        resp = redirect(request.referrer or url_for('user.subscription'))
        resp.set_cookie('country', country, max_age=86400 * 30)
        return resp
    return redirect(url_for('user.subscription'))


# ═══════════════════════════════════════════════════════════
# 🔧 HELPERS
# ═══════════════════════════════════════════════════════════

def _create_cashfree_order(order_id, amount_paise, user):
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
            'customer_name': user.username,
            'customer_email': user.email,
            'customer_phone': '9999999999'
        },
        'order_meta': {
            'return_url': f"{request.host_url}payment/success?order_id={order_id}",
            'notify_url': f"{request.host_url}payment/webhook"
        }
    }
    
    try:
        response = requests.post(f"{CASHFREE_API_URL}/orders", headers=headers, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Cashfree Error: {e}")
        return None


def _create_stripe_session(order_id, amount, currency, plan_tier, plan_type, user):
    if not STRIPE_SECRET_KEY:
        print("⚠️ Stripe not configured.")
        return None
    
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': currency.lower(),
                    'product_data': {
                        'name': f'Tragene Journal {plan_tier.upper()} Plan - {plan_type.title()}',
                        'description': f'{plan_tier.upper()} subscription - {plan_type} billing',
                    },
                    'unit_amount': int(amount * 100),
                },
                'quantity': 1,
            }],
            mode='subscription' if plan_type == 'monthly' else 'payment',
            client_reference_id=order_id,
            customer_email=user.email,
            success_url=f"{request.host_url}payment/success?order_id={order_id}",
            cancel_url=f"{request.host_url}payment/cancel",
        )
        return {'url': session.url}
    except Exception as e:
        print(f"❌ Stripe Error: {e}")
        return None


def _verify_webhook_signature(raw_body, timestamp, signature):
    if not CASHFREE_SECRET_KEY:
        return True
    if not signature or not timestamp:
        return False
    payload = timestamp + raw_body
    computed = hmac.new(CASHFREE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    computed_b64 = base64.b64encode(computed).decode()
    return hmac.compare_digest(computed_b64, signature)


def _get_cashfree_order_status(order_id):
    if not CASHFREE_APP_ID or not CASHFREE_SECRET_KEY:
        return None
    headers = {
        'x-api-version': '2023-08-01',
        'x-client-id': CASHFREE_APP_ID,
        'x-client-secret': CASHFREE_SECRET_KEY
    }
    try:
        response = requests.get(f"{CASHFREE_API_URL}/orders/{order_id}", headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Cashfree Order Status Error: {e}")
        return None


def _activate_subscription(user_id, plan_tier, plan_type):
    user = User.query.get(user_id)
    if not user:
        return
    
    end_date = datetime.utcnow() + (timedelta(days=30) if plan_type == 'monthly' else timedelta(days=365))
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub:
        sub = Subscription(user_id=user_id)
        db.session.add(sub)
    
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