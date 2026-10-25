import { readFileSync, writeFileSync } from 'node:fs';
import { checkStructures } from './structures.ts';
import type { StructureCase } from './structures.ts';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: structures-cli.ts cases.json report.json');
const cases: StructureCase[] = JSON.parse(readFileSync(input, 'utf8'));
const report = await checkStructures(cases);
writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ passed: report.passed, cases: report.cases, passed_cases: report.passed_cases }));
process.exitCode = report.passed ? 0 : 1;
