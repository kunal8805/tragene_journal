from app import create_app
from extensions import db
from models import User, Subscription, Payment
from datetime import datetime
import uuid

app = create_app()

with app.app_context():
    email = "kunaldhade40@gmail.com"
    user = User.query.filter_by(email=email).first()

    if not user:
        print(f"❌ No user found with email {email}")
    else:
        # 1. Update user's subscription tier
        user.subscription_tier = 'elite'
        user.subscription_active = True

        # 2. Create or update Subscription record
        sub = Subscription.query.filter_by(user_id=user.id).first()
        if not sub:
            sub = Subscription(user_id=user.id)
            db.session.add(sub)

        sub.plan_tier = 'elite'
        sub.plan_type = 'monthly'
        sub.start_date = datetime.utcnow()
        sub.is_active = True
        sub.auto_renew = True

        db.session.flush()  # ensures sub.id is available

        # 3. Create a Payment record (manual entry, marked SUCCESS)
        payment = Payment(
            user_id=user.id,
            subscription_id=sub.id,
            cashfree_order_id=f"MANUAL-{uuid.uuid4().hex[:12]}",
            base_amount=79900,      # ₹799 in paise
            gateway_fee=0,
            total_amount=79900,
            currency='INR',
            plan_tier='elite',
            plan_type='monthly',
            status='SUCCESS',
            payment_completed_at=datetime.utcnow()
        )
        db.session.add(payment)

        db.session.commit()
        print(f"✅ {email} upgraded to Elite. Subscription ID: {sub.id}, Payment ID: {payment.id}")
