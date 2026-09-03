// End-to-end checks for the URL-state and combobox-keyboard work.
//
// Run against a LIVE dev stack -- the Flask backend on :5001 and
// `ng serve --port 4321` -- then: `node e2e/url-state.e2e.mjs`
//
// Written after a build-clean, type-clean change turned out to have three
// runtime-only defects (Escape closing the whole modal, ArrowDown wiping
// the typed query, a frozen loading spinner). None were visible to tsc or
// to the Angular compiler; all three surfaced on the first real run.
//
// Note the modal section stays inside ONE page session on purpose: an
// anonymous student's plan is memory-only, so a full page load drops the
// major and leaves no schedule to click.

import { chromium } from 'playwright';

const BASE = 'http://localhost:4321';
const SHOTS = process.env.E2E_SHOTS ?? new URL('./shots/', import.meta.url).pathname;
const results = [];
const ok = (name, pass, detail = '') => {
  results.push({ name, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
const consoleErrors = [];
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()));
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.screenshot({ path: `${SHOTS}/01-load.png` });

// ── 1. Combobox keyboard contract ────────────────────────────────────────
// Major input inside the first-visit welcome modal.
const majorInput = page.locator('input[role="combobox"]').first();
await majorInput.waitFor({ state: 'visible', timeout: 15000 });
await majorInput.click();
await majorInput.type('CMPSC', { delay: 40 });
await page.waitForTimeout(400);

const expandedBefore = await majorInput.getAttribute('aria-expanded');
await page.keyboard.press('ArrowDown');
await page.waitForTimeout(200);
const activeDesc = await majorInput.getAttribute('aria-activedescendant');
ok('ArrowDown sets aria-activedescendant', !!activeDesc, `expanded=${expandedBefore} active=${activeDesc}`);

const activeOptionText = activeDesc
  ? await page.locator(`[id="${activeDesc}"]`).textContent().catch(() => null)
  : null;
ok('active option resolves to a real element', !!activeOptionText, (activeOptionText || '').trim().slice(0, 40));
await page.screenshot({ path: `${SHOTS}/02-combobox-arrow.png` });

// Escape closes without selecting
await page.keyboard.press('Escape');
await page.waitForTimeout(250);
ok('Escape closes the listbox', (await majorInput.getAttribute('aria-expanded')) !== 'true');

// Reopen, arrow, Enter selects
await majorInput.click();
await majorInput.type('CMPSC', { delay: 40 });
await page.waitForTimeout(400);
await page.keyboard.press('ArrowDown');
await page.keyboard.press('Enter');
await page.waitForTimeout(1200);
const chosen = await majorInput.inputValue();
ok('Enter selects the active option', /CMPSC/i.test(chosen), `input value = "${chosen}"`);
await page.screenshot({ path: `${SHOTS}/03-major-selected.png` });

// ── Dismiss the welcome modal so Home renders ────────────────────────────
for (const label of [/start planning/i, /get started/i, /continue/i, /close/i, /done/i]) {
  const b = page.getByRole('button', { name: label }).first();
  if (await b.count().then((c) => c > 0).catch(() => false)) {
    if (await b.isVisible().catch(() => false)) { await b.click().catch(() => {}); break; }
  }
}
await page.waitForTimeout(1500);
await page.screenshot({ path: `${SHOTS}/04-home.png`, fullPage: true });

// ── 2. Weekly Schedule modal <-> URL ─────────────────────────────────────
// Stays in THIS page session on purpose: an anonymous student's plan is
// held in memory only (PlannerStateService persists nothing but the
// transcript-upload timestamp), so any full page load here would drop the
// major we just picked and leave no schedule to click.
const block = page.locator('app-weekly-schedule button[title]').first();
const haveBlock = await block
  .waitFor({ state: 'visible', timeout: 25000 })
  .then(() => true)
  .catch(() => false);
ok('weekly schedule rendered course blocks', haveBlock);

if (haveBlock) {
  await block.click();
  await page.waitForTimeout(900);
  const urlAfterOpen = page.url();
  ok('clicking a block puts ?course= in the URL', /[?&]course=/.test(urlAfterOpen), urlAfterOpen.replace(BASE, ''));
  const dialogOpen = await page.locator('[role="dialog"]').first().isVisible().catch(() => false);
  ok('modal is open after click', dialogOpen);
  await page.screenshot({ path: `${SHOTS}/06-modal-open.png` });

  const courseParam = new URL(urlAfterOpen).searchParams.get('course');

  // Back should CLOSE the modal, not leave Home.
  await page.goBack();
  await page.waitForTimeout(900);
  const dialogAfterBack = await page.locator('[role="dialog"]').first().isVisible().catch(() => false);
  ok('Back closes the modal', !dialogAfterBack, page.url().replace(BASE, ''));
  ok('Back stays on Home', new URL(page.url()).pathname === '/', new URL(page.url()).pathname);
  await page.screenshot({ path: `${SHOTS}/07-after-back.png` });

  // Forward should REOPEN it.
  await page.goForward();
  await page.waitForTimeout(1200);
  const dialogAfterFwd = await page.locator('[role="dialog"]').first().isVisible().catch(() => false);
  ok('Forward reopens the modal', dialogAfterFwd, page.url().replace(BASE, ''));

  // Closing via X should land back on plain "/" and Back must NOT reopen.
  const closeBtn = page.getByRole('button', { name: /^close$/i }).first();
  if (await closeBtn.isVisible().catch(() => false)) {
    await closeBtn.click();
    await page.waitForTimeout(1200);
    ok('X drops the course param', !/[?&]course=/.test(page.url()), page.url().replace(BASE, ''));
    await page.goBack();
    await page.waitForTimeout(1000);
    const reopened = await page.locator('[role="dialog"]').first().isVisible().catch(() => false);
    ok('Back after X does not reopen the modal', !reopened, page.url().replace(BASE, ''));
    await page.screenshot({ path: `${SHOTS}/09-after-close-back.png` });
  } else {
    ok('close button found', false, 'no visible Close button');
  }
}

// ── 3. Chat panel deep link (survives a cold load — no plan needed) ──────
await page.goto(`${BASE}/?chat=1`, { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);
const chatVisible = await page.locator('app-chatbot textarea').isVisible().catch(() => false);
ok('?chat=1 opens the chat panel on a cold load', chatVisible);
await page.screenshot({ path: `${SHOTS}/10-chat-param.png` });

// ── 4. Reduced motion + theme-color ──────────────────────────────────────
const themeColor = await page.locator('meta[name="theme-color"]').getAttribute('content').catch(() => null);
ok('theme-color meta present', !!themeColor, String(themeColor));

console.log('\n--- console errors ---');
console.log(consoleErrors.length ? consoleErrors.slice(0, 12).join('\n') : '(none)');
console.log(`\n${results.filter((r) => r.pass).length}/${results.length} passed`);
await browser.close();
