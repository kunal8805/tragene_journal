from flask import Blueprint, render_template, make_response, request, url_for
from models import Blog, Category, Tag, SEOSettings, PageMetadata, FAQ
from datetime import datetime
from feedgen.feed import FeedGenerator

seo_bp = Blueprint('seo', __name__)

# ═══════════════════════════════════════════════════
# YOUR REAL DOMAIN
# ═══════════════════════════════════════════════════
SITE_URL = 'https://tragenejournal.com'


def _base_url():
    """Use real domain in production, localhost in dev"""
    if 'localhost' in request.host or '127.0.0.1' in request.host:
        return request.url_root.rstrip('/')
    return SITE_URL.rstrip('/')


# ═══════════════════════════════════════════════════
# 🆕 CONTEXT PROCESSOR - inject 'now' into all templates
# ═══════════════════════════════════════════════════

@seo_bp.context_processor
def inject_now():
    return {'now': datetime.utcnow()}


# ═══════════════════════════════════════════════════
# 🤖 ROBOTS.TXT
# ═══════════════════════════════════════════════════

@seo_bp.route('/robots.txt')
def robots_txt():
    base = _base_url()
    settings = SEOSettings.query.first()
    if settings and settings.robots_txt_content:
        content = settings.robots_txt_content
    else:
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin/\n"
            "Disallow: /dashboard/\n"
            "Disallow: /login/\n"
            "Disallow: /register/\n"
            "Disallow: /api/\n"
            "Disallow: /private/\n"
            f"\nSitemap: {base}/sitemap.xml"
        )
    
    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    return response


# ═══════════════════════════════════════════════════
# 🗺️ SITEMAP.XML
# ═══════════════════════════════════════════════════

@seo_bp.route('/sitemap.xml')
def sitemap():
    """Single flat sitemap — all URLs in one file (best for SEO)"""
    base = _base_url()

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    # ── Main pages ──
    pages = [
        {'loc': f"{base}/", 'priority': '1.0', 'changefreq': 'daily'},
        {'loc': f"{base}/login", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/register", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/blog", 'priority': '0.9', 'changefreq': 'daily'},
        {'loc': f"{base}/faq", 'priority': '0.7', 'changefreq': 'monthly'},
        # ── Company / Legal pages ──
        {'loc': f"{base}/about", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/contact", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/terms", 'priority': '0.5', 'changefreq': 'yearly'},
        {'loc': f"{base}/privacy", 'priority': '0.5', 'changefreq': 'yearly'},
        {'loc': f"{base}/refund-policy", 'priority': '0.5', 'changefreq': 'yearly'},
        # 🆕 Pillar Page
        {'loc': f"{base}/trading-journal", 'priority': '1.0', 'changefreq': 'weekly'},
        # 🆕 SEO Landing Pages
        {'loc': f"{base}/best-free-ai-trading-journal", 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': f"{base}/best-trading-journal-india", 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': f"{base}/crypto-trading-journal", 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': f"{base}/mt5-trading-journal", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/mt4-trading-journal", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/forex-trading-journal", 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': f"{base}/multi-account-trading-journal", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/affordable-trading-journal", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/ai-trading-journal", 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': f"{base}/trading-journal-vs-excel", 'priority': '0.8', 'changefreq': 'monthly'},
        # 🧮 Free Trading Calculators
        {'loc': f"{base}/tools", 'priority': '0.8', 'changefreq': 'weekly'},
        {'loc': f"{base}/tools/position-size-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/risk-reward-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/pip-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/lot-size-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/forex-profit-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/drawdown-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/win-rate-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/crypto-profit-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/dca-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/compounding-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/futures-liquidation-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/expectancy-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/risk-of-ruin-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/goal-calculator", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/tools/ai-trade-review", 'priority': '0.7', 'changefreq': 'monthly'},
    ]

    for page in pages:
        xml.append('  <url>')
        xml.append(f"    <loc>{page['loc']}</loc>")
        xml.append(f"    <changefreq>{page['changefreq']}</changefreq>")
        xml.append(f"    <priority>{page['priority']}</priority>")
        xml.append('  </url>')

    # ── Custom pages from PageMetadata ──
    custom_pages = PageMetadata.query.all()
    for page in custom_pages:
        if page.page_route and page.page_route != 'index' and not page.page_route.startswith('/'):
            xml.append('  <url>')
            xml.append(f"    <loc>{base}/{page.page_route.lstrip('/')}</loc>")
            xml.append(f"    <changefreq>weekly</changefreq>")
            xml.append(f"    <priority>0.8</priority>")
            xml.append('  </url>')

    # ── Blog posts ──
    blogs = Blog.query.filter_by(status='published').order_by(Blog.published_at.desc()).all()
    for blog in blogs:
        xml.append('  <url>')
        xml.append(f"    <loc>{base}/blog/{blog.slug}</loc>")
        xml.append(f"    <lastmod>{blog.updated_at.strftime('%Y-%m-%dT%H:%M:%S+00:00')}</lastmod>")
        xml.append(f"    <changefreq>weekly</changefreq>")
        xml.append(f"    <priority>0.8</priority>")
        xml.append('  </url>')

    # ── Blog categories ──
    categories = Category.query.all()
    for cat in categories:
        xml.append('  <url>')
        xml.append(f"    <loc>{base}/category/{cat.slug}</loc>")
        xml.append(f"    <changefreq>weekly</changefreq>")
        xml.append(f"    <priority>0.7</priority>")
        xml.append('  </url>')

    # ── Blog tags ──
    tags = Tag.query.all()
    for tag in tags:
        xml.append('  <url>')
        xml.append(f"    <loc>{base}/tag/{tag.slug}</loc>")
        xml.append(f"    <changefreq>weekly</changefreq>")
        xml.append(f"    <priority>0.6</priority>")
        xml.append('  </url>')

    xml.append('</urlset>')

    response = make_response('\n'.join(xml))
    response.headers['Content-Type'] = 'application/xml'
    return response


# ═══════════════════════════════════════════════════
# 📡 RSS FEED
# ═══════════════════════════════════════════════════

@seo_bp.route('/feed.xml')
def rss_feed():
    settings = SEOSettings.query.first()
    site_title = settings.site_title if settings and settings.site_title else "Tragene Journal"
    site_desc = settings.default_meta_description if settings and settings.default_meta_description else "AI-Powered Trading Journal — Track, Analyze, Improve."
    
    base = _base_url()
    
    fg = FeedGenerator()
    fg.title(site_title)
    fg.description(site_desc)
    fg.link(href=base, rel='alternate')
    fg.link(href=f"{base}/feed.xml", rel='self')
    fg.language('en')
    
    blogs = Blog.query.filter_by(status='published').order_by(Blog.published_at.desc()).limit(20).all()
    for blog in blogs:
        fe = fg.add_entry()
        fe.id(f"{base}/blog/{blog.slug}")
        fe.title(blog.title)
        fe.link(href=f"{base}/blog/{blog.slug}")
        fe.description(blog.excerpt or blog.meta_description or blog.title)
        if blog.author:
            fe.author(name=blog.author.username)
        fe.pubDate(blog.published_at.strftime('%a, %d %b %Y %H:%M:%S +0000'))
        
    response = make_response(fg.rss_str(pretty=True))
    response.headers['Content-Type'] = 'application/xml'
    return response


# ═══════════════════════════════════════════════════
# 📄 FAQ PAGE
# ═══════════════════════════════════════════════════

@seo_bp.route('/faq')
def faq_page():
    """Public FAQ page with all categories"""
    faqs = FAQ.query.filter_by(is_active=True).order_by(FAQ.category, FAQ.display_order).all()
    faq_categories = {}
    for faq in faqs:
        if faq.category not in faq_categories:
            faq_categories[faq.category] = []
        faq_categories[faq.category].append(faq)
    
    return render_template('faq.html',
        faq_categories=faq_categories,
        now=datetime.utcnow()
    )


# ═══════════════════════════════════════════════════
# 🏛️ PILLAR PAGE
# ═══════════════════════════════════════════════════

@seo_bp.route('/trading-journal')
def trading_journal_pillar():
    return render_template('seo/trading-journal.html',
        page_title='The Ultimate Trading Journal Guide 2026 | Tragene Journal',
        page_description='Complete guide to trading journals. AI-powered analysis, free crypto auto-sync, MT4/MT5 support, multi-market tracking. Start free at TrageneJournal.com.',
        page_keywords='trading journal, best trading journal, ai trading journal, free trading journal, crypto journal, forex journal, trading tracker'
    )


# ═══════════════════════════════════════════════════
# 🆕 SEO LANDING PAGES
# ═══════════════════════════════════════════════════

@seo_bp.route('/best-free-ai-trading-journal')
def best_free_ai_trading_journal():
    return render_template('seo/best-free-ai-trading-journal.html',
        page_title='Best Free AI Trading Journal 2026 | Tragene Journal',
        page_description='Looking for the best free AI trading journal? Tragene Journal offers free AI trade analysis, crypto auto-sync, and unlimited manual journaling. Start free today.',
        page_keywords='best free ai trading journal, free trading journal, ai trade analysis, free crypto journal'
    )


@seo_bp.route('/best-trading-journal-india')
def best_trading_journal_india():
    return render_template('seo/best-trading-journal-india.html',
        page_title='Best Trading Journal in India 2026 | ₹399/mo | Tragene Journal',
        page_description='The best trading journal for Indian traders. UPI payments, INR pricing, supports NSE, MCX, Forex & Crypto. AI-powered analysis. Start free.',
        page_keywords='best trading journal india, trading journal for indian traders, indian stock journal, nse trading journal'
    )


@seo_bp.route('/crypto-trading-journal')
def crypto_trading_journal():
    return render_template('seo/crypto-trading-journal.html',
        page_title='Crypto Trading Journal with Free Auto Sync | Tragene Journal',
        page_description='Free crypto trading journal with auto-sync for Binance, Bybit, OKX, KuCoin. AI-powered trade analysis. Track your crypto trades automatically.',
        page_keywords='crypto trading journal, free crypto sync, binance journal, crypto trade tracker, crypto pnl tracker'
    )


@seo_bp.route('/mt5-trading-journal')
def mt5_trading_journal():
    return render_template('seo/mt5-trading-journal.html',
        page_title='MT5 Trading Journal | Auto Import & AI Analysis | Tragene Journal',
        page_description='MT5 trading journal with auto import. AI-powered analysis for MetaTrader 5 trades. Track, analyze, and improve your MT5 trading performance.',
        page_keywords='mt5 trading journal, metatrader 5 journal, mt5 trade analysis, mt5 auto import'
    )


@seo_bp.route('/mt4-trading-journal')
def mt4_trading_journal():
    return render_template('seo/mt4-trading-journal.html',
        page_title='MT4 Trading Journal | Auto Import & AI Analysis | Tragene Journal',
        page_description='MT4 trading journal with auto import. AI-powered analysis for MetaTrader 4 trades. Track, analyze, and improve your MT4 trading performance.',
        page_keywords='mt4 trading journal, metatrader 4 journal, mt4 trade analysis, mt4 auto import'
    )


@seo_bp.route('/forex-trading-journal')
def forex_trading_journal():
    return render_template('seo/forex-trading-journal.html',
        page_title='Forex Trading Journal with AI Analysis | Tragene Journal',
        page_description='Professional forex trading journal with AI-powered analysis, MT5 integration, risk management, and multi-account support. Track every pip.',
        page_keywords='forex trading journal, forex trade tracker, forex pnl tracker, forex ai analysis'
    )


@seo_bp.route('/multi-account-trading-journal')
def multi_account_trading_journal():
    return render_template('seo/multi-account-trading-journal.html',
        page_title='Multi-Account Trading Journal | Manage All Accounts | Tragene Journal',
        page_description='One trading journal for all your accounts. Crypto, Forex, Futures, Indian Stocks — all in one place. AI analysis across every account.',
        page_keywords='multi account trading journal, multiple trading accounts, trading journal for all markets'
    )


@seo_bp.route('/affordable-trading-journal')
def affordable_trading_journal():
    return render_template('seo/affordable-trading-journal.html',
        page_title='Affordable Trading Journal | ₹399/mo with AI | Tragene Journal',
        page_description='Most affordable AI-powered trading journal. Pro plan at ₹399/mo with unlimited AI, MT5 sync, and 10 accounts. Free plan available.',
        page_keywords='affordable trading journal, cheap trading journal, low cost trading tracker, budget trading journal'
    )


@seo_bp.route('/ai-trading-journal')
def ai_trading_journal():
    return render_template('seo/ai-trading-journal.html',
        page_title='AI Trading Journal | AI-Powered Trade Analysis | Tragene Journal',
        page_description='AI trading journal that analyzes every trade. Get AI scores, strength/weakness detection, risk alerts, and personalized coaching. Smarter trading starts here.',
        page_keywords='ai trading journal, ai trade analysis, ai trading coach, ai trade review, artificial intelligence trading'
    )


@seo_bp.route('/trading-journal-vs-excel')
def trading_journal_vs_excel():
    return render_template('seo/trading-journal-vs-excel.html',
        page_title='Trading Journal vs Excel | Why Upgrade? | Tragene Journal',
        page_description='Trading journal vs Excel spreadsheet. Discover why professional traders switch from Excel to AI-powered trading journals. Auto-sync, AI analysis, better insights.',
        page_keywords='trading journal vs excel, excel trading journal, spreadsheet vs trading journal, why use trading journal'
    )


# ═══════════════════════════════════════════════════
# 🧮 FREE TRADING CALCULATORS
# ═══════════════════════════════════════════════════

@seo_bp.route('/tools/position-size-calculator')
def position_size_calculator():
    return render_template('tools/position-size-calculator.html')

@seo_bp.route('/tools/risk-reward-calculator')
def risk_reward_calculator():
    return render_template('tools/risk-reward-calculator.html')

@seo_bp.route('/tools/pip-calculator')
def pip_calculator():
    return render_template('tools/pip-calculator.html')

@seo_bp.route('/tools/lot-size-calculator')
def lot_size_calculator():
    return render_template('tools/lot-size-calculator.html')

@seo_bp.route('/tools/forex-profit-calculator')
def forex_profit_calculator():
    return render_template('tools/forex-profit-calculator.html')

@seo_bp.route('/tools/drawdown-calculator')
def drawdown_calculator():
    return render_template('tools/drawdown-calculator.html')

@seo_bp.route('/tools/win-rate-calculator')
def win_rate_calculator():
    return render_template('tools/win-rate-calculator.html')

@seo_bp.route('/tools/crypto-profit-calculator')
def crypto_profit_calculator():
    return render_template('tools/crypto-profit-calculator.html')

@seo_bp.route('/tools/dca-calculator')
def dca_calculator():
    return render_template('tools/dca-calculator.html')

@seo_bp.route('/tools/compounding-calculator')
def compounding_calculator():
    return render_template('tools/compounding-calculator.html')

@seo_bp.route('/tools/futures-liquidation-calculator')
def futures_liquidation_calculator():
    return render_template('tools/futures-liquidation-calculator.html')

@seo_bp.route('/tools/expectancy-calculator')
def expectancy_calculator():
    return render_template('tools/expectancy-calculator.html')

@seo_bp.route('/tools/risk-of-ruin-calculator')
def risk_of_ruin_calculator():
    return render_template('tools/risk-of-ruin-calculator.html')

@seo_bp.route('/tools/goal-calculator')
def goal_calculator():
    return render_template('tools/goal-calculator.html')

@seo_bp.route('/tools/ai-trade-review')
def ai_trade_review():
    return render_template('tools/ai-trade-review.html')

@seo_bp.route('/tools')
def tools_hub():
    return render_template('tools/index.html')
