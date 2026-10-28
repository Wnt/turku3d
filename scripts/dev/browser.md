# Automation browser

All public project automation uses Chrome for Testing, separate from desktop Chrome.
Install the pinned stable macOS ARM64 release once (shared executable, sandbox profiles):

```bash
mkdir -p "$TURKU3D_WORK/cache" "$HOME/turku3d/browsers/156.0.8078.73"
curl -fL --retry 2 https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.73/mac-arm64/chrome-mac-arm64.zip -o "$TURKU3D_WORK/cache/chrome-mac-arm64.zip"
ditto -x -k "$TURKU3D_WORK/cache/chrome-mac-arm64.zip" "$HOME/turku3d/browsers/156.0.8078.73"
```

The pin is from Google's [stable CfT manifest](https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json), checked 2026-10-28.
`chromeExecutable()` in `tools/dev/chrome-args.mjs` resolves the installed executable under that
version directory. `TURKU3D_CHROME` may override it with an absolute automation-browser executable;
the resolver rejects desktop Chrome, including symlinks, and fails if the file is missing or not executable.
There is no fallback to desktop Chrome. Update the pin and install command together after verification.
Every launch and Playwright `launchOptions` sets `executablePath: chromeExecutable()`.
Python previews invoke Node and import this same resolver; no Python browser resolver is needed.
The private Google tooling and Air sessions retain their existing browser choices.

Every Playwright config and Chrome launcher uses `chromeArgs([...])` from
`tools/dev/chrome-args.mjs`. It merges existing `--disable-features` lists into one
switch, forces `--use-mock-keychain` and `--password-store=basic` for every launch (including native CDP),
and disables `MacAppCodeSignClone`, preventing macOS from copying Chrome's
app bundle at launch. Playwright launch options also set `ignoreDefaultArgs: chromeIgnoreDefaultArgs`
from that module: this removes Playwright's original feature switch while preserving all its
features in the merged argument and avoiding duplicate feature switches.
Tests check defaults against installed Playwright; update them on upgrades. Python previews
import the same module; localhost, the integrator and gate delegate to protected configs.
Do not append another `--disable-features` switch after the helper.

```bash
node tools/dev/check-chrome-launches.mjs        # runs uncached in every fast gate
node --test tools/dev/chrome-args.test.mjs       # synthetic merger and policy checks
python3 scripts/dev/host_slots.py browser -- node tools/dev/verify-chrome-clone.mjs
```

The macOS probe runs the music-lab Playwright spec and a native Chrome CDP launch,
checking the CDP command line and sampling the system clone directory every 50 ms,
including while Chrome is running. It writes `reports/chrome-clone/verification.json`
under `$TURKU3D_WORK`. Unrelated unprotected Chrome launches share that directory and fail the probe. Existing clones are only listed.

