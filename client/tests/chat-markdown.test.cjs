'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
function setup() {
  let now = 1000, parseCount = 0, output = '';
  const timers = new Map(); let id = 0;
  const ctx = { _ref:'session-a', _liveTextKey:'1:0', esc:s => String(s).replaceAll('<','&lt;') };
  const sandbox = { Date:{now:() => now}, DOMPurify:{sanitize:html => html},
    setTimeout(fn,ms) { const key=++id; timers.set(key,{fn,at:now+ms}); return key; },
    clearTimeout(key) { timers.delete(key); } };
  sandbox.window = sandbox;
  const desktop = fs.readFileSync(path.join(__dirname, '../desk.html'), 'utf8');
  for (const [, file] of desktop.matchAll(/<script src="((?:vendor\/markdown\/marked\.umd|core\/markdown)\.js)"><\/script>/g)) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), sandbox);
  }
  const parse = sandbox.marked.parse;
  sandbox.marked = {...sandbox.marked, parse:(...args) => { parseCount++; return parse(...args); }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../components/panels/chat-markdown.js'),'utf8'), sandbox);
  let source = '';
  const markdown = sandbox.ChatMarkdown.create(ctx, () => { output=markdown._streamText(source); });
  return {ctx,markdown,timers,
    render(text) { source=text; output=markdown._streamText(source); return output; },
    advance(ms) { now+=ms; for (const [key,timer] of Array.from(timers)) if(timer.at<=now) { timers.delete(key); timer.fn(); } },
    get output() { return output; }, get parseCount() { return parseCount; } };
}
test('long streamed code retains its fence and all earlier output across 24k', () => {
  const view=setup(); const text='```js\n'+'const value = 1;\n'.repeat(2000);
  assert.match(view.render(text.slice(0,24000)), /<pre><code/);
  view.advance(400);
  const html=view.render(text);
  assert.match(html, /<pre><code/);
  assert.equal((html.match(/const value = 1;/g)||[]).length,2000);
});
test('newlines cannot bypass the parse budget and the last token flushes when input pauses', () => {
  const view=setup(); view.render('start');
  for(let i=0;i<100;i++) view.render('start\n'+('line\n'.repeat(i+1)));
  assert.equal(view.parseCount,1);
  assert.equal(view.timers.size,1);
  view.advance(400);
  assert.equal(view.parseCount,2);
  assert.equal((view.output.match(/line/g)||[]).length,100);
});
test('same block number in another session does not reuse throttled old content', () => {
  const view=setup(); view.render('previous session');
  view.ctx._ref='session-b';
  assert.match(view.render('new session'), /new session/);
  assert.doesNotMatch(view.output, /previous/);
});
test('closing a renderer cancels pending flush work', () => {
  const view=setup(); view.render('first'); view.render('first plus tail');
  assert.equal(view.timers.size,1); view.markdown.reset();
  assert.equal(view.timers.size,0); view.advance(400);
  assert.equal(view.parseCount,1);
});

test('numeric ranges retain literal tildes in completed, cached and streaming replies', () => {
  const view = setup();
  const source = '找 3~5 个用户，运行 7~14 天。';
  const expected = '<div class="nmd"><p>' + source + '</p>\n</div>';
  assert.equal(view.markdown.rich(source), expected);
  assert.equal(view.markdown.rich(source), expected);
  for (const prefix of ['找 3~5 个用户，运行 7', '找 3~5 个用户，运行 7~', source]) {
    view.advance(400);
    assert.equal(view.render(prefix), '<div class="nmd"><p>' + prefix + '</p>\n</div>');
  }
  view.markdown.reset();
  assert.equal(view.markdown.rich(source), expected);
});

test('literal tildes coexist with explicit strikethrough and other Markdown syntax', () => {
  const { markdown } = setup();
  for (const source of ['3~5\n7~14', '-3~-1 与 0.5~1.5', '~普通文字~', '说明 ~~~普通文字~~~']) {
    assert.equal(markdown.rich(source), '<div class="nmd"><p>' + source + '</p>\n</div>');
  }
  assert.match(markdown.rich('3~5 与 ~~旧建议~~，7~14 与 **新建议**'), /3~5 与 <del>旧建议<\/del>，7~14 与 <strong>新建议<\/strong>/);
  assert.match(markdown.rich(String.raw`3\~5 与 7\~14`), /3~5 与 7~14/);
  assert.match(markdown.rich('`3~5 与 7~14`'), /<code>3~5 与 7~14<\/code>/);
  assert.match(markdown.rich('~~~text\n3~5 与 7~14\n~~~'), /<pre><code class="language-text">3~5 与 7~14\n<\/code><\/pre>/);
  assert.match(markdown.rich('| 范围 |\n| --- |\n| 3~5 与 7~14 |'), /<table>[\s\S]*<td>3~5 与 7~14<\/td>/);
  assert.match(markdown.rich('[3~5](https://example.test/~user) 与 7~14'), /<a href="https:\/\/example.test\/~user">3~5<\/a> 与 7~14/);
});
