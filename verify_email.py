"""
Email Verification Service
Handles: Sending verification emails via Resend, token generation, verification
"""

from dotenv import load_dotenv
load_dotenv()

import os
import secrets
from datetime import datetime, timedelta
from extensions import db
from models import User, EmailVerification

# Resend config
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
SENDER_EMAIL = os.getenv('SENDER_EMAIL', 'onboarding@resend.dev')
APP_URL = os.getenv('APP_URL', 'http://localhost:5000')

# ═══════════════════════════════════════════════════════════
# 🔑 GENERATE VERIFICATION TOKEN
# ═══════════════════════════════════════════════════════════

def generate_token():
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)


# ═══════════════════════════════════════════════════════════
# 📧 SEND VERIFICATION EMAIL
# ═══════════════════════════════════════════════════════════

def send_verification_email(user, new_email=None):
    """Send email verification link to user"""
    
    # Rate limit: max 3 emails per hour
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent = EmailVerification.query.filter(
        EmailVerification.user_id == user.id,
        EmailVerification.created_at >= one_hour_ago
    ).count()
    
    if recent >= 3:
        return {'success': False, 'message': 'Too many requests. Please wait before requesting another email.'}
    
    # Create token (expires in 24 hours)
    token = generate_token()
    email_to_verify = new_email or user.email
    
    verification = EmailVerification(
        user_id=user.id,
        token=token,
        new_email=new_email,
        type='change' if new_email else 'verify',
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    db.session.add(verification)
    db.session.commit()
    
    # Build verification link - use request.host_url if available
    try:
        from flask import request
        base_url = request.host_url.rstrip('/')
    except:
        base_url = APP_URL
    
    verify_link = f"{base_url}/verify-email/{token}"
    
    # Send via Resend
    if RESEND_API_KEY:
        _send_resend_email(email_to_verify, user.username, verify_link)
    else:
        print(f"⚠️ Resend not configured. Verification link: {verify_link}")
    
    return {'success': True, 'message': 'Verification email sent! Check your inbox.'}


# ═══════════════════════════════════════════════════════════
# ✅ VERIFY EMAIL
# ═══════════════════════════════════════════════════════════

def verify_email_token(token):
    """Verify user's email with token"""
    
    verification = EmailVerification.query.filter_by(token=token, is_used=False).first()
    
    if not verification:
        return {'success': False, 'message': 'Invalid or expired verification link.'}
    
    if verification.expires_at < datetime.utcnow():
        return {'success': False, 'message': 'Verification link has expired. Please request a new one.'}
    
    user = User.query.get(verification.user_id)
    if not user:
        return {'success': False, 'message': 'User not found.'}
    
    # Mark token as used
    verification.is_used = True
    
    # If changing email
    if verification.type == 'change' and verification.new_email:
        existing = User.query.filter_by(email=verification.new_email).first()
        if existing and existing.id != user.id:
            return {'success': False, 'message': 'This email is already in use.'}
        user.email = verification.new_email
    
    # Mark email as verified
    user.email_verified = True
    user.email_verified_at = datetime.utcnow()
    
    db.session.commit()
    
    return {'success': True, 'message': 'Email verified successfully! 🎉'}


# ═══════════════════════════════════════════════════════════
# 📧 RESEND API CALL
# ═══════════════════════════════════════════════════════════

def _send_resend_email(to_email, username, verify_link):
    """Send email via Resend API"""
    
    import requests
    
    
    try:
        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'from': f'Tragene Journal <{SENDER_EMAIL}>',
                'to': [to_email],
                'subject': 'Verify your email for Tragene Journal',
                'html': f"""
                <div style="max-width:500px;margin:auto;font-family:Arial,sans-serif;background:#111820;color:#e6edf3;padding:30px;border-radius:16px;">
                    <h2 style="color:#4f8ef7;">🚀 Tragene Journal</h2>
                    <h3>Verify Your Email</h3>
                    <p>Hey <strong>{username}</strong>! Thanks for signing up.</p>
                    <p>Click the button below to verify your email address:</p>
                    <a href="{verify_link}" style="display:inline-block;padding:12px 28px;background:#4f8ef7;color:#fff;text-decoration:none;border-radius:10px;font-weight:bold;margin:16px 0;">Verify Email</a>
                    <p style="color:#8b949e;font-size:0.8rem;">This link expires in 24 hours.</p>
                    <p style="color:#8b949e;font-size:0.8rem;">If you didn't create an account, ignore this email.</p>
                    <hr style="border-color:#21262d;">
                    <p style="color:#6e7681;font-size:0.7rem;">Tragene Journal — Your Tragene Journal</p>
                </div>
                """
            },
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"✅ Verification email sent to {to_email}")
            print(f"   Link: {verify_link}")
            return True
        else:
            print(f"❌ Resend Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Resend API Error: {str(e)}")
        return False