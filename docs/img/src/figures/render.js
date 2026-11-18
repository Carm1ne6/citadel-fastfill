// Render the README figures in this folder to PNG files in docs/img/.
//
//   npm i puppeteer-core            (or set PUPPETEER_CORE to an installed copy; 23.11 and 24 give identical PNGs)
//   CHROME="/path/to/chrome" node render.js [figure ...]
//
// With no arguments every figure is rendered.
const fs = require("fs");
const path = require("path");

let puppeteer;
try {
  puppeteer = require(process.env.PUPPETEER_CORE || "puppeteer-core");
} catch (e) {
  console.error("puppeteer-core not found: npm i puppeteer-core, or set PUPPETEER_CORE");
  process.exit(1);
}

const CHROME_GUESSES = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];
const CHROME = process.env.CHROME || process.env.CHROME_PATH || CHROME_GUESSES.find((p) => fs.existsSync(p));

// name: [css width, css height, device scale, transparent corners]
const FIGURES = {
  banner: [1280, 400, 2, true],
  diagram_feeds: [900, 560, 2, true],
  diagram_fastfill: [900, 728, 2, true],
  diagram_smartprovide: [900, 728, 2, true],
  diagram_toxicity: [900, 640, 2, true],
  social_preview: [1280, 640, 1, false],
};

(async () => {
  if (!CHROME) throw new Error("Chrome not found: set CHROME");
  const want = process.argv.slice(2);
  const names = want.length ? want : Object.keys(FIGURES);
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--allow-file-access-from-files", "--font-render-hinting=none", "--force-color-profile=srgb"],
  });
  for (const name of names) {
    const [w, h, scale, transparent] = FIGURES[name];
    const page = await browser.newPage();
    await page.setViewport({ width: w, height: h, deviceScaleFactor: scale });
    const url = "file:///" + path.join(__dirname, name + ".html").replace(/\\/g, "/");
    await page.goto(url, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.fonts.ready);
    const out = path.join(__dirname, "..", "..", name + ".png");
    await page.screenshot({ path: out, omitBackground: transparent, clip: { x: 0, y: 0, width: w, height: h } });
    const kb = (fs.statSync(out).size / 1024).toFixed(0);
    console.log(`${name}.png  ${w * scale}x${h * scale}  ${kb} KB`);
    await page.close();
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
