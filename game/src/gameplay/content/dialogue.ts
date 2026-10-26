// Typed access to dialogue.json (~/turku3d/briefs/dialogue-contract.md). Code references lines by id only; the
// displayed text is exactly the line text, so the voice player can match the clip by text.
import data from './dialogue.json' with { type: 'json' };

interface DialogueLine {
  id: string;
  speaker: string;
  text: string;
  /** False for HUD-only prompts that get no voice clip. */
  voiced?: boolean;
}
interface DialogueFile {
  speakers: Record<string, { name: string; voice: string }>;
  lines: DialogueLine[];
}

export const DIALOGUE: DialogueFile = data;
const byId = new Map(DIALOGUE.lines.map(line => [line.id, line]));

export function dialogueLine(id: string): DialogueLine {
  const line = byId.get(id);
  if (!line) throw new Error(`unknown dialogue line ${id}`);
  return line;
}
export const lineText = (id: string) => dialogueLine(id).text;
/** Story intro paragraphs shown on the start screen of a new game. */
export const INTRO_LINE_IDS = ['intro_1', 'intro_2', 'intro_3'] as const;
