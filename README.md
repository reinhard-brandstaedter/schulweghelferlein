# Schulweghelferlein

Mobile Web-App für Schulweghelfer: Rotlicht-, Gelblicht- und sonstige Verstöße pro Standort
und Tag erfassen und als Diagramme auswerten.

- **Erfassen** – Standort antippen, Datum (Heute/Gestern/…), Zähler mit +/−, Speichern.
  Der zuletzt gewählte Standort wird auf dem Gerät gemerkt. Mehrere Einträge pro Standort und Tag sind möglich.
- **Statistik** – Tage / Wochen / Monate, gesamt oder pro Standort; Standortvergleich als
  Ø Verstöße pro Einsatz (fair, auch wenn Standorte unterschiedlich oft besetzt sind); Tabellenansicht; CSV-Export.
- **Verlauf** – letzte Einträge, Fehleinträge löschen.

Stack: Python/FastAPI, SQLite (eine Datei), statisches HTML/JS mit Chart.js (lokal eingebunden, kein CDN).

## Zugangsschutz

Es gibt keine Benutzerkonten, nur einen gemeinsamen Schlüssel (`ACCESS_KEY`).
Nach einmaliger Eingabe bleibt das Gerät ~1 Jahr angemeldet (Cookie).

Bequemer Einladungslink zum Teilen (z. B. per Messenger oder als QR-Code):

```
https://<host>/login?key=<ACCESS_KEY>
```

Schlüssel ändern ⇒ alle Geräte werden abgemeldet und müssen den neuen Schlüssel eingeben.
Ohne HTTPS wird der Schlüssel unverschlüsselt übertragen – von außen nur über einen TLS-Proxy/Tunnel erreichbar machen.

## Standorte

In [`config.yaml`](config.yaml). Die `id` wird gespeichert und darf nicht mehr geändert werden,
der `name` jederzeit. Nach Änderungen den Container/Pod neu starten.

## Lokal starten

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ACCESS_KEY=test .venv/bin/uvicorn app.main:app --reload
# http://localhost:8000/login?key=test
.venv/bin/python -m pytest
```

## Docker

```sh
docker build -t schulweghelferlein .
docker run -d -p 8000:8000 -e ACCESS_KEY=… -v swh-data:/data schulweghelferlein
```

oder `ACCESS_KEY=… docker compose up -d` (Daten in `./data`; bei Linux-Bind-Mounts muss das Verzeichnis
für UID 10001 beschreibbar sein: `mkdir data && sudo chown 10001 data`).

## Kubernetes

Das Image baut GitHub Actions bei jedem Push auf `main` (vorher laufen die Tests) und legt es unter
`ghcr.io/reinhard-brandstaedter/schulweghelferlein:main` ab (Tags `v1.2.3` ⇒ gleichnamige Image-Tags).
Das Package muss auf GitHub **öffentlich** sein, da der Cluster ohne Pull-Secret zieht.

```sh
kubectl create namespace schulweghelferlein
kubectl -n schulweghelferlein create secret generic schulweghelferlein --from-literal=access-key='…'
kubectl apply -k .
# neues Image ausrollen:
kubectl -n schulweghelferlein rollout restart deploy/schulweghelferlein
```

Erreichbar über MetalLB unter `http://192.168.1.232` (siehe `k8s/service.yaml`).

Die Standorte aus `config.yaml` landen automatisch als ConfigMap im Cluster (`kubectl apply -k .` nach Änderungen
erzeugt eine neue ConfigMap und startet den Pod neu). SQLite ⇒ genau ein Replica, Strategie `Recreate`.

Speicher: PVC über die StorageClass `nfs-client` (nfs-subdir-external-provisioner). Auf dem NAS liegt die Datenbank
im Ordner `schulweghelferlein-schulweghelferlein-data/`; wegen `onDelete: retain` bleibt er auch nach dem Löschen erhalten.
SQLite läuft bewusst ohne WAL-Modus, da WAL auf NFS nicht zuverlässig funktioniert.

**Backup:** die Datei `/data/schulweg.db` sichern (oder regelmäßig den CSV-Export).
