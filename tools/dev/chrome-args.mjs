import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Pinned to the stable CfT release checked on 2026-10-28. Never fall back to desktop Chrome.
export const chromeVersion = '156.0.8078.73';
export function chromeExecutable() {
  if (process.env.TURKU3D_CHROME && !path.isAbsolute(process.env.TURKU3D_CHROME)) {
    throw new Error('TURKU3D_CHROME must be an absolute automation-browser executable');
  }
  const executable = path.resolve(process.env.TURKU3D_CHROME ?? path.join(os.homedir(),
    'turku3d/browsers', chromeVersion, 'chrome-mac-arm64',
    'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'));
  const real = fs.realpathSync(executable);
  if (/\/Google Chrome\.app(?:\/|$)/i.test(real)) {
    throw new Error('TURKU3D_CHROME must use an automation browser, not desktop Google Chrome');
  }
  fs.accessSync(real, fs.constants.X_OK);
  return real;
}

// Remove Playwright 1.64.1's default switch to send one unambiguous feature list.
// Preserve its defaults in the merged switch; the contract test detects upgrades.
const playwrightDisabledFeatures = 'AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,AvoidCorsURLLoaderRestartOnRedirect,Translate,AutoDeElevate,OptimizationHints,NetworkTimeServiceQuerying,AimEnabled,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion';
export const chromeIgnoreDefaultArgs = [`--disable-features=${playwrightDisabledFeatures}`];

// Chrome accepts only the last --disable-features switch. Always consolidate it here.
export function chromeArgs(args = []) {
  const features = new Set(['MacAppCodeSignClone', ...playwrightDisabledFeatures.split(',')]);
  const result = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--disable-features' || arg.startsWith('--disable-features=')) {
      const value = arg === '--disable-features' ? args[++i] : arg.slice('--disable-features='.length);
      for (const feature of (value ?? '').split(',')) if (feature) features.add(feature);
    } else result.push(arg);
  }
  return [...result.filter(arg => arg !== '--use-mock-keychain' && !arg.startsWith('--password-store=')),
    '--use-mock-keychain', '--password-store=basic', `--disable-features=${[...features].join(',')}`];
}
