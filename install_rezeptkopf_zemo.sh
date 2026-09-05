#!/usr/bin/env bash
set -Eeuo pipefail

# Rezeptkopf-ZEMO Installer
# Getestete Zielkonfiguration:
# - Ubuntu 24.04 LTS
# - BIXOLON SPP-R200III via Bluetooth Classic / SPP / RFCOMM channel 1
# - ZEMO 2189, 50 mm Medienbreite, 82 mm Etikettenlänge
# - T2med: "Moderner Druck mit Papierformat und Seitenlayout"
# - Virtueller CUPS-Drucker: Rezeptkopf-ZEMO
#
# Die Kalibrierwerte unten entsprechen exakt dem im Chat final bestätigten Stand.

QUEUE_NAME="Rezeptkopf-ZEMO"
BT_MAC_PRESET="${BT_MAC:-}"
BT_MAC=""
RFCOMM_CHANNEL="${RFCOMM_CHANNEL:-1}"
RFCOMM_INDEX="${RFCOMM_INDEX:-0}"
RFCOMM_DEVICE="/dev/rfcomm${RFCOMM_INDEX}"

CONFIG_FILE="/etc/rezeptkopf-zemo.conf"
HELPER="/usr/local/sbin/rezeptkopf-zemo.py"
RFCOMM_UNIT="/etc/systemd/system/zemo-rfcomm.service"
HELPER_UNIT="/etc/systemd/system/rezeptkopf-zemo.service"

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
echo "CUPS-Drucker:    ${QUEUE_NAME}"
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
ZEMO_PORT=9101

# ZEMO 2189
CROP_W_MM=50.0
CROP_H_MM=82.0

# FINAL bestätigte Crop-Position auf der vom virtuellen Drucker
# gelieferten Letter-Seite (612 x 792 pt).
CROP_LEFT_PT=165.4961
CROP_TOP_PT=348.0000

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
PORT = int(os.getenv("ZEMO_PORT", "9101"))
SERIAL = os.getenv("RFCOMM_DEVICE", "/dev/rfcomm0")

CROP_W_MM = float(os.getenv("CROP_W_MM", "50.0"))
CROP_H_MM = float(os.getenv("CROP_H_MM", "82.0"))

CROP_W_PT = CROP_W_MM / 25.4 * 72.0
CROP_H_PT = CROP_H_MM / 25.4 * 72.0

# Final bestätigte Position auf dem vollständigen Letter-PostScript-Job.
CROP_LEFT_PT = float(os.getenv("CROP_LEFT_PT", "165.4961"))
CROP_TOP_PT = float(os.getenv("CROP_TOP_PT", "348.0000"))

RENDER_DPI = int(os.getenv("RENDER_DPI", "609"))
TARGET_DPI = int(os.getenv("TARGET_DPI", "203"))
PRINT_DOTS = int(os.getenv("PRINT_DOTS", "384"))
THRESHOLD = int(os.getenv("THRESHOLD", "188"))
KEEP_DEBUG = os.getenv("KEEP_DEBUG", "0") == "1"

FULL_MEDIA_DOTS = round(CROP_W_MM / 25.4 * TARGET_DPI)
HEIGHT_DOTS = round(CROP_H_MM / 25.4 * TARGET_DPI)


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

    # PostScript-Punkte -> Pixel bei RENDER_DPI.
    # Bezugspunkt ist LINKS OBEN der vollständigen Letter-Seite.
    left = round(CROP_LEFT_PT * RENDER_DPI / 72.0)
    top = round(CROP_TOP_PT * RENDER_DPI / 72.0)
    width = round(CROP_W_PT * RENDER_DPI / 72.0)
    height = round(CROP_H_PT * RENDER_DPI / 72.0)

    box = (left, top, left + width, top + height)

    log(
        f"Crop: left={left}, top={top}, "
        f"width={width}, height={height}"
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

    # Hochwertiges Downsampling:
    # zuerst maßhaltig auf 50 x 82 mm bei 203 dpi.
    img = img.resize(
        (FULL_MEDIA_DOTS, HEIGHT_DOTS),
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

    # SPP-R200III: 384 echte Druckpunkte (~48 mm).
    # Nicht auf 384 stauchen, sondern die rund 1 mm links/rechts
    # außerhalb des realen Druckkopfs symmetrisch verwerfen.
    left = (FULL_MEDIA_DOTS - PRINT_DOTS) // 2
    img = img.crop((left, 0, left + PRINT_DOTS, HEIGHT_DOTS))

    # Final bestätigte Leserichtung.
    img = img.rotate(180)

    # Kein Dithering: für kleine Rezeptkopfschrift war ein harter,
    # kontrollierter Schwellenwert besser lesbar.
    img = img.point(
        lambda p: 0 if p < THRESHOLD else 255,
        mode="L"
    )

    return img


def pack_escpos(img):
    w, h = img.size

    if w != PRINT_DOTS:
        raise RuntimeError(f"Breite {w}, erwartet {PRINT_DOTS}")

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
    log(
        "Rezeptkopf-ZEMO gestartet: "
        f"{HOST}:{PORT}; "
        f"Crop=({CROP_LEFT_PT:.4f},{CROP_TOP_PT:.4f}) pt; "
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

cat > "${HELPER_UNIT}" <<'EOF'
[Unit]
Description=Virtueller Rezeptkopf-ZEMO Drucker
After=cups.service bluetooth.service zemo-rfcomm.service
Wants=cups.service bluetooth.service zemo-rfcomm.service

[Service]
Type=simple
EnvironmentFile=/etc/rezeptkopf-zemo.conf
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

# Auch bei einer erneuten Installation neu starten, damit eine geänderte
# Bluetooth-MAC und Aktualisierungen des Hilfsdienstes sofort wirksam werden.
systemctl restart zemo-rfcomm.service
systemctl restart rezeptkopf-zemo.service

# Der Drucker darf beim Installieren ausgeschaltet sein; der RFCOMM-Dienst
# versucht automatisch weiter zu verbinden.
sleep 2

echo
echo "=== 7/8 Virtuellen CUPS-PostScript-Drucker anlegen ==="

MODEL="$(lpinfo -m 2>/dev/null | awk 'BEGIN{IGNORECASE=1} /Generic PostScript Printer/ {print $1; exit}')"

if [[ -z "${MODEL}" ]]; then
  echo "FEHLER: 'Generic PostScript Printer' wurde von CUPS nicht gefunden."
  echo "Verfügbare Generic-Modelle:"
  lpinfo -m 2>/dev/null | grep -i generic | head -30 || true
  exit 30
fi

echo "CUPS-Modell: ${MODEL}"

lpadmin -x "${QUEUE_NAME}" 2>/dev/null || true

lpadmin \
  -p "${QUEUE_NAME}" \
  -E \
  -v "socket://127.0.0.1:9101" \
  -m "${MODEL}"

cupsaccept "${QUEUE_NAME}"
cupsenable "${QUEUE_NAME}"

# Dies entspricht der getesteten Queue-Konfiguration.
lpadmin \
  -p "${QUEUE_NAME}" \
  -o PageSize-default=A6 \
  -o Resolution-default=600dpi \
  -o printer-is-shared=false

echo
echo "=== 8/8 Abschlussprüfung ==="

echo
echo "--- CUPS ---"
lpstat -v "${QUEUE_NAME}" || true
lpstat -p "${QUEUE_NAME}" -l || true

echo
echo "--- Rezeptkopf-Dienst ---"
systemctl --no-pager --full status rezeptkopf-zemo.service | head -16 || true

echo
echo "--- RFCOMM ---"
systemctl --no-pager --full status zemo-rfcomm.service | head -16 || true

echo
echo "============================================================"
echo " INSTALLATION ABGESCHLOSSEN"
echo "============================================================"
echo
echo "T2med:"
echo "  Drucker: Rezeptkopf-ZEMO"
echo "  Druckmodus: Moderner Druck mit Papierformat und Seitenlayout"
echo "  Print-Service-Auflösung: 600 dpi"
echo
echo "SPP-R200III:"
echo "  Label Mode EIN"
echo "  ZEMO 2189 eingelegt und Gap-Kalibrierung durchgeführt"
echo
echo "Live-Log:"
echo "  sudo journalctl -u rezeptkopf-zemo -f"
echo
echo "Bluetooth-Status:"
echo "  rfcomm"
echo
echo "Hinweis: Es wird absichtlich KEINE Testseite automatisch gedruckt,"
echo "damit kein ZEMO-Etikett verschwendet wird."
