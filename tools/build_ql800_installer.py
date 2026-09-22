#!/usr/bin/env python3
"""Build the downloadable, self-contained QL-800 installer from its sources."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    "formularkopf_klebchen.py": "backend.py",
    "samba_config.py": "samba_config.py",
    "formularkopf-klebchen.ppd": "formularkopf-klebchen.ppd",
}


def build():
    template = (ROOT / "ql800/installer.sh").read_text()
    blocks = []
    for index, (source, target) in enumerate(FILES.items()):
        data = (ROOT / "ql800" / source).read_text()
        delimiter = f"KLEBCHEN_PAYLOAD_{index}_END"
        assert delimiter not in data.splitlines()
        blocks.append(f'cat > "${{WORKDIR}}/{target}" <<\'{delimiter}\'\n{data.rstrip()}\n{delimiter}')
    assert template.count("# @@PAYLOAD@@") == 1
    return template.replace("# @@PAYLOAD@@", "\n\n".join(blocks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "install_formularkopf_klebchen.sh"
    output = build()
    if args.check:
        if not target.exists() or target.read_text() != output:
            parser.exit(1, "Installer ist nicht aktuell; tools/build_ql800_installer.py ausführen.\n")
    else:
        target.write_text(output)
        target.chmod(0o755)
