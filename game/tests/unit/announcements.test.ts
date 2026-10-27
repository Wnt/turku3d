import { describe, expect, it } from 'vitest';
import { addressSentence, nextPhrase, PHRASES } from '../../src/gameplay/content/announcements';
import { sentenceClips, type VoiceClip } from '../../src/audio/voice';
import { freshSave, parseSave } from '../../src/gameplay/missions/save';

describe('spoken announcements', () => {
  it('uses every phrase once before reshuffling, including after a save round trip', () => {
    const save = freshSave(42);
    const first = Array.from({ length: 12 }, () => nextPhrase('double', save.phrases ??= {}, save.seed));
    expect(new Set(first).size).toBe(PHRASES.double.length);
    const restored = parseSave(JSON.stringify(save), 0);
    const next = Array.from({ length: 12 }, () => nextPhrase('double', restored.phrases ??= {}, restored.seed));
    expect(new Set(next).size).toBe(PHRASES.double.length);
    expect(next[0]).not.toBe(first.at(-1));
  });

  it('assembles a double HUD sentence in the same order as its clips', () => {
    const phrase = PHRASES.double[0];
    const message = addressSentence(phrase, 'Läntinen Rantakatu 15', 'Kaskenkatu 4a');
    const clips = [
      ['phrase_double_1', phrase], ['address_lantinen_rantakatu_15', 'Läntinen Rantakatu 15'],
      ['address_then', 'Then'], ['address_kaskenkatu_4a', 'Kaskenkatu 4a'],
    ].map(([id, text]) => ({ id, text })) as VoiceClip[];
    expect(message).toContain('Läntinen Rantakatu 15. Then Kaskenkatu 4a.');
    expect(sentenceClips(message, clips)).toEqual(clips.map(c => c.text));
    const single = addressSentence(PHRASES.order[0], 'Kaskenkatu 4a');
    const singleClips = [{ ...clips[0], id: 'phrase_order_1', text: PHRASES.order[0] }, clips[3]];
    expect(sentenceClips(single, singleClips)).toEqual(singleClips.map(c => c.text));
  });
});
