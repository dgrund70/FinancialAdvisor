"""Pytest-fixtures: een geïsoleerde Flask-app + verse SQLite-DB per test."""

import os
import tempfile

import pytest
from flask import Flask

from models import db


@pytest.fixture
def app_ctx():
    """App-context met een lege tijdelijke SQLite-database; opgeruimd na de test."""
    fd, pad = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{pad}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        try:
            yield
        finally:
            db.session.remove()
            db.drop_all()

    os.unlink(pad)
