// Step 2 of the image build (step 1: python build_pages.py): render every figure HTML in this folder to ../<name>.png
//   npm i puppeteer-core            (or set PUPPETEER_CORE to an installed copy; 23.11 and 24 give identical PNGs)
//   node render.js                  all figures
//   node render.js sec_payment      one figure
// Needs Chrome: set CHROME to its path if it is not in one of the usual places below.
const path = require('path');
const fs = require('fs');

let puppeteer;
try {
  puppeteer = require(process.env.PUPPETEER_CORE || 'puppeteer-core');
} catch (e) {
  console.error('puppeteer-core not found: npm i puppeteer-core, or set PUPPETEER_CORE');
  process.exit(1);
}
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].find((p) => fs.existsSync(p));
if (!CHROME) {
  console.error('Chrome not found: set CHROME to its path');
  process.exit(1);
}

const FIGURES = [
  'sec_header', 'sec_fastfill', 'sec_fastfill_count', 'sec_smartprovide', 'sec_smartprovide_para13',
  'sec_payment', 'patent_front', 'sec_collage',
];

(async () => {
  const only = process.argv.slice(2);
  const list = only.length ? FIGURES.filter((f) => only.includes(f)) : FIGURES;
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--allow-file-access-from-files', '--force-color-profile=srgb', '--hide-scrollbars'],
  });
  let bad = 0;
  for (const name of list) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1800, height: 1200, deviceScaleFactor: 1 });
    page.on('console', (m) => { if (m.type() === 'error') console.log('  [console]', m.text()); });
    const url = 'file:///' + path.join(__dirname, name + '.html').replace(/\\/g, '/');
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForFunction('window.__ready === true', { timeout: 30000 });
    const errs = await page.evaluate(() => window.__errors || []);
    if (errs.length) { bad++; console.log('FAIL', name, errs); await page.close(); continue; }
    const r = await page.evaluate(() => {
      const b = document.getElementById('fig').getBoundingClientRect();
      return { x: b.left, y: b.top, w: b.width, h: b.height };
    });
    await page.setViewport({ width: Math.ceil(r.w), height: Math.ceil(r.h), deviceScaleFactor: 1 });
    const out = path.join(__dirname, '..', name + '.png');
    await page.screenshot({ path: out, clip: { x: r.x, y: r.y, width: Math.round(r.w), height: Math.round(r.h) } });
    console.log('ok', name, Math.round(r.w) + 'x' + Math.round(r.h), (fs.statSync(out).size / 1024).toFixed(0) + ' KB');
    await page.close();
  }
  await browser.close();
  process.exit(bad ? 1 : 0);
})();
