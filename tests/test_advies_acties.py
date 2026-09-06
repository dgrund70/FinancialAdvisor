"""Van advies naar actie: het boekingsformulier moet voor te vullen zijn."""

import app as webapp
from models import BrokerAccount, Gebruiker, db


def _client(app_ctx):
    webapp.app.config["WTF_CSRF_ENABLED"] = False
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def test_transactieformulier_vult_ticker_en_type_voor(app_ctx):
    gebruiker = Gebruiker(naam="tester")
    db.session.add(gebruiker)
    db.session.flush()
    account = BrokerAccount(naam="DEGIRO", gebruiker_id=gebruiker.id)
    db.session.add(account)
    db.session.commit()

    from flask import template_rendered
    vastgelegd = {}

    def onthoud(sender, template, context, **extra):
        vastgelegd["template"] = template.name
        vastgelegd["form_data"] = context.get("form_data")

    template_rendered.connect(onthoud, webapp.app)
    try:
        client = _client(app_ctx)
        client.get(f"/gebruiker/{gebruiker.id}/account/{account.id}"
                   f"/transactie/toevoegen?ticker=IEMA.AS&type=koop")
    finally:
        template_rendered.disconnect(onthoud, webapp.app)

    assert vastgelegd.get("template") == "transactie_form.html"
    assert vastgelegd["form_data"].get("ticker") == "IEMA.AS"
    assert vastgelegd["form_data"].get("type") == "koop"
