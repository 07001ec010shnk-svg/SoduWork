'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createRunnerRuntimeAdapter, getRunnerSessionDetails, _uuidV5 } = require('../main/agent-runtime/runner-adapter');
const catalog = require('../main/agent-runtime/runtime-catalog');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fixture(t, fixes, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'runner-plugin-gate-'));
  const file = path.join(root, 'soduwork-plugin-draft.json'), calls = [], observed = [];
  let round = 1, exposedInvalidCompletion = false;
  const body = { template: 'method', key: 'draft-check', name: 'Draft check', version: '1.0.1', description: 'Fixture', instructions: 'Summarize input' };
  const write = valid => fs.writeFileSync(file, valid ? JSON.stringify(body) : '{"name":"invalid "quote""}');
  const runtime = { async rpc(op, value) {
    calls.push({ op, value });
    if (op === 'job.dispatch') {
      fs.writeFileSync(path.join(root, 'requested-report.txt'), 'Requested report');
      write(Boolean(options.validFirst));
      return { ok: true, data: { ok: true, ref: _uuidV5('9af442c8-cd1d-4b91-a668-22da2b2635bf', value.dispatchId) } };
    }
    if (op === 'job.comment') { round++; write(options.goalScenario && round === 2 ? false : fixes); if (options.switchOwnerOnRepair) handle.deliveryOwner = 'changed-owner'; return { ok: true, data: { ok: true, state: 'running' } }; }
    if (op === 'job.session') return { ok: true, data: { ok: true, status: 'done', round, seq: round + 1, items: [
      ...(round === 1 ? [{ seq: 1, kind: 'user', text: 'Make a plugin' }] : []),
      { seq: round + 1, kind: 'say', text: 'Draft produced' },
    ] } };
    return { ok: true, data: { ok: true } };
  } };
  const model = { ok: true, model: { id: 'fixture', key: 'fixture', input: ['text'] } }, app = { getPath: () => root };
  const adapter = createRunnerRuntimeAdapter({ app, runnerRuntime: runtime, runtimeSpec: { ...catalog.runtime('opencode'), goalMode: 'native' },
    modelConfig: { fetchModel: async () => model, fetchSelection: async () => model } });
  const session = await adapter.createSession({ identity: {}, native: { runner: { openOptions: { app, cwd: root, ref: 'chat-plugin-gate', intent: options.goalScenario ? 'goal' : '' } } } });
  const handle = getRunnerSessionDetails(session).handle;
  handle.on((_event, snapshot) => {
    if (snapshot.status === 'idle' && fs.existsSync(file)) {
      try { JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { exposedInvalidCompletion = true; }
    }
  });
  t.after(async () => { await session.dispose(); await delay(20); fs.rmSync(root, { recursive: true, force: true }); });
  const turn = handle.submit('Make a plugin', [], { afterTurn: (state, result) => observed.push({ state, result }) });
  return { turn, handle, calls, observed, file, invalidCompletion: () => exposedInvalidCompletion };
}
test('Runner repairs the final draft before logical success and without a duplicate user message', async t => {
  const f = await fixture(t, true), result = await f.turn.completion;
  assert.equal(result.ok, true);
  assert.equal(result.snapshot.status, 'idle');
  assert.equal(result.snapshot.execution.running, false);
  assert.deepEqual(result.snapshot.fileChanges.files.map(row => [row.path,row.action]),
    [['requested-report.txt','created'],['soduwork-plugin-draft.json','created']]);
  assert.equal(result.snapshot.messages.length, f.handle.snapshot().messages.length);
  assert.equal(f.invalidCompletion(), false, 'invalid drafts must not publish completed presence before validation');
  assert.equal(f.calls.filter(row => row.op === 'job.comment').length, 1);
  assert.equal(f.calls.find(row => row.op === 'job.comment').value.recordUser, false);
  assert.equal(f.handle.snapshot().messages.filter(row => row.role === 'user').length, 1);
  assert.equal(f.observed.length, 1);
  assert.equal(f.observed[0].state, 'completed');
  assert.equal(JSON.parse(fs.readFileSync(f.file, 'utf8')).key, 'draft-check');
  assert.equal(f.handle.outputSnapshot().pluginValidation[0].state, 'ready');
});
test('Runner stops after two invalid repairs and reports logical failure', async t => {
  const f = await fixture(t, false), result = await f.turn.completion;
  assert.equal(result.ok, false);
  assert.equal(result.failureKind, 'plugin-validation');
  assert.equal(f.calls.filter(row => row.op === 'job.comment').length, 2);
  assert.equal(f.observed.length, 1);
  assert.equal(f.observed[0].state, 'failed');
  assert.equal(f.handle.outputSnapshot().pluginValidation[0].state, 'invalid');
});
test('Runner identity change during draft repair ends validation as cancelled', async t => {
  const f = await fixture(t, true, { switchOwnerOnRepair: true }), result = await f.turn.completion;
  assert.equal(result.cancelled, true);
  assert.equal(f.handle.snapshot().status, 'aborted');
  assert.equal(f.observed[0].state, 'stopped');
});
test('Runner resumed native goal validates the generated draft before completing', async t => {
  const f = await fixture(t, true, { validFirst: true, goalScenario: true });
  assert.equal((await f.turn.completion).ok, true);
  assert.equal(f.handle.pauseGoal().ok, true);
  await delay(10);
  const continuation = f.handle.continueGoal();
  assert.equal(continuation.ok, true);
  const result = await continuation.completion;
  assert.equal(result.ok, true);
  assert.equal(f.calls.filter(row => row.op === 'job.comment').length, 2);
  assert.equal(JSON.parse(fs.readFileSync(f.file, 'utf8')).key, 'draft-check');
  assert.equal(f.handle.outputSnapshot().pluginValidation[0].state, 'ready');
});

test('a goal paused before dispatch resumes the original request with one user message', async t => {
  const outputs = require('../main/pi-session/outputs');
  const scan = outputs.scanAsync;
  let enter, release;
  const entered = new Promise(resolve => { enter = resolve; });
  const pending = new Promise(resolve => { release = resolve; });
  t.mock.method(outputs, 'scanAsync', async (...args) => {
    enter();
    await pending;
    return scan(...args);
  });
  const f = await fixture(t, true, { goalScenario:true, validFirst:true });
  await entered;
  assert.equal(f.handle.pauseGoal().ok, true);
  release();
  assert.equal((await f.turn.completion).paused, true);
  assert.equal(f.calls.some(row => row.op.startsWith('job.')), false);
  const continued = f.handle.continueGoal();
  assert.equal(continued.accepted, true);
  assert.equal((await continued.completion).ok, true);
  assert.equal(f.calls.filter(row => row.op === 'job.dispatch').length, 1);
  assert.equal(f.calls.find(row => row.op === 'job.dispatch').value.ctx.conversationUserText, 'Make a plugin');
  assert.equal(f.calls.some(row => row.op === 'job.comment'), false);
  assert.equal(f.handle.snapshot().messages.filter(row => row.role === 'user').length, 1);
});

test('a paused goal cannot resume while its original draft validation is still active', async t => {
  const cache = require('../main/app-resource-cache.cjs'), request = cache.request;
  let entered, release, waiting = true;
  const checking = new Promise(resolve => { entered = resolve; });
  const pending = new Promise(resolve => { release = resolve; });
  t.mock.method(cache, 'request', async input => {
    if (input.action === 'draft-check' && waiting) {
      waiting = false; entered(); await pending;
    }
    return request(input);
  });
  const f = await fixture(t, true, { goalScenario: true });
  await checking;
  assert.equal(f.handle.pauseGoal().ok, true);
  await delay(10);
  assert.ok(f.calls.some(row => row.op === 'job.stop'));
  assert.ok(f.handle.snapshot().execution.turnQueue.activeQueueId);
  const continued = f.handle.continueGoal();
  release();
  const result = await f.turn.completion;
  if (continued.completion) await continued.completion;
  assert.equal(continued.ok, false, 'resume must not revive a paused validation callback');
  assert.equal(result.cancelled, true);
  assert.equal(f.calls.filter(row => row.op === 'job.comment').length, 0);
});
