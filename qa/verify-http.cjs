const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
let browser, server;
(async () => {
 const html = await fs.readFile(path.resolve(__dirname, '../index.html'));
 server = http.createServer(async (req, res) => {
  if (req.url === '/Graphitout/' || req.url === '/Graphitout/index.html') {
   res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
   res.end(html);
  } else if (/^\/Graphitout\/(manifest\.webmanifest|sw\.js|icons\/(icon-192|icon-512|maskable-512)\.png)$/.test(req.url)) {
   const file=req.url.slice('/Graphitout/'.length);
   res.writeHead(200, { 'Content-Type': file.endsWith('.png')?'image/png':file.endsWith('.js')?'text/javascript':'application/manifest+json' });
   res.end(await fs.readFile(path.resolve(__dirname, '..', file)));
  } else { res.writeHead(404); res.end(); }
 });
 await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
 const url = `http://127.0.0.1:${server.address().port}/Graphitout/`;
 browser = await chromium.launch({ headless: true, ...(process.env.GRAPHITOUT_BROWSER_CHANNEL ? { channel: process.env.GRAPHITOUT_BROWSER_CHANNEL } : {}) });
 const page = await browser.newPage({ acceptDownloads: true });
 const errors = [], resources = [];
 page.on('pageerror', e => errors.push(e.message));
 page.on('request', r => resources.push(r.url()));
 const response = await page.goto(url);
 assert.equal(response.status(), 200);
 assert.equal(await page.title(), 'Graphitout — think in connections');
 assert.equal(await page.locator('[data-node]').count(), 7);
 await page.locator('[data-add="service"]').click();
 assert.equal(await page.locator('[data-node]').count(), 8);
 await page.waitForTimeout(400);
 await page.reload();
 assert.equal(await page.locator('[data-node]').count(), 8);
 await page.locator('#export-btn').click();
 const downloadPromise = page.waitForEvent('download');
 await page.locator('[data-export="json"]').click();
 const download = await downloadPromise;
 assert.equal(await download.failure(), null);
 assert.deepEqual(errors, []);
 assert.ok(resources.every(r => r.startsWith(url) || r.startsWith('blob:')), 'Unexpected external application dependency');
 console.log('PASS HTTP /Graphitout/ serves the self-contained app, preserves edits, and exports JSON');
 await browser.close();
 await new Promise(resolve => server.close(resolve));
})().catch(async error => {
 console.error(error);
 if (browser) await browser.close();
 if (server) await new Promise(resolve => server.close(resolve));
 process.exitCode = 1;
});
