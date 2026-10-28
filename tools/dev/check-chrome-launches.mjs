// Static launch policy, including JavaScript embedded in Python preview scripts.
import ts from 'typescript';
import { chromeArgs } from './chrome-args.mjs';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export function checkChromeLaunches(name, source) {
  const errors = [];
  const snippets = name.endsWith('.py')
    ? [...source.matchAll(/(?:r)?(?:"""([\s\S]*?)"""|'''([\s\S]*?)''')/g)].map(m => m[1] ?? m[2])
    : [source];
  for (const snippet of snippets) {
    const tree = ts.createSourceFile(name + '.ts', snippet, ts.ScriptTarget.Latest, true);
    const bindings = new Map();
    let helper = false;
    let resolver = false;
    function collect(node) {
      if (ts.isImportDeclaration(node) && /(?:^|\/)chrome-args\.mjs$/.test(node.moduleSpecifier.text)) {
        resolver = node.importClause?.namedBindings?.elements?.some(e => e.name.text === 'chromeExecutable' && !e.propertyName) ?? false;
        helper = node.importClause?.namedBindings?.elements?.some(e => e.name.text === 'chromeArgs' && !e.propertyName) ?? false;
      }
      // Python passes the canonical module URI as argv[5] to embedded Node scripts.
      if (snippet.includes('await import(process.argv[5])') && source.includes('"tools/dev/chrome-args.mjs").as_uri()')) {
        helper = true;
        resolver = /\bchromeExecutable\b/.test(snippet.split('await import(process.argv[5])')[0]);
      }
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) bindings.set(node.name.text, node.initializer);
      ts.forEachChild(node, collect);
    }
    collect(tree);
    function safe(expression, seen = new Set()) {
      if (!expression) return false;
      if (ts.isCallExpression(expression) && expression.expression.getText(tree) === 'chromeArgs') return helper;
      if (ts.isIdentifier(expression) && !seen.has(expression.text)) {
        return safe(bindings.get(expression.text), new Set([...seen, expression.text]));
      }
      return false;
    }
    function argsIn(object) {
      return object && ts.isObjectLiteralExpression(object)
        ? object.properties.find(p => p.name?.getText(tree).replace(/['"]/g, '') === 'args')
        : undefined;
    }
    function check(object, node) {
      const property = argsIn(object);
      const expression = property && (ts.isShorthandPropertyAssignment(property) ? property.name : property.initializer);
      const ignored = object && ts.isObjectLiteralExpression(object)
        ? object.properties.find(p => p.name?.getText(tree) === 'ignoreDefaultArgs')?.initializer?.getText(tree)
        : undefined;
      const executable = object && ts.isObjectLiteralExpression(object)
        ? object.properties.find(p => p.name?.getText(tree) === 'executablePath')?.initializer?.getText(tree)
        : undefined;
      if (!safe(expression) || ignored !== 'chromeIgnoreDefaultArgs' || !resolver || executable !== 'chromeExecutable()') {
        const line = tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
        errors.push(`${name}:${line}: Chrome launch must use chromeExecutable(), chromeArgs() and chromeIgnoreDefaultArgs from tools/dev/chrome-args.mjs (including mock keychain/password-store defaults)`);
      }
    }
    function visit(node) {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && ['launch', 'launchPersistentContext', 'launchServer'].includes(node.expression.name.text)
        && /playwright|puppeteer|chromium/.test(snippet)) {
        check(node.arguments[node.expression.name.text === 'launchPersistentContext' ? 1 : 0], node);
      }
      if (ts.isCallExpression(node) && /^(?:spawn|execFile|execFileSync)$/.test(node.expression.getText(tree))
        && node.arguments[0] && (safe(node.arguments[1]) || /(?:Google Chrome(?: for Testing)?|chromium|chrome)(?:$|\/)/i.test(ts.isStringLiteral(node.arguments[0]) ? node.arguments[0].text : node.arguments[0].getText(tree)))
        && (!safe(node.arguments[1]) || !resolver || node.arguments[0].getText(tree) !== 'chromeExecutable()')) {
        errors.push(`${name}: native Chrome launch must use chromeExecutable() and chromeArgs()`);
      }
      if (ts.isPropertyAssignment(node) && node.name.getText(tree) === 'launchOptions') check(node.initializer, node);
      ts.forEachChild(node, visit);
    }
    visit(tree);
    if (/playwright.*config\.[cm]?[jt]s$/.test(name) && !snippet.includes('launchOptions')) {
      errors.push(`${name}: Playwright config must declare launchOptions with chromeArgs()`);
    }
  }
  // Native CDP launchers must also use the shared merger (or its literal flag in Python/shell).
  if (/--remote-debugging-port|chrome-launcher|puppeteer\.launch|\/Google Chrome\.app\/Contents\/MacOS/.test(source)
    && !name.includes('/tests/') && !source.includes('chromeExecutable(')) {
    errors.push(`${name}: native Chrome launcher must use the shared automation browser resolver`);
  }
  if (/channel\s*:\s*['"]chrome['"]|\/Applications\/Google Chrome\.app/.test(source)) {
    errors.push(`${name}: desktop Chrome path/channel is forbidden`);
  }
  return errors;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const files = execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], { encoding: 'utf8' }).split('\0');
  const errors = [];
  if (!chromeArgs().includes('--use-mock-keychain') || !chromeArgs().includes('--password-store=basic')) {
    errors.push('Shared Chrome arguments must isolate keychain access with --use-mock-keychain and --password-store=basic');
  }
  for (const name of new Set(files)) {
    if (!/\.(?:[cm]?[jt]s|py|sh)$/.test(name) || /(?:check-chrome-launches|chrome-args)\.(?:test\.)?mjs$/.test(name)) continue;
    const source = fs.readFileSync(name, 'utf8');
    if (/launch|launchOptions|remote-debugging|Google Chrome\.app/.test(source)) errors.push(...checkChromeLaunches(name, source));
  }
  if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1; }
  else console.log('Chrome launch policy: all launch sites use the automation browser resolver and clone-safe arguments');
}
