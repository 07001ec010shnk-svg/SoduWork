'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const archive = require('../main/conversation/zip-archive');

function timestamp(bytes, offset) {
  const time = bytes.readUInt16LE(offset), date = bytes.readUInt16LE(offset + 2);
  return [1980 + (date >>> 9), (date >>> 5) & 15, date & 31,
    time >>> 11, (time >>> 5) & 63, (time & 31) * 2];
}

function assertTimestamps(bytes, expected) {
  assert.equal(bytes.readUInt32LE(0), 0x04034b50);
  const footer = bytes.length - 22;
  assert.equal(bytes.readUInt32LE(footer), 0x06054b50);
  const central = bytes.readUInt32LE(footer + 16);
  assert.equal(bytes.readUInt32LE(central), 0x02014b50);
  assert.deepEqual(timestamp(bytes, 10), expected, 'local header timestamp');
  assert.deepEqual(timestamp(bytes, central + 12), expected, 'central directory timestamp');
}

test('both ZIP entry encoders clamp DOS timestamps and retain representable local dates', async context => {
  const cases = [
    ['before 1980', new Date(1979, 11, 31, 23, 59, 58).getTime(), [1980, 1, 1, 0, 0, 0]],
    ['Unix epoch', 0, [1980, 1, 1, 0, 0, 0]],
    ['after 2107', new Date(2108, 0, 1).getTime(), [2107, 12, 31, 23, 59, 58]],
    ['first representable date', new Date(1980, 0, 1).getTime(), [1980, 1, 1, 0, 0, 0]],
    ['leap day with odd seconds', new Date(2024, 1, 29, 12, 34, 57).getTime(), [2024, 2, 29, 12, 34, 56]],
    ['last representable date', new Date(2107, 11, 31, 23, 59, 59).getTime(), [2107, 12, 31, 23, 59, 58]],
  ];
  for (const [name, mtimeMs, expected] of cases) {
    await context.test(name, async () => {
      const entries = [{ name: 'report.txt', bytes: Buffer.from('fixture'), st: { mtimeMs } }];
      assertTimestamps(archive.createEntriesSync(entries), expected);
      assertTimestamps(await archive.createEntries(entries), expected);
    });
  }
});

test('folder ZIPs clamp a real pre-1980 file timestamp in synchronous and worker paths', async context => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'soduwork-zip-timestamps-'));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const filename = path.join(root, 'report.txt');
  fs.writeFileSync(filename, 'fixture');
  const modified = new Date(1979, 0, 2, 3, 4, 6);
  fs.utimesSync(filename, modified, modified);
  assert.ok(fs.statSync(filename).mtimeMs < new Date(1980, 0, 1).getTime());
  assertTimestamps(archive.createZip(root).data, [1980, 1, 1, 0, 0, 0]);
  const result = await archive.createZipAsync(root);
  assertTimestamps(Buffer.from(result.base64, 'base64'), [1980, 1, 1, 0, 0, 0]);
});
