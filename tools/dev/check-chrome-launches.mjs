// Static launch policy, including JavaScript embedded in Python preview scripts.
import ts from 'typescript';
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
    function collect(node) {
      if (ts.isImportDeclaration(node) && /(?:^|\/)chrome-args\.mjs$/.test(node.moduleSpecifier.text)) {
        helper = node.importClause?.namedBindings?.elements?.some(e => e.name.text === 'chromeArgs' && !e.propertyName) ?? false;
      }
      // Python passes the canonical module URI as argv[5] to embedded Node scripts.
      if (snippet.includes('await import(process.argv[5])') && source.includes('"tools/dev/chrome-args.mjs").as_uri()')) helper = true;
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
      if (!safe(expression)) {
        const line = tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
        errors.push(`${name}:${line}: Chrome launch args must use chromeArgs() from tools/dev/chrome-args.mjs`);
      }
    }
    function visit(node) {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && ['launch', 'launchPersistentContext', 'launchServer'].includes(node.expression.name.text)
        && /playwright|puppeteer|chromium/.test(snippet)) {
        check(node.arguments[node.expression.name.text === 'launchPersistentContext' ? 1 : 0], node);
      }
      if (ts.isPropertyAssignment(node) && node.name.getText(tree) === 'launchOptions') check(node.initializer, node);
      ts.forEachChild(node, visit);
    }
    visit(tree);
    if (/playwright.*config\.[cm]?ts$/.test(name) && !snippet.includes('launchOptions')) {
      errors.push(`${name}: Playwright config must declare launchOptions with chromeArgs()`);
    }
  }
  // Native CDP launchers must also use the shared merger (or its literal flag in Python/shell).
  if (/--remote-debugging-port|chrome-launcher|puppeteer\.launch|\/Google Chrome\.app\/Contents\/MacOS/.test(source)
    && !name.includes('/tests/') && !source.includes('chromeArgs(')
    && !source.includes('--disable-features=MacAppCodeSignClone')) {
    errors.push(`${name}: native Chrome launcher lacks MacAppCodeSignClone protection`);
  }
  return errors;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const files = execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], { encoding: 'utf8' }).split('\0');
  const errors = [];
  for (const name of new Set(files)) {
    if (!/\.(?:[cm]?[jt]s|py|sh)$/.test(name) || /(?:check-chrome-launches|chrome-args)\.(?:test\.)?mjs$/.test(name)) continue;
    const source = fs.readFileSync(name, 'utf8');
    if (/launch|launchOptions|remote-debugging|Google Chrome\.app/.test(source)) errors.push(...checkChromeLaunches(name, source));
  }
  if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1; }
  else console.log('Chrome launch policy: all launch sites use clone-safe arguments');
}
