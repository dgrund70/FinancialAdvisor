# Beleggingsdashboard op de Raspberry Pi

24/7 draaien op de Pi, bereikbaar in de browser vanaf elk apparaat op je
thuisnetwerk, met geplande data-updates — **naast** een bestaand project
(Nightwatch) zonder dat ze elkaar in de weg zitten.

> **Beveiliging:** de app heeft géén login en bindt op `0.0.0.0` (heel het LAN).
> Dat is een bewuste keuze voor een vertrouwd thuisnetwerk. **Forward poort 5002
> niet naar internet** en zet de Pi niet in een DMZ.

---

## Wat dit installeert (de "voetafdruk")

Eén plek om te weten wat van dit project is — handig als je later aan Nightwatch
werkt en je afvraagt "waar komt dit vandaan?":

| Resource | Beleggingsdashboard gebruikt |
|---|---|
| Map | `~/Investeren-en-beleggen/` (eigen git-repo) |
| Python | **eigen venv** in `.venv/` (nooit globaal `pip install`) |
| Poort (TCP) | **5002** |
| systemd-units | `beleggen.service`, `beleggen-koersen.{service,timer}`, `beleggen-nacht.{service,timer}` — allemaal met prefix **`beleggen-`** |
| Cron | **geen** — planning loopt via systemd-timers |
| Data | alleen in `~/Investeren-en-beleggen/data/` |

Alles is genaamd met `beleggen-`, dus je ziet in één oogopslag wat van dit
project is (`systemctl list-units 'beleggen-*'`) en je raakt nooit per ongeluk
Nightwatch' units of cron aan.

---

## Eenmalige installatie

```bash
# 1. Systeempakketten (additief, raken Nightwatch niet)
sudo apt update && sudo apt install -y git python3-venv

# 2. Is poort 5002 vrij naast Nightwatch?  (leeg = vrij)
ss -tlnp | grep 5002 || echo "5002 is vrij"
#   Bezet? Kies een andere poort en pas 'm aan in beleggen.service (PORT + --bind).

# 3. Code in een EIGEN map (los van Nightwatch)
cd ~ && git clone <REPO-URL> Investeren-en-beleggen
cd Investeren-en-beleggen

# 4. Eigen venv + dependencies (64-bit Pi OS heeft kant-en-klare ARM-wheels)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
#   Valt een wheel terug op een source-build? -> sudo apt install -y build-essential

# 5. Secrets: .env met je Anthropic-key (alleen nodig voor 'Genereer advies')
cp .env.example .env && nano .env      # zet ANTHROPIC_API_KEY=...
#   SECRET_KEY wordt automatisch aangemaakt in data/.

# 6. (optioneel) portefeuille + caches meenemen van de Mac:
#   draai op de MAC:  scp -r data/ pi@<pi-ip>:~/Investeren-en-beleggen/

# 7. Snelle test (handmatig), daarna Ctrl-C:
HOST=0.0.0.0 .venv/bin/python app.py
#   Open vanaf een ander apparaat: http://<pi-ip>:5002
```

### systemd inschakelen

Pas in de vier `deploy/*.service`-bestanden `User=` en de paden aan jouw Pi aan
(standaard staat er `pi` en `/home/pi/Investeren-en-beleggen`). Maak het script
uitvoerbaar en installeer de units:

```bash
chmod +x deploy/update_nacht.sh
sudo cp deploy/beleggen*.service deploy/beleggen*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beleggen.service beleggen-koersen.timer beleggen-nacht.timer
```

Bereikbaar op `http://<pi-ip>:5002` (of `http://<hostnaam>.local:5002`).

---

## Naast Nightwatch draaien zonder elkaar te hinderen

De kernregel: **niets delen wat veranderlijk is, en alles netjes namen geven.**

1. **Aparte map + aparte venv.** Installeer Python-deps altijd in `.venv/` van
   het project, nooit globaal (`sudo pip install`). Zo kan een versie die het
   ene project nodig heeft het andere nooit breken. (Geldt beide kanten op:
   doe in Nightwatch hetzelfde.)
2. **Eigen poort.** 5002 voor dit project; check dat Nightwatch een andere poort
   gebruikt (stap 2 hierboven).
3. **systemd i.p.v. de gedeelde crontab.** Dit is precies jouw zorg: als je in
   Nightwatch `crontab -e` bewerkt, kun je makkelijk regels van een ander project
   per ongeluk wijzigen. Daarom plant dít project **niets in cron** — alles loopt
   via `beleggen-*`-timers. Je twee schema's leven gescheiden; `systemctl list-timers`
   toont ze allemaal op één plek. (Gebruikt Nightwatch wél cron? Laat dat zo; ze
   bijten elkaar niet. Of verhuis ook Nightwatch naar systemd voor één overzicht.)
4. **Beleefd met resources.** De fetch-units draaien met lage CPU-/IO-prioriteit
   (`Nice`, `CPUWeight`, `IOSchedulingClass=idle`) en de nachtelijke met een
   geheugenplafond (`MemoryMax=512M`). Zo wint Nightwatch altijd als het druk is.
   Plan de nachtelijke update (`02:30`) eventueel op een rustig moment voor
   Nightwatch.
5. **Aparte data + logs.** Data staat in `data/` binnen de projectmap; logs gaan
   naar de systemd-journal per unit (`journalctl -u beleggen-...`). Geen gedeelde
   bestanden om over te struikelen.

Kortom: werk je aan Nightwatch, dan kun je dít project negeren — het zit volledig
in zijn eigen map, venv, poort en `beleggen-*`-units. En andersom.

---

## Verificatie

```bash
systemctl status beleggen                      # active (running)
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5002/gebruiker/1   # 200
systemctl list-timers 'beleggen-*'             # twee timers met 'next'-tijd
sudo systemctl start beleggen-koersen.service  # nu koersen ophalen…
#   → dashboard toont een nieuwe "Koersen: …"-tijd
sudo systemctl start beleggen-nacht.service    # nachtelijke fetch nu draaien…
journalctl -u beleggen-nacht -n 30 --no-pager  # zie de 4 fetches langskomen
```

---

## App later bijwerken

```bash
cd ~/Investeren-en-beleggen
git pull
.venv/bin/pip install -r requirements.txt      # alleen als requirements wijzigden
sudo systemctl restart beleggen.service
```

## Problemen oplossen

- **App start niet:** `journalctl -u beleggen -n 50 --no-pager`.
- **Poort bezet:** `ss -tlnp | grep 5002` → andere poort kiezen in `beleggen.service` (PORT + `--bind`) + units herladen.
- **Fetch faalt:** draai 'm handmatig: `.venv/bin/python fetch_prices.py` en lees de uitvoer.
- **Niet bereikbaar vanaf ander apparaat:** controleer dat gunicorn op `0.0.0.0` bindt (niet 127.0.0.1) en dat beide apparaten op hetzelfde netwerk zitten.
