'use strict';
// Run with Electron and a dedicated backend test Python. The dialog selection
// and model summary are fixtures; ZIP workers, renderer sends, Hub storage,
// recipient downloads and Chromium execution are real and isolated.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const net = require('node:net'), crypto = require('node:crypto'), zlib = require('node:zlib');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');
const artifacts = require('../main/conversation/artifact-runtime');
const outputs = require('../main/pi-session/outputs');
const archive = require('../main/conversation/zip-archive');
const { createPickFolderHandler } = require('../main/pi-session/mate-message-ipc');
const root = path.resolve(__dirname, '../..');
const outputIndex = process.argv.indexOf('--output');
const out = path.resolve(outputIndex >= 0 ? process.argv[outputIndex + 1] : path.join(os.tmpdir(), 'soduwork-sharing-evidence-' + process.pid));
fs.mkdirSync(out, { recursive: true });
const report = { pid: process.pid, startedAt: new Date().toISOString(), status: 'running', cases: [],
  platform: process.platform, electron: process.versions.electron, limitations: ['Synthetic dialog selection and summary', 'No native Windows/macOS acceptance'] };
function save() {
  const temporary = path.join(out, '.result.tmp');
  fs.writeFileSync(temporary, JSON.stringify(report, null, 2) + '\n');
  fs.renameSync(temporary, path.join(out, 'result.json'));
}
save();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'soduwork-sharing-review-'));
let backend, sender, recipient, selected, folderOnly;
app.setPath('userData', path.join(scratch, 'profile'));
// Keep asynchronous fixture cleanup alive after the final preview closes.
app.on('window-all-closed', () => {});
app.on('before-quit', event => event.preventDefault());
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function unpack(bytes) {
  const files = new Map(); let at = 0;
  while (bytes.readUInt32LE(at) === 0x04034b50) {
    const method = bytes.readUInt16LE(at + 8), size = bytes.readUInt32LE(at + 18);
    const length = bytes.readUInt16LE(at + 26), extra = bytes.readUInt16LE(at + 28);
    const name = bytes.subarray(at + 30, at + 30 + length).toString('utf8');
    assert.ok(method === 0 || method === 8);
    at += 30 + length + extra;
    const compressed = bytes.subarray(at, at + size); at += size;
    assert.equal(files.has(name), false);
    files.set(name, method === 8 ? zlib.inflateRawSync(compressed) : compressed);
  }
  assert.equal(bytes.readUInt32LE(at), 0x02014b50);
  return files;
}
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function main() {
  await app.whenReady();
  const port = await freePort(), base = 'http://127.0.0.1:' + port;
  const python = process.env.SODUWORK_TEST_PYTHON || path.join(root, 'hub-backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  backend = spawn(python, ['-B', 'e2e_mate_messages.py', String(port)], { cwd: path.join(root, 'hub-backend'),
    env: { ...process.env, SODUWORK_ENV: 'development', CLUSTER_REDIS_URL: '',
      AUTH_SECRET: crypto.randomBytes(32).toString('hex'), ADMIN_PASS: crypto.randomBytes(24).toString('hex'),
      PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'] });
  report.backendPid = backend.pid; save();
  backend.stdout.on('data', data => fs.appendFileSync(path.join(out, 'backend.log'), data));
  backend.stderr.on('data', data => fs.appendFileSync(path.join(out, 'backend.log'), data));
  let startupError;
  backend.on('error', error => { startupError = error; });
  for (let i = 0; i < 150; i++) {
    if (startupError) throw startupError;
    if (backend.exitCode !== null) throw Error('The isolated Hub exited during startup');
    try { if ((await fetch(base + '/test/health', { signal: AbortSignal.timeout(1000) })).ok) break; }
    catch (_) { /* The isolated listener has not started yet. */ }
    if (i === 149) throw Error('The isolated Hub did not become ready');
    await wait(100);
  }
  const preload = path.join(scratch, 'preload.cjs');
  fs.writeFileSync(preload, `const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('sharingReviewBridge',{
outputs:()=>ipcRenderer.invoke('sharing-review-outputs'),
action:(ref,id,action)=>ipcRenderer.invoke('sharing-review-action',ref,id,action),
pick:()=>ipcRenderer.invoke('sharing-review-pick')});`);
  sender = new BrowserWindow({ show: false, width: 1360, height: 900,
    webPreferences: { preload, sandbox: true, contextIsolation: true, nodeIntegration: false } });
  const session = () => ({ cwd: selected, outputSnapshot: () => outputs.scan(selected, 0, 0) });
  const verifySender = event => assert.equal(event.sender, sender.webContents);
  ipcMain.handle('sharing-review-outputs', event => {
    verifySender(event);
    return { ok: true, artifacts: folderOnly ? [] : artifacts.decorate('chat-a', { items: session().outputSnapshot().delivery.items }) };
  });
  ipcMain.handle('sharing-review-action', (event, ref, id, action) => {
    verifySender(event); assert.equal(ref, 'chat-a');
    return artifacts.action(session(), ref, id, action, {});
  });
  const pick = createPickFolderHandler({ BrowserWindow, shareArchive: archive,
    dialog: { showOpenDialog: async () => ({ filePaths: [selected] }) } });
  ipcMain.handle('sharing-review-pick', event => { verifySender(event); return pick(event); });
  await sender.loadURL(pathToFileURL(path.join(root, 'client/tests/mate-messages.html')).href + '?backend=' + encodeURIComponent(base));
  const js = source => sender.webContents.executeJavaScript(source);
  await js(`api.outputs=()=>sharingReviewBridge.outputs();
api.artifactAction=(ref,id,action)=>sharingReviewBridge.action(ref,id,action);
api.pickShareFolder=()=>sharingReviewBridge.pick();outputMode='none';`);
  async function until(source) {
    for (let i = 0; i < 150; i++) { if (await js(source)) return; await wait(50); }
    throw Error('Sharing UI did not reach the expected state: ' + source);
  }
  const events = async () => {
    const response = await fetch(base + '/api/v1/team/messages?folder=received', { headers: { 'X-Test-User': 'recipient' } });
    assert.equal(response.status, 200);
    const inbox = await response.json(); assert.equal(inbox.ok, true);
    return inbox.items;
  };
  for (const entry of ['conversation', 'folder']) {
    for (const scenario of [{ name: 'depth-12', depth: 12 }, { name: 'depth-13-required', depth: 13, failure: true },
      { name: 'depth-13-empty', depth: 13, empty: true }]) {
      folderOnly = entry === 'folder';
      selected = path.join(scratch, entry + '-' + scenario.name);
      const directory = path.join(selected, ...Array.from({ length: scenario.depth }, (_, i) => 'level-' + i));
      fs.mkdirSync(directory, { recursive: true });
      const script = scenario.empty ? 'app.js' : path.relative(selected, path.join(directory, 'required.js')).replaceAll('\\', '/');
      fs.writeFileSync(path.join(selected, 'index.html'), '<script src="' + script + '"></script>');
      fs.writeFileSync(path.join(selected, script), 'window.fixtureLoaded=true;');
      const before = new Set((await events()).map(event => event.messageId || event.id));
      await js(`document.getElementById('status').textContent=''; input.value='@林';input.selectionStart=input.value.length;input.dispatchEvent(new Event('input',{bubbles:true}));`);
      await until(`Boolean(document.querySelector('.nmate-menu .nplugin-option'))`);
      await js(`document.querySelector('.nmate-menu .nplugin-option').click()`);
      if (entry === 'folder') {
        await until(`!document.querySelector('.nmate-card-send').disabled`);
        await js(`Array.from(document.querySelectorAll('.nmate-card-tools button')).find(n=>n.textContent==='添加文件夹').click()`);
      }
      if (scenario.failure) {
        await until(`document.querySelector('.nmate-results').textContent.includes('nested more than 12 levels')||document.getElementById('status').textContent.includes('nested more than 12 levels')`);
        assert.equal(await js(`document.querySelectorAll('.nmate-results input:checked').length`), 0);
        if (entry === 'conversation') assert.equal(await js(`document.querySelector('.nmate-card-send').disabled`), true);
        await js(`document.querySelector('.nmate-card-send').click()`);
        if (entry === 'folder') await until(`document.querySelector('.nmate-card').hidden`);
        const outgoing = (await events()).filter(event => !before.has(event.messageId || event.id));
        assert.ok(outgoing.every(event => !event.attachments?.length));
        report.cases.push({ entry, scenario: scenario.name, errorShown: true, attachments: 0, status: 'passed' });
      } else {
        await until(`!document.querySelector('.nmate-card-send').disabled&&document.querySelectorAll('.nmate-results input:checked').length===1`);
        await js(`document.querySelector('.nmate-card-send').click()`);
        await until(`document.querySelector('.nmate-card').hidden`);
        const outgoing = (await events()).filter(event => !before.has(event.messageId || event.id));
        assert.equal(outgoing.length, 1); assert.equal(outgoing[0].attachments.length, 1);
        const id = outgoing[0].attachments[0].id;
        const metadata = await fetch(base + '/api/v1/team/message-files/' + id, { headers: { 'X-Test-User': 'recipient' } });
        assert.equal(metadata.status, 200);
        const file = await metadata.json();
        const denied = await fetch(base + '/api/v1/team/message-files/' + id, { headers: { 'X-Test-User': 'other' } });
        assert.equal(denied.status, 404);
        const response = await fetch(base + file.url);
        assert.equal(response.status, 200);
        const bytes = Buffer.from(await response.arrayBuffer()), extracted = unpack(bytes);
        assert.deepEqual([...extracted.keys()].sort(), ['index.html', script].sort());
        const received = path.join(scratch, 'received-' + entry + '-' + scenario.name);
        for (const [name, value] of extracted) {
          assert.deepEqual(value, fs.readFileSync(path.join(selected, name)));
          const target = path.resolve(received, name);
          assert.ok(target.startsWith(received + path.sep));
          fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, value);
        }
        recipient = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
        await recipient.loadFile(path.join(received, 'index.html'));
        assert.equal(await recipient.webContents.executeJavaScript('window.fixtureLoaded===true'), true);
        recipient.destroy(); recipient = null;
        report.cases.push({ entry, scenario: scenario.name, attachments: 1, zipSha256: hash(bytes),
          files: [...extracted.keys()], exactBytes: true, recipientPageExecuted: true, unauthorizedReadDenied: true, status: 'passed' });
      }
      save();
      await js(`document.querySelector('.nmate-card-close')?.click()`);
    }
  }
  report.status = 'passed';
}
main().catch(error => { report.status = 'failed'; report.error = String(error.stack || error); })
  .finally(async () => {
    recipient?.destroy(); sender?.destroy();
    for (const channel of ['outputs', 'action', 'pick']) ipcMain.removeHandler('sharing-review-' + channel);
    if (backend && backend.exitCode === null) {
      backend.kill();
      await Promise.race([new Promise(resolve => backend.once('exit', resolve)), wait(3000)]);
      if (backend.exitCode === null) backend.kill('SIGKILL');
    }
    fs.rmSync(scratch, { recursive: true, force: true });
    report.completedAt = new Date().toISOString(); save();
    console.log(JSON.stringify(report, null, 2)); app.exit(report.status === 'passed' ? 0 : 1);
  });
