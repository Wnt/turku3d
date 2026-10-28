import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {ResponseCatalogue, inventoryState} from './gmaps_capture_state.mjs';

test('session bodies survive empty and resumed quarters without request replay', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'gmaps-session-'));
  try {
    const catalogue=new ResponseCatalogue(path.join(root,'session'));
    const body=Buffer.from('original observed metadata');
    const sha=createHash('sha256').update(body).digest('hex');
    catalogue.add({url:'native/BulkMetadata/one'},sha,body);
    assert.equal(new ResponseCatalogue(path.join(root,'session')).rows.size,1);
    for (const name of ['new','resumed']) {
      const out=path.join(root,name);
      fs.mkdirSync(path.join(out,'responses'),{recursive:true});
      const record={responses:[]};
      catalogue.merge(record,out);
      catalogue.merge(record,out);
      assert.equal(record.responses.length,1);
      assert.deepEqual(fs.readFileSync(path.join(out,record.responses[0].file)),body);
      assert.equal(fs.statSync(path.join(out,record.responses[0].file)).ino,
        fs.statSync(path.join(root,'session',record.responses[0].file)).ino);
    }
    assert.equal(new ResponseCatalogue(path.join(root,'session')).rows.size,1);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('zero missing and a directory never establish eligibility', () => {
  const plan={complete:true,metadata_complete:true,available:0};
  assert.equal(inventoryState(plan),'planned');
  plan.available=100;
  plan.metadata_complete=false;
  assert.equal(inventoryState(plan),'visited');
  plan.metadata_complete=true;
  plan.complete=false;
  assert.equal(inventoryState(plan),'metadata resolved');
  plan.complete=true;
  assert.equal(inventoryState(plan),'eligible');
});
