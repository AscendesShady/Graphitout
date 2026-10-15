// Development helper. Regenerate the original vector brand as installable PNG icons.
const fs = require('node:fs/promises');
const { chromium } = require('playwright');
(async () => {
 const browser = await chromium.launch({ headless: true, ...(process.env.GRAPHITOUT_BROWSER_CHANNEL ? { channel: process.env.GRAPHITOUT_BROWSER_CHANNEL } : {}) });
 try {
  const page = await browser.newPage();
  const svg = await fs.readFile('icons/icon.svg', 'utf8');
  for (const size of [192, 512]) {
   const data = await page.evaluate(async ({ svg, size }) => {
    const img = new Image(); img.src = 'data:image/svg+xml,' + encodeURIComponent(svg); await img.decode();
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
    canvas.getContext('2d').drawImage(img, 0, 0, size, size);
    return canvas.toDataURL('image/png').split(',')[1];
   }, { svg, size });
   await fs.writeFile(`icons/icon-${size}.png`, Buffer.from(data, 'base64'));
   if (size === 512) await fs.writeFile('icons/maskable-512.png', Buffer.from(data, 'base64'));
  }
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
