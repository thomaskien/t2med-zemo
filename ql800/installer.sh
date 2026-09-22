#!/usr/bin/env bash
# Generated standalone installer; edit ql800/installer.sh and rebuild with
# python3 tools/build_ql800_installer.py.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 022

QUEUE=formularkopf-klebchen
CONFIG=/etc/formularkopf-klebchen.json
LIBDIR=/usr/local/lib/formularkopf-klebchen
VENV=/opt/formularkopf-klebchen/venv
SMB_CONFIG=/etc/samba/smb.conf
PRINTER_URI=""
MEDIA_WIDTH=""
AUTO_POWER_OFF=""
SMB_USER=""
USE_EXISTING_AUTH=0

usage() {
    cat <<'HELP'
Formularkopf-Klebchen: Brother QL-800 per USB, 50/62-mm-Endlosrolle + Schnitt bei 82 mm.
Netzwerkfreigabe über Samba und Bonjour/IPP (Avahi).
Für Debian 12+, Ubuntu 22.04+ und Raspberry Pi OS ab Bookworm (systemd/CUPS 2).

  sudo bash install_formularkopf_klebchen.sh
  sudo bash install_formularkopf_klebchen.sh --samba-user klebchen
  sudo bash install_formularkopf_klebchen.sh --media-width 62
  sudo bash install_formularkopf_klebchen.sh --media-width 62 --auto-power-off off

Optionen:
  --printer-uri URI       USB-Ziel vorgeben, z.B. usb://0x04f9:0x209b/SERIENNUMMER
  --media-width 50|62     Breite der weißen Endlosrolle; Inhalt bleibt gleich groß.
                          Vorgabe bei Neuinstallation: 50; sonst bisherigen Wert behalten.
  --auto-power-off WERT  Abschaltung: off, 10, 20, 30, 40, 50, 60 (Minuten) oder keep.
                          Ohne Option: Abfrage mit Vorgabe off; ohne Terminal ebenfalls off.
  --samba-user NAME       Lokalen Samba-Benutzer verwenden/bei Bedarf anlegen;
                          ein fehlendes Samba-Passwort wird interaktiv abgefragt.
  --existing-samba-auth   Bestehende Samba-Anmeldung verwenden (auch Domäne).
  -h, --help             Diese Hilfe; keine Änderungen.

Ohne Optionen: USB-Erkennung; vorhandene Samba-Anmeldung weiterverwenden.
Bei neuer Samba-Einrichtung wird Benutzer/Passwort interaktiv eingerichtet.
Kein echter Testdruck wird automatisch ausgelöst.
Eine gewählte Abschaltzeit wird im Drucker gespeichert und anschließend ausgelesen.
HELP
}
fail() { echo "FEHLER: $*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --printer-uri|--samba-user|--media-width|--auto-power-off)
            (($# >= 2)) || fail "Wert für $1 fehlt."
            case "$1" in
                --printer-uri) PRINTER_URI=$2 ;;
                --samba-user) SMB_USER=$2 ;;
                --media-width) MEDIA_WIDTH=$2 ;;
                --auto-power-off) AUTO_POWER_OFF=$2 ;;
            esac
            shift 2 ;;
        --existing-samba-auth) USE_EXISTING_AUTH=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unbekannte Option: $1 (siehe --help)" ;;
    esac
done
[[ -z "$MEDIA_WIDTH" || "$MEDIA_WIDTH" == 50 || "$MEDIA_WIDTH" == 62 ]] || fail "--media-width erwartet 50 oder 62 (weißes Endlospapier)."
case "$AUTO_POWER_OFF" in
    ''|keep|off|10|20|30|40|50|60) ;;
    *) fail "--auto-power-off erwartet off, 10, 20, 30, 40, 50, 60 oder keep." ;;
esac
[[ $EUID == 0 ]] || fail "Bitte mit sudo bash $0 ausführen."
[[ -r /etc/os-release ]] || fail "Linux mit /etc/os-release erforderlich."
# Standard OS identification file owned by the system administrator.
# shellcheck source=/dev/null
. /etc/os-release
case "${ID:-}" in
    debian|raspbian) [[ ${VERSION_ID%%.*} -ge 12 ]] || fail "Debian/Raspberry Pi OS ab Version 12 erforderlich." ;;
    ubuntu) [[ ${VERSION_ID%%.*} -ge 22 ]] || fail "Ubuntu ab 22.04 erforderlich." ;;
    *) fail "Unterstützt: Debian, Ubuntu und Raspberry Pi OS." ;;
esac
[[ -d /run/systemd/system ]] || fail "Dieser Installer benötigt ein laufendes systemd."
command -v apt-get >/dev/null || fail "apt-get fehlt."
[[ -z "$SMB_USER" || $USE_EXISTING_AUTH == 0 ]] || fail "Nur eine Samba-Anmeldeoption wählen."
if [[ -n "$SMB_USER" ]]; then
    [[ "$SMB_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || fail "Ungültiger lokaler Benutzername."
fi

if [[ -z "$AUTO_POWER_OFF" ]]; then
    if [[ -t 0 ]]; then
        echo 'Automatische Abschaltung des QL-800:'
        echo '  aus = eingeschaltet bleiben; 10/20/30/40/50/60 = Minuten ohne Druck'
        echo '  beibehalten = aktuelle Einstellung im Gerät unverändert lassen'
        while true; do
            read -r -p 'Gewünschte Abschaltung [aus]: ' POWER_CHOICE || fail "Eingabe abgebrochen."
            case "${POWER_CHOICE,,}" in
                keep|beibehalten) AUTO_POWER_OFF=keep; break ;;
                ''|off|aus|0) AUTO_POWER_OFF=off; break ;;
                10|20|30|40|50|60) AUTO_POWER_OFF=$POWER_CHOICE; break ;;
                *) echo 'Bitte aus, 10, 20, 30, 40, 50, 60 oder beibehalten eingeben.' ;;
            esac
        done
    else
        AUTO_POWER_OFF=off
        echo 'Ohne Terminal gilt die Vorgabe: automatische Abschaltung aus (--auto-power-off off).'
    fi
fi

HAD_SAMBA_CONFIG=0
[[ ! -f "$SMB_CONFIG" ]] || HAD_SAMBA_CONFIG=1
WORKDIR=$(mktemp -d /tmp/formularkopf-klebchen-install.XXXXXXXX)
trap 'rm -rf -- "$WORKDIR"' EXIT
trap 'echo "FEHLER: Installation in Zeile $LINENO abgebrochen; siehe Ausgabe oben." >&2' ERR

# @@PAYLOAD@@

echo '=== 1/6 Pakete für CUPS, Samba, Bonjour und USB installieren ==='
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
    install -y cups cups-client cups-filters ghostscript samba samba-common-bin \
    smbclient avahi-daemon avahi-utils python3 python3-venv python3-pil \
    libusb-1.0-0 usbutils udev
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ erforderlich"'
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install --disable-pip-version-check 'brother-ql==0.9.4'
# Exercise the actual conversion API before making queue/configuration changes.
"$VENV/bin/python" - <<'PY'
from PIL import Image
from brother_ql.raster import BrotherQLRaster
raster = BrotherQLRaster('QL-800')
raster.add_initialize()
raster.add_autocut(True)
raster.add_margins(35)
raster.add_raster_data(Image.new('1', (720, 899), 0))
assert raster.data
PY

# Package installation creates a default smb.conf if none existed before.
# Preflight the package default or the pre-existing server configuration.
python3 "$WORKDIR/samba_config.py" "$SMB_CONFIG" --check
systemctl enable --now cups.service
if lpstat -p "$QUEUE" >/dev/null 2>&1; then
    CURRENT_URI=$(lpstat -v "$QUEUE")
    [[ "$CURRENT_URI" == *" formularkopf-klebchen://ql800" ]] || fail "Die vorhandene Queue $QUEUE gehört nicht zu diesem Installer."
    [[ -z "$(lpstat -W not-completed -o "$QUEUE")" ]] || fail "Für $QUEUE liegen noch Druckaufträge vor. Zuerst abschließen oder gezielt löschen."
fi

echo '=== 2/6 QL-800 auswählen und Konfiguration prüfen ==='
if [[ -z "$PRINTER_URI" && -f "$CONFIG" ]]; then
    PRINTER_URI=$(python3 - "$CONFIG" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get('printer_uri', ''))
PY
)
fi
if [[ -z "$PRINTER_URI" ]]; then
    "$VENV/bin/python" "$WORKDIR/backend.py" --list-printers > "$WORKDIR/printers.json"
    mapfile -t PRINTERS < <(python3 - "$WORKDIR/printers.json" <<'PY'
import json, sys
for uri in json.load(open(sys.argv[1])):
    print(uri)
PY
)
    case ${#PRINTERS[@]} in
        0) fail "Kein QL-800 (04f9:209b) erkannt. USB anschließen, einschalten und Editor Lite ausschalten; danach erneut starten." ;;
        1) PRINTER_URI=${PRINTERS[0]} ;;
        *)
            echo 'Mehrere QL-800 erkannt:'
            printf '  %s\n' "${PRINTERS[@]}"
            fail "Bitte mit --printer-uri und einer der Seriennummern erneut starten." ;;
    esac
fi
python3 - "$CONFIG" "$WORKDIR/config.json" "$PRINTER_URI" "$MEDIA_WIDTH" <<'PY'
import json, pathlib, sys
source, target, uri, media_width = sys.argv[1:]
config = dict(printer_uri=uri, render_dpi=600, crop_left_pt=26.6, crop_top_pt=16.5,
              crop_width_mm=82.0, crop_height_mm=50.0, rotation=270,
              label_length_mm=82.0, threshold=188, media_width_mm=50)
if pathlib.Path(source).exists():
    config.update(json.loads(pathlib.Path(source).read_text()))
config['printer_uri'] = uri
if media_width:
    config['media_width_mm'] = int(media_width)
pathlib.Path(target).write_text(json.dumps(config, indent=2) + '\n')
PY
"$VENV/bin/python" "$WORKDIR/backend.py" --config "$WORKDIR/config.json" --check-config

echo '=== 3/6 Samba-Anmeldung vorbereiten ==='
ROLE=$(testparm -s --parameter-name='server role' "$SMB_CONFIG" 2>/dev/null)
NEEDS_SAMBA_USER=$((1 - HAD_SAMBA_CONFIG))
if [[ "$ROLE" == 'standalone server' && -z "$(pdbedit -L 2>/dev/null)" ]]; then
    NEEDS_SAMBA_USER=1
fi
if [[ -z "$SMB_USER" && $NEEDS_SAMBA_USER == 1 && $USE_EXISTING_AUTH == 0 ]]; then
    [[ -t 0 ]] || fail "Für die neue Samba-Anmeldung interaktiv starten oder --samba-user NAME verwenden."
    read -r -p 'Neuer/existierender lokaler Samba-Druckbenutzer [klebchen]: ' SMB_USER
    SMB_USER=${SMB_USER:-klebchen}
    [[ "$SMB_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || fail "Ungültiger lokaler Benutzername."
fi
if [[ -n "$SMB_USER" ]]; then
    ROLE=$(testparm -s --parameter-name='server role' "$SMB_CONFIG" 2>/dev/null)
    [[ "$ROLE" == 'standalone server' ]] || fail "Bei Domänenmitgliedschaft --existing-samba-auth und bestehende Domänenkonten verwenden."
    if ! id "$SMB_USER" >/dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin "$SMB_USER"
    fi
    if ! pdbedit -L -u "$SMB_USER" 2>/dev/null | cut -d: -f1 | grep -Fxq "$SMB_USER"; then
        echo "Samba-Passwort für $SMB_USER festlegen:"
        smbpasswd -a "$SMB_USER"
    fi
else
    echo 'Vorhandene Samba-Anmeldung bleibt bestehen; Zugriff für authentifizierte Benutzer gemäß bestehenden Regeln.'
fi

echo '=== 4/6 Druckhelfer und USB-Rechte installieren ==='
install -d -m 755 "$LIBDIR" /usr/local/share/formularkopf-klebchen
install -d -o lp -g lp -m 700 /var/lib/formularkopf-klebchen
install -d -m 755 /etc/udev/rules.d
install -m 644 "$WORKDIR/backend.py" "$LIBDIR/backend.py"
install -m 644 "$WORKDIR/formularkopf-klebchen.ppd" /usr/local/share/formularkopf-klebchen/formularkopf-klebchen.ppd
if [[ -f "$CONFIG" ]]; then
    cp -a "$CONFIG" "$CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
fi
install -o root -g lp -m 640 "$WORKDIR/config.json" "$CONFIG"
cat > /etc/udev/rules.d/70-formularkopf-klebchen.rules <<'RULE'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04f9", ATTR{idProduct}=="209b", GROUP="lp", MODE="0660"
RULE
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=04f9 --attr-match=idProduct=209b
udevadm settle
# Debian/Ubuntu CUPS ServerBin normally /usr/lib/cups; respect a custom setting.
CUPS_SERVERBIN=$(cups-config --serverbin 2>/dev/null || true)
CUPS_SERVERBIN=${CUPS_SERVERBIN:-/usr/lib/cups}
[[ -d "$CUPS_SERVERBIN/backend" ]] || fail "CUPS-Backend-Verzeichnis fehlt: $CUPS_SERVERBIN/backend"
cat > "$WORKDIR/klebchen" <<'BACKEND'
#!/bin/sh
exec /opt/formularkopf-klebchen/venv/bin/python /usr/local/lib/formularkopf-klebchen/backend.py "$@"
BACKEND
install -o root -g root -m 755 "$WORKDIR/klebchen" "$CUPS_SERVERBIN/backend/formularkopf-klebchen"
# Check that CUPS' unprivileged account can load configuration and dependencies.
runuser -u lp -- "$VENV/bin/python" "$LIBDIR/backend.py" --check-config

if [[ "$AUTO_POWER_OFF" != keep ]]; then
    POWER_MINUTES=$AUTO_POWER_OFF
    [[ "$POWER_MINUTES" != off ]] || POWER_MINUTES=0
    echo 'Gewählte automatische Abschaltung im QL-800 einstellen und bestätigen:'
    runuser -u lp -- "$VENV/bin/python" "$LIBDIR/backend.py" --set-auto-power-off "$POWER_MINUTES"
else
    echo 'Automatische Abschaltung: vorhandene Geräteeinstellung beibehalten.'
fi

echo '=== 5/6 Virtuelle A4-Queue, Samba und Bonjour einrichten ==='
lpadmin -p "$QUEUE" -E -v formularkopf-klebchen://ql800 \
    -P /usr/local/share/formularkopf-klebchen/formularkopf-klebchen.ppd \
    -D "$QUEUE" -L 'Brother QL-800 per USB' \
    -o PageSize=A4 -o media=A4 -o Resolution=600dpi \
    -o printer-is-shared=true -o printer-error-policy=stop-printer
# CUPS publishes the queue and its actual capabilities through Avahi/DNS-SD.
# --share-printers enables IPP access from the local subnet by default;
# existing broader access/admin settings remain under the administrator's control.
systemctl enable --now avahi-daemon.service
if [[ -f /etc/cups/cupsd.conf ]]; then
    cp -a /etc/cups/cupsd.conf "/etc/cups/cupsd.conf.klebchen-backup-$(date +%Y%m%d-%H%M%S)"
fi
cupsctl --share-printers Browsing=Yes BrowseLocalProtocols=dnssd
install -d -o root -g root -m 1777 /var/spool/samba/formularkopf-klebchen
SAMBA_OPTIONS=()
if [[ $HAD_SAMBA_CONFIG == 0 ]]; then SAMBA_OPTIONS+=(--new-server); fi
python3 "$WORKDIR/samba_config.py" "$SMB_CONFIG" "${SAMBA_OPTIONS[@]}"
systemctl enable --now smbd.service
systemctl reload smbd.service

echo '=== 6/6 Ergebnis prüfen ==='
lpstat -v "$QUEUE"
[[ "$(testparm -s --section-name="$QUEUE" --parameter-name='printer name' "$SMB_CONFIG" 2>/dev/null)" == "$QUEUE" ]] || fail "Samba-Freigabe konnte nicht bestätigt werden."
systemctl is-active --quiet avahi-daemon.service || fail "Avahi für Bonjour ist nicht aktiv."
CUPS_SETTINGS=$(cupsctl)
grep -Eq '^_share_printers=1$' <<< "$CUPS_SETTINGS" || fail "CUPS-Netzwerkfreigabe ist nicht aktiviert."
echo
printf 'Fertig: \\\\%s\\%s\n' "$(hostname -s)" "$QUEUE"
printf 'Bonjour/IPP: ipp://%s.local:631/printers/%s\n' "$(hostname -s)" "$QUEUE"
echo 'Drucker automatisch per Bonjour suchen oder die IPP-Adresse verwenden.'
echo 'Samba-Clients: generischer PostScript-Treiber; alle Clients: A4 Hochformat, 600 dpi, 100 %.'
echo 'In T2med Strg+E -> formularkopf-klebchen. Kein Brother-QL-Treiber auf den Clients.'
echo 'Passende weiße DK-Endlosrolle einlegen: 50 mm (DK-22223) oder 62 mm (DK-22205).'
echo 'Editor Lite ausschalten. Die Rollenbreite wird vor dem Druck geprüft.'
echo 'Ersten Ausdruck am Gerät auf Leserichtung, 82 mm Gesamtlänge und Schnitt prüfen.'
echo "Konfiguration: $CONFIG"
echo "Rolle ohne Druck auslesen: sudo $VENV/bin/python $LIBDIR/backend.py --printer-status"
echo "Abschaltzeit auslesen: sudo $VENV/bin/python $LIBDIR/backend.py --power-off-status"
echo 'Firewall im Praxisnetz: TCP 445 für Samba, TCP 631 für IPP und UDP 5353 für Bonjour.'
echo 'Bonjour prüfen: avahi-browse -rt _ipp._tcp'
echo 'Anleitung: FORMULARKOPF-KLEBCHEN.md (Windows-/Linux-Einrichtung und Diagnose).'
