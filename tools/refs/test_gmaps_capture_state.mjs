import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {ResponseCatalogue, parseMapCoordinate, inventoryState} from './gmaps_capture_state.mjs';

test('native coordinates are strict geographic observations', () => {
  assert.deepEqual(parseMapCoordinate('60.450123, 22.261234'), {lat:60.450123,lon:22.261234});
  assert.equal(parseMapCoordinate('https://maps/@60.450123,22.261234'), null);
  assert.equal(parseMapCoordinate('99.450123, 22.261234'), null);
});

test('session bodies survive empty and resumed quarters without request replay', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'gmaps-session-'));
  try {
    const catalogue=new ResponseCatalogue(path.join(root,'session'));
    const body=Buffer.from('original observed metadata');
    const sha=createHash('sha256').update(body).digest('hex');
    catalogue.add({url:'native/BulkMetadata/one'},sha,body);
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
  const record={detail_visits:[]};
  const plan={complete:true,frontier:0,summary:{terminal_data_nodes:0}};
  assert.equal(inventoryState(record,plan),'planned');
  record.detail_visits.push({timing:{target_error_m:1}});
  assert.equal(inventoryState(record,plan),'visited');
  plan.summary.terminal_data_nodes=100;
  plan.frontier=1;
  assert.equal(inventoryState(record,plan),'visited');
  plan.frontier=0;
  plan.complete=false;
  assert.equal(inventoryState(record,plan),'metadata resolved');
  plan.complete=true;
  assert.equal(inventoryState(record,plan),'eligible');
});
