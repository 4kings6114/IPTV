'use strict';
// Optional dev-only DOM regression test; no browser network calls or server.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(path.resolve('.cache/ui-test/node_modules/jsdom'));
const payload = execFileSync('python', ['-B', '-c', `
import json
from iptv_builder.dashboard import render_dashboard
rows = [{'identity':f'id:channel{i}.us','name':f'Channel {i}', 'url':f'https://streams.test/{i}.m3u8','source':'https://source.test/playlist','country':'us','language':'en','group':'News','resolution':1080,'bitrate':4000000,'status':'healthy','cached':False,'epg':'covered'} for i in range(205)]
rows[1]['name'] = '</script><img src=x onerror="window.injected=true">'
variants = [{'identity':'id:channel0.us','name':'Channel 0','country':'us','language':'en','resolution':height,'bitrate':rate,'status':'healthy','stability':10,'m3u':f'#EXTINF:-1 tvg-id="Channel0.us",Channel 0\\nhttps://streams.test/{height}.m3u8\\n'} for height,rate in [(1080,4000000),(360,800000)]]
variants.append({'identity':'id:unknown.us','name':'Unknown','country':'us','language':'en','resolution':480,'bitrate':0,'status':'healthy','stability':10,'m3u':'#EXTINF:-1 tvg-id="Unknown.us",Unknown\\nhttps://streams.test/unknown.m3u8\\n'})
report = {'channels':205,'generated_at':'2026-11-09T00:00:00Z','last_run':{'generated_at':'2026-11-10T00:00:00Z','status':'unchanged'},'channel_catalog':rows,'editor':{'channels':variants,'favorites':[],'epg_urls':[],'profiles':{'slow':{'languages':[],'countries':[],'max_resolution':720,'max_bitrate':1000000}}},'personalization':{'profiles':{'slow':{'file':'profile-slow.m3u','channels':1}}}}
print(render_dashboard(report))
`], {encoding: 'utf8', maxBuffer: 10 * 1024 * 1024});
const errors = [], downloads = [];
const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error));
const dom = new JSDOM(payload, {runScripts: 'dangerously', url: 'https://example.test/IPTV/', virtualConsole: console,
  beforeParse(window) {
    window.URL.createObjectURL = () => 'blob:https://example.test/local';
    window.URL.revokeObjectURL = () => {};
    window.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
  }});
const window = dom.window, document = window.document, $ = id => document.getElementById(id);
function change(id, value) { $(id).value = value; $(id).dispatchEvent(new window.Event('change')); }
function tick() { return new Promise(resolve => setTimeout(resolve, 0)); }
(async () => {
  assert.equal(errors.length, 0, errors.map(error => error.stack).join('\n'));
  assert.equal(window.injected, undefined);
  assert.equal(document.querySelectorAll('img').length, 0, 'metadata is never turned into executable markup');
  assert.equal(document.querySelectorAll('#channels tr').length, 100);
  $('next').click(); assert.equal($('page').textContent, '2 / 3');
  $('search').value = 'Channel 120'; $('search').dispatchEvent(new window.Event('input'));
  assert.equal(document.querySelectorAll('#channels tr').length, 1);
  assert.match($('count').textContent, /1 از 205/);
  $('search').value = ''; $('search').dispatchEvent(new window.Event('input'));
  change('country', 'us'); assert.equal(document.querySelectorAll('#channels tr').length, 100);
  assert.ok(document.querySelector('a[href="profile-slow.m3u"]'), 'managed profile download link works');
  assert.notEqual($('published').textContent, $('last-run').textContent);
  assert.equal(window.iptvEditor.personalSelection().selected.length, 2);
  change('edit-profile', 'slow');
  let list = window.iptvEditor.personalPlaylist();
  assert.match(list, /360\.m3u8/); assert.doesNotMatch(list, /1080\.m3u8|unknown\.m3u8/);
  assert.equal(window.iptvEditor.personalSelection().selected.length, 1);
  $('edit-unknown-bitrate').checked = true; $('edit-unknown-bitrate').dispatchEvent(new window.Event('change'));
  assert.match(window.iptvEditor.personalPlaylist(), /unknown\.m3u8/);
  document.querySelector('#channels button[aria-pressed]').click();
  assert.equal(window.iptvEditor.favoritesSet.size, 1);
  $('edit-favorites-only').checked = true; $('edit-favorites-only').dispatchEvent(new window.Event('change'));
  assert.equal(window.iptvEditor.personalSelection().selected.length, 1);
  $('download-personal').click(); $('download-favorites').click(); $('download-profile').click();
  assert.deepEqual(downloads, ['personal.m3u', 'favorites.json', 'profiles.json']);
  $('edit-name').value = '../../bad'; $('download-profile').click();
  assert.equal(downloads.length, 3); assert.ok($('editor-error').textContent);
  const file = {size: 32, text: async () => JSON.stringify({channels: ['Channel0.gb@HD', 'Local.us@East']})};
  Object.defineProperty($('import-favorites'), 'files', {configurable: true, value: [file]});
  $('import-favorites').dispatchEvent(new window.Event('change')); await tick();
  assert.ok(window.iptvEditor.favoritesSet.has('id:channel0.uk'));
  assert.ok(window.iptvEditor.favoritesSet.has('id:local.us@east'));
  const before = window.iptvEditor.favoritesSet.size;
  Object.defineProperty($('import-favorites'), 'files', {configurable: true, value: [{size: 1048577}]});
  $('import-favorites').dispatchEvent(new window.Event('change')); await tick();
  assert.equal(window.iptvEditor.favoritesSet.size, before);
  assert.ok($('editor-error').textContent);
  const script = payload.split('<script>')[1].split('</script>')[0];
  const hash = createHash('sha256').update(script).digest('base64');
  assert.ok(payload.includes(`script-src 'sha256-${hash}'`));
  assert.equal(errors.length, 0, errors.map(error => error.stack).join('\n'));
  process.stdout.write('Dashboard DOM, filtering, pagination, local favorites/profile/M3U downloads, cap semantics, import limits, injection and CSP hash: PASS\n');
  window.close();
})().catch(error => { process.stderr.write(error.stack + '\n'); window.close(); process.exitCode = 1; });
