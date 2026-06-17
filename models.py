from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# Koppeltabel posities ↔ tags (many-to-many)
positie_tags = db.Table(
    "positie_tags",
    db.Column("positie_id", db.Integer, db.ForeignKey("posities.id"), primary_key=True),
    db.Column("tag_id",     db.Integer, db.ForeignKey("tags.id"),     primary_key=True),
)


class Gebruiker(db.Model):
    __tablename__ = "gebruikers"
    id            = db.Column(db.Integer, primary_key=True)
    naam          = db.Column(db.String(100), nullable=False)
    broker_accounts = db.relationship(
        "BrokerAccount", backref="gebruiker",
        cascade="all, delete-orphan", lazy=True
    )
    tags = db.relationship(
        "Tag", backref="gebruiker",
        cascade="all, delete-orphan", lazy=True
    )
    adviezen = db.relationship(
        "Advies", backref="gebruiker",
        cascade="all, delete-orphan", lazy=True
    )


class BrokerAccount(db.Model):
    __tablename__  = "broker_accounts"
    id             = db.Column(db.Integer, primary_key=True)
    naam           = db.Column(db.String(100), nullable=False)
    gebruiker_id   = db.Column(db.Integer, db.ForeignKey("gebruikers.id"), nullable=False)
    posities       = db.relationship(
        "Positie", backref="broker_account",
        cascade="all, delete-orphan", lazy=True
    )


class Positie(db.Model):
    __tablename__      = "posities"
    id                 = db.Column(db.Integer, primary_key=True)
    broker_account_id  = db.Column(db.Integer, db.ForeignKey("broker_accounts.id"), nullable=False)
    ticker             = db.Column(db.String(20), nullable=False)
    naam               = db.Column(db.String(100), nullable=False, default="")
    aantal             = db.Column(db.Float, nullable=False)
    aankoopprijs       = db.Column(db.Float, nullable=False)   # GAK per stuk
    aankoopdatum       = db.Column(db.Date, nullable=True)
    koers_type         = db.Column(db.String(20), nullable=False, default="live")  # "live" of "handmatig"
    handmatige_koers   = db.Column(db.Float, nullable=True)
    valuta             = db.Column(db.String(3), nullable=False, default="EUR")   # handelsvaluta GAK
    tags               = db.relationship(
        "Tag", secondary=positie_tags, backref="posities", lazy=True
    )


class Tag(db.Model):
    __tablename__  = "tags"
    id             = db.Column(db.Integer, primary_key=True)
    gebruiker_id   = db.Column(db.Integer, db.ForeignKey("gebruikers.id"), nullable=False)
    naam           = db.Column(db.String(100), nullable=False)


class NieuwsArtikel(db.Model):
    __tablename__  = "nieuws_cache"
    id             = db.Column(db.Integer, primary_key=True)
    ticker         = db.Column(db.String(20), nullable=True, index=True)
    titel          = db.Column(db.String(500), nullable=False)
    samenvatting   = db.Column(db.Text, nullable=True)
    url            = db.Column(db.String(1000), nullable=False, unique=True)
    bron           = db.Column(db.String(100), nullable=True)
    gepubliceerd   = db.Column(db.DateTime, nullable=True)
    opgeslagen     = db.Column(db.DateTime, default=datetime.utcnow)


class Advies(db.Model):
    __tablename__       = "adviezen"
    id                  = db.Column(db.Integer, primary_key=True)
    gebruiker_id        = db.Column(db.Integer, db.ForeignKey("gebruikers.id"), nullable=False)
    tag_id              = db.Column(db.Integer, db.ForeignKey("tags.id"), nullable=True)  # None = generiek
    gegenereerd         = db.Column(db.DateTime, default=datetime.utcnow)
    portfolio_snapshot  = db.Column(db.Text, nullable=False)
    advies_tekst        = db.Column(db.Text, nullable=False)
    model               = db.Column(db.String(50), nullable=True)
    risico_score        = db.Column(db.Integer, nullable=True)   # 1 (defensief) – 5 (offensief)
    risico_reden        = db.Column(db.Text, nullable=True)
    aanbevelingen       = db.relationship(
        "Aanbeveling", backref="advies",
        cascade="all, delete-orphan", lazy=True
    )


class Aanbeveling(db.Model):
    __tablename__  = "aanbevelingen"
    id             = db.Column(db.Integer, primary_key=True)
    advies_id      = db.Column(db.Integer, db.ForeignKey("adviezen.id"), nullable=False)
    ticker         = db.Column(db.String(20), nullable=False)
    instapkoers    = db.Column(db.Float, nullable=True)
    valuta         = db.Column(db.String(10), nullable=True)
    opgeslagen     = db.Column(db.DateTime, default=datetime.utcnow)
