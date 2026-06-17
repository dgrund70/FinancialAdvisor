from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Toegestane transactietypes. koop/verkoop/dividend hebben een ticker;
# storting/opname/kosten zijn puur cash; correctie her-baselined een holding
# (handmatige aanpassing van aantal/GAK) zonder cash-effect.
TRANSACTIE_TYPES = ("koop", "verkoop", "dividend",
                    "storting", "opname", "kosten", "correctie")

# Benchmarks voor de "had ik beter de index kunnen kopen?"-vergelijking.
# Gebruik EUR-genoteerde (UCITS) tickers zodat geen historische FX nodig is.
BENCHMARKS = (
    {"label": "MSCI World", "ticker": "IWDA.AS"},
    {"label": "Nasdaq 100", "ticker": "EQQQ.DE"},
)


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
    transacties    = db.relationship(
        "Transactie", backref="broker_account",
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
    gerealiseerde_winst = db.Column(db.Float, nullable=False, default=0.0)        # cumulatief, in handelsvaluta
    tags               = db.relationship(
        "Tag", secondary=positie_tags, backref="posities", lazy=True
    )


class Transactie(db.Model):
    """Grootboek: holdings (Positie) worden hieruit afgeleid via projectie.py.

    Cash-tekenconventie t.o.v. het accountsaldo:
      storting   : +bedrag
      opname     : -bedrag
      koop       : -(aantal*prijs + kosten)
      verkoop    : +(aantal*prijs - kosten)
      dividend   : +(bedrag - kosten)
      kosten     : -bedrag
      correctie  :  0   (alleen her-baseline van aantal/GAK, geen cash-effect)
    """
    __tablename__     = "transacties"
    id                = db.Column(db.Integer, primary_key=True)
    broker_account_id = db.Column(db.Integer, db.ForeignKey("broker_accounts.id"),
                                  nullable=False, index=True)
    type              = db.Column(db.String(20), nullable=False)   # zie TRANSACTIE_TYPES
    ticker            = db.Column(db.String(20), nullable=True)    # vereist voor koop/verkoop/dividend
    aantal            = db.Column(db.Float, nullable=True)         # stuks (koop/verkoop/correctie)
    prijs             = db.Column(db.Float, nullable=True)         # per stuk in `valuta` (koop/verkoop/correctie)
    bedrag            = db.Column(db.Float, nullable=True)         # cash-bedrag (storting/opname/dividend/kosten)
    kosten            = db.Column(db.Float, nullable=False, default=0.0)   # courtage/kosten in `valuta`
    valuta            = db.Column(db.String(3), nullable=False, default="EUR")
    datum             = db.Column(db.Date, nullable=False, index=True)
    notitie           = db.Column(db.String(200), nullable=True)
    aangemaakt        = db.Column(db.DateTime, default=datetime.utcnow)


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


class BenchmarkPunt(db.Model):
    """Cache van dagelijkse EUR-slotkoersen per benchmark-ticker, voor de
    deposit-matched vergelijking. Gevuld door fetch_benchmark.py."""
    __tablename__ = "benchmark_punten"
    id     = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), nullable=False, index=True)
    datum  = db.Column(db.Date, nullable=False, index=True)
    koers  = db.Column(db.Float, nullable=False)   # EUR-slotkoers
    __table_args__ = (
        db.UniqueConstraint("ticker", "datum", name="uq_benchmark_ticker_datum"),
    )


class Fundamental(db.Model):
    """Cache van fundamentals per ticker (bron: yfinance), als context voor de
    adviesmodule. Gevuld door fetch_fundamentals.py. Alle cijfers nullable —
    yfinance levert niet voor elke ticker hetzelfde."""
    __tablename__      = "fundamentals"
    id                 = db.Column(db.Integer, primary_key=True)
    ticker             = db.Column(db.String(20), nullable=False, unique=True, index=True)
    naam               = db.Column(db.String(120), nullable=True)
    sector             = db.Column(db.String(80), nullable=True)
    markt_kap          = db.Column(db.Float, nullable=True)   # marketCap
    pe                 = db.Column(db.Float, nullable=True)   # trailingPE
    forward_pe         = db.Column(db.Float, nullable=True)   # forwardPE
    koers_boekwaarde   = db.Column(db.Float, nullable=True)   # priceToBook
    dividend_rendement = db.Column(db.Float, nullable=True)   # dividendYield
    winstmarge         = db.Column(db.Float, nullable=True)   # profitMargins (fractie)
    omzetgroei         = db.Column(db.Float, nullable=True)   # revenueGrowth (fractie)
    winstgroei         = db.Column(db.Float, nullable=True)   # earningsGrowth (fractie)
    rendement_ev       = db.Column(db.Float, nullable=True)   # returnOnEquity (fractie)
    schuld_ev          = db.Column(db.Float, nullable=True)   # debtToEquity
    koersdoel          = db.Column(db.Float, nullable=True)   # targetMeanPrice
    aanbeveling        = db.Column(db.String(30), nullable=True)  # recommendationKey
    valuta             = db.Column(db.String(10), nullable=True)
    opgehaald          = db.Column(db.DateTime, default=datetime.utcnow)
