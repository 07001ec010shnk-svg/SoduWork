'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const vm = require('node:vm'), zlib = require('node:zlib'), crypto = require('node:crypto');
const archive = require('../main/conversation/zip-archive');
const artifacts = require('../main/conversation/artifact-runtime');
const outputs = require('../main/pi-session/outputs');
const mate = require('../components/panels/chat-mate-mention');
const { createPickFolderHandler } = require('../main/pi-session/mate-message-ipc');

function website(t, depth, empty = false) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'soduwork-website-send-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const directory = path.join(root, ...Array.from({ length: depth }, (_, i) => 'level-' + i));
  fs.mkdirSync(directory, { recursive: true });
  const script = empty ? 'app.js' : path.relative(root, path.join(directory, 'required.js')).replaceAll('\\', '/');
  fs.writeFileSync(path.join(root, 'index.html'), '<script src="' + script + '"></script>');
  fs.writeFileSync(path.join(root, script), 'window.fixtureLoaded = true;');
  const session = { cwd: root, outputSnapshot: () => outputs.scan(root, 0, 0) };
  const api = {
    outputs: async () => ({ ok: true, artifacts: artifacts.decorate('fixture', { items: session.outputSnapshot().delivery.items }) }),
    artifactAction: (ref, id, action) => artifacts.action(session, ref, id, action, {}),
    pickShareFolder: () => createPickFolderHandler({ BrowserWindow: { fromWebContents: () => null },
      dialog: { showOpenDialog: async () => ({ filePaths: [root] }) }, shareArchive: archive })({ sender: {} }),
  };
  return { root, script, api, context: { ref: 'fixture', api } };
}

function entries(bytes) {
  const result = new Map(); let at = 0;
  while (bytes.readUInt32LE(at) === 0x04034b50) {
    const method = bytes.readUInt16LE(at + 8), size = bytes.readUInt32LE(at + 18);
    const nameLength = bytes.readUInt16LE(at + 26), extraLength = bytes.readUInt16LE(at + 28);
    const name = bytes.subarray(at + 30, at + 30 + nameLength).toString('utf8');
    at += 30 + nameLength + extraLength;
    const data = bytes.subarray(at, at + size); at += size;
    result.set(name, method === 8 ? zlib.inflateRawSync(data) : data);
  }
  assert.equal(bytes.readUInt32LE(at), 0x02014b50);
  return result;
}

function events() {
  const handlers = new Map();
  return {
    addEventListener(type, handler) { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(handler); },
    removeEventListener(type, handler) { handlers.get(type)?.delete(handler); },
    async dispatchEvent(event) { for (const handler of handlers.get(event.type) || []) await handler(event); },
  };
}
function element(tag = 'div') {
  return { ...events(), tag, children: [], style: {}, value: '', textContent: '', attributes: {}, nodeType: 1,
    isConnected: true, scrollHeight: 80, selectionStart: 0, focus() {}, remove() {},
    setAttribute(name, value) { this.attributes[name] = value; },
    appendChild(child) { this.children.push(child); }, append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; }, contains(target) { return target === this || this.children.some(c => c.contains(target)); },
    querySelector(tag) { return this.children.find(c => c.tag === tag); },
    set innerHTML(value) { this.children = (value.match(/<(b|span)>/g) || []).map(t => element(t.slice(1, -1))); },
  };
}
async function settled(predicate) {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'The sharing card did not finish its current action');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
async function sharingCard(t, fixture, folderOnly = false) {
  const input = element('textarea'), host = element(), document = { ...events(), createElement: element };
  host.getBoundingClientRect = () => ({ left: 0, top: 500, bottom: 550, width: 600 });
  const browser = { ...events(), document, innerWidth: 1200, innerHeight: 900, crypto, Event, setTimeout, clearTimeout,
    requestAnimationFrame: callback => setTimeout(callback, 0), cancelAnimationFrame: clearTimeout };
  browser.window = browser;
  vm.runInNewContext(fs.readFileSync(require.resolve('../components/panels/chat-mate-mention'), 'utf8'), browser);
  const sent = [], messages = [];
  const api = folderOnly ? { ...fixture.api, outputs: async () => ({ ok: true, artifacts: [] }) } : fixture.api;
  const controller = browser.ChatMateMention.create({ input, host,
    mates: async () => ({ mates: [{ id: 'recipient', name: 'Recipient', hasBox: true }] }),
    context: () => ({ ref: 'fixture', api }), progress: async () => 'Fixture ready to share',
    send: async (id, text, payload) => { sent.push(payload); return { ok: true, delivered: true }; },
    say: message => messages.push(message) });
  t.after(() => controller.destroy());
  input.value = '@'; input.selectionStart = 1; await input.dispatchEvent({ type: 'input' });
  const menu = host.children[0];
  await settled(() => menu.children.some(c => c.tag === 'button'));
  await menu.children.find(c => c.tag === 'button').dispatchEvent({ type: 'click' });
  const card = host.children[1];
  const results = () => card.children.find(c => c.className === 'nmate-results');
  await settled(() => !String(results()?.children[0]?.textContent || '').includes('正在整理'));
  const send = card.children.find(c => c.className === 'nmate-card-send');
  await settled(() => (!send.disabled && card.children.find(c => c.tag === 'textarea').value) ||
    results().children.some(c => c.tag === 'button'));
  return { card, send, sent, messages, results, async pickFolder() {
    const tools = card.children.find(c => c.className === 'nmate-card-tools');
    await tools.children.find(c => c.textContent === '添加文件夹').dispatchEvent({ type: 'click' });
  } };
}

for (const entry of ['conversation', 'folder']) {
  for (const scenario of [{ name: 'required assets at depth 12', depth: 12 },
    { name: 'a shallow website with an empty directory at depth 13', depth: 13, empty: true }]) {
    test(entry + ' send preserves all bytes for ' + scenario.name, async t => {
      const fixture = website(t, scenario.depth, scenario.empty);
      const ui = await sharingCard(t, fixture, entry === 'folder');
      if (entry === 'folder') await ui.pickFolder();
      assert.equal(ui.send.disabled, false);
      await ui.send.dispatchEvent({ type: 'click' });
      assert.equal(ui.sent.length, 1);
      assert.equal(ui.sent[0].files.length, 1, 'Only the complete ZIP may contribute an attachment');
      const attachment = ui.sent[0].files[0]; assert.match(attachment.name, /\.zip$/);
      const extracted = entries(Buffer.from(attachment.data, 'base64'));
      assert.deepEqual([...extracted.keys()].sort(), ['index.html', fixture.script].sort());
      for (const [name, bytes] of extracted) assert.deepEqual(bytes, fs.readFileSync(path.join(fixture.root, name)));
      const browser = { window: {} };
      vm.runInNewContext(extracted.get(fixture.script).toString('utf8'), browser);
      assert.equal(browser.window.fixtureLoaded, true);
    });
  }
  test(entry + ' rejects a required asset at depth 13 without any outgoing attachment', async t => {
    const fixture = website(t, 13);
    const ui = await sharingCard(t, fixture, entry === 'folder');
    if (entry === 'folder') await ui.pickFolder();
    const errors = ui.messages.join('\n') + ui.results().children.map(c => c.textContent).join('\n');
    assert.match(errors, /nested more than 12 levels/);
    assert.equal(ui.results().children.filter(c => c.tag === 'label').length, 0);
    await ui.send.dispatchEvent({ type: 'click' });
    assert.ok(ui.sent.every(payload => payload.files.length === 0), 'No partial ZIP or standalone HTML may be sent');
  });
}

test('send preparation never falls back to bare HTML for an unbundled website', async t => {
  const fixture = website(t, 13);
  const rows = (await fixture.api.outputs()).artifacts.filter(file => file.name === 'index.html');
  await assert.rejects(mate.outputPayload(fixture.context, rows, 'fixture-message'), /nested more than 12 levels/);
});

test('legacy website send preparation packages complete files and keeps preview metadata', async t => {
  const fixture = website(t, 12);
  const rows = (await fixture.api.outputs()).artifacts.filter(file => file.name === 'index.html');
  const payload = await mate.outputPayload(fixture.context, rows, 'legacy-message');
  assert.equal(payload.files.length, 1);
  assert.match(payload.files[0].name, /\.zip$/);
  assert.equal(payload.website.status, 'zip_only');
  const extracted = entries(Buffer.from(payload.files[0].data, 'base64'));
  assert.deepEqual(extracted.get(fixture.script), fs.readFileSync(path.join(fixture.root, fixture.script)));
});

test('a website packaged during send still obeys the single attachment size limit', async () => {
  const context = { ref: 'fixture', api: { artifactAction: async (_ref, _id, action) => {
    assert.equal(action, 'share-related');
    return { ok: true, name: 'site.zip', data: '', size: 20 * 1024 * 1024 + 1 };
  } } };
  await assert.rejects(mate.outputPayload(context, [{ artifactId: 'site', name: 'index.html', kind: 'website' }], 'size-message'), /20 MB/);
});

test('missing or malformed website packaging cannot leave a standalone HTML selected', async () => {
  const result = { ok: true, artifacts: [{ artifactId: 'site', name: 'index.html', kind: 'website' }] };
  await assert.rejects(mate.conversationOutputs({ ref: 'fixture', api: { outputs: async () => result } }), /打包失败/);
  for (const bundled of [null, { ok: false }, { ok: true }]) {
    await assert.rejects(mate.conversationOutputs({ ref: 'fixture', api: {
      outputs: async () => result, artifactAction: async () => bundled } }), /打包失败/);
  }
});

test('depth errors in both sharing entry points follow every existing native locale', async t => {
  const locale = require('../main/native-locale.cjs');
  const catalogs = require('../core/locales/builtin-ui');
  const previous = locale.getLocale(); t.after(() => locale.setLocale(previous));
  const fixture = website(t, 13);
  const row = (await fixture.api.outputs()).artifacts.find(file => file.name === 'index.html');
  const key = '文件夹包含超过12层的文件，请将文件移近所选文件夹后重试';
  for (const id of ['zh-CN', 'en-US', 'ja-JP', 'ko-KR']) {
    locale.setLocale(id);
    const folder = await fixture.api.pickShareFolder();
    const related = await fixture.api.artifactAction('fixture', row.artifactId, 'share-related');
    assert.equal(folder.ok, false); assert.equal(related.ok, false);
    assert.equal(folder.reason, catalogs[id][key], id);
    assert.equal(related.reason, catalogs[id][key], id);
  }
  await assert.rejects(archive.createZipAsync(fixture.root), error => error.code === 'ZIP_DEPTH_LIMIT');
});
