"""Install pinned Khronos macOS arm64 tools inside the sandbox without a system installer."""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import urllib.request

from ..paths import tools_dir

VERSION = "4.4.2"
SHA256 = "500bd8f9d63358c3f3a0d83b724c8574436a72c37dc0e4bad90ec1ca38032c3c"
URL = f"https://github.com/KhronosGroup/KTX-Software/releases/download/v{VERSION}/KTX-Software-{VERSION}-Darwin-arm64.pkg"


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit(
            "This setup target installs macOS arm64 tools; use toktx on PATH or the Node encoder elsewhere."
        )
    root = tools_dir() / "ktx"
    root.mkdir(parents=True, exist_ok=True)
    package = root / "software.pkg"
    if not package.exists():
        with urllib.request.urlopen(URL, timeout=120) as response:
            package.write_bytes(response.read())
    if hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
        raise SystemExit(f"Checksum mismatch: {package}")
    extracted = root / "expanded"
    if not extracted.exists():
        subprocess.run(["pkgutil", "--expand-full", str(package), str(extracted)], check=True)
    for component, sub, names in [
        ("tools", "bin", ("toktx", "ktx")),
        ("library", "lib", ("libktx*.dylib",)),
    ]:
        source = (
            extracted
            / f"KTX-Software-{VERSION}-Darwin-arm64-{component}.pkg/Payload/usr/local"
            / sub
        )
        dest = root / sub
        dest.mkdir(exist_ok=True)
        for name in names:
            for path in source.glob(name):
                shutil.copy2(path, dest / path.name)
    subprocess.run([str(root / "bin/toktx"), "--version"], check=True)


if __name__ == "__main__":
    main()
