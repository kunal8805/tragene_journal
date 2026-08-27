from flask import Blueprint, render_template, request, abort, jsonify
from models import Blog, Category, Tag, SEOSettings, NewsletterSubscriber
from extensions import db
from datetime import datetime

blog_bp = Blueprint('blog', __name__)

@blog_bp.route('/blog')
def index():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('q', '')
    
    query = Blog.query.filter_by(status='published')
    
    if search_query:
        query = query.filter(
            (Blog.title.ilike(f'%{search_query}%')) | 
            (Blog.content.ilike(f'%{search_query}%'))
        )
        
    pagination = query.order_by(Blog.published_at.desc()).paginate(page=page, per_page=10, error_out=False)
    
    categories = Category.query.all()
    tags = Tag.query.all()
    
    featured_post = None
    if page == 1 and not search_query:
        featured_post = Blog.query.filter_by(status='published', is_featured=True).order_by(Blog.published_at.desc()).first()
        
    return render_template('blog/index.html', 
                           blogs=pagination.items, 
                           pagination=pagination,
                           categories=categories,
                           tags=tags,
                           featured_post=featured_post,
                           search_query=search_query)

@blog_bp.route('/blog/<slug>')
def post(slug):
    blog = Blog.query.filter_by(slug=slug, status='published').first_or_404()
    
    # Increment view counter
    blog.views += 1
    db.session.commit()
    
    # Get related posts (same category, excluding current)
    related_posts = []
    if blog.category_id:
        related_posts = Blog.query.filter(
            Blog.category_id == blog.category_id,
            Blog.id != blog.id,
            Blog.status == 'published'
        ).limit(3).all()
        
    return render_template('blog/post.html', blog=blog, related_posts=related_posts)

@blog_bp.route('/category/<slug>')
def category(slug):
    category = Category.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    
    pagination = Blog.query.filter_by(status='published', category_id=category.id)\
        .order_by(Blog.published_at.desc())\
        .paginate(page=page, per_page=10, error_out=False)
        
    return render_template('blog/category.html', category=category, blogs=pagination.items, pagination=pagination)

@blog_bp.route('/tag/<slug>')
def tag(slug):
    tag = Tag.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    
    pagination = Blog.query.filter(Blog.tags.contains(tag), Blog.status == 'published')\
        .order_by(Blog.published_at.desc())\
        .paginate(page=page, per_page=10, error_out=False)
        
    return render_template('blog/tag.html', tag=tag, blogs=pagination.items, pagination=pagination)

@blog_bp.route('/api/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    email = request.form.get('email') or request.json.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400
        
    existing = NewsletterSubscriber.query.filter_by(email=email).first()
    if existing:
        return jsonify({'message': 'Already subscribed!'})
        
    sub = NewsletterSubscriber(email=email)
    db.session.add(sub)
    db.session.commit()
    
    return jsonify({'message': 'Successfully subscribed!'})
