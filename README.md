# 🥗 Flatfood

> **Smarte, diät- und anwesenheitsbewusste WG-Küchenkasse** mit automatischer Kassenzettel-Erkennung (Vision-KI & Regel-Engine), TmM-Verteilungsschlüssel und fairer Abrechnung.

---

## 🌟 Features

- **Automatischer Kassenbon-Scan:** Foto mit dem Smartphone aufnehmen oder hochladen ➔ Erkennung der einzelnen Artikel, Preise, Mengen und Rabatte via Google Gemini Vision (`gemini-3.8-flash`) oder lokalem Fallback.
- **Selbstlernende Produkt-Datenbank:** Einmal manuell korrigierte Posten (z. B. *"Alsan Streichfett"* zu *Basis/Vegan*) werden als deterministische Regeln gespeichert und schlagen künftig die KI.
- **Diät- & Ernährungsprofile:** Mitbewohner erhalten frei konfigurierbare Tags (z. B. `fleisch`, `alkohol`, `milch`). Wer einen Tag nicht besitzt, zahlt 0 € für diese Kategorie (z. B. Daniel zahlt nichts für Fleisch & Alkohol).
- **„Tage wo da“ (TmM):** Posten werden tagesgenau nach der Anwesenheit der jeweiligen Teilnehmer im Abrechnungsmonat aufgeteilt.
- **Minimaler Schuldenausgleich:** Zeigt direkt übersichtlich *"Wer zahlt wem wie viel"*, inklusive 1-Klick-Buchung von Ausgleichszahlungen.
- **Statistiken & Benchmarks:** Ausgabenverlauf (€/TmM) im Vergleich zum 30-Monate-Freiburg-Median (**7,26 €**).
- **Self-Hosted & Reversibel:** SQLite im WAL-Modus, Docker Compose, 0 Cloud-Zwang für Daten, 1-Klick SQLite-Backup-Download.

---

## 🚀 Schnellstart (Lokal)

```bash
# 1. Repository klonen & Verzeichnis betreten
cd C:\Users\scheb\dev\flatfood

# 2. Virtual Environment aktivieren (wurde bereits vorbereitet)
.\.venv\Scripts\activate

# 3. .env anlegen und Gemini API Key eintragen
cp .env.example .env
# Trage deinen GEMINI_API_KEY in .env ein

# 4. Server starten
uvicorn app.main:app --reload --port 8080
```

Öffne anschließend [http://localhost:8080](http://localhost:8080) im Browser.

---

## 🐳 Docker & Homelab (TrueNAS)

### Mit Docker Compose

```bash
docker compose up -d --build
```

- Web-UI erreichbar unter: `http://<deine-server-ip>:8080`
- Persistente Daten liegen im gemounteten Verzeichnis `./data`:
  - `data/flatfood.db` (SQLite Datenbank)
  - `data/receipts/` (gespeicherte Original-Belegfotos)

### TrueNAS Scale Setup

1. Erstelle ein Dataset auf deinem Pool (z. B. `/mnt/tank/apps/flatfood`).
2. Nutze *Custom App* oder Docker Compose in TrueNAS.
3. Mappe das Dataset auf `/data` im Container.
4. Setze Umgebungsvariable `GEMINI_API_KEY` oder binde `.env` ein.

---

## 🧪 Tests ausführen

```bash
pytest -v
```

Alle 19 Tests für Engine, Rundung (Largest-Remainder-Verfahren), Diät-Splits, Normalisierung, Bon-Abkürzungen und FastAPI-Routen laufen in unter 1 Sekunde durch.

---

## 🔒 Datenschutz & Google API Key

1. Gehe auf [aistudio.google.com](https://aistudio.google.com) und erstelle einen API-Key in deinem verknüpften Google-Cloud-Billing-Projekt.
2. Trage den Key ausschließlich lokal in `.env` ein:
   ```env
   GEMINI_API_KEY=AIzaSy...
   ```
3. Zur Bon-Erkennung wird lediglich das komprimierte Belegfoto an die Gemini API übertragen. Die gesamte Abrechnung, WG-Namen und Historie verbleiben zu 100 % lokal in der SQLite-Datenbank.
