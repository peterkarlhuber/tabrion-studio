# Tabrion Studio – Testversion installieren und starten

Tabrion Studio ist ein Programm zum **Ansehen** von Datenbanken. Es läuft nur auf deinem Computer und zeigt sich in
deinem Browser. Es ändert keine Daten. Diese Testversion ist **kein Release**. Sie ist nur für den Usability-Test
gedacht und bringt ein Beispielprojekt mit Shop-Daten mit.

Alles, was Studio braucht, landet **im Ordner „TabrionStudio-QA“ selbst**. Einzige Ausnahme unter Windows: Studios
Einstellungen und Protokolle liegen in einem privaten Ordner deines Benutzerkontos
(`%LOCALAPPDATA%\TabrionStudio-QA`). „4 - Deinstallieren“ entfernt auch diesen.

Die Testversion entspricht dem aktuellen Entwicklungsstand von Studio 1.0. Studio zeigt sich darin noch als Version
**0.1.0.dev0**, und die Meldungen im schwarzen Fenster sind englisch. Beides ist kein Fehler.

## 1. Herunterladen und entpacken

1. Lade die ZIP-Datei herunter, die du bekommen hast.
2. Entpacke sie:
   - **Windows:** Rechtsklick auf die ZIP-Datei → **„Alle extrahieren…“** → **„Extrahieren“**. Bitte nicht direkt
     in der ZIP-Datei doppelklicken, sondern erst entpacken.
   - **macOS:** Doppelklick auf die ZIP-Datei.
   - **Linux:** Rechtsklick → **„Hier entpacken“** (je nach System ähnlich benannt).
3. Danach hast du einen Ordner **„TabrionStudio-QA“**. Am einfachsten lässt du ihn im Ordner **Downloads**.
   Unter Windows bitte **nicht** in einen Ordner legen, den OneDrive synchronisiert (bei vielen Computern ist das
   „Dokumente“ oder „Desktop“): Das Synchronisieren der vielen kleinen Dateien bremst und kann die Installation
   stören.

Im Ordner findest du die vier Dateien, die du per Doppelklick startest, und das „Beispielprojekt“:

| Datei | Wozu |
|---|---|
| **1 - Installieren** | einmalig, richtet Studio ein |
| **2 - Studio starten** | startet Studio und öffnet den Browser |
| **3 - Studio beenden** | beendet Studio |
| **4 - Deinstallieren** | entfernt, was die Installation heruntergeladen hat |

Die Endung hängt vom System ab: `.cmd` (Windows), `.command` (macOS), `.sh` (Linux).

## 2. Beim ersten Mal: Sicherheitsabfrage bestätigen

Dein System kennt die Dateien nicht und fragt deshalb nach. Das ist normal.

**Windows**

- Erscheint **„Der Computer wurde durch Windows geschützt“**: auf **„Weitere Informationen“** klicken, dann auf
  **„Trotzdem ausführen“**.
- Erscheint **„Datei öffnen – Sicherheitswarnung“**: auf **„Ausführen“** klicken.

Das kann bei jeder der vier Dateien beim ersten Öffnen kommen. Ist auf deinem Computer die **intelligente
App-Steuerung** („Smart App Control“) eingeschaltet, kann Windows die Dateien ohne Ausweg blockieren. Dann bitte
melden; ausschalten solltest du sie dafür nicht.

**macOS 15**

1. Doppelklick auf **„1 - Installieren.command“**. macOS meldet, dass die Datei nicht geöffnet wurde. Auf
   **„Fertig“** klicken.
2. **Systemeinstellungen** öffnen → **„Datenschutz & Sicherheit“** → ganz nach unten scrollen. Dort steht ein Hinweis
   zu „1 - Installieren.command“. Auf **„Dennoch öffnen“** klicken und mit Passwort oder Touch ID bestätigen.
3. Im nächsten Fenster noch einmal **„Dennoch öffnen“** wählen.

Das ist **nur bei der ersten Datei** nötig: Die Installation gibt danach den ganzen Ordner frei, die anderen Dateien
starten ohne Nachfrage. Fragt macOS, ob **„Terminal“ auf den Ordner „Downloads“ zugreifen darf**, bitte
**„Erlauben“** wählen.

**Linux**

Der sicherste Weg unter Linux ist ein Terminal-Fenster: Im Ordner „TabrionStudio-QA“ ein Terminal öffnen (oft
Rechtsklick → **„Im Terminal öffnen“**) und diese Zeile eingeben, dann Enter:

  ```
  bash "1 - Installieren.sh"
  ```

Für die anderen Dateien genauso, z. B. `bash "2 - Studio starten.sh"`.

Ein Doppelklick bzw. Rechtsklick → **„Als Programm ausführen“** geht auch: Die Dateien öffnen sich dann selbst in
einem Terminal-Fenster. Erscheint dabei kein Fenster, bitte den Weg über das Terminal nehmen.

## 3. Installieren (einmalig)

Doppelklick auf **„1 - Installieren“**.

- Du brauchst dafür **Internet**.
- Es dauert etwa **2 bis 5 Minuten**. Ein Fenster zeigt, was passiert. Bitte warte, bis es sagt, dass es fertig
  ist.
- Dabei werden Python und die nötigen Bibliotheken **in den Ordner „TabrionStudio-QA“** heruntergeladen. An deinem
  System wird nichts verändert.

## 4. Studio starten

Doppelklick auf **„2 - Studio starten“**.

- Ein Fenster öffnet sich. **Lass es offen**, solange du mit Studio arbeitest. Wenn du es schließt, endet Studio.
- Dein Browser öffnet Studio von selbst.
- Beim **ersten Start** fragt Studio **„Projektprofilen vertrauen?“**. Das Beispielprojekt bringt zwei Verbindungen
  mit, „Shop (DEV)“ und „Shop (PROD)“. Für den Test bitte **„Vertrauen“** wählen.

## 5. Studio beenden

- Doppelklick auf **„3 - Studio beenden“**, oder
- das Fenster von „2 - Studio starten“ schließen.

Wenn kein Studio-Tab mehr offen ist, beendet sich Studio nach einer Weile auch von selbst (normalerweise nach
30 Minuten).

## 6. Deinstallieren

1. Doppelklick auf **„4 - Deinstallieren“**.
2. Den Ordner **„TabrionStudio-QA“** in den Papierkorb ziehen.

Fertig. Es bleibt nichts zurück (unter Windows entfernt Schritt 1 auch `%LOCALAPPDATA%\TabrionStudio-QA`). Nur Dateien, die du selbst aus Studio heruntergeladen hast
(Export, Bericht, Bild), liegen in deinem Download-Ordner. Die kannst du dort löschen.

## 7. Wenn etwas nicht klappt

| Was passiert | Was du tun kannst |
|---|---|
| Der Browser öffnet sich nicht, oder du hast den Studio-Tab geschlossen. | **„2 - Studio starten“** noch einmal doppelklicken. Läuft Studio schon, öffnet sich einfach ein neuer, angemeldeter Tab. |
| Ein Tab zeigt **„Nicht angemeldet“**. | Das passiert bei einem Tab, den du selbst geöffnet hast (Lesezeichen, kopierte Adresse). Studio meldet nur den Tab an, den es selbst öffnet. **„2 - Studio starten“** noch einmal doppelklicken. |
| Ein Tab zeigt „Deine Sitzung wurde beendet“ oder „Der lokale Server antwortet nicht“. | Studio ist beendet. Mit **„2 - Studio starten“** neu starten. |
| Die Firewall fragt nach einem Netzwerkzugriff oder einem Port. | Studio ist nur auf diesem Computer erreichbar (Adresse `127.0.0.1`), nie aus dem Netzwerk. Du kannst die Freigabe ablehnen. Funktioniert Studio danach nicht, melde es bitte. |
| Linux: Der Browser zeigt „Datei nicht gefunden“ statt Studio. | Das kann bei einem Browser als Snap- oder Flatpak-Paket passieren. Bitte melden, mit Screenshot. |
| Windows: Studio startet nicht und meldet „cannot use the state directory“. | Bitte melden, mit dem Inhalt des Fensters. |
| Etwas anderes stimmt nicht. | Bitte mit der Vorlage „Problem melden“ in der Checkliste notieren. |

**Wo ist das Protokoll (Log)?** Den genauen Pfad zeigt das Fenster von „2 - Studio starten“ in der Zeile `Log:`.
Unter macOS und Linux ist es `TabrionStudio-QA/_kit/studio-daten/logs`, unter Windows
`%LOCALAPPDATA%\TabrionStudio-QA\studio-daten\logs`. Es enthält keine Passwörter
und keine Daten. Bei einem Problem bitte die Log-Datei mitschicken. Zeigt eine Fehlermeldung eine Log-ID
(12 Zeichen), steht dieselbe ID auch im Log.
