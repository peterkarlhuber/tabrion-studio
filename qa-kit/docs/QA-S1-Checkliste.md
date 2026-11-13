# QA-S1: Usability-Durchgang Tabrion Studio 1.0

**Tester:** Peter · **Datum:** ____________ · **Dauer:** ca. 30 Minuten
**System:** ☐ Windows ☐ macOS ☐ Linux · **Browser:** ____________

> **Bitte vorab lesen**
>
> - **Das ist eine lokale Testversion, kein Release.** Sie läuft nur auf deinem Computer und nur mit dem
>   Beispielprojekt „Beispielprojekt“ (Shop-Daten). Echte Daten sind nicht beteiligt.
> - **Daten bearbeiten (Data Editor) ist absichtlich nicht Teil von 1.0.** Studio liest nur. Dass du nirgends Daten
>   ändern kannst, ist gewollt und **kein** Fehler.
> - **Abgelehnte Schreibversuche dürfen nach „HTTP 400“ oder „405“ klingen.** Wichtig ist nur, dass nichts
>   geschrieben wird. Diese Wortwahl ist so abgenommen.
> - Studio zeigt sich noch als Version **0.1.0.dev0**, und die Meldungen im schwarzen Startfenster sind englisch.
>   Das ist bei dieser Testversion so und kein Befund.
> - Die Installation (siehe `Anleitung.md`) bitte **vor** der Sitzung erledigen. Sie zählt nicht zu den 30 Minuten.
> - Es gibt kein Richtig oder Falsch für dich. Wenn etwas unklar ist, ist das ein Befund. Bitte kurz notieren und
>   weitermachen.

**So füllst du die Liste aus:** Bei jedem Punkt ☐ OK oder ☐ Problem ankreuzen. Unter „Notiz“ reicht ein Stichwort.
Für ein größeres Problem gibt es unten die Vorlage „Problem melden“. Punkte mit **(optional)** darfst du
überspringen, wenn die Zeit knapp wird.

## Zeitplan (30 Minuten)

| Minute | Abschnitt | Checks |
|---|---|---|
| 0–5 | A Erster Start und Verbinden | S1-01 bis S1-04 |
| 5–10 | B Explorer und ER-Diagramm | S1-05 bis S1-09 |
| 10–17 | C SQL-Editor | S1-10 bis S1-16 |
| 17–20 | D Datenansicht und Export | S1-17 bis S1-20 |
| 20–23 | E PROD-Warnungen | S1-21 bis S1-24 |
| 23–25 | F Migrationsstand | S1-25 bis S1-27 |
| 25–27 | G Diagnose und Bericht | S1-28 bis S1-29 |
| 27–29 | H Einstellungen, I Verbindung getrennt | S1-30 bis S1-32 |
| 29–30 | J Beenden | S1-33 bis S1-34 |
| danach | Gesamteindruck, Problemmeldungen | ca. 5 Minuten |

---

## A Erster Start und Verbinden

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-01 | Im Ordner „TabrionStudio-QA“ **„2 - Studio starten“** doppelklicken. | Ein Fenster öffnet sich und bleibt offen. Der Browser öffnet Studio von selbst. | ☐ OK ☐ Problem | |
| S1-02 | Beim ersten Start erscheint ein Dialog. Lies ihn durch. | Titel **„Projektprofilen vertrauen?“**. Er listet die zwei Verbindungen „Shop (DEV)“ und „Shop (PROD)“ und ihre Dateien. Knöpfe **„Vertrauen“** und **„Nicht laden“**. Verstehst du, worum es geht? | ☐ OK ☐ Problem | |
| S1-03 | Auf **„Vertrauen“** klicken. **Stoppuhr starten.** Ohne Hilfe und ohne Anleitung versuchen, die Tabelle `customers` von „Shop (DEV)“ anzusehen. | Du findest den Weg selbst (zum Beispiel über **„Verbindungen“** → „Shop (DEV)“ → **„Verbinden“**, dann **„Explorer“**). Ziel: **unter 60 Sekunden** bis zur ersten Tabelle. Zeit: ______ s | ☐ OK ☐ Problem | |
| S1-04 | Nach dem Verbinden auf die Kopfzeile oben schauen. | Sie zeigt die Verbindung „Shop (DEV)“, das Kennzeichen **DEV** und **„Schreibgeschützt“**. Ein Klick auf „Schreibgeschützt“ öffnet nichts. | ☐ OK ☐ Problem | |

## B Explorer und ER-Diagramm

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-05 | Im **Explorer** links den Baum durchsehen. Ins Feld **„Objekte filtern“** `ord` tippen. | Der Baum zeigt Tabellen und Views (unter anderem `monthly_revenue` und `top_customers`). Der Filter lässt `orders` und `order_items` übrig. | ☐ OK ☐ Problem | |
| S1-06 | `orders` anklicken und die Reiter **„Spalten“**, **„Constraints“**, **„Indizes“**, **„Beziehungen“** und **„DDL“** ansehen. | Rechts erscheinen die Details. „Beziehungen“ zeigt, worauf `orders` verweist (zum Beispiel `customers`) und wer auf `orders` verweist. „DDL“ zeigt das `CREATE TABLE`. | ☐ OK ☐ Problem | |
| S1-07 | Bei `orders` auf **„Zeilen zählen“** klicken. | Studio zeigt die Anzahl der Zeilen und wie lange das Zählen gedauert hat. | ☐ OK ☐ Problem | |
| S1-08 | Oben von **„Objekte“** auf **„ER-Diagramm“** umschalten. Mit **„Vergrößern“**, **„Verkleinern“** und **„An Ansicht anpassen“** zoomen, den Hintergrund verschieben. | Die Tabellen erscheinen als Kästen mit Spalten und Linien für die Fremdschlüssel. Eine Übersichtskarte zeigt in der Ecke, wo du bist. Unter dem Diagramm steht eine Tabelle mit denselben Beziehungen als Text. | ☐ OK ☐ Problem | |
| S1-09 | Im Diagramm auf den Namen `products` klicken. Danach **„Als PNG speichern“** (oder **„Als SVG speichern“**). | Der Klick zeigt `products` im Explorer mit seinen Details. Der Browser lädt eine Bilddatei mit dem ganzen Diagramm herunter. | ☐ OK ☐ Problem | |

## C SQL-Editor

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-10 | **„SQL-Editor“** öffnen, als **„Verbindung“** „Shop (DEV)“ wählen und eintippen: `select order_id, order_date, status from orders where status = :status` Dann **„Ausführen“** (Strg+Enter, Mac: ⌘↵). | Nichts läuft. Unter **„Parameter“** erscheint eine Zeile für `:status`, ihr Feld bekommt den Fokus, und Studio bittet, `:status` einen Wert zu geben. | ☐ OK ☐ Problem | |
| S1-11 | Als Wert `paid` eintragen (Typ „Text“) und erneut **„Ausführen“**. | Unten erscheint **„Ergebnis 1“** mit Zeilen, deren Status `paid` ist, dazu Zeilenzahl und Dauer. Ein Klick auf eine Zelle zeigt den ganzen Wert an der Seite. | ☐ OK ☐ Problem | |
| S1-12 | Auf **„Formatieren“** klicken (oder Alt+Umschalt+F, Mac: ⌥⇧F). Danach Strg+Z (Mac: ⌘Z). | Der Text wird übersichtlich umgebrochen, Schlüsselwörter in GROSSBUCHSTABEN, `:status` bleibt unverändert. Studio meldet „Formatiert. Rückgängig mit ⌘Z / Strg+Z.“ Strg+Z stellt den alten Text wieder her. | ☐ OK ☐ Problem | |
| S1-13 | **„Öffnen…“** klicken. | Eine Liste der `.sql`-Skripte des Projekts erscheint, darunter die aus `skripte/`. Ein gewähltes Skript öffnet sich in einem neuen Tab und läuft **nicht** von selbst. | ☐ OK ☐ Problem | |
| S1-14 | Das absichtlich **schreibende** Beispielskript aus `skripte/` öffnen und auf „Shop (DEV)“ **„Ausführen“**. | Studio lehnt ab, zum Beispiel mit „Studio führt nur lesende Statements aus: SELECT, WITH, …“. Es wird nichts geändert. Ein Hinweis auf HTTP 400/405 ist zulässig. Ist die Meldung für dich verständlich? | ☐ OK ☐ Problem | |
| S1-15 | Den **Abfrageverlauf** öffnen (Strg+H, Mac: ⌘Y, oder das Uhr-Symbol links). Den Eintrag aus S1-11 suchen und **„Im Editor öffnen“** klicken. | Der Verlauf zeigt die Abfrage und „Parameter (Werte nicht gespeichert): status“. Der Wert `paid` steht **nirgends**. „Im Editor öffnen“ öffnet die Abfrage in einem neuen Tab, ohne sie auszuführen. | ☐ OK ☐ Problem | |
| S1-16 | **(optional)** Einen geänderten Tab speichern: Strg+S (Mac: ⌘S), als Pfad `skripte/peter-test.sql` eingeben. Danach einen Tab mit ungespeicherter Änderung schließen. | Studio speichert das Skript im Projekt („In … gespeichert.“). Beim Schließen eines ungespeicherten Tabs fragt Studio nach: „Speichern“, „Verwerfen“ oder „Abbrechen“. | ☐ OK ☐ Problem | |

## D Datenansicht und Export

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-17 | Im **Explorer** `orders` wählen und **„Daten anzeigen“** (oder Reiter **„Daten“**). Mit **„Weiter“** und **„Zurück“** blättern, **„Zeilen je Seite“** ändern. | Die erste Seite erscheint, zum Beispiel „Zeilen 1–100; weitere Zeilen folgen.“ Blättern und Seitengröße funktionieren. Es gibt keine Gesamtzahl, das ist so gewollt. | ☐ OK ☐ Problem | |
| S1-18 | **„Sortieren nach“** `order_date`, **„Absteigend“**. Dann **„Filter hinzufügen“**: `status` = `shipped`, **„Anwenden“**. | Die Zeilen sind neu sortiert und zeigen nur noch den gewählten Status. Die Ansicht beginnt wieder auf Seite 1. | ☐ OK ☐ Problem | |
| S1-19 | Eine Zelle doppelklicken und versuchen, etwas zu ändern. | Nichts lässt sich bearbeiten. Es gibt keinen Knopf zum Ändern, Einfügen oder Löschen. Das ist für 1.0 so gewollt (kein Data Editor). | ☐ OK ☐ Problem | |
| S1-20 | Zurück zum **SQL-Editor** und zum Ergebnis aus S1-11. **„Exportieren…“** klicken, **„CSV (für Tabellenkalkulationen)“** wählen, Zeilen: die geholten Zeilen, dann **„Exportieren“**. Die Datei z. B. in Excel oder LibreOffice öffnen. | Der Browser lädt eine CSV-Datei herunter, Studio meldet „Export gespeichert: …“. Die Datei enthält dieselben Spalten und Zeilen. Hinweis: In 1.0 gibt es den Export im SQL-Editor; in der Datenansicht ist (noch) keiner vorgesehen. | ☐ OK ☐ Problem | |

## E PROD-Warnungen und Bestätigungen

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-21 | Unter **„Verbindungen“** „Shop (PROD)“ wählen und **„Verbinden“** klicken. Den Dialog lesen, zuerst **„Abbrechen“**. | Titel **„Produktionsdatenbank“**, Text sinngemäß: „Shop (PROD) ist als PROD gekennzeichnet. Studio bleibt schreibgeschützt; Abfragen sind auf 15 s begrenzt.“ Der Fokus steht auf „Abbrechen“. Nach „Abbrechen“ wird nicht verbunden. | ☐ OK ☐ Problem | |
| S1-22 | Erneut **„Verbinden“**, diesmal im Dialog **„Verbinden“** wählen. Kopfzeile ansehen und mit der Maus auf den Zugriffs-Hinweis zeigen, dann darauf klicken. | Die Seite ist deutlich als **PROD** markiert. Die Kopfzeile zeigt **„Schreiben gesperrt“**; der Tooltip sagt „Produktionsverbindung: Schreiben ist immer gesperrt.“ Ein Klick öffnet nichts. Ist dir sofort klar, dass du auf PROD bist? | ☐ OK ☐ Problem | |
| S1-23 | Im **SQL-Editor** als Verbindung „Shop (PROD)“ wählen und das schreibende Beispielskript **„Ausführen“**. | Studio lehnt ab, bevor etwas läuft. Nichts wird geändert (HTTP-400/405-Wortlaut ist zulässig). | ☐ OK ☐ Problem | |
| S1-24 | Die Browserseite neu laden (F5, Mac: ⌘R) und wieder mit „Shop (PROD)“ arbeiten, z. B. im **Explorer** **„Öffnen“**. | Studio fragt erneut „Produktionsdatenbank“, bevor es verbindet. | ☐ OK ☐ Problem | |

## F Migrationsstand

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-25 | **„Migrationen“** öffnen, Verbindungsprofil „Shop (DEV)“, **„Stand anzeigen“**. | Studio zeigt die **„Version in der Datenbank“**. Ein Hinweis sagt, dass den Alembic-Dateien des Projekts noch nicht vertraut wird; die Revisionen sind deshalb „unbekannt“. | ☐ OK ☐ Problem | |
| S1-26 | Unter **„Migrationscode“** auf **„Dateien prüfen“**. Dialog lesen, dann **„Vertrauen und ausführen“**. | Dialog **„Migrationscode des Projekts ausführen?“** listet die Alembic-Konfiguration und die Revisionsdateien; der Fokus steht auf „Nicht vertrauen“. Danach zeigt der Stand **„1 Revision ist noch nicht angewendet.“** mit der Revision unter **„Noch nicht angewendet“**. | ☐ OK ☐ Problem | |
| S1-27 | Auf **„Befehl kopieren“** klicken und in einen Texteditor einfügen. **Den Befehl nicht ausführen.** | Studio meldet „Der Befehl ist in der Zwischenablage.“ Eingefügt steht `alembic upgrade head`. Es gibt keinen Knopf, mit dem Studio selbst migriert. | ☐ OK ☐ Problem | |

## G Diagnose und Bericht

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-28 | **„Diagnose“** öffnen, Verbindungsprofil „Shop (DEV)“, **„Diagnose starten“**. | Die Befunde erscheinen nach Schwere sortiert, jeweils mit Symbol **und** Wort („Fehler“, „Warnung“, „Hinweis“), mit Erklärung und Abhilfe. Darunter der Abschnitt **„Sicherungen“**. Texte mit Code `TAB-…` dürfen englisch sein (so entschieden). | ☐ OK ☐ Problem | |
| S1-29 | **„Bericht als Markdown herunterladen“** klicken und die Datei in einem Texteditor öffnen. | Der Browser speichert eine Datei wie `tabrion-studio-diagnosis-….md`. Der Bericht ist absichtlich **englisch** und enthält keine Dateipfade, keinen Dateinamen der Datenbank und keine Passwörter. | ☐ OK ☐ Problem | |

## H Einstellungen

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-30 | **„Einstellungen“** → **„Allgemein“** → **„Sprache“** auf **„English“** stellen, kurz durch zwei Seiten klicken, dann zurück auf **„Deutsch“**. | Die Oberfläche wechselt sofort ganz die Sprache, ohne Neustart. Auf den deutschen Seiten fallen dir keine englischen Sätze oder technischen Schlüssel (wie `editor.title`) auf. | ☐ OK ☐ Problem | |
| S1-31 | **„Darstellung“** auf **„Hell“**, dann **„Dunkel“** stellen und jeweils Explorer und SQL-Editor ansehen. | Beide Darstellungen sind gut lesbar, nichts verschwindet oder ist kaum zu erkennen. | ☐ OK ☐ Problem | |

## I „Verbindung getrennt“ (optional)

Nur dieser sichere Weg: **Keine Datei umbenennen, verschieben oder löschen.**

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-32 | **(optional)** Mit „Shop (DEV)“ verbunden im **SQL-Editor** bleiben (Tab 1). **„2 - Studio starten“** noch einmal doppelklicken: Es öffnet sich ein zweiter Studio-Tab. Dort unter **„Verbindungen“** „Shop (DEV)“ **„Trennen“**. Zurück in Tab 1 erneut **„Ausführen“**. | Studio zeigt einen Hinweis wie **„Verbindung getrennt: Shop (DEV)“** mit **„Neu verbinden“** (oder fordert auf andere Weise auf, neu zu verbinden). „Neu verbinden“ stellt die Verbindung wieder her, und es ging nichts verloren. | ☐ OK ☐ Problem ☐ übersprungen | |

## J Beenden

| ID | Schritt | Erwartet | Ergebnis | Notiz |
|---|---|---|---|---|
| S1-33 | **„3 - Studio beenden“** doppelklicken. Danach in den noch offenen Browser-Tab schauen. | Studio wird beendet. Der Browser-Tab sagt dann, dass die Sitzung beendet ist oder der lokale Server nicht antwortet (zum Beispiel „Deine Sitzung wurde beendet. …“). | ☐ OK ☐ Problem | |
| S1-34 | **(optional)** **„2 - Studio starten“** noch einmal, dann **„SQL-Editor“** öffnen. Danach wieder mit **„3 - Studio beenden“** schließen. | Studio startet wieder; die Editor-Tabs von vorhin sind wieder da (ohne Parameterwerte), die Spracheinstellung ist geblieben. | ☐ OK ☐ Problem | |

---

## Problem melden (Vorlage)

Pro Problem einmal ausfüllen (kopieren, so oft du brauchst).

```
Problem Nr.: ____      Check-ID: S1-____
Was:        (ein Satz, was nicht stimmt)
Wo:         (Seite/Ansicht, z. B. „SQL-Editor“, „Migrationen“)
Schritte:   1.
            2.
            3.
Erwartet:
Tatsächlich:
Schwere:    ☐ Blocker  ☐ Hoch  ☐ Mittel  ☐ Niedrig
Screenshot: ☐ ja, Dateiname: ______________   ☐ nein
Log-ID:     (falls eine Fehlermeldung eine 12-stellige Log-ID zeigt)
```

**Schwere – so ist sie gemeint**

- **Blocker:** Ich komme nicht weiter, oder Studio hat etwas verändert, das es nicht ändern darf.
- **Hoch:** Eine Hauptfunktion geht nicht oder ist irreführend (z. B. PROD ist nicht klar erkennbar).
- **Mittel:** Es geht, aber nur umständlich oder mit unklarer Meldung.
- **Niedrig:** Schönheitsfehler, Tippfehler, falsche Übersetzung.

Screenshot: Windows Win+Umschalt+S, macOS ⌘⇧4, Linux meist die Taste „Druck“.
Die Log-Datei liegt in `TabrionStudio-QA/_kit/studio-daten/logs` (sie enthält keine Passwörter).

---

## Gesamteindruck

| Frage | Antwort |
|---|---|
| Wie leicht fandest du dich zurecht? (1 = sehr schwer, 5 = sehr leicht) | ☐ 1 ☐ 2 ☐ 3 ☐ 4 ☐ 5 |
| Hast du das Ziel „erste Tabelle in unter 60 Sekunden“ geschafft? | ☐ ja ☐ nein · Zeit: ______ s |
| War dir jederzeit klar, ob du auf DEV oder PROD arbeitest? | ☐ ja ☐ meistens ☐ nein |
| Waren Meldungen und Dialoge verständlich? | ☐ ja ☐ meistens ☐ nein |
| Was hat dir am besten gefallen? | |
| Was hat dich am meisten gestört oder verwirrt? | |
| Gab es ein Wort oder einen Text, der falsch oder holprig klang? | |
| Würdest du Studio so für 1.0 freigeben? | ☐ ja ☐ ja, nach Behebung der Blocker ☐ nein |
| Sonstiges | |

Anzahl Probleme: Blocker ___ · Hoch ___ · Mittel ___ · Niedrig ___

Danke!
