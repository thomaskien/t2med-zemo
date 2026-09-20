#!/usr/bin/env bash
set -Eeuo pipefail

# Rezeptkopf-ZEMO Installer
# Getestete Zielkonfiguration:
# - Ubuntu 24.04 LTS
# - BIXOLON SPP-R200III via Bluetooth Classic / SPP / RFCOMM channel 1
# - ZEMO 2189, 50 mm Medienbreite, 82 mm Etikettenlänge
# - T2med: "Moderner Druck mit Papierformat und Seitenlayout"
# - Virtuelle CUPS-Drucker: Rezeptkopf-ZEMO und Formularkopf-ZEMO
#
# Die Kalibrierwerte unten entsprechen exakt dem im Chat final bestätigten Stand.

RECIPE_QUEUE="Rezeptkopf-ZEMO"
FORM_QUEUE="Formularkopf-ZEMO"
RECIPE_PORT=9101
FORM_PORT=9102
BT_MAC_PRESET="${BT_MAC:-}"
BT_MAC=""
RFCOMM_CHANNEL="${RFCOMM_CHANNEL:-1}"
RFCOMM_INDEX="${RFCOMM_INDEX:-0}"
RFCOMM_DEVICE="/dev/rfcomm${RFCOMM_INDEX}"

CONFIG_FILE="/etc/rezeptkopf-zemo.conf"
HELPER="/usr/local/sbin/rezeptkopf-zemo.py"
RFCOMM_UNIT="/etc/systemd/system/zemo-rfcomm.service"
RECIPE_UNIT="/etc/systemd/system/rezeptkopf-zemo.service"
FORM_UNIT="/etc/systemd/system/formularkopf-zemo.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bitte als root starten, z. B.:"
  echo "  sudo bash $0"
  exit 1
fi

echo "============================================================"
echo " Rezeptkopf-ZEMO Installer"
echo "============================================================"

while true; do
  if [[ -n "${BT_MAC_PRESET}" ]]; then
    BT_MAC_PROMPT="Bluetooth-MAC des SPP-R200III [${BT_MAC_PRESET}]: "
  else
    BT_MAC_PROMPT="Bluetooth-MAC des SPP-R200III (AA:BB:CC:DD:EE:FF): "
  fi

  if ! read -r -p "${BT_MAC_PROMPT}" BT_MAC_INPUT; then
    echo
    echo "FEHLER: Die Bluetooth-MAC konnte nicht eingelesen werden."
    exit 2
  fi

  BT_MAC="${BT_MAC_INPUT:-${BT_MAC_PRESET}}"
  BT_MAC="${BT_MAC//[[:space:]]/}"
  BT_MAC="${BT_MAC//-/:}"
  BT_MAC="$(printf '%s' "${BT_MAC}" | tr '[:lower:]' '[:upper:]')"

  if [[ "${BT_MAC}" =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]]; then
    break
  fi

  echo "Ungültige Bluetooth-MAC. Erwartetes Format: AA:BB:CC:DD:EE:FF"
done

echo
echo "Bluetooth-MAC:   ${BT_MAC}"
echo "RFCOMM-Kanal:    ${RFCOMM_CHANNEL}"
echo "RFCOMM-Gerät:    ${RFCOMM_DEVICE}"
echo "CUPS-Drucker:    ${RECIPE_QUEUE} (${RECIPE_PORT})"
echo "                  ${FORM_QUEUE} (${FORM_PORT})"
echo

echo "=== 1/8 Abhängigkeiten installieren ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  bluez \
  cups \
  cups-client \
  cups-filters \
  ghostscript \
  python3 \
  python3-pil

systemctl enable --now bluetooth.service
systemctl enable --now cups.service

echo
echo "=== 2/8 Bluetooth prüfen ==="
bluetoothctl power on >/dev/null 2>&1 || true

if bluetoothctl info "${BT_MAC}" 2>/dev/null | grep -q "Paired: yes"; then
  echo "Drucker ist bereits gekoppelt."
else
  echo "Drucker ist noch nicht gekoppelt."
  echo "Es wird ein Pairing-Versuch gestartet."
  bluetoothctl --timeout 8 scan on >/dev/null 2>&1 || true

  if ! bluetoothctl pair "${BT_MAC}"; then
    echo
    echo "FEHLER: Automatisches Pairing ist fehlgeschlagen."
    echo "Bitte den SPP-R200III einmal über die Ubuntu-Bluetooth-Einstellungen"
    echo "oder mit bluetoothctl koppeln (PIN ggf. 0000) und den Installer"
    echo "danach erneut starten."
    echo
    echo "Die Adresse ist: ${BT_MAC}"
    exit 20
  fi
fi

bluetoothctl trust "${BT_MAC}" >/dev/null 2>&1 || true
bluetoothctl --timeout 2 scan off >/dev/null 2>&1 || true

echo
echo "=== 3/8 Exakte Rezeptkopf-ZEMO-Konfiguration schreiben ==="

if [[ -f "${CONFIG_FILE}" ]]; then
  cp -a "${CONFIG_FILE}" "${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
fi

cat > "${CONFIG_FILE}" <<EOF
# Rezeptkopf-ZEMO – final kalibrierte Konfiguration
BT_MAC=${BT_MAC}
RFCOMM_CHANNEL=${RFCOMM_CHANNEL}
RFCOMM_INDEX=${RFCOMM_INDEX}
RFCOMM_DEVICE=${RFCOMM_DEVICE}

ZEMO_HOST=127.0.0.1
RECIPE_PORT=${RECIPE_PORT}
FORM_PORT=${FORM_PORT}

# Physisches ZEMO-2189-Medium
MEDIA_W_MM=50.0
MEDIA_H_MM=82.0

# Alte Rezeptmethode: bestätigter 50-x-82-mm-Crop auf der vom virtuellen
# Drucker gelieferten Letter-Seite (612 x 792 pt).
RECIPE_CROP_W_MM=50.0
RECIPE_CROP_H_MM=82.0
RECIPE_CROP_LEFT_PT=165.4961
RECIPE_CROP_TOP_PT=348.0000

# Formularkopf aus der A4-Ausgabe von T2med (Strg+E): fester, zentrierter
# 82-x-50-mm-Crop um die gemessene Inhaltsbox. Ursprung links oben;
# 26.6 pt entsprechen ca. 9.4 mm, 16.5 pt ca. 5.8 mm.
FORM_CROP_W_MM=82.0
FORM_CROP_H_MM=50.0
FORM_CROP_LEFT_PT=26.6
FORM_CROP_TOP_PT=16.5

# Leserichtung des Formularkopfs; zulässig sind ausschließlich 90 oder 270.
FORM_ROTATION=270

# Qualitätsparameter
RENDER_DPI=609
TARGET_DPI=203
PRINT_DOTS=384
THRESHOLD=188

# 0 = keine Patientendaten als Debugdateien behalten
KEEP_DEBUG=0
EOF

chmod 600 "${CONFIG_FILE}"

echo
echo "=== 4/8 RFCOMM-Autoconnect als systemd-Dienst einrichten ==="

cat > "${RFCOMM_UNIT}" <<'EOF'
[Unit]
Description=Bluetooth RFCOMM connection for Rezeptkopf-ZEMO
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
EnvironmentFile=/etc/rezeptkopf-zemo.conf
ExecStartPre=-/usr/bin/rfcomm release ${RFCOMM_INDEX}
ExecStart=/usr/bin/rfcomm connect ${RFCOMM_DEVICE} ${BT_MAC} ${RFCOMM_CHANNEL}
ExecStop=-/usr/bin/rfcomm release ${RFCOMM_INDEX}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo
echo "=== 5/8 Hochqualitäts-Crop-/Raster-Dienst installieren ==="

cat > "${HELPER}" <<'PY'
#!/usr/bin/env python3

import os
import socket
import tempfile
import subprocess
import shutil
import time
from pathlib import Path

from PIL import Image, ImageOps, ImageFilter

HOST = os.getenv("ZEMO_HOST", "127.0.0.1")
PORT = int(os.getenv("ZEMO_PORT", "0"))
SERIAL = os.getenv("RFCOMM_DEVICE", "/dev/rfcomm0")
QUEUE_NAME = os.getenv("QUEUE_NAME", "unbekannt")

INPUT_MODE = os.getenv("INPUT_MODE", "").lower()
if INPUT_MODE not in ("formularkopf", "rezept"):
    raise RuntimeError(
        f"Ungültiger INPUT_MODE={INPUT_MODE!r}; erwartet formularkopf oder rezept"
    )

MEDIA_W_MM = float(os.getenv("MEDIA_W_MM", "50.0"))
MEDIA_H_MM = float(os.getenv("MEDIA_H_MM", "82.0"))

# Alte, final bestätigte Position auf dem vollständigen Letter-PostScript-Job.
RECIPE_CROP_W_MM = float(os.getenv("RECIPE_CROP_W_MM", "50.0"))
RECIPE_CROP_H_MM = float(os.getenv("RECIPE_CROP_H_MM", "82.0"))
RECIPE_CROP_LEFT_PT = float(os.getenv("RECIPE_CROP_LEFT_PT", "165.4961"))
RECIPE_CROP_TOP_PT = float(os.getenv("RECIPE_CROP_TOP_PT", "348.0000"))

# Fester 82-x-50-mm-Ausschnitt der A4-Formularkopf-Ausgabe (Strg+E).
# Er ist um die gemessene Inhaltsbox zentriert; Ursprung ist links oben.
FORM_CROP_W_MM = float(os.getenv("FORM_CROP_W_MM", "82.0"))
FORM_CROP_H_MM = float(os.getenv("FORM_CROP_H_MM", "50.0"))
FORM_CROP_LEFT_PT = float(os.getenv("FORM_CROP_LEFT_PT", "26.6"))
FORM_CROP_TOP_PT = float(os.getenv("FORM_CROP_TOP_PT", "16.5"))
FORM_ROTATION = None
if INPUT_MODE == "formularkopf":
    try:
        FORM_ROTATION = int(os.getenv("FORM_ROTATION", "270"))
    except ValueError as exc:
        raise RuntimeError(
            "Ungültige FORM_ROTATION; erwartet 90 oder 270"
        ) from exc
    if FORM_ROTATION not in (90, 270):
        raise RuntimeError(
            f"Ungültige FORM_ROTATION={FORM_ROTATION}; erwartet 90 oder 270"
        )

RENDER_DPI = int(os.getenv("RENDER_DPI", "609"))
TARGET_DPI = int(os.getenv("TARGET_DPI", "203"))
PRINT_DOTS = int(os.getenv("PRINT_DOTS", "384"))
THRESHOLD = int(os.getenv("THRESHOLD", "188"))
KEEP_DEBUG = os.getenv("KEEP_DEBUG", "0") == "1"

MEDIA_W_DOTS = round(MEDIA_W_MM / 25.4 * TARGET_DPI)
MEDIA_H_DOTS = round(MEDIA_H_MM / 25.4 * TARGET_DPI)


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def sanitize_job(data: bytes) -> bytes:
    """PJL/UEL vor einem PostScript- oder PDF-Dokument entfernen."""
    ps = data.find(b"%!PS")
    pdf = data.find(b"%PDF-")
    positions = [x for x in (ps, pdf) if x >= 0]
    if positions:
        data = data[min(positions):]
    return data


def render_crop(jobfile: str, pngfile: str):
    """
    Die vollständige Seite zuerst hochauflösend rendern und erst DANACH
    als Raster croppen. Das ist absichtlich so: T2med/Generic-PostScript
    setzt selbst PageSize/setpagedevice und würde einen Ghostscript-
    PageOffset sonst überschreiben.
    """
    fullfile = pngfile + ".full.png"

    cmd = [
        "gs",
        "-q",
        "-dNOPAUSE",
        "-dBATCH",
        "-dSAFER",
        "-dTextAlphaBits=4",
        "-dGraphicsAlphaBits=4",
        "-dDOINTERPOLATE",
        "-sDEVICE=pnggray",
        f"-r{RENDER_DPI}",
        f"-sOutputFile={fullfile}",
        "-f",
        jobfile,
    ]

    log("Ghostscript: vollständige Seite hochauflösend rendern")
    subprocess.run(cmd, check=True)

    full = Image.open(fullfile).convert("L")
    log(f"Vollseite: {full.width} x {full.height}")

    if INPUT_MODE == "formularkopf":
        crop_w_mm = FORM_CROP_W_MM
        crop_h_mm = FORM_CROP_H_MM
        crop_left_pt = FORM_CROP_LEFT_PT
        crop_top_pt = FORM_CROP_TOP_PT
    else:
        crop_w_mm = RECIPE_CROP_W_MM
        crop_h_mm = RECIPE_CROP_H_MM
        crop_left_pt = RECIPE_CROP_LEFT_PT
        crop_top_pt = RECIPE_CROP_TOP_PT

    # PostScript-Punkte -> Pixel bei RENDER_DPI.
    # Bezugspunkt ist LINKS OBEN der vollständigen Eingabeseite.
    left = round(crop_left_pt * RENDER_DPI / 72.0)
    top = round(crop_top_pt * RENDER_DPI / 72.0)
    width = round(crop_w_mm / 25.4 * RENDER_DPI)
    height = round(crop_h_mm / 25.4 * RENDER_DPI)

    box = (left, top, left + width, top + height)

    log(
        f"Crop ({INPUT_MODE}): left={crop_left_pt:.4f} pt/{left} px, "
        f"top={crop_top_pt:.4f} pt/{top} px, "
        f"size={crop_w_mm:.1f} x {crop_h_mm:.1f} mm/"
        f"{width} x {height} px"
    )

    crop = full.crop(box)
    crop.save(pngfile)

    if KEEP_DEBUG:
        shutil.copy(fullfile, "/tmp/rezeptkopf-zemo-full-debug.png")
        crop.save("/tmp/rezeptkopf-zemo-crop-debug.png")


def prepare_bitmap(pngfile: str):
    img = Image.open(pngfile).convert("L")
    log(f"Crop-Raster: {img.width} x {img.height}")

    img = ImageOps.autocontrast(img)

    # Hochwertiges, maßhaltiges Downsampling bei 203 dpi. Der Formularkopf
    # beginnt als 82 x 50 mm großer Landschaftsausschnitt; das alte Rezept
    # bereits als 50 x 82 mm großer Hochformausschnitt.
    if INPUT_MODE == "formularkopf":
        img = img.resize(
            (MEDIA_H_DOTS, MEDIA_W_DOTS),
            Image.Resampling.LANCZOS
        )
    else:
        img = img.resize(
            (MEDIA_W_DOTS, MEDIA_H_DOTS),
            Image.Resampling.LANCZOS
        )

    # Leichte Schärfung nach dem Downsampling.
    img = img.filter(
        ImageFilter.UnsharpMask(
            radius=0.6,
            percent=120,
            threshold=2
        )
    )

    if INPUT_MODE == "formularkopf":
        # 82 x 50 mm werden durch die Drehung zum physischen 50-x-82-mm-
        # Etikett. expand=True ist wichtig, damit nichts abgeschnitten wird.
        img = img.rotate(FORM_ROTATION, expand=True)

    # SPP-R200III: 384 echte Druckpunkte (~48 mm). Nicht auf 384 stauchen,
    # sondern die rund 1 mm links/rechts symmetrisch verwerfen.
    left = (MEDIA_W_DOTS - PRINT_DOTS) // 2
    img = img.crop((left, 0, left + PRINT_DOTS, MEDIA_H_DOTS))

    if INPUT_MODE == "rezept":
        # Final bestätigte 180-Grad-Leserichtung der bisherigen Methode,
        # weiterhin nach dem symmetrischen 384-Punkte-Crop.
        img = img.rotate(180)

    if img.size != (PRINT_DOTS, MEDIA_H_DOTS):
        raise RuntimeError(
            f"Endformat {img.width} x {img.height}, erwartet "
            f"{PRINT_DOTS} x {MEDIA_H_DOTS}"
        )

    # Kein Dithering: für kleine Rezeptkopfschrift war ein harter,
    # kontrollierter Schwellenwert besser lesbar.
    img = img.point(
        lambda p: 0 if p < THRESHOLD else 255,
        mode="L"
    )

    return img


def pack_escpos(img):
    w, h = img.size

    if (w, h) != (PRINT_DOTS, MEDIA_H_DOTS):
        raise RuntimeError(
            f"Endformat {w} x {h}, erwartet {PRINT_DOTS} x {MEDIA_H_DOTS}"
        )

    rowbytes = (w + 7) // 8
    bitmap = bytearray(rowbytes * h)
    px = img.load()

    for y in range(h):
        base = y * rowbytes
        for x in range(w):
            if px[x, y] < 128:
                bitmap[base + (x // 8)] |= 0x80 >> (x % 8)

    xL = rowbytes & 0xff
    xH = (rowbytes >> 8) & 0xff
    yL = h & 0xff
    yH = (h >> 8) & 0xff

    out = bytearray()

    # Initialisierung
    out += b"\x1b@"

    # GS v 0 – Raster Bit Image
    out += bytes([
        0x1d, 0x76, 0x30, 0x00,
        xL, xH, yL, yH
    ])
    out += bitmap

    # WICHTIG: Im Label Mode bis zur nächsten Etikettenposition fördern.
    # Dieser Form Feed war im finalen Test exakt richtig.
    out += b"\x0c"

    return out


def wait_for_serial():
    """
    Kurz auf den automatischen RFCOMM-Reconnect warten.
    Der Drucker sollte für einen Druckauftrag eingeschaltet sein.
    """
    deadline = time.time() + 60
    announced = False

    while not os.path.exists(SERIAL):
        if not announced:
            log(f"Warte auf Bluetooth-Drucker ({SERIAL}) ...")
            announced = True
        if time.time() >= deadline:
            raise RuntimeError(
                f"{SERIAL} ist nach 60 s nicht verfügbar. "
                "Drucker einschalten und Bluetooth-Verbindung prüfen."
            )
        time.sleep(1)


def send_serial(data):
    wait_for_serial()

    log(f"Sende {len(data)} Bytes an {SERIAL}")

    fd = os.open(SERIAL, os.O_WRONLY | os.O_NOCTTY)

    try:
        view = memoryview(data)

        # 1024-Byte-Blöcke: im Test stabiler als ein einzelner
        # großer RFCOMM-Write.
        while len(view):
            chunk = view[:1024]
            sent = 0

            while sent < len(chunk):
                n = os.write(fd, chunk[sent:])
                if n <= 0:
                    raise RuntimeError("Schreiben auf RFCOMM fehlgeschlagen")
                sent += n

            view = view[len(chunk):]

        try:
            import termios
            termios.tcdrain(fd)
        except Exception:
            pass

    finally:
        os.close(fd)


def process_job(data):
    data = sanitize_job(data)

    with tempfile.TemporaryDirectory(
        prefix="rezeptkopf-zemo-",
        dir="/tmp"
    ) as td:
        job = os.path.join(td, "job.dat")
        png = os.path.join(td, "crop.png")

        Path(job).write_bytes(data)

        log(f"Druckjob empfangen: {len(data)} Bytes")

        render_crop(job, png)
        img = prepare_bitmap(png)

        log(
            f"Endformat: {img.width} x {img.height} Punkte "
            f"bei {TARGET_DPI} dpi"
        )

        if KEEP_DEBUG:
            shutil.copy(job, "/tmp/rezeptkopf-zemo-last-job")
            img.save("/tmp/rezeptkopf-zemo-last.png")

        escpos = pack_escpos(img)
        send_serial(escpos)

        log("Druckjob abgeschlossen")


def main():
    if INPUT_MODE == "formularkopf":
        crop_description = (
            f"({FORM_CROP_LEFT_PT:.4f},{FORM_CROP_TOP_PT:.4f}) pt, "
            f"{FORM_CROP_W_MM:.1f} x {FORM_CROP_H_MM:.1f} mm, "
            f"Rotation={FORM_ROTATION}°"
        )
    else:
        crop_description = (
            f"({RECIPE_CROP_LEFT_PT:.4f},{RECIPE_CROP_TOP_PT:.4f}) pt, "
            f"{RECIPE_CROP_W_MM:.1f} x {RECIPE_CROP_H_MM:.1f} mm, "
            "Rotation=180°"
        )

    log(
        f"{QUEUE_NAME} gestartet: "
        f"{HOST}:{PORT}; "
        f"Modus={INPUT_MODE}; Crop={crop_description}; "
        f"Render={RENDER_DPI} dpi -> {TARGET_DPI} dpi"
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(5)

        while True:
            conn, _addr = server.accept()

            with conn:
                chunks = []

                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)

                data = b"".join(chunks)
                if not data:
                    continue

                try:
                    process_job(data)
                except Exception as exc:
                    log(f"FEHLER: {exc}")


if __name__ == "__main__":
    main()
PY

chmod 755 "${HELPER}"

cat > "${RECIPE_UNIT}" <<'EOF'
[Unit]
Description=Virtueller Rezeptkopf-ZEMO Drucker
After=cups.service bluetooth.service zemo-rfcomm.service
Wants=cups.service bluetooth.service zemo-rfcomm.service

[Service]
Type=simple
EnvironmentFile=/etc/rezeptkopf-zemo.conf
Environment=INPUT_MODE=rezept
Environment=ZEMO_PORT=9101
Environment=QUEUE_NAME=Rezeptkopf-ZEMO
ExecStart=/usr/local/sbin/rezeptkopf-zemo.py
Restart=always
RestartSec=2
User=root
Group=root

# Keine Patientendaten dauerhaft speichern.
PrivateTmp=false

[Install]
WantedBy=multi-user.target
EOF

cat > "${FORM_UNIT}" <<'EOF'
[Unit]
Description=Virtueller Formularkopf-ZEMO Drucker
After=cups.service bluetooth.service zemo-rfcomm.service
Wants=cups.service bluetooth.service zemo-rfcomm.service

[Service]
Type=simple
EnvironmentFile=/etc/rezeptkopf-zemo.conf
Environment=INPUT_MODE=formularkopf
Environment=ZEMO_PORT=9102
Environment=QUEUE_NAME=Formularkopf-ZEMO
ExecStart=/usr/local/sbin/rezeptkopf-zemo.py
Restart=always
RestartSec=2
User=root
Group=root

# Keine Patientendaten dauerhaft speichern.
PrivateTmp=false

[Install]
WantedBy=multi-user.target
EOF

echo
echo "=== 6/8 Dienste aktivieren ==="
systemctl daemon-reload
systemctl enable zemo-rfcomm.service
systemctl enable rezeptkopf-zemo.service
systemctl enable formularkopf-zemo.service

# Auch bei einer erneuten Installation neu starten, damit eine geänderte
# Bluetooth-MAC und Aktualisierungen des Hilfsdienstes sofort wirksam werden.
systemctl restart zemo-rfcomm.service
systemctl restart rezeptkopf-zemo.service
systemctl restart formularkopf-zemo.service

# Der Drucker darf beim Installieren ausgeschaltet sein; der RFCOMM-Dienst
# versucht automatisch weiter zu verbinden.
sleep 2

echo
echo "=== 7/8 Virtuelle CUPS-PostScript-Drucker anlegen ==="

MODEL="$(lpinfo -m 2>/dev/null | awk 'BEGIN{IGNORECASE=1} /Generic PostScript Printer/ {print $1; exit}')"

if [[ -z "${MODEL}" ]]; then
  echo "FEHLER: 'Generic PostScript Printer' wurde von CUPS nicht gefunden."
  echo "Verfügbare Generic-Modelle:"
  lpinfo -m 2>/dev/null | grep -i generic | head -30 || true
  exit 30
fi

echo "CUPS-Modell: ${MODEL}"

lpadmin -x "${RECIPE_QUEUE}" 2>/dev/null || true
lpadmin -x "${FORM_QUEUE}" 2>/dev/null || true

lpadmin \
  -p "${RECIPE_QUEUE}" \
  -E \
  -v "socket://127.0.0.1:${RECIPE_PORT}" \
  -m "${MODEL}"

cupsaccept "${RECIPE_QUEUE}"
cupsenable "${RECIPE_QUEUE}"

# Dies entspricht der getesteten Rezept-Queue-Konfiguration.
lpadmin \
  -p "${RECIPE_QUEUE}" \
  -o PageSize-default=A6 \
  -o Resolution-default=600dpi \
  -o printer-is-shared=false

lpadmin \
  -p "${FORM_QUEUE}" \
  -E \
  -v "socket://127.0.0.1:${FORM_PORT}" \
  -m "${MODEL}"

cupsaccept "${FORM_QUEUE}"
cupsenable "${FORM_QUEUE}"

lpadmin \
  -p "${FORM_QUEUE}" \
  -o PageSize-default=A4 \
  -o Resolution-default=600dpi \
  -o printer-is-shared=false

echo
echo "=== 8/8 Abschlussprüfung ==="

echo
echo "--- CUPS ---"
lpstat -v "${RECIPE_QUEUE}" || true
lpstat -p "${RECIPE_QUEUE}" -l || true
lpstat -v "${FORM_QUEUE}" || true
lpstat -p "${FORM_QUEUE}" -l || true

echo
echo "--- Rezeptkopf-Dienst ---"
systemctl --no-pager --full status rezeptkopf-zemo.service | head -16 || true

echo
echo "--- Formularkopf-Dienst ---"
systemctl --no-pager --full status formularkopf-zemo.service | head -16 || true

echo
echo "--- RFCOMM ---"
systemctl --no-pager --full status zemo-rfcomm.service | head -16 || true

echo
echo "============================================================"
echo " INSTALLATION ABGESCHLOSSEN"
echo "============================================================"
echo
echo "T2med:"
echo "  Formularkopf-ZEMO: Formularkopf mit Strg+E ausgeben"
echo "    (keine Auswahl zwischen Grünem und normalem/Kassenrezept)"
echo "  Rezeptkopf-ZEMO: vollständige Rezeptseite wie bisher ausgeben"
echo "  Druckmodus: Moderner Druck mit Papierformat und Seitenlayout"
echo "  Print-Service-Auflösung: 600 dpi"
echo
echo "WICHTIG: Nicht gleichzeitig über beide Warteschlangen drucken."
echo "Beide Dienste verwenden gemeinsam ${RFCOMM_DEVICE}."
echo
echo "SPP-R200III:"
echo "  Label Mode EIN"
echo "  ZEMO 2189 eingelegt und Gap-Kalibrierung durchgeführt"
echo
echo "Live-Log:"
echo "  sudo journalctl -u rezeptkopf-zemo -f"
echo "  sudo journalctl -u formularkopf-zemo -f"
echo
echo "Bluetooth-Status:"
echo "  rfcomm"
echo
echo "Hinweis: Es wird absichtlich KEINE Testseite automatisch gedruckt,"
echo "damit kein ZEMO-Etikett verschwendet wird."
