// Emit parameterised MCP operations; the caller dispatches them through Blenderwright.
import fs from 'node:fs';
import { loftRecipe, panelRecipe } from './lib/fleet-cage.mjs';
const [spec] = process.argv.slice(2);
if(!spec)throw Error('Usage: node scripts/fleet-recipe.mjs <cage-spec.json>');
const data=JSON.parse(fs.readFileSync(spec,'utf8'));
console.log(JSON.stringify((Array.isArray(data)?data:[data]).flatMap(item=>item.points?panelRecipe(item):loftRecipe(item))));
