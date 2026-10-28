// Chrome accepts only the last --disable-features switch. Always consolidate it here.
export function chromeArgs(args = []) {
  const features = new Set(['MacAppCodeSignClone']);
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
