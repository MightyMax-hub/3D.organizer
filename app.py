from flask import Flask

from config import SECRET_KEY
from database.database import init_database
from routes.main import main


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    init_database()
    app.register_blueprint(main)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=False)