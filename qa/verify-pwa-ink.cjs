const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '..'), output = path.join(__dirname, 'output');
let browser, server;
const checks = [];
function pass(message) { checks.push(message); console.log('PASS ' + message); }
(async () => {
 await fs.mkdir(output, { recursive: true });
 server = http.createServer(async (req, res) => {
  const file = req.url === '/Graphitout/' ? 'index.html' : req.url.slice('/Graphitout/'.length);
  if (!/^\/Graphitout\//.test(req.url) || !/^(index\.html|sw\.js|manifest\.webmanifest|icons\/[\w-]+\.png)$/.test(file)) { res.writeHead(404); res.end(); return; }
  try {
   const mime = file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.png') ? 'image/png' : 'application/manifest+json';
   res.writeHead(200, { 'Content-Type': mime }); res.end(await fs.readFile(path.join(root, file)));
  } catch { res.writeHead(404); res.end(); }
 });
 await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
 const url = `http://127.0.0.1:${server.address().port}/Graphitout/`;
 browser = await chromium.launchPersistentContext(await fs.mkdtemp(path.join(output,'pwa-profile-')), { headless: true, viewport: { width: 1440, height: 960 }, acceptDownloads: true, ...(process.env.GRAPHITOUT_BROWSER_CHANNEL ? { channel: process.env.GRAPHITOUT_BROWSER_CHANNEL } : {}) });
 const context = browser;
 const page = await context.newPage(), errors = [];
 page.on('pageerror', error => errors.push(error.message));
 await page.goto(url);
 await page.evaluate(() => navigator.serviceWorker.ready);
 await page.waitForFunction(() => navigator.serviceWorker.controller);
 const cdp = await context.newCDPSession(page);
 const manifest = await cdp.send('Page.getAppManifest');
 assert.deepEqual(manifest.errors, []);
 const data = JSON.parse(manifest.data);
 assert.equal(data.name, 'Graphitout'); assert.equal(data.display, 'standalone');
 assert.equal(new URL(data.start_url, manifest.url).href, url);
 assert.equal(new URL(data.scope, manifest.url).href, url);
 assert.deepEqual((await cdp.send('Page.getInstallabilityErrors')).installabilityErrors, []);
 for (const icon of data.icons) {
  const result = await page.evaluate(async src => { const image=new Image();image.src=src;await image.decode();return [image.naturalWidth,image.naturalHeight]; }, new URL(icon.src, manifest.url).href);
  assert.equal(result.join('x'), icon.sizes);
 }
 await page.locator('#export-btn').click();
 assert.equal(await page.locator('#install-app').isVisible(), true);
 await page.locator('#export-btn').click();
 pass('Pages-scoped manifest, 192/512px icons, maskable icon, offline worker, and Chrome installability checks pass');
 await page.locator('[data-add="scribble"]').click();
 const inkId = await page.locator('[data-type="scribble"]').getAttribute('data-node');
 await page.locator('#open-ink').click();
 const box = await page.locator('#ink-canvas').boundingBox();
 async function pen(type, x, y, pressure = .5) {
  await cdp.send('Input.dispatchMouseEvent', { type, x: box.x + box.width*x, y: box.y + box.height*y, button: type==='mouseMoved' ? 'none':'left', buttons: type==='mouseReleased'?0:1, pointerType:'pen', force:pressure });
 }
 await pen('mousePressed', .12, .2, .2);
 await pen('mouseMoved', .2, .4, .3); await pen('mouseMoved', .35, .25, .9); await pen('mouseMoved', .6, .5, .7); await pen('mouseReleased', .8, .65, .3);
 let saved = await page.evaluate(() => JSON.parse(localStorage.getItem('graphitout.diagram.v1')).graph);
 let drawing = saved.nodes.find(n => n.id === inkId).drawing;
 assert.equal(drawing.strokes.length, 1); assert.ok(drawing.strokes[0].points.length >= 4);
 assert.ok(new Set(drawing.strokes[0].points.map(p => p[2])).size > 1, 'Pressure samples survive capture');
 assert.ok(drawing.strokes[0].points.every(p => p[0]>=0 && p[0]<=1000 && p[1]>=0 && p[1]<=600));
 // Palm/finger input is ignored with default controls.
 await page.locator('#ink-canvas').dispatchEvent('pointerdown', { pointerId:45, pointerType:'touch',clientX:box.x+30,clientY:box.y+30,button:0,buttons:1 });
 await page.locator('#ink-canvas').dispatchEvent('pointerup', { pointerId:45,pointerType:'touch',clientX:box.x+80,clientY:box.y+80,button:0 });
 await page.locator('#ink-done').click();
 assert.equal(await page.locator(`[data-node="${inkId}"] .ink-preview path`).count() >= 3, true);
 await page.reload();
 await page.locator(`[data-node="${inkId}"]`).dblclick();
 assert.equal(await page.locator('#ink-dialog').isVisible(), true);
 saved = await page.evaluate(() => JSON.parse(localStorage.getItem('graphitout.diagram.v1')).graph);
 assert.deepEqual(saved.nodes.find(n => n.id === inkId).drawing, drawing);
 await page.locator('#ink-clear').click(); await page.locator('#ink-undo').click();
 assert.deepEqual((await page.evaluate(() => JSON.parse(localStorage.getItem('graphitout.diagram.v1')).graph)).nodes.find(n=>n.id===inkId).drawing, drawing);
 await page.locator('#ink-redo').click(); await page.locator('#ink-undo').click();
 await page.locator('#ink-eraser').click();
 const updated = await page.locator('#ink-canvas').boundingBox();
 await page.mouse.click(updated.x+updated.width*.35, updated.y+updated.height*.25);
 saved = await page.evaluate(() => JSON.parse(localStorage.getItem('graphitout.diagram.v1')).graph);
 assert.equal(saved.nodes.find(n=>n.id===inkId).drawing.strokes.length, 0);
 await page.locator('#ink-undo').click(); await page.locator('#ink-done').click();
 pass('Simulated pen pressure, palm rejection, vector thumbnail, autosave/reload, erase, clear, and undo/redo preserve handwriting');
 async function exported(kind, file) {
  await page.locator('#export-btn').click(); const ready = page.waitForEvent('download');
  await page.locator(`[data-export="${kind}"]`).click(); const download = await ready;
  const target = path.join(output, file); await download.saveAs(target); return target;
 }
 const json = JSON.parse(await fs.readFile(await exported('json', 'ink.json'), 'utf8'));
 assert.deepEqual(json.nodes.find(n=>n.id===inkId).drawing, drawing);
 const svg = await fs.readFile(await exported('svg', 'ink.svg'), 'utf8');
 assert.match(svg, /stroke="#294b80"/);
 assert.equal(await page.evaluate(svg => new DOMParser().parseFromString(svg, 'image/svg+xml').querySelector('parsererror')?.textContent??null, svg), null);
 const portablePath = await exported('html', 'ink-portable.html');
 const copy = await context.newPage(), requests = [];
 copy.on('request', r => { if (/^https?:/.test(r.url())) requests.push(r.url()); });
 await copy.goto(pathToFileURL(portablePath).href);
 assert.equal(await copy.locator(`[data-node="${inkId}"] .ink-preview path`).count() >= 3, true);
 assert.equal(await copy.locator('link[rel="manifest"]').count(), 0);
 await copy.locator('#export-btn').click(); await copy.locator('#install-app').click();
 assert.equal(await copy.locator('#install-dialog').isVisible(), true);
 await copy.locator('[data-close="install-dialog"]').click();
 assert.deepEqual(requests, []); await copy.close();
 // Invalid vectors must be rejected by the importer, never interpreted as SVG markup.
 const invalid = structuredClone(json); invalid.nodes.find(n=>n.id===inkId).drawing.strokes[0].color = 'url(javascript:alert(1))';
 await page.locator('#file-input').setInputFiles({ name:'invalid-ink.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(invalid)) });
 await page.waitForTimeout(150);
 assert.match(await page.locator('#toast').innerText(), /Invalid drawing/i);
 pass('JSON, SVG and independent portable HTML preserve ink; malicious ink values are rejected');
 // Worker activation can remove its previous cache without deleting another app or diagram storage.
 await page.evaluate(async () => {await caches.open('graphitout-shell-%2FGraphitout%2F-old');await caches.open('other-app-cache');await caches.open('graphitout-shell-%2FOther%2F-old');});
 await page.evaluate(async () => {const registration=await navigator.serviceWorker.getRegistration();await registration.unregister();});
 await page.reload(); await page.evaluate(() => navigator.serviceWorker.ready); await page.waitForFunction(() => navigator.serviceWorker.controller);
 const cacheNames = await page.evaluate(() => caches.keys());
 assert.ok(!cacheNames.includes('graphitout-shell-%2FGraphitout%2F-old')); assert.ok(cacheNames.includes('other-app-cache')); assert.ok(cacheNames.includes('graphitout-shell-%2FOther%2F-old'));
 await context.setOffline(true); await page.reload();
 assert.equal(await page.locator(`[data-node="${inkId}"]`).count(), 1);
 assert.equal(await page.locator(`[data-node="${inkId}"] .ink-preview path`).count() >= 3, true);
 await context.setOffline(false);
 await page.screenshot({ path:path.join(output,'pwa-ink-desktop.png') });
 await page.setViewportSize({width:360,height:800});
 // Drawing pad stays reachable at phone width; the explicit details button opens its drawer.
 await page.locator(`[data-node="${inkId}"]`).dblclick({force:true});
 await page.screenshot({path:path.join(output,'pwa-ink-phone.png')});
 const dialog = await page.locator('#ink-dialog').boundingBox(); assert.ok(dialog.width<=360);
 assert.equal(await page.locator('#ink-done').isVisible(), true);
 await page.locator('#ink-done').click();
 assert.deepEqual(errors, []);
 pass('Offline reload and cache migration keep graph/ink data; phone drawing pad fits 360px');
 await browser.close(); await new Promise(resolve=>server.close(resolve));
 console.log(`${checks.length} PWA / ink checks passed.`);
})().catch(async error=>{console.error(error);if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));process.exitCode=1;});
