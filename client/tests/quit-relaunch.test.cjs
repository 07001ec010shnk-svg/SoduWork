'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const quitRelaunch = require('../main/quit-relaunch.cjs');

function fakeApp() {
  const app = { relaunches: 0 };
  app.relaunch = () => { app.relaunches += 1; };
  return app;
}

test('a launch while the app is running is not deferred', () => {
  const app = fakeApp();
  const state = quitRelaunch.create(app);
  assert.equal(state.deferLaunch(), false);
  assert.equal(app.relaunches, 0);
});

test('a launch after a confirmed quit schedules exactly one relaunch', () => {
  const app = fakeApp();
  const state = quitRelaunch.create(app);
  state.markQuitting();
  assert.equal(state.deferLaunch(), true);
  assert.equal(state.deferLaunch(), true);
  assert.equal(app.relaunches, 1);
});

test('main.js marks user quits and checks them before reopening a window', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const quit = source.slice(source.indexOf('function _quitSoduWork('));
  assert.match(quit.slice(0, quit.indexOf('\n}')), /_quitRelaunch\.markQuitting\(\)/);
  const handler = source.slice(source.indexOf("app.on('second-instance'"));
  const body = handler.slice(0, handler.indexOf('openDeskWindow'));
  assert.ok(body.indexOf('_quitRelaunch.deferLaunch()') >= 0, 'second-instance must defer during quit');
  assert.ok(body.indexOf('_quitRelaunch.deferLaunch()') < body.indexOf('_fileOpen.enqueue'),
    'files from a launch during quit must not be queued into the exiting process');
});
