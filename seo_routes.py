from flask import Blueprint, render_template, make_response, request, url_for
from models import Blog, Category, Tag, SEOSettings, PageMetadata
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


@seo_bp.route('/sitemap.xml')
def sitemap():
    """Single flat sitemap — all URLs in one file (best for SEO)"""
    base = _base_url()
    now = datetime.utcnow().strftime('%Y-%m-%d')

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    # ── Main pages ──
    pages = [
        {'loc': f"{base}/", 'priority': '1.0', 'changefreq': 'daily'},
        {'loc': f"{base}/login", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/register", 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{base}/blog", 'priority': '0.9', 'changefreq': 'daily'},
        # ── Company / Legal pages ──
        {'loc': f"{base}/about", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/contact", 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': f"{base}/privacy-policy", 'priority': '0.5', 'changefreq': 'yearly'},
        {'loc': f"{base}/terms-of-service", 'priority': '0.5', 'changefreq': 'yearly'},
        {'loc': f"{base}/refund-policy", 'priority': '0.5', 'changefreq': 'yearly'},
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


@seo_bp.route('/feed.xml')
def rss_feed():
    settings = SEOSettings.query.first()
    site_title = settings.site_title if settings and settings.site_title else "Tragene Journal"
    site_desc = settings.default_meta_description if settings and settings.default_meta_description else "AI-Powered Tragene Journal — Track, Analyze, Improve."
    
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