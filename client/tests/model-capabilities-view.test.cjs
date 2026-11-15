const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../components/panels/model-capabilities.js'), 'utf8');
function load() {
  const context = vm.createContext({});
  // Match production core/store.js: const declarations are not global object properties.
  vm.runInContext('const esc = value => String(value);', context);
  vm.runInContext(source, context);
  assert.equal(context.esc, undefined);
  return context.ModelCapabilitiesUI;
}
test('model capability view renders inside a host with lexical, rather than window, helpers', () => {
  const ui = load();
  assert.match(ui.badges({ config: { imageRouting: 'native' } }), /图片输入<b>支持/);
  assert.match(ui.fields({ config: { maxOutputTokens: 8192 } }), /value="8192"/);
  assert.match(ui.report({ checkedAt: '2026-10-01T00:00:00Z', checks: { connection: { status: 'passed' } } }), /检测通过/);
});
test('capability errors and form values are escaped independently of the host helper', () => {
  const ui = load();
  const value = '<img src=x onerror="alert(1)">&\'';
  for (const html of [ui.report({ error: value }), ui.fields({ config: { maxOutputTokens: value } })]) {
    assert.ok(!html.includes('<img'));
    assert.ok(html.includes('&lt;img'));
    assert.ok(html.includes('&quot;'));
    assert.ok(html.includes('&amp;'));
    assert.ok(html.includes('&#39;'));
  }
});

function probeFixture(localOnly) {
  const calls = [], saves = [], scope = 'fixture-account/device';
  const providerId = localOnly ? 'device:shared-provider' : 'shared-provider';
  const provider = { id: providerId, name: 'Saved fixture service', localOnly,
    personal: true, baseUrl: 'https://fixture.invalid/v1', authMode: 'key', myKeySet: true };
  const context = { URL, structuredClone, setTimeout, clearTimeout,
    document: { getElementById: () => null }, addEventListener() {},
    esc: value => String(value ?? ''), _humanErr: error => error.message,
    fixtureCatalog: { providers: [provider], myModels: [{ providerId, name: 'saved-model', localOnly }],
      usable: [], revision: 'hub-1', localRevision: 1 },
    SoduWorkAPI: { taskStorageKey: () => scope, checkMyModelCapabilities: async request => {
      calls.push(structuredClone(request));
      return { ok: true, capabilityProfile: { imageInput: { state: 'unknown', source: 'fixture' } } };
    } },
    saveProbe: async closeAfter => { saves.push(closeAfter); return true; },
  };
  context.window = context;
  vm.createContext(context);
  for (const file of ['model-capabilities.js', 'model-catalog-editor.js', 'panels-models.js'])
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../components/panels', file), 'utf8'), context, { filename: file });
  const evaluate = source => vm.runInContext(source, context);
  evaluate(`_mdData = fixtureCatalog; _mdMyModels = fixtureCatalog.myModels.slice(); _mdLoadState = 'ready';
    _mdNew = {id:'device:pending-service', name:'Unfinished draft', baseUrl:'', personal:true, localOnly:true};
    _mdKeyEdits[_mdNew.id] = 'synthetic-unsaved-draft-key';
    _mdMyModels.push({providerId:_mdNew.id, name:'unsaved-draft-model', localOnly:true});
    _mdDrafts.set(_mdNew.id, {open:true, changed:true, fields:{label:'Pending editor'}});
    _mdLocalDirty = _mdDirty = true; _mdLocalKeyIds = new Set([_mdNew.id]);
    _mdSave = saveProbe;`);
  const draft = () => evaluate(`JSON.stringify({service:_mdNew, key:_mdKeyEdits[_mdNew.id],
    editor:_mdDrafts.get(_mdNew.id), models:_mdMyModels.filter(row=>row.providerId===_mdNew.id)})`);
  return { context, calls, saves, providerId, evaluate, draft,
    check: () => context._mdCheckModel(providerId, 'saved-model') };
}

for (const localOnly of [true, false]) {
  const ownership = localOnly ? 'device-local' : 'Hub-managed';
  test(`${ownership} capability probes retain ownership without saving an unrelated incomplete service`, async () => {
    const f = probeFixture(localOnly), draft = f.draft();
    assert.equal(f.evaluate(`_mdNeedsSave(${JSON.stringify(f.providerId)})`), false,
      'only the unrelated incomplete draft is dirty');
    await f.check();
    assert.deepEqual(f.saves, []);
    assert.deepEqual(f.calls, [{ providerId: f.providerId, model: 'saved-model', localOnly,
      checks: ['connection', 'imageInput', 'toolCalling', 'streaming'] }]);
    assert.equal(f.draft(), draft, 'probing another service cannot discard its draft, key or model editor');
    assert.equal(f.evaluate('_mdDirty'), true);
    assert.equal(f.evaluate(`_mdChecks.get(_mdCheckKey(${JSON.stringify(f.providerId)}, 'saved-model')).ok`), true);
  });

  test(`${ownership} capability probes save relevant changes before passing ownership to the API`, async () => {
    const f = probeFixture(localOnly), order = [];
    f.context.saveProbe = async closeAfter => { order.push('save'); f.saves.push(closeAfter); return true; };
    f.context.SoduWorkAPI.checkMyModelCapabilities = async request => {
      order.push('check'); f.calls.push(structuredClone(request)); return { ok: true };
    };
    f.evaluate(`_mdSave = saveProbe; _mdTouchProvider(${JSON.stringify(f.providerId)}, {name:'Changed fixture service'});`);
    assert.equal(f.evaluate(`_mdNeedsSave(${JSON.stringify(f.providerId)})`), true);
    await f.check();
    assert.deepEqual(order, ['save', 'check']);
    assert.deepEqual(f.saves, [false]);
    assert.equal(f.calls[0].localOnly, localOnly);
    assert.equal(f.calls[0].providerId, f.providerId);
  });

  test(`${ownership} capability probes stop when relevant configuration cannot be saved`, async () => {
    const f = probeFixture(localOnly);
    f.context.saveProbe = async closeAfter => { f.saves.push(closeAfter); return false; };
    f.evaluate(`_mdSave = saveProbe; _mdTouchProvider(${JSON.stringify(f.providerId)}, {name:'Changed fixture service'});`);
    await f.check();
    assert.deepEqual(f.saves, [false]);
    assert.deepEqual(f.calls, [], 'a failed save cannot test a different persisted credential');
    assert.match(f.evaluate(`_mdChecks.get(_mdCheckKey(${JSON.stringify(f.providerId)}, 'saved-model')).error`), /请先保存模型配置/);
  });
}
