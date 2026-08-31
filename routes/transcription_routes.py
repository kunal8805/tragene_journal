"""
TRAGENE - Audio Transcription Routes
Handles: Audio upload, Whisper transcription, quota tracking, diary saving
Models: whisper-1 for audio, gemini-1.5-flash for text (via ai_service)
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import TranscriptionUsage, DiaryAudioEntry, DiaryEntry
from datetime import datetime
from openai import OpenAI
import os
import tempfile
import json

transcription_bp = Blueprint('transcription', __name__, url_prefix='/transcription')

# ═══════════════════════════════════════════════════════════
# 📊 TIER LIMITS (minutes per month)
# ═══════════════════════════════════════════════════════════

TRANSCRIPTION_LIMITS = {
    'free': 0,           # No access
    'pro': 100,          # 100 minutes/month (₹399)
    'elite': 300,        # 300 minutes/month (₹799)
    'enterprise': float('inf')  # Unlimited
}

# ═══════════════════════════════════════════════════════════
# 🔧 HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def get_month_key():
    """Get current month key like '2026-08'"""
    return datetime.utcnow().strftime('%Y-%m')


def get_user_usage(user_id):
    """Get user's transcription usage for current month. Returns (minutes_used, count)"""
    usage = TranscriptionUsage.query.filter_by(
        user_id=user_id,
        month_key=get_month_key()
    ).first()
    
    if not usage:
        return 0.0, 0
    
    return usage.get_minutes_used(), usage.transcription_count


def get_remaining_minutes(user):
    """Get remaining transcription minutes for user"""
    limit = TRANSCRIPTION_LIMITS.get(user.subscription_tier, 0)
    
    if limit == float('inf'):
        return float('inf')
    
    used_minutes, _ = get_user_usage(user.id)
    return max(0, limit - used_minutes)


def check_transcription_access(user):
    """Check if user can use transcription. Returns (can_use, message)"""
    tier = user.subscription_tier
    
    if tier not in ['pro', 'elite', 'enterprise']:
        return False, 'Transcription requires Pro (₹399) or Elite (₹799) plan. Upgrade to unlock!'
    
    remaining = get_remaining_minutes(user)
    if remaining == 0:
        return False, 'Monthly transcription limit reached. Upgrade for more minutes.'
    
    return True, None


def estimate_audio_duration(file_path):
    """
    Estimate audio duration from file size.
    WebM audio: ~1MB ≈ 60 seconds (rough estimate)
    """
    file_size_bytes = os.path.getsize(file_path)
    file_size_mb = file_size_bytes / (1024 * 1024)
    estimated_seconds = file_size_mb * 60
    return estimated_seconds


def get_whisper_client():
    """Get OpenAI client for Whisper API"""
    return OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        base_url=os.getenv('OPENAI_API_BASE', 'https://aicredits.in/v1')
    )


# ═══════════════════════════════════════════════════════════
# 📊 STATUS ENDPOINT
# ═══════════════════════════════════════════════════════════

@transcription_bp.route('/status')
@login_required
def status():
    """Get user's transcription status and quota"""
    tier = current_user.subscription_tier
    
    if tier not in ['pro', 'elite', 'enterprise']:
        return jsonify({
            'success': False,
            'allowed': False,
            'message': 'Transcription requires Pro or Elite plan.',
            'limit_minutes': 0,
            'used_minutes': 0,
            'remaining_minutes': 0
        })
    
    limit = TRANSCRIPTION_LIMITS.get(tier, 0)
    used_minutes, count = get_user_usage(current_user.id)
    remaining = get_remaining_minutes(current_user)
    
    return jsonify({
        'success': True,
        'allowed': True,
        'limit_minutes': limit if limit != float('inf') else 'unlimited',
        'used_minutes': round(used_minutes, 1),
        'remaining_minutes': round(remaining, 1) if remaining != float('inf') else 'unlimited',
        'transcription_count': count,
        'tier': tier
    })


# ═══════════════════════════════════════════════════════════
# 🎤 TRANSCRIBE ENDPOINT
# ═══════════════════════════════════════════════════════════

@transcription_bp.route('/transcribe', methods=['POST'])
@login_required
def transcribe():
    """Upload audio file, transcribe with Whisper, return text"""
    temp_dir = None
    temp_path = None
    
    try:
        # Check access
        can_use, message = check_transcription_access(current_user)
        if not can_use:
            return jsonify({'success': False, 'message': message}), 403
        
        # Check remaining quota
        remaining = get_remaining_minutes(current_user)
        
        # Check if audio file provided
        if 'audio' not in request.files:
            return jsonify({'success': False, 'message': 'No audio file provided'}), 400
        
        audio_file = request.files['audio']
        
        if audio_file.filename == '':
            return jsonify({'success': False, 'message': 'No audio file selected'}), 400
        
        # Check file type
        allowed_extensions = ['webm', 'mp3', 'wav', 'ogg', 'm4a', 'mp4', 'flac']
        file_ext = audio_file.filename.rsplit('.', 1)[-1].lower() if '.' in audio_file.filename else ''
        
        if file_ext not in allowed_extensions:
            return jsonify({
                'success': False,
                'message': f'Unsupported audio format: .{file_ext}. Supported: {", ".join(allowed_extensions)}'
            }), 400
        
        # Save to temp file
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, f'recording.{file_ext}')
        audio_file.save(temp_path)
        
        # Estimate duration
        estimated_seconds = estimate_audio_duration(temp_path)
        estimated_minutes = estimated_seconds / 60
        
        # Check if recording exceeds remaining quota
        if remaining != float('inf') and estimated_minutes > remaining:
            return jsonify({
                'success': False,
                'message': f'Recording too long. You have {remaining:.1f} minutes left but this recording is ~{estimated_minutes:.1f} minutes.'
            }), 429
        
        # Check minimum size (avoid empty files)
        if estimated_seconds < 1:
            return jsonify({'success': False, 'message': 'Recording too short. Please record at least 1 second.'}), 400
        
        # Call Whisper API
        client = get_whisper_client()
        
        with open(temp_path, 'rb') as f:
            response = client.audio.transcriptions.create(
                model='openai/whisper-1',  # ← Add 'openai/' prefix
                file=f,
                response_format='verbose_json'
            )
        
        # Extract transcript
        transcript = response if isinstance(response, str) else str(response)
        transcript = transcript.strip()
        
        if not transcript:
            return jsonify({'success': False, 'message': 'Could not transcribe audio. Please try again.'}), 500
        
        # Save to database
        audio_entry = DiaryAudioEntry(
            user_id=current_user.id,
            transcript=transcript,
            original_transcript=transcript,
            duration_seconds=estimated_seconds
        )
        db.session.add(audio_entry)
        
        # Update usage
        month_key = get_month_key()
        usage = TranscriptionUsage.query.filter_by(
            user_id=current_user.id,
            month_key=month_key
        ).first()
        
        if not usage:
            usage = TranscriptionUsage(
                user_id=current_user.id,
                month_key=month_key,
                seconds_used=0,
                transcription_count=0
            )
            db.session.add(usage)
        
        usage.seconds_used += estimated_seconds
        usage.transcription_count += 1
        
        db.session.commit()
        
        # Get new remaining
        new_remaining = get_remaining_minutes(current_user)
        
        return jsonify({
            'success': True,
            'transcript': transcript,
            'entry_id': audio_entry.id,
            'duration_seconds': round(estimated_seconds, 1),
            'duration_display': f'{int(estimated_seconds // 60)}m {int(estimated_seconds % 60)}s',
            'remaining_minutes': round(new_remaining, 1) if new_remaining != float('inf') else 'unlimited'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Transcription error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Transcription failed: {str(e)[:200]}'}), 500
    
    finally:
        # Cleanup temp files
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            if temp_dir and os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except:
            pass


# ═══════════════════════════════════════════════════════════
# 💾 SAVE TO DIARY ENDPOINT
# ═══════════════════════════════════════════════════════════

@transcription_bp.route('/save-to-diary', methods=['POST'])
@login_required
def save_to_diary():
    """Save edited transcript to diary entry"""
    try:
        data = request.get_json()
        
        entry_id = data.get('entry_id')
        edited_text = data.get('transcript', '').strip()
        title = data.get('title', 'Voice Entry')
        diary_date = data.get('date', datetime.utcnow().date().isoformat())
        mood = data.get('mood', 'voice')
        
        if not entry_id:
            return jsonify({'success': False, 'message': 'Entry ID required'}), 400
        
        if not edited_text:
            return jsonify({'success': False, 'message': 'Transcript is empty'}), 400
        
        # Get audio entry
        audio_entry = DiaryAudioEntry.query.get(entry_id)
        if not audio_entry or audio_entry.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Entry not found'}), 404
        
        # Check if already saved
        if audio_entry.diary_entry_id:
            return jsonify({'success': False, 'message': 'This transcript is already saved to diary'}), 400
        
        # Check if edited
        if edited_text != audio_entry.original_transcript:
            audio_entry.is_edited = True
        
        audio_entry.transcript = edited_text
        
        # Create diary entry
        diary_entry = DiaryEntry(
            user_id=current_user.id,
            account_id=current_user.current_account_id,
            entry_date=datetime.strptime(diary_date, '%Y-%m-%d').date(),
            title=title[:200],
            content=edited_text,
            mood=mood
        )
        db.session.add(diary_entry)
        db.session.flush()
        
        audio_entry.diary_entry_id = diary_entry.id
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'diary_entry_id': diary_entry.id,
            'message': 'Saved to diary successfully!'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Save to diary error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error saving: {str(e)[:200]}'}), 500


# ═══════════════════════════════════════════════════════════
# 📜 HISTORY ENDPOINT
# ═══════════════════════════════════════════════════════════

@transcription_bp.route('/history')
@login_required
def history():
    """Get user's transcription history"""
    try:
        entries = DiaryAudioEntry.query.filter_by(
            user_id=current_user.id
        ).order_by(DiaryAudioEntry.created_at.desc()).limit(20).all()
        
        return jsonify({
            'success': True,
            'entries': [{
                'id': e.id,
                'transcript_preview': e.transcript[:150] + ('...' if len(e.transcript) > 150 else ''),
                'duration_seconds': e.duration_seconds,
                'duration_display': e.get_duration_display(),
                'is_edited': e.is_edited,
                'created_at': e.created_at.isoformat(),
                'saved_to_diary': e.diary_entry_id is not None,
                'diary_entry_id': e.diary_entry_id
            } for e in entries]
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ═══════════════════════════════════════════════════════════
# 🗑️ DELETE TRANSCRIPTION ENDPOINT
# ═══════════════════════════════════════════════════════════

@transcription_bp.route('/delete/<int:entry_id>', methods=['DELETE'])
@login_required
def delete_transcription(entry_id):
    """Delete a transcription entry"""
    try:
        entry = DiaryAudioEntry.query.get(entry_id)
        
        if not entry or entry.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Entry not found'}), 404
        
        db.session.delete(entry)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Transcription deleted'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500