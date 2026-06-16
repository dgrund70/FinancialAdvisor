# Investeren en beleggen
Flask-app voor portefeuillebeheer, prijsupdates, nieuwsverversing en AI-advies.

## Geverifieerde endpoints
Onderstaande endpoints zijn functioneel getest met Flask test client.

### Dashboard en pagina's
- `GET /` → `200 OK`
- `GET /advies` → `200 OK`
- `GET /positie/toevoegen` → `200 OK`

### Posities
- `POST /positie/toevoegen` (ongeldige input) → `200 OK` (formulier wordt opnieuw getoond met validatiefouten)
- `POST /positie/toevoegen` (geldige input) → `302 Found` (redirect naar dashboard)
- `POST /positie/<id>/verwijderen` → `302 Found` (redirect naar dashboard)

### Koersen, nieuws en advies
- `POST /prijzen/verversen` → `302 Found` (redirect naar dashboard)
- `POST /nieuws/verversen` → `302 Found` (redirect naar adviespagina)
- `POST /advies/genereer` zonder `ANTHROPIC_API_KEY` → `302 Found` (redirect met foutmelding)
- `POST /advies/genereer` met `ANTHROPIC_API_KEY` → `302 Found` (redirect met succesmelding)

### Thema's
- `POST /themas/toevoegen` (ongeldige input) → `302 Found`
- `POST /themas/toevoegen` (geldige input) → `302 Found`
- `POST /themas/<id>/toggle` → `302 Found`
- `POST /themas/<id>/verwijderen` → `302 Found`

## Notities bij verificatie
- Tijdens verificatie zijn subprocess-calls gemockt voor veilige en snelle endpoint-tests.
- CSRF is alleen in de testcontext uitgeschakeld.
- Tijdelijke testdata is na afloop opgeschoond.
