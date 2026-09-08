"""Pytest-fixtures: een geïsoleerde Flask-app + verse SQLite-DB per test."""

import os
import tempfile

# MOET vóór het importeren van app.py gebeuren: die leest BELEGGEN_DB op
# importmoment. Zonder dit draaien routetests tegen data/app.db — de echte
# portefeuille. Conftest wordt vóór de testmodules geïmporteerd, dus dit is de
# enige plek waar deze regel op tijd komt.
_TEST_DB = os.path.join(tempfile.gettempdir(), f"beleggen-test-{os.getpid()}.db")
os.environ["BELEGGEN_DB"] = _TEST_DB

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


@pytest.fixture
def webclient():
    """Testclient op de échte Flask-app, maar met een verse tijdelijke database.

    Voor routetests die de database schrijven. De app is bij import al aan
    BELEGGEN_DB gekoppeld (zie boven); hier maken we de tabellen en gooien we
    ze na afloop weg.
    """
    import app as webapp

    webapp.app.config["TESTING"] = True
    webapp.app.config["WTF_CSRF_ENABLED"] = False
    with webapp.app.app_context():
        db.create_all()
        try:
            yield webapp.app.test_client()
        finally:
            db.session.remove()
            db.drop_all()
