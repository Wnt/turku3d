// The external profiling recorder contains a nested JS template: preserve its literal escapes before use.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

export function checkedRecorder(sourceFile, outputDirectory) {
  const original = fs.readFileSync(sourceFile, 'utf8');
  let source = original;
  if (!source.includes('const probe = String.raw`')) {
    source = source.replaceAll(".split('\\n')", ".split('\\\\n')")
      .replaceAll(".replace(/\\?[^:]*/", ".replace(/\\\\?[^:]*/");
  }
  if (!source.includes("physics?.worker?.addEventListener('message', onReply)")) {
    source = source.replace("    const desc = Object.getOwnPropertyDescriptor(Worker.prototype, 'onmessage');",
      "    window.__game?.physics?.worker?.addEventListener('message', onReply);\n    const desc = Object.getOwnPropertyDescriptor(Worker.prototype, 'onmessage');");
  }
  const marker = 'const probe = ';
  const declaration = source.indexOf(marker);
  const start = declaration + marker.length;
  const end = source.indexOf('\n`;', start) + 2;
  if (declaration < 0 || end < start) throw new Error('recorder probe template not found');
  const injected = vm.runInNewContext(source.slice(start, end));
  new vm.Script(injected); // Fail before taking a measurement if the injected probe cannot compile.
  const target = path.join(outputDirectory, 'recorder.mjs');
  fs.writeFileSync(target, source);
  return target;
}
