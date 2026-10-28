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
  return [...result, `--disable-features=${[...features].join(',')}`];
}
