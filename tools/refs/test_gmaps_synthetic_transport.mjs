import {test} from 'node:test';
import assert from 'node:assert/strict';
import {installNativeTransport, requestsFromPlan} from './gmaps_synthetic_transport.mjs';

test('synthetic requests reuse observed native XHR headers, session and format', async () => {
  const sent=[];
  class XHR {
    headers={};
    status=200;
    response=new ArrayBuffer(3);
    open(method,url) {this.method=method;this.url=url;}
    setRequestHeader(name,value) {this.headers[name]=value;}
    send() {sent.push(this);queueMicrotask(()=>this.onload?.());}
  }
  globalThis.window={XMLHttpRequest:XHR};
  try {
    installNativeTransport();
    const native=new window.XMLHttpRequest();
    native.open('GET','https://kh.google.com/rt/earth/NodeData/pb=!1m2!1s306!2u1016!2e1!3u1040!4b0');
    native.setRequestHeader('x-native-format','observed');
    native.withCredentials=true;
    native.send();
    const result=await window.__gmapsRequest('NodeData','306061717',945);
    assert.equal(result.bytes,3);
    assert.equal(sent[1].url,'https://kh.google.com/rt/earth/NodeData/pb=!1m2!1s306061717!2u945!2e1!3u1040!4b0');
    assert.deepEqual(sent[1].headers,{'x-native-format':'observed'});
    assert.equal(sent[1].withCredentials,true);
    await assert.rejects(window.__gmapsRequest('BulkMetadata','306',1000),/No native GET/);
  } finally {delete globalThis.window;}
});

test('metadata frontier resolves before requesting only advertised terminal epochs', () => {
  const plan={frontier:[{node:'3060',bulk_epoch:1016,epoch:945}],nodes:[{node:'30601',epoch:946}]};
  assert.deepEqual(requestsFromPlan(plan),[{kind:'BulkMetadata',node:'3060',epoch:1016}]);
  plan.frontier=[];
  assert.deepEqual(requestsFromPlan(plan),[{kind:'NodeData',node:'30601',epoch:946}]);
});
