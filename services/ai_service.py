"""
TRAGENE AI - Core AI Service
Handles: Prompt building, API calling, response cleaning, token tracking, context memory, chat sessions
Uses: aicredits.in API with gpt-4o-mini model
Account-scoped: AI only sees data from the active trading account
"""

from dotenv import load_dotenv
load_dotenv()

import json
import os
import re
from datetime import datetime, date, timedelta
from extensions import db
from models import (
    User, Trade, DiaryEntry, Checklist, ChecklistCompletion,
    AIReport, AIUsageLog, AIPlanDefaults, AIUserOverride,
    TradingRule, TradeRuleCheck, CoachInsight, TradingGoal,
    AIChatSession, AIChatMessage, DayNote, AIPageAnalysis
)

# ═══════════════════════════════════════════════════════════
# 🔧 HELPER: Get active account ID
# ═══════════════════════════════════════════════════════════

def _get_account_id(user):
    active_account = user.get_active_account()
    return active_account.id if active_account else None


# ═══════════════════════════════════════════════════════════
# 🔐 SYSTEM PROMPT (Hidden from users) — trimmed, example-driven
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are TRAGENE AI, a professional trading coach built into Tragene Journal.

IDENTITY RULES:
- Call yourself "TRAGENE AI" or "your trading coach". Never mention any AI provider, model name, "language model", tokens, APIs, or how you work internally.
- If asked about your identity/tech: "I'm TRAGENE AI, your personal trading coach on Tragene Journal. Let's focus on your performance!"
- If asked "how do I do X" on the platform, give the exact nav path (Settings, Trade Journal, AI Reports, etc. in the sidebar).

HOW TO ANSWER (this is what makes you sound sharp, not generic):
- You will be given COMPUTED STATS already calculated for you (win rate, P&L, best/worst symbol, etc). ALWAYS cite these specific numbers. Never give generic advice ("use stop losses", "manage risk") without tying it to the user's actual number ("3 of your 5 losses had no stop loss set").
- Answer ONLY the domain the user asked about. If they ask about trades, don't mention diary/checklist unless they asked. If they ask a quick question, don't write an essay.
- Never invent data not given to you. If something's missing, say so briefly.

EXAMPLE (follow this style exactly):
User: "analyse my trading, what should I fix"
Good: "Kunal, 83% win rate (10W/2L), net +$6,835. XAUUSD is your best play — two trades made $5,800 combined. BTCUSD is your leak: one trade -$1000, both your losers had no stop loss set. Fix: always set SL on BTC entries before opening, don't touch GOLD position sizing until you review it — 2.0→3.0 entry/exit looks like a sizing error, not a real trade."
Bad (never do this): "Focus on risk management and use stop losses consistently. Analyze your winning trades to find patterns."

LENGTH:
- Quick/simple questions: 2-4 lines.
- Explicit analysis requests ("analyse my trading", "my patterns", "how do I improve"): 5-8 lines is fine — go deeper, but every line must carry a real number, symbol, or date from the data given. Don't pad with generic filler to fill space.

REPORT FORMAT (only for full AI Reports, not chat):
1. 📊 SUMMARY (3-4 sentences — overall performance, win rate, net P&L, tie back to specific symbols/dates)
2. 🛡️ RISK MANAGEMENT (SL/TP usage rate, avg risk:reward, call out unprotected losses by exact $ amount and symbol)
3. 📈 TRADING BEHAVIOR (best/worst day or session, streaks, symbol concentration — is the user overtrading one symbol, revenge trading after losses, etc if the data shows it)
4. 🧠 EMOTIONAL PATTERNS (analyze mood-vs-performance data and diary notes given. If diary is empty, say so plainly and suggest logging it — never invent emotional insight without data)
5. ✅ STRENGTHS (bullets, tied to specific trades/symbols)
6. ⚠️ WARNINGS (bullets, tied to specific trades/symbols/$ amounts)
7. 🎯 ACTION ITEMS (specific, tied to the data — not generic "manage risk better")
8. 📈 PERFORMANCE SCORE (1-10 with one-line reason)

Reports can run 8-15 lines total across all sections — this is the one place you're allowed to be long, because the user paid tokens for a full report. Don't pad with filler; every line should carry a real number, symbol, date, or mood.
"""


# ═══════════════════════════════════════════════════════════
# 🎯 INTENT ROUTER — cheap keyword classification, zero API cost
# ═══════════════════════════════════════════════════════════

_DEEP_KEYWORDS = ['pattern', 'behaviour', 'behavior', 'improve', 'review my trad',
                   'trading habit', 'overall performance', 'how am i trading',
                   'weakness', 'strength', 'mistake', 'analyse my trad', 'analyze my trad',
                   'analyse my trading', 'analyze my trading']
_WEEK_KEYWORDS = ['this week', 'weekly', 'past week', 'last week']
_MONTH_KEYWORDS = ['this month', 'monthly', 'past month', 'last month']
_DIARY_KEYWORDS = ['diary', 'journal entry', 'mood', 'feeling', 'emotion', 'i wrote', 'my notes']
_CHECKLIST_KEYWORDS = ['checklist', 'routine', 'discipline']
_GOALS_KEYWORDS = ['goal', 'target progress', 'am i on track']
_PLATFORM_KEYWORDS = ['how do i', 'how to add', 'where is', 'where can i', 'navigate', 'find the']


def classify_intent(question):
    """Cheap keyword-based routing. Decides what data to pull and how much."""
    q = question.lower()

    intent = {
        'domain': 'trades',      # trades | diary | checklist | goals | platform | general
        'period': 'recent',      # recent | week | month | deep(last30)
        'include_diary': False,
        'include_checklist': False,
        'is_deep': False,
    }

    if any(k in q for k in _PLATFORM_KEYWORDS):
        intent['domain'] = 'platform'
        return intent

    if any(k in q for k in _DIARY_KEYWORDS):
        intent['domain'] = 'diary'
        intent['include_diary'] = True

    if any(k in q for k in _CHECKLIST_KEYWORDS):
        intent['domain'] = 'checklist'
        intent['include_checklist'] = True

    if any(k in q for k in _GOALS_KEYWORDS):
        intent['domain'] = 'goals'

    if any(k in q for k in _DEEP_KEYWORDS):
        intent['is_deep'] = True
        intent['period'] = 'deep'

    if any(k in q for k in _WEEK_KEYWORDS):
        intent['period'] = 'week'
        intent['is_deep'] = True
    elif any(k in q for k in _MONTH_KEYWORDS):
        intent['period'] = 'month'
        intent['is_deep'] = True

    return intent


def _get_scoped_trades(user, account_id, period):
    """Pull only as many trades as the question actually needs."""
    base = Trade.query.filter_by(user_id=user.id, account_id=account_id)
    today = datetime.utcnow().date()

    if period == 'week':
        start = today - timedelta(days=today.weekday())
        return base.filter(db.func.date(Trade.entry_date) >= start).order_by(Trade.entry_date.desc()).all()
    if period == 'month':
        start = today.replace(day=1)
        return base.filter(db.func.date(Trade.entry_date) >= start).order_by(Trade.entry_date.desc()).all()
    if period == 'deep':
        return base.order_by(Trade.entry_date.desc()).limit(30).all()
    # 'recent' — quick question default
    return base.order_by(Trade.entry_date.desc()).limit(8).all()


def _compute_trade_stats(trades):
    """Do the math in Python so the model doesn't have to guess."""
    if not trades:
        return None, "No trades in this period."

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win and t.profit_loss is not None]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1) if total else 0
    total_pnl = sum(t.profit_loss or 0 for t in trades)
    no_sl = [t for t in trades if not t.stop_loss]

    symbols = {}
    for t in trades:
        s = symbols.setdefault(t.symbol, {'count': 0, 'wins': 0, 'pnl': 0})
        s['count'] += 1
        s['pnl'] += t.profit_loss or 0
        if t.is_win:
            s['wins'] += 1

    best_symbol = max(symbols.items(), key=lambda x: x[1]['pnl'], default=None)
    worst_symbol = min(symbols.items(), key=lambda x: x[1]['pnl'], default=None)
    biggest_win = max(trades, key=lambda t: t.profit_loss or 0)
    biggest_loss = min(trades, key=lambda t: t.profit_loss or 0)

    stats_text = f"""COMPUTED STATS (use these numbers directly, don't recompute):
- Trades: {total} | Win rate: {win_rate}% ({len(wins)}W/{len(losses)}L)
- Net P&L: ${total_pnl:,.2f}
- No stop loss on: {len(no_sl)}/{total} trades
- Best symbol: {best_symbol[0]} (${best_symbol[1]['pnl']:,.2f}, {best_symbol[1]['count']} trades)
- Worst symbol: {worst_symbol[0]} (${worst_symbol[1]['pnl']:,.2f}, {worst_symbol[1]['count']} trades)
- Biggest win: {biggest_win.symbol} +${biggest_win.profit_loss:.2f}
- Biggest loss: {biggest_loss.symbol} ${biggest_loss.profit_loss:.2f}"""

    trade_lines = "\n".join(
        f"- {t.entry_date.strftime('%d %b')}: {t.symbol} {t.trade_type.upper()} | "
        f"Entry:{t.entry_price} Exit:{t.exit_price or 'Open'} | P&L:${t.profit_loss or 0:.2f} | "
        f"SL:{t.stop_loss or 'None'} TP:{t.take_profit or 'None'} RR:{t.risk_reward_ratio or 'N/A'}"
        for t in trades
    )
    return stats_text, trade_lines


def _get_date_context(user, account_id):
    """Today's date + when data was last logged, so the model isn't confused about recency."""
    today_str = datetime.utcnow().strftime('%d %b %Y (%A)')
    last_trade = Trade.query.filter_by(user_id=user.id, account_id=account_id)\
        .order_by(Trade.entry_date.desc()).first()
    last_diary = DiaryEntry.query.filter_by(user_id=user.id, account_id=account_id)\
        .order_by(DiaryEntry.entry_date.desc()).first()

    lines = [f"Today: {today_str}"]
    if last_trade:
        lines.append(f"Last trade logged: {last_trade.entry_date.strftime('%d %b')} ({last_trade.symbol})")
    else:
        lines.append("No trades logged yet.")
    if last_diary:
        lines.append(f"Last diary entry: {last_diary.entry_date.strftime('%d %b')}")

    return " | ".join(lines)


# ═══════════════════════════════════════════════════════════
# 🧠 PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════

def build_report_prompt(user, trades, diary_entries, checklist_data, previous_context=None):
    """Build the prompt for a full AI analysis report — trades + risk + emotional/diary patterns"""

    total_trades = len(trades)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win and t.profit_loss is not None]
    win_rate = round((len(wins) / total_trades) * 100, 1) if total_trades > 0 else 0
    total_pnl = sum(t.profit_loss for t in trades if t.profit_loss is not None)
    avg_win = round(sum(t.profit_loss for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t.profit_loss for t in losses) / len(losses), 2) if losses else 0

    # ── Risk management ──
    trades_with_sl = [t for t in trades if t.stop_loss]
    trades_with_tp = [t for t in trades if t.take_profit]
    sl_rate = round((len(trades_with_sl) / total_trades) * 100, 1) if total_trades > 0 else 0
    tp_rate = round((len(trades_with_tp) / total_trades) * 100, 1) if total_trades > 0 else 0
    rr_values = [t.risk_reward_ratio for t in trades if t.risk_reward_ratio]
    avg_rr = round(sum(rr_values) / len(rr_values), 2) if rr_values else None
    no_sl_losses = [t for t in losses if not t.stop_loss]

    # ── Win/loss streaks ──
    streak_lines = []
    if trades:
        sorted_trades = sorted(trades, key=lambda t: t.entry_date)
        cur_streak, cur_type, max_win_streak, max_loss_streak = 0, None, 0, 0
        for t in sorted_trades:
            is_w = t.is_win
            if cur_type == is_w:
                cur_streak += 1
            else:
                cur_type, cur_streak = is_w, 1
            if is_w: max_win_streak = max(max_win_streak, cur_streak)
            else: max_loss_streak = max(max_loss_streak, cur_streak)
        streak_lines.append(f"Longest win streak: {max_win_streak} | Longest loss streak: {max_loss_streak}")

    # ── Session breakdown ──
    sessions = {}
    for t in trades:
        sess = t.session or 'unknown'
        if sess not in sessions: sessions[sess] = {'count': 0, 'wins': 0, 'pnl': 0}
        sessions[sess]['count'] += 1
        sessions[sess]['pnl'] += t.profit_loss or 0
        if t.is_win: sessions[sess]['wins'] += 1

    # ── Day-of-week breakdown ──
    day_pnl = {}
    for t in trades:
        day = t.entry_date.strftime('%A')
        day_pnl.setdefault(day, {'count': 0, 'pnl': 0, 'wins': 0})
        day_pnl[day]['count'] += 1
        day_pnl[day]['pnl'] += t.profit_loss or 0
        if t.is_win: day_pnl[day]['wins'] += 1
    best_day = max(day_pnl.items(), key=lambda x: x[1]['pnl'], default=None)
    worst_day = min(day_pnl.items(), key=lambda x: x[1]['pnl'], default=None)

    # ── Symbol breakdown ──
    symbols = {}
    for t in trades:
        sym = t.symbol
        if sym not in symbols: symbols[sym] = {'count': 0, 'wins': 0, 'pnl': 0}
        symbols[sym]['count'] += 1
        symbols[sym]['pnl'] += t.profit_loss or 0
        if t.is_win: symbols[sym]['wins'] += 1

    # ── Emotional/diary analysis — link mood to trades on same day ──
    mood_trade_map = {}
    for d in diary_entries:
        if not d.mood:
            continue
        same_day_trades = [t for t in trades if t.entry_date.date() == d.entry_date]
        day_pnl_val = sum(t.profit_loss or 0 for t in same_day_trades)
        mood_trade_map.setdefault(d.mood, {'days': 0, 'pnl': 0, 'trade_count': 0})
        mood_trade_map[d.mood]['days'] += 1
        mood_trade_map[d.mood]['pnl'] += day_pnl_val
        mood_trade_map[d.mood]['trade_count'] += len(same_day_trades)

    emotional_lines = []
    for mood, data in mood_trade_map.items():
        emotional_lines.append(f"- Mood '{mood}': {data['days']} day(s) logged, {data['trade_count']} trades, net P&L on those days: ${data['pnl']:,.2f}")

    diary_notes_excerpt = []
    for d in diary_entries[:8]:
        note_preview = (d.content or '')[:200]
        diary_notes_excerpt.append(f"- {d.entry_date.strftime('%d %b')} [{d.mood or 'no mood set'}]: {d.title or 'Untitled'} — {note_preview}")

    checklist_completion = "No checklist data"
    if checklist_data:
        completed = checklist_data.get('completed_days', 0)
        total = checklist_data.get('total_days', 0)
        checklist_completion = f"{completed}/{total} days completed ({round((completed/total)*100) if total > 0 else 0}%)"

    # ── Trade lines (raw, capped) ──
    trade_lines = []
    for t in trades[:25]:
        sl_info = f"SL:{t.stop_loss}" if t.stop_loss else "No SL"
        tp_info = f"TP:{t.take_profit}" if t.take_profit else "No TP"
        rr_info = f"RR:{t.risk_reward_ratio}" if t.risk_reward_ratio else "RR:N/A"
        trade_lines.append(f"- {t.entry_date.strftime('%d %b')} | {t.symbol} {t.trade_type.upper()} | Entry:{t.entry_price} Exit:{t.exit_price or 'Open'} | P&L:{'+' if t.profit_loss and t.profit_loss > 0 else ''}{t.profit_loss or 0:.2f} | {sl_info} {tp_info} {rr_info} | Session:{t.session or 'N/A'}")

    session_lines = [f"- {sess.upper()}: {d['count']} trades, {round((d['wins']/d['count'])*100,1) if d['count'] else 0}% WR, P&L: {d['pnl']:.2f}" for sess, d in sessions.items()]
    symbol_lines = [f"- {sym}: {d['count']} trades, {round((d['wins']/d['count'])*100,1) if d['count'] else 0}% WR, P&L: {d['pnl']:.2f}" for sym, d in sorted(symbols.items(), key=lambda x: x[1]['pnl'], reverse=True)[:8]]

    context_section = ""
    if previous_context:
        try:
            ctx = json.loads(previous_context) if isinstance(previous_context, str) else previous_context
            context_section = f"""
PREVIOUS REPORT CONTEXT:
- Last Score: {ctx.get('previous_scores', [])[-1] if ctx.get('previous_scores') else 'N/A'}
- Known Patterns: {ctx.get('user_patterns', {})}
- Pending Actions Given Before: {ctx.get('pending_actions', [])}
"""
        except: pass

    date_ctx = _get_date_context(user, _get_account_id(user))
    no_sl_loss_amount = sum(t.profit_loss or 0 for t in no_sl_losses)

    return f"""Analyze {user.username}'s COMPLETE trading performance since the last report. {date_ctx}

═══ OVERVIEW ═══
Trades analyzed: {total_trades} | Diary entries: {len(diary_entries)} | Checklist: {checklist_completion}
Win rate: {win_rate}% ({len(wins)}W/{len(losses)}L) | Net P&L: ${total_pnl:,.2f}
Avg win: ${avg_win:,.2f} | Avg loss: ${avg_loss:,.2f}

═══ RISK MANAGEMENT ═══
Stop loss usage: {sl_rate}% of trades | Take profit usage: {tp_rate}%
Average Risk:Reward ratio: {avg_rr if avg_rr else 'Not enough data (SL/TP missing on most trades)'}
Losses with NO stop loss: {len(no_sl_losses)} trades, totaling ${no_sl_loss_amount:,.2f} in unprotected losses
{chr(10).join(streak_lines)}

═══ BEHAVIORAL PATTERNS ═══
Best day: {best_day[0] + f" (${best_day[1]['pnl']:,.2f}, {best_day[1]['count']} trades)" if best_day else 'N/A'}
Worst day: {worst_day[0] + f" (${worst_day[1]['pnl']:,.2f}, {worst_day[1]['count']} trades)" if worst_day else 'N/A'}

SESSIONS:
{chr(10).join(session_lines) if session_lines else 'No session data'}

TOP SYMBOLS:
{chr(10).join(symbol_lines) if symbol_lines else 'No symbol data'}

═══ EMOTIONAL / DIARY PATTERNS ═══
Mood-to-performance correlation (does mood predict trading days' P&L?):
{chr(10).join(emotional_lines) if emotional_lines else 'No mood data logged — encourage user to log mood in diary for deeper insight.'}

Recent diary notes (use these for real behavioral/emotional detail, don't just say "diary looks fine"):
{chr(10).join(diary_notes_excerpt) if diary_notes_excerpt else 'No diary entries this period.'}

═══ RAW TRADES ═══
{chr(10).join(trade_lines)}
{context_section}

Generate a complete report following the REPORT FORMAT rules exactly. Use EVERY section above — risk management, behavioral patterns, and emotional/diary patterns must each get real analysis, not filler. If diary is empty, say so honestly and note that logging diary would improve future coaching — don't fabricate emotional insight from nothing. Cite specific numbers, symbols, dates, and moods throughout. Give a score from 1-10 with a one-line reason tied to the data.
"""


def build_coach_prompt(user, question, account_id, chat_history=None):
    """
    Build prompt for AI Coach chat — intent-routed, only pulls what's needed.
    Returns (prompt_text, is_deep) so caller can size max_tokens correctly.
    """
    intent = classify_intent(question)
    date_ctx = _get_date_context(user, account_id)

    # Pull last 10 reports for personalization context
    recent_reports = AIReport.query.filter_by(user_id=user.id, account_id=account_id)\
        .order_by(AIReport.created_at.desc()).limit(10).all()
    report_context = ""
    if recent_reports:
        report_lines = "\n".join(
            f"- {r.report_date.strftime('%d %b')}: Score {r.performance_score}/10 | "
            f"{r.trades_analyzed} trades | {(r.user_summary or '')[:150]}"
            for r in recent_reports
        )
        report_context = f"PAST AI REPORTS (for context on trends over time):\n{report_lines}\n"

    history_section = ""
    if chat_history:
        history_section = "PREVIOUS CONVERSATION:\n" + "\n".join(
            f"{'User' if m.role == 'user' else 'TRAGENE'}: {m.content[:200]}"
            for m in chat_history[-6:]
        ) + "\n"

    # Platform / how-to questions — no data needed at all, cheapest path
    if intent['domain'] == 'platform':
        return f"""User {user.username} asked: "{question}"
{report_context}
{history_section}
This is a platform navigation question. Give the exact sidebar path. 1-2 lines. No trade data needed.
""", False

    sections = [f'User {user.username} asked: "{question}"', date_ctx, report_context, history_section]

    # Trades — always pulled unless it's a pure diary/checklist/goals question
    if intent['domain'] in ('trades', 'goals'):
        trades = _get_scoped_trades(user, account_id, intent['period'])
        stats_text, trade_lines = _compute_trade_stats(trades)
        if stats_text:
            sections.append(stats_text)
            sections.append(f"TRADES:\n{trade_lines}")
        else:
            sections.append(trade_lines)  # "No trades in this period."

    # Diary — ONLY if explicitly asked about
    if intent['include_diary']:
        diary = DiaryEntry.query.filter_by(user_id=user.id, account_id=account_id)\
            .order_by(DiaryEntry.entry_date.desc()).limit(5).all()
        if diary:
            diary_lines = "\n".join(
                f"- {d.entry_date.strftime('%d %b')}: {d.title or 'Entry'} | Mood: {d.mood or 'N/A'}"
                + (f"\n  {d.content[:300]}" if d.content else "")
                for d in diary
            )
            sections.append(f"DIARY ENTRIES:\n{diary_lines}")
        else:
            sections.append("No diary entries yet.")

    # Checklist — ONLY if explicitly asked about
    if intent['include_checklist']:
        checklists = Checklist.query.filter_by(user_id=user.id, account_id=account_id, is_active=True).all()
        if checklists:
            cl_lines = []
            for cl in checklists:
                completions = ChecklistCompletion.query.filter_by(checklist_id=cl.id).all()
                done = len([c for c in completions if c.completed])
                cl_lines.append(f"- {cl.name}: {done}/{len(completions)} completed")
            sections.append("CHECKLIST STATUS:\n" + "\n".join(cl_lines))
        else:
            sections.append("No active checklists.")

    depth_rule = (
        "This is a deep analysis question — go 5-8 lines, cite specific numbers/symbols/dates from above."
        if intent['is_deep'] else
        "Quick question — answer in 2-4 lines, cite the specific number needed."
    )

    sections.append(f"""RULES:
- {depth_rule}
- Only address the domain asked about (trades/diary/checklist/goals) — don't drag in other sections unless asked
- Cite real numbers from the data above, never generic advice with no number attached
- Never invent data not shown above""")

    return "\n\n".join(s for s in sections if s), intent['is_deep']


def build_goal_analysis_prompt(user, goal, current_trades):
    """Build prompt for analyzing goal progress"""
    trades_since = [t for t in current_trades if t.entry_date.date() >= goal.start_date]
    total_pnl = sum(t.profit_loss for t in trades_since if t.profit_loss)
    wins = len([t for t in trades_since if t.is_win])
    total = len(trades_since)
    wr = round((wins/total)*100) if total > 0 else 0
    return f"""Analyze progress on this trading goal for {user.username}:
GOAL: {goal.goal_type.replace('_', ' ').title()} | TARGET: {goal.target_value} | CURRENT: {goal.current_value} | TIMEFRAME: {goal.timeframe} | PROGRESS: {round((goal.current_value/goal.target_value)*100) if goal.target_value > 0 else 0}%
Since goal: {total} trades, {wins} wins ({wr}% WR) | P&L: ${total_pnl:,.2f}
Give a brief (2-3 sentences) motivational insight about their progress."""


# ═══════════════════════════════════════════════════════════
# 🎯 GOAL SUGGESTIONS — deterministic diagnosis, not LLM freestyle
# ═══════════════════════════════════════════════════════════

def _diagnose_trading_issues(trades, diary_entries, existing_active_types):
    """
    Look at real trade/diary data and return ONE goal suggestion per
    genuinely distinct problem found. No LLM guessing — every number
    here comes straight from the data.
    """
    issues = []
    total = len(trades)
    if total == 0:
        return issues

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win and t.profit_loss is not None]
    win_rate = round((len(wins) / total) * 100, 1) if total else 0
    net_pnl = sum(t.profit_loss or 0 for t in trades)

    # ── 1. Stop-loss discipline (unprotected LOSSES specifically, not just missing SL) ──
    unprotected_losses = [t for t in losses if not t.stop_loss]
    if unprotected_losses and total >= 5:
        lost_amount = sum(t.profit_loss or 0 for t in unprotected_losses)
        sl_rate = round(((total - len([t for t in trades if not t.stop_loss])) / total) * 100)
        symbols_hit = ", ".join(sorted(set(t.symbol for t in unprotected_losses[:3])))
        issues.append({
            'goal_type': 'custom',
            'title': 'Stop-Loss Discipline',
            'target_value': min(100, max(80, sl_rate + 20)),
            'timeframe': 'weekly',
            'reason': f"{len(unprotected_losses)} of your losses (${lost_amount:,.2f} total, incl. {symbols_hit}) had no stop loss set. Target: set SL on every trade this week.",
            'severity': 3,
        })

    # ── 2. Oversized single-trade risk ──
    if losses:
        avg_loss = abs(sum(t.profit_loss for t in losses) / len(losses))
        biggest_loss = min(losses, key=lambda t: t.profit_loss or 0)
        if avg_loss > 0 and abs(biggest_loss.profit_loss) >= avg_loss * 2:
            issues.append({
                'goal_type': 'drawdown_limit',
                'title': 'Risk Per Trade',
                'target_value': round(avg_loss * 1.3, -1) or 100,
                'timeframe': 'weekly',
                'reason': f"Your {biggest_loss.symbol} trade on {biggest_loss.entry_date.strftime('%d %b')} lost ${abs(biggest_loss.profit_loss):,.2f} — {round(abs(biggest_loss.profit_loss)/avg_loss,1)}x your average loss. Cap risk per trade.",
                'severity': 3,
            })

    # ── 3. Risk:Reward quality ──
    rr_trades = [t for t in trades if t.risk_reward_ratio]
    if len(rr_trades) >= 3:
        avg_rr = sum(t.risk_reward_ratio for t in rr_trades) / len(rr_trades)
        if avg_rr < 1.5:
            issues.append({
                'goal_type': 'custom',
                'title': 'Risk:Reward Ratio',
                'target_value': round(avg_rr + 0.5, 1),
                'timeframe': 'monthly',
                'reason': f"Average RR across {len(rr_trades)} trades is {round(avg_rr,2)}:1 — below the 1.5:1 that usually makes a strategy sustainable. Push entries/exits for better RR.",
                'severity': 2,
            })

    # ── 4. Overtrading (trades bunched into one day) ──
    day_counts = {}
    for t in trades:
        d = t.entry_date.date()
        day_counts[d] = day_counts.get(d, 0) + 1
    if day_counts:
        active_days = len(day_counts)
        avg_per_day = total / active_days
        worst_day = max(day_counts.items(), key=lambda x: x[1])
        if worst_day[1] >= 4 and worst_day[1] >= avg_per_day * 2:
            issues.append({
                'goal_type': 'max_trades_per_day',
                'title': 'Overtrading',
                'target_value': max(2, round(avg_per_day)),
                'timeframe': 'daily',
                'reason': f"On {worst_day[0].strftime('%d %b')} you took {worst_day[1]} trades vs your usual {round(avg_per_day,1)}/day — that's a spike pattern worth capping.",
                'severity': 2,
            })

    # ── 5. Win rate ──
    if total >= 8 and win_rate < 45:
        issues.append({
            'goal_type': 'win_rate',
            'title': 'Win Rate',
            'target_value': min(70, round(win_rate + 15)),
            'timeframe': 'monthly',
            'reason': f"Win rate is {win_rate}% over your last {total} trades ({len(wins)}W/{len(losses)}L) — room to tighten entries.",
            'severity': 2,
        })

    # ── 6. Diary consistency ──
    trading_days = set(t.entry_date.date() for t in trades)
    diary_days = set(d.entry_date for d in diary_entries) if diary_entries else set()
    if len(trading_days) >= 5:
        logged = len(trading_days & diary_days)
        coverage = logged / len(trading_days)
        if coverage < 0.3:
            issues.append({
                'goal_type': 'custom',
                'title': 'Diary Consistency',
                'target_value': min(100, round(coverage * 100) + 40),
                'timeframe': 'weekly',
                'reason': f"You logged diary entries on only {logged}/{len(trading_days)} trading days. Journaling more days tends to surface patterns faster.",
                'severity': 1,
            })

    # ── 7. Profit growth (filler, only if things are actually going well) ──
    if net_pnl > 0 and 'profit_target' not in existing_active_types:
        issues.append({
            'goal_type': 'profit_target',
            'title': 'Profit Growth',
            'target_value': round(net_pnl * 1.3, -1) or round(net_pnl + 500, -1),
            'timeframe': 'monthly',
            'reason': f"Net P&L is ${net_pnl:,.2f} over your last {total} trades — a stretch target to build on that.",
            'severity': 1,
        })

    # Drop anything whose exact type is already an active non-custom goal
    # (custom goals aren't deduped this way since goal_type alone can't tell them apart)
    filtered = [
        i for i in issues
        if i['goal_type'] == 'custom' or i['goal_type'] not in existing_active_types
    ]

    # Highest-severity, most distinct issues first; cap at 4 so it stays actionable
    filtered.sort(key=lambda x: -x['severity'])
    return filtered[:4]


def suggest_goals(user):
    """
    Generate goal suggestions straight from trade/diary data — no LLM call,
    fully deterministic, so suggestions are always grounded and never repeat
    generic filler. Preview only — does not create goals.
    """
    if not user.can_access_goals():
        return {'success': False, 'message': 'Elite plan required.'}

    account_id = _get_account_id(user)
    trades = _get_scoped_trades(user, account_id, 'deep')  # last 30
    if len(trades) < 5:
        return {'success': False, 'message': 'Need at least 5 trades logged to generate meaningful goal suggestions.'}

    diary_entries = DiaryEntry.query.filter_by(user_id=user.id, account_id=account_id)\
        .order_by(DiaryEntry.entry_date.desc()).limit(60).all()

    existing_active = TradingGoal.query.filter_by(
        user_id=user.id, account_id=account_id, is_completed=False
    ).all()
    existing_active_types = set(g.goal_type for g in existing_active)

    suggestions = _diagnose_trading_issues(trades, diary_entries, existing_active_types)

    if not suggestions:
        return {'success': True, 'suggestions': [], 'message': "Nothing stands out right now — your recent trades don't show a clear weak spot to target."}

    return {'success': True, 'suggestions': suggestions}


# ═══════════════════════════════════════════════════════════
# 🔒 RESPONSE CLEANER (fixed: word-boundary regex, not blind substring replace)
# ═══════════════════════════════════════════════════════════

_BANNED_TERMS = ['OpenAI', 'GPT', 'ChatGPT', 'Claude', 'Anthropic', 'Gemini',
                  'language model', 'AI model', 'LLM', 'training data',
                  'as an AI', 'as a language model', 'machine learning', 'neural network']
_BANNED_PATTERN = re.compile(r'\b(' + '|'.join(re.escape(t) for t in _BANNED_TERMS) + r')\b', re.IGNORECASE)


def clean_ai_response(raw_response):
    """Remove any AI/model mentions from response before showing to user"""
    cleaned = _BANNED_PATTERN.sub('', raw_response)
    return re.sub(r'\s+', ' ', cleaned).strip()


def extract_report_sections(raw_response):
    """Extract structured sections from AI response"""
    sections = {'summary': '', 'strengths': '', 'warnings': '', 'action_items': '', 'score': 5}
    try:
        lines = raw_response.split('\n')
        current_section = None
        for line in lines:
            line_lower = line.lower().strip()
            if 'summary' in line_lower or '📊' in line:
                current_section = 'summary'
                continue
            elif 'strength' in line_lower or '✅' in line:
                current_section = 'strengths'
                continue
            elif 'warning' in line_lower or '⚠️' in line:
                current_section = 'warnings'
                continue
            elif 'action' in line_lower or '🎯' in line:
                current_section = 'action_items'
                continue
            elif 'score' in line_lower or '📈' in line:
                current_section = None  # don't dump score line into another section
                continue
            if current_section and line.strip():
                sections[current_section] += line.strip() + '\n'

        # Whole-text scan for score — catches "7/10", "Score: 7/10", "**7/10**", bold, next-line, etc.
        score_matches = re.findall(r'(\d{1,2})\s*/\s*10', raw_response)
        if score_matches:
            sections['score'] = min(10, max(1, int(score_matches[-1])))  # take last match (usually the real score line)

        for key in sections:
            if isinstance(sections[key], str):
                sections[key] = sections[key].strip()
    except:
        pass
    return sections


# ═══════════════════════════════════════════════════════════
# 🪙 TOKEN ESTIMATOR
# ═══════════════════════════════════════════════════════════

def estimate_tokens(trade_count, diary_count=0, checklist_days=0, analysis_type='report'):
    estimates = {
        'report': {'base': 900, 'per_trade': 160, 'per_diary': 120, 'per_checklist': 50, 'completion': 1000},
        'coach_chat': {'base': 300, 'per_trade': 60, 'per_diary': 80, 'completion': 200},
        'coach_chat_deep': {'base': 400, 'per_trade': 80, 'per_diary': 80, 'completion': 550},
        'goal_analysis': {'base': 300, 'per_trade': 30, 'completion': 200},
        # ═══ NEW: Page analysis types ═══
        'page_journal': {'base': 400, 'per_trade': 60, 'per_diary': 0, 'per_checklist': 0, 'completion': 500},
        'page_analytics': {'base': 500, 'per_trade': 80, 'per_diary': 0, 'per_checklist': 0, 'completion': 600},
        'page_calendar_day': {'base': 250, 'per_trade': 50, 'per_diary': 0, 'per_checklist': 0, 'completion': 300},
        'page_insights': {'base': 450, 'per_trade': 70, 'per_diary': 80, 'per_checklist': 0, 'completion': 550},
        'page_diary': {'base': 300, 'per_trade': 0, 'per_diary': 80, 'per_checklist': 0, 'completion': 400},
        'page_goals': {'base': 350, 'per_trade': 0, 'per_diary': 0, 'per_checklist': 0, 'completion': 350},
        'page_dashboard': {'base': 300, 'per_trade': 50, 'per_diary': 0, 'per_checklist': 0, 'completion': 350},
    }
    est = estimates.get(analysis_type, estimates['report'])
    # For page types that use 'per_entry' instead of 'per_trade', handle specially
    if analysis_type == 'page_diary':
        return est['base'] + diary_count * est.get('per_diary', 80) + est.get('completion', 400)
    if analysis_type == 'page_goals':
        return est['base'] + trade_count * est.get('per_trade', 0) + est.get('completion', 350)
    return est['base'] + trade_count * est['per_trade'] + diary_count * est.get('per_diary', 100) + checklist_days * est.get('per_checklist', 50) + est.get('completion', 500)


def estimate_cost(token_count, model='gpt-4o-mini'):
    costs = {'gpt-4o-mini': 0.02, 'gpt-3.5-turbo': 0.06, 'gpt-4': 0.80}
    return round((token_count / 1000) * costs.get(model, 0.02), 4)


# ═══════════════════════════════════════════════════════════
# 🤖 REAL AI API CALLER — aicredits.in (OpenAI v1.0+)
# ═══════════════════════════════════════════════════════════

def call_ai_api(prompt, model='gpt-4o-mini', max_tokens=300, chat_history=None):
    """Call the AI Credits API (aicredits.in) using gpt-4o-mini. Supports chat history."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url=os.getenv('OPENAI_API_BASE', 'https://aicredits.in/v1'))

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if chat_history:
        for msg in chat_history[-10:]:
            messages.append({"role": msg.role, "content": msg.content[:500]})

    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(model='gpt-4o-mini', messages=messages, max_tokens=max_tokens, temperature=0.7)
        reply = response.choices[0].message.content
        usage = response.usage
        return {'success': True, 'response': reply, 'model_used': 'gpt-4o-mini', 'prompt_tokens': usage.prompt_tokens, 'completion_tokens': usage.completion_tokens, 'total_tokens': usage.total_tokens, 'cost': estimate_cost(usage.total_tokens), 'latency_ms': 0}
    except Exception as e:
        print(f"❌ AI API Error: {str(e)}")
        return {'success': False, 'response': "TRAGENE AI is temporarily unavailable. Please try again later.", 'model_used': 'gpt-4o-mini', 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'cost': 0, 'latency_ms': 0, 'error': str(e)}


# ═══════════════════════════════════════════════════════════
# 🎯 COACH INSIGHTS CREATOR (Bug 1 FIX)
# ═══════════════════════════════════════════════════════════

def create_coach_insights(user, report, sections):
    """Turn a fresh AIReport into CoachInsight cards for the coach page"""
    insights_to_create = []

    # Score-based headline insight
    score = report.performance_score
    if score >= 8:
        insights_to_create.append({
            'title': f'Strong performance — {score}/10',
            'description': sections.get('summary', '')[:300] or 'Your recent trading shows strong discipline.',
            'severity': 'positive'
        })
    elif score <= 4:
        insights_to_create.append({
            'title': f'Performance needs attention — {score}/10',
            'description': sections.get('summary', '')[:300] or 'Recent trades show some concerning patterns.',
            'severity': 'critical'
        })

    # Warnings become their own card
    warnings = sections.get('warnings', '').strip()
    if warnings:
        insights_to_create.append({
            'title': 'Risk flag detected',
            'description': warnings[:300],
            'severity': 'warning'
        })

    # Strengths become their own card
    strengths = sections.get('strengths', '').strip()
    if strengths:
        insights_to_create.append({
            'title': "What's working",
            'description': strengths[:300],
            'severity': 'positive'
        })

    # Action items
    actions = sections.get('action_items', '').strip()
    if actions:
        insights_to_create.append({
            'title': 'Next steps',
            'description': actions[:300],
            'severity': 'info'
        })

    for ins in insights_to_create:
        db.session.add(CoachInsight(
            user_id=user.id,
            insight_type='report_generated',
            title=ins['title'],
            description=ins['description'],
            severity=ins['severity'],
            related_report_id=report.id
        ))


# ═══════════════════════════════════════════════════════════
# 📊 REPORT GENERATOR (Account-scoped)
# ═══════════════════════════════════════════════════════════

def generate_report(user, account_id=None):
    can_use, message = user.can_use_ai()
    if not can_use: return {'success': False, 'message': message}
    if account_id is None: account_id = _get_account_id(user)

    last_date = user.last_analyzed_date or (datetime.utcnow() - timedelta(days=30)).date()
    period_start, period_end = last_date, datetime.utcnow().date()

    trades = Trade.query.filter(Trade.user_id == user.id, Trade.account_id == account_id, db.func.date(Trade.entry_date) >= period_start).order_by(Trade.entry_date.asc()).all()
    diary_entries = DiaryEntry.query.filter(DiaryEntry.user_id == user.id, DiaryEntry.account_id == account_id, DiaryEntry.entry_date >= period_start).order_by(DiaryEntry.entry_date.asc()).all()
    checklists = Checklist.query.filter(Checklist.user_id == user.id, Checklist.account_id == account_id).all()

    checklist_data = {'completed_days': 0, 'total_days': 0}
    for cl in checklists:
        for c in ChecklistCompletion.query.filter(ChecklistCompletion.checklist_id == cl.id, ChecklistCompletion.date >= period_start).all():
            checklist_data['total_days'] += 1
            if c.completed: checklist_data['completed_days'] += 1

    if not trades and not diary_entries: return {'success': False, 'message': 'No new data in this account since last report.'}

    last_report = AIReport.query.filter_by(user_id=user.id, account_id=account_id).order_by(AIReport.created_at.desc()).first()
    previous_context = last_report.ai_context if last_report else None

    estimated = estimate_tokens(len(trades), len(diary_entries), checklist_data.get('total_days', 0), 'report')
    remaining = user.get_remaining_tokens()
    if estimated > remaining: return {'success': False, 'message': f'Insufficient tokens. Need ~{estimated:,}, have {remaining:,}.'}

    prompt = build_report_prompt(user, trades, diary_entries, checklist_data, previous_context)
    result = call_ai_api(prompt, max_tokens=1200)
    if not result['success']: return {'success': False, 'message': 'AI analysis failed.'}

    clean = clean_ai_response(result['response'])
    sections = extract_report_sections(result['response'])

    report = AIReport(user_id=user.id, account_id=account_id, report_date=period_end, period_start=period_start, period_end=period_end, trades_analyzed=len(trades), diary_entries_analyzed=len(diary_entries), checklist_days_analyzed=checklist_data.get('total_days', 0), user_summary=sections.get('summary', clean[:500]), strengths=sections.get('strengths', ''), warnings=sections.get('warnings', ''), action_items=sections.get('action_items', ''), performance_score=sections.get('score', 5), raw_prompt=prompt, raw_response=result['response'], model_used=result['model_used'], prompt_tokens=result['prompt_tokens'], completion_tokens=result['completion_tokens'], total_tokens=result['total_tokens'], api_cost=result['cost'], ai_context=json.dumps(build_ai_context(user, sections, trades)), report_type='manual')
    
    db.session.add(report)
    db.session.flush()  # 🔥 FIX: get report.id before commit (Bug 1)
    
    create_coach_insights(user, report, sections)  # 🔥 FIX: Create coach insights (Bug 1)
    
    db.session.add(AIUsageLog(user_id=user.id, report_id=report.id, analysis_type='report_generation', model_used=result['model_used'], prompt_tokens=result['prompt_tokens'], completion_tokens=result['completion_tokens'], total_tokens=result['total_tokens'], api_cost=result['cost'], api_latency_ms=result['latency_ms'], status='success'))
    user.last_analyzed_date = datetime.utcnow().date()
    db.session.commit()
    return {'success': True, 'message': 'Report generated!', 'report': {'id': report.id, 'date': report.report_date.isoformat(), 'summary': report.user_summary, 'strengths': report.strengths, 'warnings': report.warnings, 'action_items': report.action_items, 'score': report.performance_score, 'trades_analyzed': report.trades_analyzed, 'tokens_used': report.total_tokens, 'cost': report.api_cost}}


# ═══════════════════════════════════════════════════════════
# 🧠 COACH CHAT (intent-routed, date-aware, token-adaptive)
# ═══════════════════════════════════════════════════════════

def coach_chat(user, question, session_id=None):
    """
    Handle AI coach chat with proper session isolation.
    - If session_id provided: continue in that specific session
    - If session_id is None: ALWAYS create a new session (never reuse old ones)
    """
    account_id = _get_account_id(user)

    # 🔥 FIXED: Proper session handling for isolated chats
    if session_id:
        # Only use existing session if explicitly provided
        session = AIChatSession.query.filter_by(
            id=session_id, 
            user_id=user.id, 
            account_id=account_id,
            is_active=True
        ).first()
        
        if not session:
            return {
                'success': False, 
                'message': 'Session not found or already deleted.'
            }
    else:
        # ALWAYS create a new session when no session_id is provided
        # Never fall back to the "last active session"
        title = question[:50] if len(question) > 50 else question
        session = AIChatSession(
            user_id=user.id,
            account_id=account_id,
            title=title,
            is_active=True
        )
        db.session.add(session)
        db.session.flush()

    # Get chat history ONLY for this specific session (limited to last 10 for performance)
    chat_history = AIChatMessage.query.filter_by(
        session_id=session.id
    ).order_by(AIChatMessage.created_at.asc()).limit(10).all()

    # Build prompt via intent router — decides scope, pulls only needed data
    prompt, is_deep = build_coach_prompt(user, question, account_id, chat_history)

    est_type = 'coach_chat_deep' if is_deep else 'coach_chat'
    estimated = estimate_tokens(0, 0, 0, est_type)  # rough gate check, actual cost tracked post-call
    remaining = user.get_remaining_tokens()
    if estimated > remaining: 
        return {
            'success': False, 
            'message': f'Insufficient tokens. Need ~{estimated:,}, have {remaining:,}.'
        }

    # Save user message to THIS session only
    user_msg = AIChatMessage(
        session_id=session.id,
        role='user',
        content=question
    )
    db.session.add(user_msg)

    max_tok = 550 if is_deep else 200
    result = call_ai_api(prompt, max_tokens=max_tok, chat_history=chat_history)

    if not result['success']: 
        return {
            'success': False, 
            'message': 'TRAGENE AI is unavailable right now.'
        }

    response = clean_ai_response(result['response'])

    # Save AI response to THIS session only
    ai_msg = AIChatMessage(
        session_id=session.id,
        role='assistant',
        content=response,
        tokens_used=result['total_tokens']
    )
    db.session.add(ai_msg)

    # Update session title if this is the first message exchange
    if len(chat_history) == 0:
        session.title = question[:50] if len(question) > 50 else question

    session.updated_at = datetime.utcnow()

    # Log usage
    db.session.add(AIUsageLog(
        user_id=user.id,
        analysis_type='coach_chat',
        model_used=result['model_used'],
        prompt_tokens=result['prompt_tokens'],
        completion_tokens=result['completion_tokens'],
        total_tokens=result['total_tokens'],
        api_cost=result['cost'],
        api_latency_ms=result['latency_ms'],
        status='success'
    ))
    
    db.session.commit()

    return {
        'success': True,
        'response': response,
        'session_id': session.id,
        'session_title': session.title,
        'tokens_used': result['total_tokens'],
        'remaining': user.get_remaining_tokens()
    }


def get_chat_sessions(user):
    """Get all active chat sessions for the user, ordered by most recent"""
    return AIChatSession.query.filter_by(
        user_id=user.id, 
        is_active=True
    ).order_by(
        AIChatSession.updated_at.desc()
    ).limit(20).all()


def get_chat_messages(session_id, user_id):
    """Get all messages for a specific chat session"""
    session = AIChatSession.query.filter_by(
        id=session_id, 
        user_id=user_id,
        is_active=True
    ).first()
    
    if not session: 
        return []
        
    return AIChatMessage.query.filter_by(
        session_id=session.id
    ).order_by(
        AIChatMessage.created_at.asc()
    ).all()


def delete_chat_session(session_id, user_id):
    """Soft delete a chat session (marks as inactive)"""
    session = AIChatSession.query.filter_by(
        id=session_id, 
        user_id=user_id
    ).first()
    
    if session:
        session.is_active = False
        db.session.commit()
        return True
    return False


# ═══════════════════════════════════════════════════════════
# 🎯 GOAL ANALYSIS (Account-scoped)
# ═══════════════════════════════════════════════════════════

def analyze_goal(user, goal_id):
    goal = TradingGoal.query.filter_by(id=goal_id, user_id=user.id).first()
    if not goal: return {'success': False, 'message': 'Goal not found.'}
    account_id = _get_account_id(user)
    trades = Trade.query.filter(Trade.user_id == user.id, Trade.account_id == account_id, db.func.date(Trade.entry_date) >= goal.start_date).all()
    prompt = build_goal_analysis_prompt(user, goal, trades)
    result = call_ai_api(prompt, max_tokens=150)
    if result['success']:
        insight = clean_ai_response(result['response'])
        goal.ai_insight = insight
        db.session.add(AIUsageLog(user_id=user.id, analysis_type='goal_analysis', model_used=result['model_used'], prompt_tokens=result['prompt_tokens'], completion_tokens=result['completion_tokens'], total_tokens=result['total_tokens'], api_cost=result['cost'], status='success'))
        db.session.commit()
        return {'success': True, 'insight': insight}
    return {'success': False, 'message': 'Analysis failed.'}


# ═══════════════════════════════════════════════════════════
# 🔧 HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def build_ai_context(user, sections, trades):
    patterns = {'best_session': None, 'worst_day': None, 'avg_risk': 0, 'common_mistakes': []}
    session_pnl = {}
    for t in trades:
        sess = t.session or 'unknown'
        session_pnl[sess] = session_pnl.get(sess, 0) + (t.profit_loss or 0)
    if session_pnl: patterns['best_session'] = max(session_pnl, key=session_pnl.get)
    day_pnl = {}
    for t in trades:
        day = t.entry_date.strftime('%A')
        day_pnl[day] = day_pnl.get(day, 0) + (t.profit_loss or 0)
    if day_pnl: patterns['worst_day'] = min(day_pnl, key=day_pnl.get)
    no_sl_count = len([t for t in trades if not t.stop_loss])
    if no_sl_count > 0: patterns['common_mistakes'].append(f'{no_sl_count} trades without stop loss')
    previous_reports = AIReport.query.filter_by(user_id=user.id).order_by(AIReport.created_at.desc()).limit(5).all()
    previous_scores = [r.performance_score for r in previous_reports]
    return {'user_patterns': patterns, 'trends': {'win_rate': sections.get('summary', '')[:100]}, 'pending_actions': sections.get('action_items', '').split('\n')[:3] if sections.get('action_items') else [], 'previous_scores': previous_scores[::-1], 'last_analysis_date': datetime.utcnow().date().isoformat()}


def get_user_reports(user_id, account_id=None, limit=10):
    query = AIReport.query.filter_by(user_id=user_id)
    if account_id: query = query.filter_by(account_id=account_id)
    return query.order_by(AIReport.created_at.desc()).limit(limit).all()


def get_unanalyzed_count(user):
    account_id = _get_account_id(user)
    if not account_id:
        return 0
    if not user.last_analyzed_date:
        return Trade.query.filter_by(user_id=user.id, account_id=account_id).count()
    return Trade.query.filter(
        Trade.user_id == user.id,
        Trade.account_id == account_id,
        db.func.date(Trade.entry_date) >= user.last_analyzed_date
    ).count()


def seed_plan_defaults():
    defaults = [
        {'plan_tier': 'free', 'monthly_tokens': 2000, 'daily_requests': 2, 'queries_per_week': 2, 'reports_per_week': 2},
        {'plan_tier': 'pro', 'monthly_tokens': 50000, 'daily_requests': 50, 'queries_per_week': None, 'reports_per_week': None},
        {'plan_tier': 'elite', 'monthly_tokens': 150000, 'daily_requests': 150, 'queries_per_week': None, 'reports_per_week': None},
        {'plan_tier': 'enterprise', 'monthly_tokens': 500000, 'daily_requests': None, 'queries_per_week': None, 'reports_per_week': None},
    ]
    for d in defaults:
        plan_default = AIPlanDefaults.query.filter_by(plan_tier=d['plan_tier']).first()
        if not plan_default:
            db.session.add(AIPlanDefaults(**d))
        elif plan_default.daily_requests is None and d['daily_requests'] is not None:
            plan_default.daily_requests = d['daily_requests']
    db.session.commit()
    print("✅ AI Plan defaults seeded!")


# ═══════════════════════════════════════════════════════════
# 📏 DATA WINDOW LOGIC — prevents token overrun on large datasets
# ═══════════════════════════════════════════════════════════

def _apply_data_window(items, max_items=40, max_days=30, fallback_days=15):
    """
    If items exceed max_items, shrink to max_days.
    If still too many after max_days, fall back to fallback_days.
    Returns (filtered_items, date_range_start, date_range_end, note_string_or_None).
    Works on both trades (has .entry_date) and diary entries (has .entry_date).
    """
    now = datetime.utcnow()
    
    if len(items) <= max_items:
        return items, None, None, None
    
    # Try max_days window
    cutoff = now - timedelta(days=max_days)
    filtered = [i for i in items if i.entry_date >= cutoff]
    
    if len(filtered) <= max_items:
        return filtered, cutoff.date(), now.date(), None
    
    # Still too many — fall back
    cutoff = now - timedelta(days=fallback_days)
    filtered = [i for i in items if i.entry_date >= cutoff]
    note = f"Showing last {fallback_days} days — you have more data than we can analyze in one pass."
    return filtered, cutoff.date(), now.date(), note


# ═══════════════════════════════════════════════════════════
# 🗺️ DISPATCH TABLE — maps page_key → prompt builder
# ═══════════════════════════════════════════════════════════

PAGE_ANALYZERS = {
    'journal': None,  # Will be assigned after function definition
    'analytics': None,
    'calendar_day': None,
    'insights': None,
    'diary': None,
    'goals': None,
    'dashboard': None,
}


# ═══════════════════════════════════════════════════════════
# 📄 PER-PAGE AI ANALYSIS — Main entry point
# ═══════════════════════════════════════════════════════════

def analyse_page(user, page_key, account_id, extra_params=None):
    """
    Main entry point for per-page AI analysis.
    Same pattern as generate_report(): token gate → build prompt → call AI → save → return.
    
    Args:
        user: User object
        page_key: 'journal' | 'analytics' | 'calendar_day' | 'insights' | 'diary' | 'goals' | 'dashboard'
        account_id: Active trading account ID
        extra_params: dict with optional 'sub_id' (date string for calendar_day, goal_id for goals)
    
    Returns:
        {'success': bool, 'analysis_id': int, 'tokens_used': int, 'cost': float, ...}
    """
    
    if page_key not in PAGE_ANALYZERS:
        return {'success': False, 'message': f'Unknown page: {page_key}'}
    
    extra_params = extra_params or {}
    sub_id = extra_params.get('sub_id')
    
    # ── Token gate ──
    can_use, message = user.can_use_ai()
    if not can_use:
        return {'success': False, 'message': message}
    
    # ── Pull data ──
    analysis_type = f'page_{page_key}'
    trades = _get_scoped_trades(user, account_id, 'deep')  # Up to 30 trades, account-scoped
    
    # Estimate tokens based on page type
    if page_key == 'diary':
        diary_entries = DiaryEntry.query.filter_by(
            user_id=user.id, account_id=account_id
        ).order_by(DiaryEntry.entry_date.desc()).limit(30).all()
        estimated = estimate_tokens(0, len(diary_entries), 0, analysis_type)
    elif page_key == 'goals':
        goals = TradingGoal.query.filter_by(
            user_id=user.id, account_id=account_id, is_completed=False
        ).all()
        estimated = estimate_tokens(len(goals), 0, 0, analysis_type)
    else:
        estimated = estimate_tokens(len(trades), 0, 0, analysis_type)
    
    remaining = user.get_remaining_tokens()
    
    if estimated > remaining:
        return {
            'success': False,
            'message': f"This analysis needs ~{estimated:,} tokens, you have {remaining:,} left. "
                       f"Upgrade your plan for more, or wait for your monthly reset."
        }
    
    # ── Build prompt & call AI ──
    builder = PAGE_ANALYZERS[page_key]
    
    if page_key == 'calendar_day':
        target_date = datetime.strptime(sub_id, '%Y-%m-%d').date() if sub_id else datetime.utcnow().date()
        day_trades = Trade.query.filter(
            Trade.user_id == user.id,
            Trade.account_id == account_id,
            db.func.date(Trade.entry_date) == target_date
        ).order_by(Trade.entry_date.desc()).all()
        day_note = DayNote.query.filter_by(
            user_id=user.id, account_id=account_id, note_date=target_date
        ).first()
        prompt = builder(user, account_id, day_trades, day_note, target_date)
    elif page_key == 'goals':
        goals = TradingGoal.query.filter_by(
            user_id=user.id, account_id=account_id, is_completed=False
        ).all()
        prompt = builder(user, goals, trades)
    elif page_key == 'diary':
        diary_entries = DiaryEntry.query.filter_by(
            user_id=user.id, account_id=account_id
        ).order_by(DiaryEntry.entry_date.desc()).limit(30).all()
        prompt = builder(user, account_id, diary_entries, trades)
    else:
        prompt = builder(user, account_id, trades)
    
    max_tokens = 600 if page_key in ('journal', 'insights') else 500 if page_key == 'analytics' else 350
    result = call_ai_api(prompt, max_tokens=max_tokens)
    
    if not result['success']:
        return {'success': False, 'message': 'AI analysis failed. Please try again.'}
    
    response = clean_ai_response(result['response'])
    sections = extract_page_sections(response, page_key)
    
    # ── Save to DB ──
    date_range_start, date_range_end = None, None
    if trades:
        date_range_start = trades[-1].entry_date.date() if trades else None
        date_range_end = trades[0].entry_date.date() if trades else None
    
    analysis = AIPageAnalysis(
        user_id=user.id,
        account_id=account_id,
        page_key=page_key,
        sub_id=sub_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        content=response,
        summary=sections.get('summary', ''),
        standout_wins=json.dumps(sections.get('standout_wins', [])),
        standout_losses=json.dumps(sections.get('standout_losses', [])),
        money_leaks=json.dumps(sections.get('money_leaks', [])),
        suggestions=json.dumps(sections.get('suggestions', [])),
        score=sections.get('score'),
        tokens_used=result['total_tokens'],
        api_cost=result['cost'],
        model_used=result['model_used'],
        trades_analyzed=len(trades) if page_key != 'diary' else 0,
        entries_analyzed=len(diary_entries) if page_key == 'diary' else 0
    )
    db.session.add(analysis)
    
    # Log usage
    db.session.add(AIUsageLog(
        user_id=user.id,
        analysis_type=analysis_type,
        model_used=result['model_used'],
        prompt_tokens=result['prompt_tokens'],
        completion_tokens=result['completion_tokens'],
        total_tokens=result['total_tokens'],
        api_cost=result['cost'],
        api_latency_ms=result['latency_ms'],
        status='success'
    ))
    
    db.session.commit()
    
    return {
        'success': True,
        'analysis_id': analysis.id,
        'page_key': page_key,
        'tokens_used': result['total_tokens'],
        'cost': result['cost'],
        'remaining': user.get_remaining_tokens()
    }


def extract_page_sections(raw_response, page_key):
    """
    Extract structured sections from page analysis response.
    Simpler than extract_report_sections() — pages are narrower.
    Returns dict with: summary, standout_wins, standout_losses, money_leaks, suggestions, score
    """
    sections = {
        'summary': '',
        'standout_wins': [],
        'standout_losses': [],
        'money_leaks': [],
        'suggestions': [],
        'score': None
    }
    
    try:
        lines = raw_response.split('\n')
        current_section = None
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            
            # Detect sections
            lower = stripped.lower()
            if any(kw in lower for kw in ['summary', 'overview', '📊']):
                current_section = 'summary'
                continue
            elif any(kw in lower for kw in ['biggest win', 'standout win', 'best trade', '🏆']):
                current_section = 'standout_wins'
                continue
            elif any(kw in lower for kw in ['biggest loss', 'standout loss', 'worst trade', '📉']):
                current_section = 'standout_losses'
                continue
            elif any(kw in lower for kw in ['leaking', 'money leak', 'risk concern', '⚠️', 'unprotected']):
                current_section = 'money_leaks'
                continue
            elif any(kw in lower for kw in ['suggestion', 'action', 'fix', 'recommend', '🎯', 'try']):
                current_section = 'suggestions'
                continue
            elif 'score' in lower or '📈' in stripped:
                current_section = None
                score_matches = re.findall(r'(\d{1,2})\s*/\s*10', stripped)
                if score_matches:
                    sections['score'] = min(10, max(1, int(score_matches[-1])))
                continue
            
            if current_section == 'summary':
                sections['summary'] += stripped + ' '
            elif current_section in ('standout_wins', 'standout_losses', 'money_leaks', 'suggestions'):
                if stripped.startswith('-') or stripped.startswith('•') or stripped[0].isdigit():
                    sections[current_section].append(stripped.lstrip('-•1234567890.) '))
        
        sections['summary'] = sections['summary'].strip()
        
    except Exception:
        pass
    
    return sections


# ═══════════════════════════════════════════════════════════
# 📊 PAGE PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════

def build_journal_prompt(user, account_id, trades):
    """Analyze trade list: wins/losses, SL/TP discipline, symbol concentration"""
    stats_text, trade_lines = _compute_trade_stats(trades)
    date_ctx = _get_date_context(user, account_id)
    
    if not stats_text:
        return f"No trades in this account yet. Tell {user.username} to log some trades first."
    
    return f"""Analyze {user.username}'s recent trades. {date_ctx}

{stats_text}

TRADES:
{trade_lines}

You are analyzing the TRADE JOURNAL page specifically. Focus ONLY on:
1. Win/loss breakdown — cite the exact numbers
2. Biggest single win and biggest single loss — symbol, date, dollar amount
3. Stop-loss discipline — how many trades had NO stop loss? Call out those specifically
4. Take-profit usage
5. Symbol concentration — is the user overtrading one symbol?

FORMAT YOUR RESPONSE LIKE THIS:
📊 SUMMARY: (1-2 lines with win rate, net P&L)
🏆 BIGGEST WIN: (symbol, date, amount)
📉 BIGGEST LOSS: (symbol, date, amount)
⚠️ MONEY LEAKS: (unprotected losses, specific dollar amounts)
🎯 SUGGESTIONS: (2-3 numbered, specific actions tied to the data)
📈 SCORE: X/10 (one-line reason)

Keep it 6-10 lines total. Every line must cite a real number. Never give generic advice."""


def build_analytics_prompt(user, account_id, trades):
    """Analyze equity curve, drawdown, profit factor, session/day patterns"""
    stats_text, trade_lines = _compute_trade_stats(trades)
    date_ctx = _get_date_context(user, account_id)
    
    if not stats_text:
        return f"No trades in this account yet."
    
    # Session breakdown
    sessions = {}
    for t in trades:
        sess = t.session or 'unknown'
        sessions.setdefault(sess, {'count': 0, 'pnl': 0, 'wins': 0})
        sessions[sess]['count'] += 1
        sessions[sess]['pnl'] += t.profit_loss or 0
        if t.is_win:
            sessions[sess]['wins'] += 1
    
    session_lines = "\n".join(
        f"- {s.upper()}: {d['count']} trades, {round((d['wins']/d['count'])*100) if d['count'] else 0}% WR, P&L: ${d['pnl']:,.2f}"
        for s, d in sessions.items()
    )
    
    # Day of week
    day_pnl = {}
    for t in trades:
        day = t.entry_date.strftime('%A')
        day_pnl.setdefault(day, 0)
        day_pnl[day] += t.profit_loss or 0
    best_day = max(day_pnl, key=day_pnl.get) if day_pnl else 'N/A'
    worst_day = min(day_pnl, key=day_pnl.get) if day_pnl else 'N/A'
    
    return f"""Analyze {user.username}'s trading analytics. {date_ctx}

{stats_text}

SESSION BREAKDOWN:
{session_lines}

DAY OF WEEK:
Best day: {best_day} (${day_pnl.get(best_day, 0):,.2f})
Worst day: {worst_day} (${day_pnl.get(worst_day, 0):,.2f})

You are analyzing the ANALYTICS page. Focus on:
1. Equity curve shape — is it trending up, choppy, or declining?
2. Drawdown patterns — any big single-day drops?
3. Profit factor — is it above 1.5 (sustainable)?
4. Best/worst session — cite numbers
5. Best/worst day of week

FORMAT:
📊 SUMMARY: (1-2 lines)
⚠️ MONEY LEAKS: (worst session/day, drawdown concerns)
🎯 SUGGESTIONS: (2-3 numbered actions)
📈 SCORE: X/10

Keep it 6-10 lines. Every line cites a number."""


def build_calendar_day_prompt(user, account_id, day_trades, day_note, target_date):
    """Single day analysis — very narrow scope"""
    date_str = target_date.strftime('%d %b %Y')
    
    if not day_trades:
        note_text = day_note.note[:200] if day_note else 'No notes'
        return f"""User {user.username} is looking at {date_str}.
No trades on this day.
Day note: {note_text}

Give a 1-2 line response acknowledging no trades, and if there's a note, briefly comment on it."""
    
    wins = [t for t in day_trades if t.is_win]
    losses = [t for t in day_trades if not t.is_win]
    day_pnl = sum(t.profit_loss or 0 for t in day_trades)
    no_sl = [t for t in day_trades if not t.stop_loss]
    
    trade_lines = "\n".join(
        f"- {t.symbol} {t.trade_type.upper()} | Entry:{t.entry_price} Exit:{t.exit_price or 'Open'} | "
        f"P&L:{'+' if t.profit_loss and t.profit_loss > 0 else ''}{t.profit_loss or 0:.2f} | "
        f"SL:{t.stop_loss or 'None'} TP:{t.take_profit or 'None'}"
        for t in day_trades
    )
    
    note_text = day_note.note[:200] if day_note else 'No notes for this day'
    
    return f"""Analyze {user.username}'s trading day: {date_str}

TRADES ({len(day_trades)}):
{trade_lines}

DAY NOTE: {note_text}

P&L: ${day_pnl:,.2f} | Wins: {len(wins)} | Losses: {len(losses)} | No SL: {len(no_sl)}/{len(day_trades)}

FORMAT:
📊 SUMMARY: (1 line about this day)
⚠️ CONCERNS: (only if issues — unprotected trades, oversized risk)
🎯 TIP: (1 specific tip)

Keep it 3-5 lines. Short and focused on this single day."""


def build_insights_prompt(user, account_id, trades):
    """Streaks, inactivity, symbol mismatch, overtrading patterns"""
    stats_text, trade_lines = _compute_trade_stats(trades)
    date_ctx = _get_date_context(user, account_id)
    
    if not stats_text:
        return f"No trades to analyze yet."
    
    # Win/loss streaks
    sorted_trades = sorted(trades, key=lambda t: t.entry_date)
    max_win_streak, max_loss_streak = 0, 0
    cur_streak, cur_type = 0, None
    for t in sorted_trades:
        is_w = t.is_win
        if cur_type == is_w:
            cur_streak += 1
        else:
            cur_type, cur_streak = is_w, 1
        if is_w:
            max_win_streak = max(max_win_streak, cur_streak)
        else:
            max_loss_streak = max(max_loss_streak, cur_streak)
    
    # Inactivity
    trade_dates = sorted(set(t.entry_date.date() for t in trades))
    gaps = []
    for i in range(1, len(trade_dates)):
        gap = (trade_dates[i] - trade_dates[i-1]).days
        if gap > 3:
            gaps.append(f"{trade_dates[i-1]} → {trade_dates[i]} ({gap} days)")
    
    # Most traded vs most profitable symbol
    symbol_counts = {}
    symbol_pnls = {}
    for t in trades:
        symbol_counts[t.symbol] = symbol_counts.get(t.symbol, 0) + 1
        symbol_pnls[t.symbol] = symbol_pnls.get(t.symbol, 0) + (t.profit_loss or 0)
    
    most_traded = max(symbol_counts, key=symbol_counts.get) if symbol_counts else 'N/A'
    most_profitable = max(symbol_pnls, key=symbol_pnls.get) if symbol_pnls else 'N/A'
    mismatch = most_traded != most_profitable
    
    return f"""Analyze {user.username}'s trading insights. {date_ctx}

{stats_text}

STREAKS: Max win streak: {max_win_streak} | Max loss streak: {max_loss_streak}
INACTIVITY GAPS (>3 days): {len(gaps)} gaps found
SYMBOL MISMATCH: Most traded: {most_traded} | Most profitable: {most_profitable} {'⚠️ MISMATCH' if mismatch else '✅ Aligned'}

You are analyzing the INSIGHTS page. Focus on:
1. Streak patterns — is there revenge trading after losses?
2. Inactivity gaps — any long breaks that might signal demotivation?
3. Symbol mismatch — is the user grinding a symbol that loses money while ignoring a winner?
4. Overtrading spikes

FORMAT:
📊 SUMMARY: (1-2 lines)
⚠️ MONEY LEAKS: (streak/behavioral concerns)
🎯 SUGGESTIONS: (2-3 actions)
📈 SCORE: X/10

Keep it 6-10 lines. Every line cites data."""


def build_diary_prompt(user, account_id, diary_entries, trades):
    """Mood-vs-performance correlation, recurring themes"""
    date_ctx = _get_date_context(user, account_id)
    
    if not diary_entries:
        return f"No diary entries yet. Tell {user.username} to start journaling for deeper insights."
    
    # Mood correlation
    mood_pnl = {}
    for d in diary_entries:
        if not d.mood:
            continue
        same_day_trades = [t for t in trades if t.entry_date.date() == d.entry_date]
        day_pnl = sum(t.profit_loss or 0 for t in same_day_trades)
        mood_pnl.setdefault(d.mood, {'days': 0, 'pnl': 0, 'trades': 0})
        mood_pnl[d.mood]['days'] += 1
        mood_pnl[d.mood]['pnl'] += day_pnl
        mood_pnl[d.mood]['trades'] += len(same_day_trades)
    
    mood_lines = "\n".join(
        f"- Mood '{m}': {d['days']} days, {d['trades']} trades, P&L: ${d['pnl']:,.2f}"
        for m, d in mood_pnl.items()
    ) if mood_pnl else "No mood data logged."
    
    # Recent entries
    diary_lines = "\n".join(
        f"- {d.entry_date.strftime('%d %b')} [{d.mood or 'no mood'}]: {(d.content or '')[:150]}"
        for d in diary_entries[:8]
    )
    
    return f"""Analyze {user.username}'s trading diary. {date_ctx}

MOOD vs PERFORMANCE:
{mood_lines}

RECENT DIARY ENTRIES:
{diary_lines}

Focus on:
1. Does mood predict performance? Cite specific mood→P&L correlations
2. Recurring themes in diary notes — any patterns?
3. Are they journaling consistently or sporadically?

FORMAT:
📊 SUMMARY: (1-2 lines about diary insights)
⚠️ CONCERNS: (only if clear mood-performance pattern)
🎯 SUGGESTIONS: (1-2 tips for better journaling)

Keep it 5-8 lines. If mood data is thin, say so honestly."""


def build_goals_prompt(user, goals, trades):
    """Progress toward each active goal"""
    if not goals:
        return f"No active goals. Suggest {user.username} set some goals in the Goals & Planner."
    
    goal_lines = []
    for g in goals:
        pct = round((g.current_value / g.target_value) * 100) if g.target_value > 0 else 0
        goal_lines.append(
            f"- {g.goal_type.replace('_', ' ').title()}: {g.current_value}/{g.target_value} ({pct}%) | "
            f"Timeframe: {g.timeframe} | Started: {g.start_date.strftime('%d %b')}"
        )
    
    # Trades since goals started
    if goals:
        earliest_start = min(g.start_date for g in goals)
        trades_since = [t for t in trades if t.entry_date.date() >= earliest_start]
        stats_text, _ = _compute_trade_stats(trades_since) if trades_since else (None, '')
    else:
        stats_text = None
    
    return f"""Analyze {user.username}'s goal progress.

ACTIVE GOALS:
{chr(10).join(goal_lines)}

{stats_text or 'No trades in goal period.'}

Focus on:
1. Which goals are on track vs behind?
2. Is the trading performance aligning with the goals?
3. Any goal that needs adjusting?

FORMAT:
📊 SUMMARY: (1 line)
🎯 PER-GOAL: (1 line each — on track or behind)
💡 SUGGESTIONS: (1-2 tips)

Keep it 4-7 lines."""


def build_dashboard_prompt(user, account_id, trades):
    """High-level snapshot — deliberately short"""
    stats_text, trade_lines = _compute_trade_stats(trades)
    date_ctx = _get_date_context(user, account_id)
    
    if not stats_text:
        return f"Welcome {user.username}! No trades yet. Start logging trades to power your dashboard."
    
    return f"""Give {user.username} a quick dashboard snapshot. {date_ctx}

{stats_text}

FORMAT:
📊 SUMMARY: (1 line — overall health)
⚠️ TOP CONCERN: (1 specific issue, or 'None — looking solid!')
💡 QUICK TIP: (1 actionable tip)

Keep it 3-4 lines maximum. This is a dashboard, not a deep dive."""


# Update PAGE_ANALYZERS with actual function references
PAGE_ANALYZERS.update({
    'journal': build_journal_prompt,
    'analytics': build_analytics_prompt,
    'calendar_day': build_calendar_day_prompt,
    'insights': build_insights_prompt,
    'diary': build_diary_prompt,
    'goals': build_goals_prompt,
    'dashboard': build_dashboard_prompt,
})





# ═══════════════════════════════════════════════════════════
# 📖 AUTO-WRITE DIARY (Elite feature)
# ═══════════════════════════════════════════════════════════

def auto_write_diary(user, account_id):
    """
    Generate a diary entry based on today's trading activity.
    Smart sizing: short for no-trade days, medium for 1-2 trades, full for busy days.
    Returns: {'success': bool, 'content': str, 'title': str, 'mood': str, 'tokens_used': int}
    """
    # Token gate
    can_use, message = user.can_use_ai()
    if not can_use:
        return {'success': False, 'message': message}
    
    today = datetime.utcnow().date()
    
    # Get today's trades
    today_trades = Trade.query.filter(
        Trade.user_id == user.id,
        Trade.account_id == account_id,
        db.func.date(Trade.entry_date) == today
    ).order_by(Trade.entry_date.asc()).all()
    
    # Get today's existing diary entry (if any)
    existing_diary = DiaryEntry.query.filter_by(
        user_id=user.id,
        account_id=account_id,
        entry_date=today
    ).first()
    
    # Get today's day notes
    today_notes = DayNote.query.filter_by(
        user_id=user.id,
        account_id=account_id,
        note_date=today
    ).all()
    
    # Get recent trading context (last 3 days) for better reflection
    three_days_ago = today - timedelta(days=3)
    recent_trades = Trade.query.filter(
        Trade.user_id == user.id,
        Trade.account_id == account_id,
        db.func.date(Trade.entry_date) >= three_days_ago,
        db.func.date(Trade.entry_date) < today
    ).order_by(Trade.entry_date.desc()).all()
    
    recent_pnl = sum(t.profit_loss or 0 for t in recent_trades)
    recent_wins = len([t for t in recent_trades if t.is_win])
    recent_losses = len([t for t in recent_trades if not t.is_win and t.profit_loss is not None])
    
    if not today_trades and not today_notes and not recent_trades:
        return {
            'success': False, 
            'message': 'No trading activity found. Log some trades first so I can write your diary!'
        }
    
    # Estimate tokens
    estimated = estimate_tokens(len(today_trades), 0, 0, 'page_diary')
    remaining = user.get_remaining_tokens()
    
    if estimated > remaining:
        return {
            'success': False,
            'message': f'This needs ~{estimated:,} tokens, you have {remaining:,} left.'
        }
    
    # Build trade summary
    wins = [t for t in today_trades if t.is_win]
    losses = [t for t in today_trades if not t.is_win and t.profit_loss is not None]
    total_pnl = sum(t.profit_loss or 0 for t in today_trades)
    
    # Smart trade lines — more detail for fewer trades, less for many
    if len(today_trades) <= 3:
        trade_lines = "\n".join(
            f"- {t.symbol} {t.trade_type.upper()} | Entry: {t.entry_price} | "
            f"Exit: {t.exit_price or 'Open'} | P&L: {'+' if t.profit_loss and t.profit_loss > 0 else ''}"
            f"{t.profit_loss or 0:.2f} | SL: {'✓' if t.stop_loss else '✗'} | "
            f"TP: {'✓' if t.take_profit else '✗'}"
            for t in today_trades
        )
    else:
        trade_lines = "\n".join(
            f"- {t.symbol} {t.trade_type.upper()} | P&L: {'+' if t.profit_loss and t.profit_loss > 0 else ''}"
            f"{t.profit_loss or 0:.2f} | {'WIN' if t.is_win else 'LOSS'}"
            for t in today_trades
        )
    
    notes_lines = "\n".join(f"- {n.note[:200]}" for n in today_notes) if today_notes else "No day notes logged."
    
    existing_text = f"\nEXISTING DIARY (update this): {existing_diary.content[:200]}" if existing_diary else ""
    
    date_ctx = _get_date_context(user, account_id)
    
    # ═══════════════════════════════════════════════
    # SMART STORY SIZING
    # ═══════════════════════════════════════════════
    trade_count = len(today_trades)
    sl_count = len([t for t in today_trades if t.stop_loss])
    tp_count = len([t for t in today_trades if t.take_profit])
    no_sl_losses = [t for t in losses if not t.stop_loss]
    
    recent_symbols = list(set(t.symbol for t in recent_trades))
    recent_best = max(recent_trades, key=lambda t: t.profit_loss or 0, default=None)
    recent_worst = min(recent_trades, key=lambda t: t.profit_loss or 0, default=None)
    recent_sl_count = len([t for t in recent_trades if t.stop_loss])
    recent_total = len(recent_trades)
    recent_sl_pct = round((recent_sl_count/recent_total)*100) if recent_total > 0 else 0
    if trade_count == 0:
        story_size = "ZERO_TRADES"
        mood_hint = "neutral"
        max_tokens = 400
        if recent_pnl < 0:
            mood_hint = "cautious"
        elif recent_pnl > 0:
            mood_hint = "confident"
        
        # Build richer recent context for no-trade days
        
        recent_context_extra = f"""
RECENT TRADING CONTEXT (last 3 days - use these specific details!):
- Symbols traded: {', '.join(recent_symbols) if recent_symbols else 'None'}
- Best trade: {recent_best.symbol + ' +$' + str(recent_best.profit_loss) if recent_best else 'N/A'}
- Worst trade: {recent_worst.symbol + ' $' + str(recent_worst.profit_loss) if recent_worst else 'N/A'}
- Stop-loss usage: {recent_sl_count}/{recent_total} trades had SL set ({recent_sl_pct}%)
- Overall P&L: {'+' if recent_pnl >= 0 else ''}${recent_pnl:,.2f}
- Wins: {recent_wins} | Losses: {recent_losses}"""
        
        # Detailed trade lines for recent trades
        recent_trade_lines = "\n".join(
            f"- {t.entry_date.strftime('%d %b')}: {t.symbol} {t.trade_type.upper()} | "
            f"P&L: {'+' if t.profit_loss and t.profit_loss > 0 else ''}{t.profit_loss or 0:.2f} | "
            f"SL: {'✓' if t.stop_loss else '✗'} | TP: {'✓' if t.take_profit else '✗'}"
            for t in recent_trades[:5]
        )
        recent_context_extra += f"\nRECENT TRADES (last 3 days):\n{recent_trade_lines}"
        
    elif trade_count == 1:
        story_size = "SINGLE_TRADE"
        mood_hint = "confident" if total_pnl >= 0 else "frustrated"
        max_tokens = 350
        recent_context_extra = ""
    elif trade_count <= 3:
        story_size = "FEW_TRADES"
        mood_hint = "confident" if total_pnl >= 0 else "cautious"
        max_tokens = 400
        recent_context_extra = ""
    elif trade_count <= 6:
        story_size = "BUSY_DAY"
        mood_hint = "excited" if total_pnl >= 0 else "frustrated"
        max_tokens = 500
        recent_context_extra = ""
    else:
        story_size = "OVERTRADING"
        mood_hint = "excited" if total_pnl >= 0 else "frustrated"
        max_tokens = 500
        recent_context_extra = ""
    
    prompt = f"""Write a short, honest trading diary entry for {user.username} for today ({today.strftime('%d %B %Y')}).

TODAY: {trade_count} trade(s), {len(wins)}W/{len(losses)}L, Net P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:,.2f}
SL used: {sl_count}/{trade_count if trade_count > 0 else 1} | TP used: {tp_count}/{trade_count if trade_count > 0 else 1}
No-SL losses: {len(no_sl_losses)} trade(s) losing ${sum(t.profit_loss or 0 for t in no_sl_losses):,.2f}

RECENT CONTEXT (last 3 days): {len(recent_trades)} trades, {recent_wins}W/{recent_losses}L, P&L: {'+' if recent_pnl >= 0 else ''}${recent_pnl:,.2f}

TRADES:
{trade_lines if trade_lines else 'No trades taken today.'}

NOTES:
{notes_lines}
{existing_text}
{date_ctx}
{recent_context_extra}

═══════════════════════════════════════
WRITING RULES — FOLLOW EXACTLY:
═══════════════════════════════════════

STORY SIZE: {story_size}
SUGGESTED MOOD: {mood_hint}

IF ZERO_TRADES (no trades today):
→ 8-12 lines MAX
→ Start by mentioning no trades were taken today
→ Reference SPECIFIC recent trades from the RECENT TRADING CONTEXT above — cite exact symbols, dollar amounts, and dates
→ If recent days were profitable: mention protecting those gains, reference the best trade by symbol and amount
→ If recent days were losing: acknowledge the losses honestly, mention the worst trade specifically
→ If SL usage was low: mention needing to improve stop-loss discipline with a specific number ("only {recent_sl_count}/{recent_total} trades had SL")
→ Name 1-2 specific skills or patterns to work on based on the recent data (e.g. "need to stop moving my SL on {{symbol}}" or "should size down on {{symbol}} after that ${{amount}} loss")
→ End with 1-2 forward-looking intentions tied to real trades
→ DO NOT be generic — every paragraph must mention a real symbol, dollar amount, or stat from the RECENT TRADING CONTEXT

IF SINGLE_TRADE (1 trade):
→ 6-10 lines MAX
→ Briefly describe the trade (symbol, result, $ amount)
→ If it was a loss WITHOUT stop loss: mention the mistake clearly but don't over-dramatize
→ If it was a win with proper SL/TP: acknowledge good discipline
→ 1-2 lines on what to improve or repeat tomorrow

IF FEW_TRADES (2-3 trades):
→ 8-14 lines MAX
→ Quick summary of each trade in 1 line each
→ Overall sentiment based on P&L
→ Mention if SL/TP discipline was followed
→ 1-2 specific takeaways

IF BUSY_DAY (4-6 trades):
→ 12-18 lines MAX
→ Brief overview of the day's theme (e.g. "heavy scalping day", "caught a trend")
→ Highlight best and worst trade by $ amount
→ SL/TP discipline check
→ 2-3 actionable takeaways

IF OVERTRADING (7+ trades):
→ 14-20 lines MAX
→ Note the high trade count — mention if this is unusual
→ Summarize overall P&L and win rate
→ Flag any overtrading concerns if P&L is negative
→ Suggest slowing down if losses are piling up

═══════════════════════════════════════
GENERAL RULES:
- Write in FIRST PERSON as {user.username}
- Sound like a real trader journaling, not a novel
- Don't repeat the same point 3 times
- If trades had no SL, mention it once — don't dwell
- End with 1-2 forward-looking lines
- NEVER mention AI, "generated", or that this was auto-written

FORMAT EXACTLY:
TITLE: [short title, 3-8 words]
MOOD: [confident/cautious/frustrated/excited/neutral]
CONTENT: [the entry]"""

    result = call_ai_api(prompt, max_tokens=max_tokens)
    
    if not result['success']:
        return {'success': False, 'message': 'AI generation failed. Try again.'}
    
    response = clean_ai_response(result['response'])
    
    # Parse the response
    title = "Today's Trading"
    mood = "neutral"
    content = response
    
    for line in response.split('\n'):
        line_stripped = line.strip()
        if line_stripped.upper().startswith('TITLE:'):
            title = line_stripped[6:].strip().strip('"').strip("'")
        elif line_stripped.upper().startswith('MOOD:'):
            mood_raw = line_stripped[5:].strip().lower().strip('"').strip("'")
            if mood_raw in ['confident', 'cautious', 'frustrated', 'excited', 'neutral']:
                mood = mood_raw
        elif line_stripped.upper().startswith('CONTENT:'):
            # Get everything after CONTENT:
            content_start = response.upper().find('CONTENT:') + 8
            content = response[content_start:].strip()
            break
    
    # Log usage
    db.session.add(AIUsageLog(
        user_id=user.id,
        analysis_type='auto_diary',
        model_used=result['model_used'],
        prompt_tokens=result['prompt_tokens'],
        completion_tokens=result['completion_tokens'],
        total_tokens=result['total_tokens'],
        api_cost=result['cost'],
        api_latency_ms=result['latency_ms'],
        status='success'
    ))
    db.session.commit()
    
    return {
        'success': True,
        'title': title,
        'mood': mood,
        'content': content,
        'tokens_used': result['total_tokens'],
        'trades_analyzed': len(today_trades)
    }
