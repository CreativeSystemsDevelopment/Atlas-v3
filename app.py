"""
Schematic Extraction MVP - Flask Application Entry Point.

Industrial schematic diagram analysis using Gemini 3 API.
"""
import os
import sys

from flask import Flask

from config import Config
from models import init_db
from routes import api, ui


def create_app() -> Flask:
    """
    Create and configure Flask application.
    
    Returns:
        Configured Flask app
    """
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(Config)
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
    
    # Register blueprints
    app.register_blueprint(ui)
    app.register_blueprint(api, url_prefix='/api')
    
    # Initialize database
    with app.app_context():
        init_db()
    
    # Error handlers
    @app.errorhandler(413)
    def file_too_large(e):
        return {"error": f"File too large. Maximum size is {Config.MAX_CONTENT_LENGTH // (1024*1024)}MB"}, 413
    
    @app.errorhandler(500)
    def internal_error(e):
        return {"error": "Internal server error", "details": str(e)}, 500
    
    return app


def main():
    """Run the application."""
    app = create_app()
    
    # Get host and port from environment or use defaults
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = Config.DEBUG
    
    print(f"""
=================================================================
  Schematic Extraction MVP - Industrial Diagram Analysis
=================================================================
  Server running at: http://{host}:{port}
  Debug mode: {debug}
  Gemini Model: {Config.GEMINI_MODEL}
  Media Resolution: {Config.GEMINI_MEDIA_RESOLUTION}
=================================================================
    """)
    
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()

