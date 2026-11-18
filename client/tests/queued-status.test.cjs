'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
global.window = global;
const status = require('../components/panels/panels-chat-status');
const workspace = require('../components/panels/chat-workspace-info');

test('queued snapshots preserve reported status before stale thinking and tool activity', () => {
  const snapshot = {
    status: 'queued', statusText: '排队中',
    messages: [{ role: 'user' }, { role: 'assistant', blocks: [{ kind: 'thinking', text: 'old thought' }] }],
    tools: [{ name: 'bash', state: 'running', args: { command: 'npm test' } }],
    execution: { running: false, longRunning: true, stalled: true, quality: { phase: 'implementing' } },
  };
  for (const pending of ['', 'new request']) {
    assert.equal(status.work(snapshot, pending), '排队中');
    assert.equal(workspace.status(snapshot, pending), '排队中');
  }
  snapshot.statusText = '等待执行设备连接';
  assert.equal(status.work(snapshot), snapshot.statusText);
  assert.equal(workspace.status(snapshot), snapshot.statusText);
  delete snapshot.statusText;
  assert.equal(status.work(snapshot), '排队中');
  assert.equal(workspace.status(snapshot), '排队中');
});

test('metrics follow queue, running and cancellation transitions', () => {
  const current = { snap: { status: 'queued', statusText: '排队中', execution: { running: false } } };
  const view = status.createThinking(() => current);
  assert.equal(status.runtimeBusy(current.snap), true, 'queued work remains cancellable');
  assert.match(view.metricsHtml(true), /data-thinking-label>排队中</);
  current.snap.status = 'streaming';
  current.snap.execution.running = true;
  assert.match(view.metricsHtml(true), /data-thinking-label>处理中</);
  assert.equal(status.work(current.snap), '正在理解你的要求…');
  current.snap.status = 'idle';
  current.snap.execution.running = false;
  assert.equal(status.runtimeBusy(current.snap), false);
  assert.match(view.metricsHtml(false), /data-thinking-label>已处理</);
});

test('waiting for user input retains its status and does not become active queued work', () => {
  const snapshot = { status: 'waiting-user', statusText: '等待输入',
    execution: { running: false, queued: 1, turnQueue: { activeQueueId: 'waiting' } } };
  assert.equal(status.work(snapshot), '等待输入');
  assert.equal(status.runtimeBusy(snapshot), false);
  assert.match(status.createThinking(() => ({ snap: snapshot })).statusHtml(false), /等待输入/);
});
