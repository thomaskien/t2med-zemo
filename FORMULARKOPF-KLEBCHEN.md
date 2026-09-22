# Formularkopf-Klebchen – Brother QL-800 per USB

Der separate Installer richtet genau einen virtuellen Drucker namens
**`formularkopf-klebchen`** mit **Samba- und Bonjour-/IPP-Freigabe** ein. Eingang ist die
**A4-Formularkopf-Ausgabe von T2med mit `Strg` + `E`**. Der Linux-Rechner
schneidet den bewährten Ausschnitt aus, bereitet ihn bei 300 dpi auf und druckt
auf eine selbstklebende **50- oder 62-mm-Endlosrolle**, mit Schnitt nach jedem Etikett.
Die voreingestellte Gesamtlänge ist **82 mm einschließlich Vorschubrändern**.

## Voraussetzungen

- Debian **12 oder neuer**, Ubuntu **22.04 oder neuer** oder Raspberry Pi OS
  **Bookworm/12 oder neuer**, mit systemd und CUPS 2; x86 und ARM werden mit
  demselben Python-Druckhelfer bedient. Benötigte Pakete müssen in den
  konfigurierten Distributionsquellen verfügbar sein.
- Internetzugang für `apt` und PyPI bei der Installation.
- QL-800 direkt am USB-Port des Druckservers; die gemeldete Kennung
  **`04f9:209b`** wird gezielt erkannt. Bei mehreren Geräten muss eine
  Seriennummer gewählt werden.
- QL-kompatible weiße selbstklebende Endlosrolle: **50 mm / DK-22223** oder
  **62 mm / DK-22205**. Schwarz-Rot-Rollen (DK-22251) und vorgestanzte
  Einzeletiketten verwenden andere Druckmodi und werden hier nicht unterstützt.
- **Editor Lite ausschalten**: Die grüne Editor-Lite-LED am Drucker darf nicht
  leuchten. Die Taste ggf. gedrückt halten, bis sie erlischt.

Die Software wird ohne Brother-x86-Binärtreiber installiert. Sie verwendet
`brother-ql==0.9.4` in einer eigenen Python-Umgebung, Pillow aus der Distribution
und libusb. Die bestehende Bluetooth-/BIXOLON-Lösung kann parallel bestehen.

## Installation auf dem USB-Druckserver

`install_formularkopf_klebchen.sh` ist eine eigenständige Datei; weitere Dateien
aus dem Repository sind zur Installation nicht erforderlich.

```bash
sudo bash install_formularkopf_klebchen.sh
```

Für DK-22205 / 62 mm direkt `--media-width 62` ergänzen. Die aktuelle
eigenständige Installer-Datei kann auf dem Linux-Druckserver heruntergeladen
werden:

```bash
curl -fL https://raw.githubusercontent.com/thomaskien/t2med-zemo/main/install_formularkopf_klebchen.sh \
  -o install_formularkopf_klebchen.sh
sudo bash install_formularkopf_klebchen.sh --media-width 62
```

Der Installer installiert die Abhängigkeiten, erkennt den angeschlossenen
QL-800, richtet die CUPS-Queue und USB-Zugriffsrechte ein und ergänzt Samba
sowie Avahi für Bonjour.
Die Änderungen an `smb.conf` werden mit `testparm` geprüft und vorher gesichert.
Die CUPS-Konfiguration wird vor Aktivierung der Netzwerkfreigabe ebenfalls
gesichert. CUPS kündigt die Queue mit ihren tatsächlichen Fähigkeiten über
Bonjour/DNS-SD an und nimmt Druckaufträge über IPP entgegen.

Bei vorhandener Samba-Konfiguration bleiben Benutzeranmeldung, Domäne und
andere Freigaben bestehen. Bei einer Neueinrichtung fragt der Installer nach
einem lokalen Druckbenutzer (Vorgabe `klebchen`) und dessen Samba-Passwort.
Es wird keine Gastfreigabe eingerichtet. Ein eigener Benutzer kann auch auf
einem bestehenden eigenständigen Samba-Server angelegt werden:

```bash
sudo bash install_formularkopf_klebchen.sh --samba-user klebchen
```

Ist der Benutzer bereits in Samba eingerichtet, bleibt sein Passwort erhalten.
Für einen Domänenmitgliedsserver vorhandene Domänenkonten verwenden:

```bash
sudo bash install_formularkopf_klebchen.sh --existing-samba-auth
```

Samba-AD-Domain-Controller sowie Konfigurationen mit `disable spoolss = yes`
werden mit einer konkreten Meldung abgewiesen. Eine fremde Freigabe oder Queue
mit demselben Namen wird nicht übernommen.

Ein bestimmtes USB-Gerät lässt sich explizit angeben:

```bash
sudo bash install_formularkopf_klebchen.sh \
  --printer-uri 'usb://0x04f9:0x209b/SERIENNUMMER'
```

Ohne Seriennummer ist auch `usb://0x04f9:0x209b` möglich, sofern beim Drucken
exakt ein QL-800 angeschlossen ist. Die Seriennummer ist stabiler als die
wechselnden USB-Bus-/Gerätenummern aus `lsusb`.

Eine Wiederholung der Installation erhält die vorhandenen Crop-Einstellungen
und erstellt keine doppelten Samba-Blöcke. Noch offene Druckaufträge müssen
vor einem Update abgeschlossen oder gezielt gelöscht werden. Der Installer
löst **keinen automatischen Testdruck** aus. Zum Nachrüsten von Bonjour bei einer
vorhandenen Installation denselben aktualisierten Installer erneut ausführen.

## Automatische Abschaltung

Der Installer fragt zu Beginn:

```text
Gewünschte Abschaltung [aus]:
```

**Enter übernimmt `aus`**: Der QL-800 bleibt auch bei längeren Druckpausen
eingeschaltet. Mögliche Antworten sind:

| Antwort | Wirkung |
|---|---|
| Enter, `aus` oder `off` | Automatische Abschaltung deaktivieren (Standard) |
| `10`, `20`, `30`, `40`, `50`, `60` | Nach dieser Anzahl Minuten automatisch abschalten |
| `beibehalten` oder `keep` | Vorhandene Geräteeinstellung unverändert lassen |

Eine Vorgabe auf der Kommandozeile überspringt die Frage:

```bash
# Eingeschaltet bleiben:
sudo bash install_formularkopf_klebchen.sh --media-width 62 --auto-power-off off

# Nach 30 Minuten abschalten:
sudo bash install_formularkopf_klebchen.sh --media-width 62 --auto-power-off 30

# Bisherige Geräteeinstellung erhalten:
sudo bash install_formularkopf_klebchen.sh --media-width 62 --auto-power-off keep
```

Ohne Terminal und ohne Option gilt ebenfalls **`off`**. Die gewählte Einstellung
wird einmalig über USB im Drucker gespeichert und anschließend zurückgelesen.
Bei einem bereits passenden Wert erfolgt kein erneuter Schreibvorgang. Ein
Wachhalte-Dienst ist nicht erforderlich. `keep` sendet keine Einstellungsbefehle.
Die Einstellung „Automatisch einschalten“ wird nicht verändert.

Der QL-800 muss eingeschaltet, angeschlossen und im Leerlauf sein. Bei fehlender
oder abweichender Bestätigung bricht der Installer mit einer Fehlermeldung ab;
die Installation ist erst nach der Abschlussmeldung **„Fertig“** vollständig.

Nach der Installation lassen sich die Einstellung auslesen oder gezielt ändern:

```bash
sudo /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py --power-off-status

# 0 = automatische Abschaltung aus; alternativ 10, 20, 30, 40, 50 oder 60:
sudo /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py --set-auto-power-off 0
```

`auto_power_off_minutes: 0` und `automatic_shutdown_disabled: true` bestätigen
die deaktivierte Abschaltung. Diese Geräteoption gehört nicht in die Datei
`/etc/formularkopf-klebchen.json`. Die USB-Sperre schützt die Abfrage und Änderung
gegen gleichzeitig laufende Druckaufträge.

Brother beschreibt die Geräteoption in seiner
[Anleitung zur automatischen Abschaltung](https://support.brother.com/g/b/faqend.aspx?c=za&faqid=faqp00001613_001&lang=en&prod=lpql800eas).
Am angeschlossenen QL-800 wurde **Aus → 20 Minuten → Aus** mit dem neuen Helfer
gesetzt und jeweils zurückgelesen. Die übrigen Zeitwerte verwenden dieselbe
Einstellung in Zehn-Minuten-Schritten; die Abschaltzeiten wurden nicht jeweils
über die volle Wartezeit am Gerät getestet.

## Rollenbreite ändern / rotes Blinken bei Auftragseingang

Ein Auftrag für 50-mm-Endlosrolle passt nicht zu einer vom QL-800 als 62 mm
erkannten Rolle. Der Helfer kann beide Breiten ansteuern. Für weißes
**62-mm-Endlospapier (DK-22205)** den aktualisierten Installer so starten:

```bash
sudo bash install_formularkopf_klebchen.sh --media-width 62
```

Die physische Ausgabe ist dann **62 × 82 mm**. Der Formularkopf behält die
Größe der bisherigen 50-mm-Ausgabe und wird auf der breiteren Rolle zentriert.
Der Cutter schneidet die Länge; eine 62-mm-Rolle wird dadurch nicht 50 mm breit.
Für die ursprüngliche Rolle `--media-width 50` verwenden. Ohne Option bleibt
ein bereits gespeicherter Wert erhalten; alte Konfigurationen ohne Breitenfeld
und neue Installationen verwenden weiterhin 50 mm.

Wenn bereits ein fehlgeschlagener Auftrag in der Warteschlange liegt, zuerst
dessen ID feststellen und nur den betroffenen Auftrag löschen:

```bash
lpstat -W not-completed -o formularkopf-klebchen
# NUMMER durch die angezeigte Nummer des fehlgeschlagenen Auftrags ersetzen:
cancel formularkopf-klebchen-NUMMER
```

Vor einer Wiederholung prüfen, ob bereits Etiketten ausgegeben wurden. Den
Drucker nach dem bisherigen Medienfehler aus- und wieder einschalten. Nach
dem Update einen neuen Formularkopf mit `Strg` + `E` drucken.

Die Konfiguration `/etc/formularkopf-klebchen.json` enthält dafür das Feld
`"media_width_mm": 62` (alternativ `50`). **60 mm wird nicht automatisch als
62 mm behandelt**: Es gibt auch 60 × 86 mm große Einzeletiketten. Bei unklarer
Rollenbezeichnung die Rolle nach dem Update ohne Druck auslesen:

```bash
sudo /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py --printer-status
```

Die Ausgabe enthält `media_width_mm`, `media_type` und `media_length_mm` sowie
gegebenenfalls Gerätefehler. Für diese Lösung muss `media_type` **Endlosrolle**
und `media_length_mm` **0** sein. Die Statusmeldung unterscheidet die
Schwarz-Rot-Beschichtung nicht zuverlässig; dafür die Rollenbezeichnung prüfen.
Der Helfer akzeptiert sowohl den dokumentierten Endlosrollen-Code `0x4A`
als auch den am angeschlossenen QL-800 tatsächlich gemeldeten Code `0x0A`.

Vor jedem Druckauftrag wird derselbe Status abgefragt. Bei abweichender
Breite oder falschem Rollentyp werden keine Rasterdaten gesendet; der Auftrag
wird mit einer konkreten Fehlermeldung angehalten. Das gilt auch, wenn keine
gültige Statusantwort empfangen wird.

## Bonjour / automatische Druckersuche

Der Installer installiert und aktiviert `avahi-daemon`, aktiviert die
CUPS-Druckerfreigabe und setzt `Browsing=Yes`, `BrowseLocalProtocols=dnssd`
sowie `printer-is-shared=true` für `formularkopf-klebchen`. CUPS verwaltet die
Bonjour-Ankündigung selbst; eine zusätzliche statische Avahi-Service-Datei
ist nicht erforderlich.

Im selben lokalen Netz den Druckerdialog öffnen und **`formularkopf-klebchen`**
auswählen; je nach Client wird der Servername mit angezeigt. Alternativ die
IPP-Adresse manuell eintragen:

```text
ipp://SERVER.local:631/printers/formularkopf-klebchen
```

`SERVER` durch den Hostnamen des Linux-Druckservers ersetzen; bei manueller
Einrichtung kann auch dessen IP-Adresse verwendet werden. Der Client kann die
bereitgestellte CUPS-Queue und deren Fähigkeiten abfragen. Für eine manuelle
Treiberauswahl den generischen PostScript-Treiber bzw. die bereitgestellte PPD
verwenden. Auch hier **A4, Hochformat, 600 dpi und 100 %** einstellen: Die
Etikettenaufbereitung erfolgt auf dem Server.

Für Bonjour muss **UDP 5353/mDNS** und zum Drucken **TCP 631/IPP** im lokalen
Netz erreichbar sein. mDNS wird normalerweise nicht zwischen getrennten VLANs
weitergeleitet. Samba nutzt zusätzlich TCP 445.

`cupsctl --share-printers` erlaubt bei einer üblichen lokalen CUPS-Konfiguration
den Druck aus dem eigenen Subnetz. Bereits vorhandene CUPS-Zugriffsregeln und
Authentifizierungsrichtlinien gelten weiter. Der IPP-Zugang verwendet die
CUPS-Regeln, unabhängig von der Samba-Anmeldung. Die globale CUPS-Freigabe
gilt auch für andere Queues, die bereits als freigegeben markiert sind; deren
Freigabestatus wird durch den Installer nicht geändert.

## Windows-Arbeitsplatz

1. Den Linux-Server per Hostname oder IP erreichen. Bei aktiver Firewall muss
   **TCP 445** vom Praxisnetz zum Druckserver erlaubt sein.
2. Mit den vorhandenen Samba-Zugangsdaten anmelden, z.B. im Explorer über
   `\\SERVER`. Bei Bedarf zunächst in einer Eingabeaufforderung:

   ```bat
   net use \\SERVER\IPC$ /user:BENUTZER *
   ```

   Das Sternchen fragt das Passwort verdeckt ab. Für eine Domänenanmeldung den
   Domänenbenutzer verwenden. `SERVER` und `BENUTZER` durch eigene Werte ersetzen.
3. In der Druckereinrichtung manuell einen Drucker hinzufügen. Als vorhandene
   Freigabe **`\\SERVER\formularkopf-klebchen`** verwenden. Da der Server keinen
   Windows-Treiber verteilt, den Treiber lokal auswählen/installieren. Falls
   die direkte Verbindung keinen Treiberdialog anbietet: „Lokalen Drucker mit
   manuellen Einstellungen“ → neuer Anschluss **Local Port** → denselben
   UNC-Pfad als Anschlussnamen eintragen.
4. Einen **generischen PostScript-Treiber** auswählen, beispielsweise
   **Generic / MS Publisher Imagesetter**, sofern auf diesem Windows verfügbar.
   Sonst einen verfügbaren signierten PostScript-Treiber installieren. Die
   Verfügbarkeit der Treiberauswahl hängt von Windows-Version und Richtlinien
   ab; lokale Administratorrechte können erforderlich sein.
5. Druckername **`formularkopf-klebchen`**, Papier **A4**, Hochformat,
   **600 dpi**, eine Seite pro Blatt, Skalierung **100 %**. Automatische
   Anpassung und Verkleinerung auf Etikettenformat deaktivieren.
6. In T2med den Formularkopf mit `Strg` + `E` erzeugen und diese Queue auswählen.
   Den bewährten Print Service „Moderner Druck mit Papierformat und
   Seitenlayout“ verwenden.

**Keinen Brother-QL-Treiber, Generic/Text-Only-, PCL- oder XPS-Treiber wählen.**
Der Server erwartet die vollständige A4-Seite als PostScript oder PDF; erst
serverseitig entsteht das Etikett. Ein echter T2med-Testdruck ist aussagekräftiger
als die Windows-Testseite, deren Testtext meist außerhalb des Ausschnitts liegt.

## Linux-Arbeitsplatz

Den automatisch per Bonjour gefundenen Drucker auswählen oder wie oben über
IPP verbinden. Alternativ in der Druckerverwaltung „Windows-Drucker über
Samba“ hinzufügen:

```text
smb://SERVER/formularkopf-klebchen
```

Authentifizierung mit dem Samba-/Domänenkonto aktivieren. Als Treiber
**Generic PostScript Printer** wählen; alternativ die mitgelieferte
`ql800/formularkopf-klebchen.ppd` verwenden. Diese PPD wird auf dem Druckserver
auch unter `/usr/local/share/formularkopf-klebchen/formularkopf-klebchen.ppd`
installiert und kann auf den Arbeitsplatz kopiert werden.

Auf Debian-/Ubuntu-Arbeitsplätzen werden dafür ggf. `smbclient`, `cups-client`
und `system-config-printer` benötigt. Einstellungen und T2med-Verwendung sind
wie unter Windows: **A4, Hochformat, 600 dpi, 100 %, `Strg` + `E`**.
Samba-Passwörter nicht in URI oder Shell-History schreiben; im Druckerdialog
eintragen. Direkt auf dem Druckserver wird die lokale CUPS-Queue verwendet.

## Geometrie und Verarbeitung

| Einstellung | Vorgabe |
|---|---:|
| Eingabe | vollständige A4-Seite |
| Vollseitenrendering | 600 dpi, Graustufen |
| Crop | 82 × 50 mm |
| Crop-Ursprung | links 26,6 pt; oben 16,5 pt |
| Rotation | 270°, alternativ 90° |
| Papier | 50 oder 62 mm Endlos, schwarz auf weiß |
| Druckauflösung | 300 dpi |
| Nutzbare Rollenbreite | 554 Punkte bei 50 mm; 696 Punkte bei 62 mm |
| Breite der Inhaltsfläche | stets 554 Punkte, etwa 46,9 mm, zentriert |
| Vorschubrand | 35 Punkte, etwa 3 mm pro Ende |
| Gesamtlänge | nominell 82 mm |
| Rasterhöhe bei 82 mm | 899 Punkte plus zweimal 35 Randpunkte |
| Schwellenwert | 188, kein Dithering |
| Schnitt | nach jedem Etikett |

Der Formularkopf wird vollständig und proportional in die nutzbare Fläche
verkleinert und zentriert. Dadurch bleiben die Angaben trotz der nicht
bedruckbaren Ränder erhalten. Die endgültige Schnittlänge muss einmal am
Gerät nachgemessen werden; die Rastervorgabe beträgt rund 82,04 mm.

Jede Dokumentseite erzeugt ein Etikett; Kopien werden ebenfalls einzeln
geschnitten. CUPS verarbeitet die Aufträge nacheinander. Vor dem ersten Druck
werden alle Seiten eines Auftrags gerendert und validiert. USB-Zugriff wird
zusätzlich über eine gemeinsame Sperrdatei serialisiert.

Die Konfiguration steht in `/etc/formularkopf-klebchen.json`. Vorhandene Werte
bleiben bei einer erneuten Installation erhalten. Für eine umgekehrte
Leserichtung `rotation` zwischen `270` und `90` ändern. Anschließend prüfen:

```bash
sudo /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py --check-config
```

Der Helfer wird für jeden Auftrag neu gestartet; kein eigener Socket- oder
systemd-Druckdienst ist erforderlich.

## Prüfung und Diagnose

```bash
lsusb -d 04f9:209b
lpstat -v formularkopf-klebchen
lpstat -p formularkopf-klebchen -l
lpstat -W not-completed -o formularkopf-klebchen
sudo testparm -s
systemctl status cups smbd avahi-daemon
avahi-browse -rt _ipp._tcp
sudo journalctl -u cups -u smbd -u avahi-daemon --since '10 minutes ago'
```

USB-Erkennung mit den Rechten des Druckhelfers prüfen:

```bash
sudo -u lp /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py --list-printers
```

Bei fehlendem Gerät: Strom, USB-Kabel, Editor-Lite-Modus und ggf. einmaliges
Ab-/Anstecken nach der Installation prüfen. Keine zweite physische CUPS-Queue
oder Anwendung gleichzeitig direkt auf denselben QL-800 drucken lassen.

Der Helfer meldet einen Auftrag nur nach bestätigtem Druckabschluss als
abgeschlossen. Bei USB-/Papierfehlern oder unklarem Ergebnis hält CUPS den
Auftrag an. **Vor einer Wiederholung zuerst prüfen, ob bereits ein Etikett
herausgekommen ist**, insbesondere bei Aufträgen mit mehreren Seiten/Kopien.
Ein teilgedruckter Auftrag wird bei erneuter Freigabe vollständig wiederholt.

```bash
# JOB-ID durch die tatsächlich angezeigte ID ersetzen:
lp -i JOB-ID -H resume
# Falls CUPS die Queue angehalten hat:
sudo cupsenable formularkopf-klebchen
```

Mit einer synthetischen A4-PS-/PDF-Datei kann ohne USB-Druck eine Vorschau
angelegt werden. Das Ausgabeverzeichnis darf dabei ausdrücklich Dateien
enthalten, die der Anwender behalten möchte:

```bash
sudo /opt/formularkopf-klebchen/venv/bin/python \
  /usr/local/lib/formularkopf-klebchen/backend.py \
  --preview beispiel.pdf /tmp/klebchen-vorschau
```

Der normale Helfer speichert keine dauerhaften Debugbilder und protokolliert
keine Jobtitel, Benutzernamen oder Dokumentinhalte. Samba und CUPS verwenden
ihre üblichen Spool-Verzeichnisse; deren vorhandene Aufbewahrungseinstellungen
bleiben bestehen. Angehaltene Aufträge können dort bis zur Freigabe oder
Löschung liegen. Vorschauen mit echten Patientendaten gezielt wieder löschen.

## Deinstallation

Zuerst offene Aufträge in der Druckerverwaltung prüfen und abschließen oder
gezielt löschen. Danach:

```bash
sudo lpadmin -x formularkopf-klebchen
```

CUPS entfernt damit auch die Bonjour-Ankündigung dieser Queue. Die globale
CUPS-Freigabe und Avahi bleiben für andere Drucker bestehen. Falls keine
weiteren Drucker darüber freigegeben werden sollen, kann die CUPS-Freigabe
gezielt mit `sudo cupsctl --no-share-printers` abgeschaltet werden.

In `/etc/samba/smb.conf` ausschließlich den Block zwischen
`# BEGIN formularkopf-klebchen (installer)` und
`# END formularkopf-klebchen (installer)` entfernen. Dann `sudo testparm -s`
und `sudo systemctl reload smbd` ausführen. Eine alte Gesamtsicherung nicht
blind zurückspielen, wenn Samba inzwischen anderweitig geändert wurde.

Die zugehörigen Dateien/Verzeichnisse können anschließend gezielt entfernt
werden:

```text
/etc/formularkopf-klebchen.json
/etc/udev/rules.d/70-formularkopf-klebchen.rules
/usr/lib/cups/backend/formularkopf-klebchen  (bei angepasstem ServerBin dort)
/usr/local/lib/formularkopf-klebchen/
/usr/local/share/formularkopf-klebchen/
/opt/formularkopf-klebchen/
/var/lib/formularkopf-klebchen/
/var/spool/samba/formularkopf-klebchen/
```

Danach `sudo udevadm control --reload-rules` ausführen. Allgemeine Pakete,
Samba-Benutzer und andere Drucker bleiben erhalten; einen eigens angelegten
Druckbenutzer nur entfernen, wenn er nicht anderweitig genutzt wird.

## Entwicklung und Prüfgrenzen

Die bearbeitbaren Quellen liegen unter `ql800/`. Nach Änderungen den
Standalone-Installer neu erzeugen:

```bash
python3 tools/build_ql800_installer.py
python3 tools/build_ql800_installer.py --check
bash -n install_formularkopf_klebchen.sh
python3 -m unittest discover -s tests -v
```

Die Druckhelfertests benötigen Pillow, `brother-ql==0.9.4` und Ghostscript.
Automatische Tests prüfen Raster-/Schnittbefehle, Crop und Orientierung,
mehrseitige Dokumente, Fehlerbehandlung, Samba-Konfigurationsänderungen sowie
den Abschaltdialog und die USB-Abfrage mit Rückleseprüfung. Die Dialogtests
verwenden ein Pseudoterminal, damit auch Enter als Standardauswahl geprüft wird.

Alle 54 Tests wurden unter Linux einschließlich der echten Ghostscript-Ausgabe
ausgeführt. Auf dem Entwicklungs-Mac wird der Ghostscript-Test übersprungen,
wenn das dort installierte Programm nicht ausführbar ist.

Der USB-Druck auf DK-22205 wurde mit einem angeschlossenen QL-800 erfolgreich
ausgeführt und vom Anwender bestätigt. Auch das Deaktivieren der Abschaltung
wurde direkt am Gerät zurückgelesen. Eine vollständige Neuinstallation auf
jeder unterstützten Distribution und jede Windows-/Linux-Clientkombination
wurde nicht separat getestet.

Technische Grundlagen:
[Brother QL-800 Rasterreferenz](https://download.brother.com/welcome/docp100278/cv_ql800_eng_raster_101.pdf),
[brother_ql](https://github.com/pklaus/brother_ql),
[Samba-Druckfreigaben und Optionen](https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html),
[CUPS-Freigabe über Bonjour/IPP](https://openprinting.github.io/cups/doc/sharing.html),
[CUPS-Backends](https://openprinting.github.io/cups/doc/api-filter.html).
