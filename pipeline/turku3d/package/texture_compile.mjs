// Content-addressed compilation shared across tiles; only complete files become visible.
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const GRACE_MS = 15 * 60 * 1000;
const MAX_BYTES = 1024 ** 3;
const lastPrune = new Map();

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}

function touch(file) {
  const now = new Date();
  fs.utimesSync(file, now, now);
}

// Entries used by an active package have a grace interval. This is a rebuildable
// cache, separate from published assets and the two retained tile generations.
export function pruneCompiledTextures(root, now = Date.now(), budget = MAX_BYTES) {
  const entries = fs.readdirSync(root).filter(name => /^[a-f0-9]{64}\.(png|ktx2|rgb)$/.test(name))
    .flatMap(name => {
      const file = path.join(root, name);
      try { const stat = fs.statSync(file); return [{ file, bytes:stat.size, used:stat.mtimeMs }]; }
      catch (error) { if (error.code === 'ENOENT') return []; throw error; }
    }).sort((a, b) => a.used - b.used);
  let bytes = entries.reduce((sum, entry) => sum + entry.bytes, 0);
  for (const entry of entries) {
    if (bytes <= budget || entry.used > now - GRACE_MS) break;
    try {
      if (fs.statSync(entry.file).mtimeMs > now - GRACE_MS) continue;
      fs.unlinkSync(entry.file); bytes -= entry.bytes;
    }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}

export async function compileTexture(request, key, extension, produce) {
  const root = request.sharedTextureCache ?? request.textureCache;
  fs.mkdirSync(root, { recursive:true });
  const file = path.join(root, `${key}.${extension}`);
  const lock = `${file}.lock`;
  const stats = request.textureCompilation ??= { hits:0, misses:0, waits:0, seconds:0 };
  const started = performance.now();
  const hit = () => {
    try { touch(file); stats.hits++; return true; }
    catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  };
  let locked = false;
  try {
    while (!hit()) {
      try {
        fs.writeFileSync(lock, JSON.stringify({ pid:process.pid }), { flag:'wx' });
        locked = true;
        break;
      } catch (error) { if (error.code !== 'EEXIST') throw error; }
      // A killed producer leaves no valid output. Reclaim its lock, never its process.
      try {
        const descriptor = fs.openSync(lock, 'r');
        let owner, inode;
        try {
          inode = fs.fstatSync(descriptor).ino;
          owner = JSON.parse(fs.readFileSync(descriptor, 'utf8'));
        } finally { fs.closeSync(descriptor); }
        if (!alive(owner.pid)) {
          // Elect one reaper. Two waiters must never unlink a replacement lock.
          const reap = `${lock}.reap`;
          try {
            fs.linkSync(lock, reap);
            try {
              if (fs.statSync(lock).ino === inode && fs.statSync(reap).ino === inode) fs.unlinkSync(lock);
            } finally { fs.unlinkSync(reap); }
          } catch (reapError) {
            if (!['ENOENT', 'EEXIST'].includes(reapError.code)) throw reapError;
          }
          continue;
        }
      } catch (error) {
        if (error.code === 'ENOENT') continue;
        if (!(error instanceof SyntaxError)) throw error;
        // The owner may still be between open(O_EXCL) and write.
        try { if (Date.now() - fs.statSync(lock).mtimeMs > GRACE_MS) fs.unlinkSync(lock); }
        catch (statError) { if (statError.code !== 'ENOENT') throw statError; }
      }
      if (performance.now() - started > 300_000) throw new Error(`Texture compile lock timed out: ${key}`);
      stats.waits++;
      await delay(50);
    }
    if (locked && !hit()) {
      const temp = path.join(root, `.${key}.${process.pid}.${randomUUID()}.${extension}`);
      try {
        // Adopt retained tile encodes when introducing the cross-tile cache.
        const local = path.join(request.textureCache, `${key}.${extension}`);
        if (local !== file && fs.existsSync(local)) fs.copyFileSync(local, temp);
        else { stats.misses++; await produce(temp); }
        fs.renameSync(temp, file);
        touch(file);
      } finally { fs.rmSync(temp, { force:true }); }
    }
    // Local packages retain their own references; shared entries use bounded LRU.
    if (root === request.textureCache) (request.usedTextureCache ??= new Set()).add(path.basename(file));
    return file;
  } finally {
    if (locked) fs.unlinkSync(lock);
    stats.seconds += (performance.now() - started) / 1000;
    if (request.sharedTextureCache && Date.now() - (lastPrune.get(root) ?? 0) > 60_000) {
      lastPrune.set(root, Date.now());
      pruneCompiledTextures(root);
    }
  }
}
