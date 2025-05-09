import os
import time
import logging
import secrets
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from phi.agent import Agent
from phi.tools.crawl4ai_tools import Crawl4aiTools
from phi.model.google import Gemini
from dotenv import load_dotenv
import traceback
import re
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('scraper_debug.log')
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    logger.critical("GOOGLE_API_KEY not found in .env file")
    raise ValueError("GOOGLE_API_KEY not found in .env file")

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # For session management

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<User {self.username}>'

# Create database tables
with app.app_context():
    db.create_all()

# Constants
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB

def scrape_website(url, debug_id="DEBUG"):
    """Scrape website content using Crawl4aiTools"""
    try:
        logger.debug(f"[{debug_id}] Starting scrape with Crawl4aiTools")
        crawler = Crawl4aiTools(max_length=None)
        scraped_data = crawler.web_crawler(url)
        logger.debug(f"[{debug_id}] Scrape completed successfully")
        return scraped_data
    except Exception as e:
        logger.error(f"[{debug_id}] Scraping failed: {str(e)}")
        raise

def get_structured_gemini_response(content, prompt):
    """Analyze scraped content with Gemini and return cleaned response"""
    agent = Agent(model=Gemini(id="gemini-2.0-flash-exp", temperature=0.2))
    full_prompt = f"Based on this website content:\n\n{content}\n\n{prompt}"
    
    response = agent.run(full_prompt)  # Get raw response
    response_str = str(response)  # Ensure response is a string

    # Clean the response
    cleaned_content = response_str.split("content_type=")[0].strip()
    
    # Replace single asterisks used for emphasis with bold tags
    cleaned_content = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', cleaned_content)
    
    # Replace newlines with markdown line breaks
    cleaned_content = cleaned_content.replace('\n', '  \n')  # Markdown needs two spaces for line breaks
    
    return cleaned_content

@app.route("/")
def index():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("index.html")

@app.route("/home")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user"] = username
            flash("Logged in successfully!", "success")
            return redirect(url_for("index"))
        flash("Invalid username or password", "danger")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists", "danger")
        else:
            new_user = User(
                username=username,
                password_hash=generate_password_hash(password)
            )
            db.session.add(new_user)
            db.session.commit()
            
            flash("Account created successfully! Please login.", "success")
            return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out successfully!", "info")
    return redirect(url_for("home"))

@app.route("/scrape", methods=["POST"])
def scrape_url():
    if "user" not in session:
        return jsonify({"error": "Authentication required", "status": "error"}), 401
        
    debug_id = f"SCRAPE-{time.time_ns()}"
    logger.debug(f"[{debug_id}] Starting scrape request")
    
    url = request.form.get("url", "").strip()
    if not url:
        logger.warning(f"[{debug_id}] Empty URL provided")
        return jsonify({
            "error": "URL is required",
            "status": "error",
            "debug_id": debug_id
        }), 400

    try:
        if not url.startswith(('http://', 'https://')):
            url = f'https://{url}'
            logger.debug(f"[{debug_id}] Added https prefix: {url}")

        # Scrape content
        logger.debug(f"[{debug_id}] Scraping content...")
        scraped_content = scrape_website(url, debug_id)
        
        return jsonify({
            "content": scraped_content,
            "status": "success",
            "debug_id": debug_id
        })

    except Exception as e:
        logger.error(f"[{debug_id}] Scraping failed: {str(e)}")
        logger.debug(f"[{debug_id}] Full traceback:\n{traceback.format_exc()}")
        return jsonify({
            "error": f"Scraping failed: {str(e)}",
            "status": "error",
            "debug_id": debug_id
        }), 500

@app.route("/analyze", methods=["POST"])
def analyze_content():
    if "user" not in session:
        return jsonify({"error": "Authentication required", "status": "error"}), 401
        
    debug_id = f"ANALYZE-{time.time_ns()}"
    logger.debug(f"[{debug_id}] Starting analysis request")
    
    content = request.form.get("content", "").strip()
    prompt = request.form.get("prompt", "").strip()
    
    if not content:
        logger.warning(f"[{debug_id}] Empty content provided")
        return jsonify({
            "error": "Content is required",
            "status": "error",
            "debug_id": debug_id
        }), 400
        
    if not prompt:
        logger.warning(f"[{debug_id}] Empty prompt provided")
        return jsonify({
            "error": "Prompt is required",
            "status": "error",
            "debug_id": debug_id
        }), 400

    try:
        logger.debug(f"[{debug_id}] Getting structured Gemini response...")
        analysis = get_structured_gemini_response(content, prompt)
        
        # Return clean structured response
        return jsonify({
            "analysis": analysis,
            "status": "success"
        })
        
    except Exception as e:
        logger.error(f"[{debug_id}] Analysis failed: {str(e)}")
        logger.debug(f"[{debug_id}] Full traceback:\n{traceback.format_exc()}")
        return jsonify({
            "error": f"Analysis failed: {str(e)}",
            "status": "error"
        }), 500
    
@app.route("/admin/db-viewer", methods=['GET', 'POST'])
def db_viewer():
    # Check if user is logged in first
    if "user" not in session:
        flash("Please login to access this page", "danger")
        return redirect(url_for("login"))

    # Handle admin authentication form submission
    if request.method == 'POST':
        username = request.form.get('admin_username')
        password = request.form.get('admin_password')
        
        if username == 'admin' and password == '12345678':
            session['admin_authenticated'] = True
            users = User.query.all()
            return render_template("db_viewer.html", users=users)
        else:
            flash("Invalid admin credentials", "danger")
    
    # Check if already authenticated
    if session.get('admin_authenticated'):
        users = User.query.all()
        return render_template("db_viewer.html", users=users)
    
    # Show admin login form
    return render_template("admin_login.html")


if __name__ == "__main__":
    logger.info("Starting Flask application")
    app.run(debug=True, host='0.0.0.0', port=5000)

