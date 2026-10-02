// AP40 — keyboard-only walkthrough + modal behaviour assertions + axe pass.
// No mouse: every interaction is page.keyboard. The only non-keyboard step is
// opening the magic-link URL, which stands in for clicking the link in an email.
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';

const BASE = 'http://localhost:5174';
const OUT = process.env.OUT;
const LOG = process.env.SERVER_LOG;
fs.mkdirSync(OUT, { recursive: true });

const steps = [];
let failures = 0;
const ok = (name, cond, detail = '') => {
  steps.push({ step: name, pass: !!cond, detail });
  if (!cond) failures++;
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();

const active = () => page.evaluate(() => {
  const el = document.activeElement;
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {
    tag: el.tagName.toLowerCase(), id: el.id, cls: el.className,
    text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60),
    role: el.getAttribute('role'),
    outline: `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}`,
    inDialog: !!el.closest('[role="dialog"]'),
  };
});

// Tab (or Shift+Tab) until `pred(activeInfo)` holds; returns the info or null.
async function tabTo(pred, { max = 60, shift = false } = {}) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press(shift ? 'Shift+Tab' : 'Tab');
    const a = await active();
    if (a && pred(a)) return a;
  }
  return null;
}
const ringVisible = (a) => a && a.outline.startsWith('solid') && !a.outline.includes(' 0px');

async function axe(name) {
  await page.waitForTimeout(1500); // let staggered fade-ins finish: mid-animation text reads as low contrast
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  fs.writeFileSync(`${OUT}/axe_${name}.json`, JSON.stringify(r.violations, null, 2));
  const sc = r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const n = sc.reduce((s, v) => s + v.nodes.length, 0);
  console.log(`axe ${name}: serious/critical nodes=${n} [${sc.map((v) => v.id + 'x' + v.nodes.length).join(', ')}] | all: ${r.violations.map((v) => `${v.id}(${v.impact})x${v.nodes.length}`).join(', ') || 'none'}`);
  return { name, serious_critical_nodes: n, violations: r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })) };
}
const axeResults = [];

// ── 1. landing: axe, then sign in by keyboard ───────────────────────────────
await page.goto(BASE + '/', { waitUntil: 'networkidle' });
axeResults.push(await axe('landing'));

let a = await tabTo((x) => x.tag === 'button' && /sign in/i.test(x.text));
ok('Tab reaches "Sign in"', a, a && a.text);
ok('focus ring visible on Sign in', ringVisible(a), a && a.outline);
await page.screenshot({ path: `${OUT}/01_focus_sign_in.png` });
await page.keyboard.press('Enter');
await page.waitForSelector('#login-email');
a = await active();
ok('login email field has focus (autoFocus)', a && a.id === 'login-email');
const tokensBefore = (fs.readFileSync(LOG, 'utf8').match(/verify\?token=/g) || []).length;
await page.keyboard.type('keyboard@ap40.dev');
await page.keyboard.press('Enter');
await page.waitForSelector('text=Check your inbox');
const statusText = await page.locator('[role="status"]').first().textContent();
ok('"link sent" is in a live status region', /sign-in link sent/i.test(statusText || ''), statusText);

// The email link (the one step that is not in-app keyboard use).
let token = null;
for (let i = 0; i < 20 && !token; i++) {
  const m = fs.readFileSync(LOG, 'utf8').match(/verify\?token=([\w-]+)/g);
  if (m && m.length > tokensBefore) token = m[m.length - 1].split('token=')[1];
  else await page.waitForTimeout(300);
}
ok('magic link found in server log', token);
await page.goto(`${BASE}/auth/verify?token=${token}`, { waitUntil: 'networkidle' });
await page.waitForSelector('#goal');
await page.waitForSelector('text=keyboard@ap40.dev', { timeout: 10000 }).catch(() => {});
ok('signed in (email shown in the auth bar)', (await page.locator('text=keyboard@ap40.dev').count()) > 0);

// ── 2. failing generation: the error must land in role=alert ────────────────
const alertBefore = await page.locator('form [role="alert"]').count();
ok('error live region exists BEFORE any error (persistent)', alertBefore === 1, `count=${alertBefore}`);
a = await tabTo((x) => x.id === 'goal');
ok('Tab reaches the goal field', a);
await page.keyboard.type('FAILME please');
a = await tabTo((x) => x.tag === 'button' && /generate/i.test(x.text));
ok('Tab reaches Generate', a);
await page.keyboard.press('Enter');
await page.waitForFunction(() => (document.querySelector('form [role="alert"]')?.textContent || '').length > 3, null, { timeout: 15000 });
const alertText = await page.locator('form [role="alert"]').textContent();
ok('failed generation is announced via role=alert', alertText.length > 3, alertText.trim().slice(0, 90));
const snap = await page.locator('form').ariaSnapshot();
fs.writeFileSync(`${OUT}/aria_after_failed_generation.txt`, snap);

// ── 3. successful generation by keyboard ────────────────────────────────────
a = await tabTo((x) => x.id === 'goal', { shift: true });
await page.keyboard.press('Control+A');
await page.keyboard.type('Learn Python for data analysis');
a = await tabTo((x) => x.tag === 'button' && /generate/i.test(x.text));
await page.keyboard.press('Enter');
await page.waitForSelector('.milestone-toggle', { timeout: 20000 });
ok('path generated (keyboard Enter on Generate)', true);
axeResults.push(await axe('path'));

// Milestone 2: the stub's descriptions for milestone 1 are under quizzes.py's 100-char floor.
a = await tabTo((x) => x.cls.includes('milestone-toggle') && /Milestone 2/.test(x.text));
ok('Tab reaches milestone 2 disclosure button', a, a && a.text);
ok('focus ring visible on milestone toggle', ringVisible(a), a && a.outline);
await page.screenshot({ path: `${OUT}/02_focus_milestone_toggle.png` });
await page.keyboard.press('Enter');
const expanded = await page.evaluate(() => document.activeElement.getAttribute('aria-expanded'));
ok('Enter expands the milestone (aria-expanded=true)', expanded === 'true');
await page.keyboard.press('Space');
ok('Space collapses it again', (await page.evaluate(() => document.activeElement.getAttribute('aria-expanded'))) === 'false');
await page.keyboard.press('Enter');

// ── 4. the quiz modal: five behaviours, asserted separately ─────────────────
const seq = [];
a = await tabTo((x) => { seq.push(x.tag + ':' + x.text.slice(0, 30)); return x.tag === 'button' && /take quiz/i.test(x.text); });
ok('Tab reaches "Take quiz"', a, a ? '' : seq.slice(0, 25).join(' | '));
await page.screenshot({ path: `${OUT}/03_focus_take_quiz.png` });
await page.keyboard.press('Enter');
await page.waitForSelector('[role="dialog"] h2#quiz-modal-title:not(.sr-only)', { timeout: 15000 });
a = await active();
ok('(1) on open, focus is inside the dialog', a && (a.inDialog || a.role === 'dialog'), JSON.stringify(a));
const label = await page.evaluate(() => {
  const d = document.querySelector('[role="dialog"]');
  const id = d.getAttribute('aria-labelledby');
  const h = id && document.getElementById(id);
  return h ? h.textContent.trim() : null;
});
ok('(2) aria-labelledby points at a real heading', label, label);
axeResults.push(await axe('quiz_modal'));
await page.keyboard.press('Escape');
await page.waitForSelector('[role="dialog"]', { state: 'detached', timeout: 5000 }).catch(() => {});
ok('(3) Escape closes the dialog', (await page.locator('[role="dialog"]').count()) === 0);
a = await active();
ok('(4) focus returns to the "Take quiz" trigger', a && /take quiz/i.test(a.text), a && a.text);

await page.keyboard.press('Enter');
await page.waitForSelector('[role="dialog"] h2#quiz-modal-title:not(.sr-only)', { timeout: 15000 });
let escaped = 0;
for (let i = 0; i < 25; i++) {
  await page.keyboard.press(i % 3 === 2 ? 'Shift+Tab' : 'Tab');
  const x = await active();
  if (!(x.inDialog || x.role === 'dialog')) escaped++;
}
ok('(5) Tab/Shift+Tab x25 never leaves the dialog', escaped === 0, `escaped=${escaped}`);

// Answer with the keyboard: Q1 = option 1 (Space), Q2 = option 2 (ArrowDown), Q3 = option 3.
await page.locator('#quiz-modal-title').evaluate((h) => h.closest('[role="dialog"]').focus());
a = await tabTo((x) => x.tag === 'input' && x.inDialog);
await page.keyboard.press('Space');
a = await tabTo((x) => x.tag === 'input');
await page.keyboard.press('ArrowDown');
a = await tabTo((x) => x.tag === 'input');
await page.keyboard.press('ArrowDown');
await page.keyboard.press('ArrowDown');
await page.screenshot({ path: `${OUT}/04_focus_quiz_radio.png` });
const checked = await page.evaluate(() => [0, 1, 2].map((i) => [...document.querySelectorAll(`input[name="q-${i}"]`)].findIndex((r) => r.checked)));
ok('radios answered by keyboard (Space / arrows)', JSON.stringify(checked) === '[0,1,2]', JSON.stringify(checked));
a = await tabTo((x) => x.tag === 'button' && /^submit$/i.test(x.text));
ok('Tab reaches Submit', a);
await page.keyboard.press('Enter');
await page.waitForSelector('text=Passed', { timeout: 15000 });
ok('quiz submitted and passed', true);
await page.keyboard.press('Escape');
ok('Escape closes the result dialog', (await page.locator('[role="dialog"]').count()) === 0);
await page.waitForTimeout(150); // the hook restores focus one animation frame after close
a = await active();
ok('after the milestone completes (trigger gone), focus lands on that milestone toggle', a && a.cls.includes('milestone-toggle'), a && a.text);
axeResults.push(await axe('path_with_completed_milestone'));

// ── 5. explore + public profile + share, axe ────────────────────────────────
const pathId = await page.evaluate(async () => {
  const r = await fetch('/api/paths', { credentials: 'include' });
  const ps = await r.json();
  return ps[0]?.id;
});
await page.evaluate(async (id) => {
  await fetch(`/api/paths/${id}/share`, { method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_public: true }) });
  await fetch('/api/me/profile/visibility', { method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_public_profile: true }) });
}, pathId);
const me = await page.evaluate(async () => (await (await fetch('/api/auth/me', { credentials: 'include' })).json()));

await page.goto(BASE + '/explore', { waitUntil: 'networkidle' });
axeResults.push(await axe('explore'));
a = await tabTo((x) => x.role === 'button' && x.tag === 'article');
ok('Tab reaches an explore card', a, a && a.text);
await page.keyboard.press(' ');
await page.waitForURL(/\/share\//, { timeout: 10000 }).catch(() => {});
ok('Space on an explore card opens the shared path', /\/share\//.test(page.url()), page.url());
await page.waitForLoadState('networkidle');
axeResults.push(await axe('share'));

await page.goto(`${BASE}/u/${me.id}?spa=1`, { waitUntil: 'networkidle' });
await page.waitForSelector('text=Learner Profile', { timeout: 10000 }).catch(() => {});
axeResults.push(await axe('public_profile'));

// ── 6. reduced motion ───────────────────────────────────────────────────────
const rm = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
const rp = await rm.newPage();
await rp.goto(BASE + '/', { waitUntil: 'networkidle' });
const motion = await rp.evaluate(() => {
  const els = [...document.querySelectorAll('.fade-in, .bg-gradient-1, .bg-gradient-2, .bg-gradient-3')];
  return els.map((e) => { const cs = getComputedStyle(e); return { d: cs.animationDuration, n: cs.animationIterationCount, o: cs.opacity }; });
});
ok('reduced motion: every animation is ~0s and runs once', motion.length > 0 && motion.every((m) => m.d === '1e-05s' || m.d === '0.00001s' || parseFloat(m.d) <= 0.001) && motion.every((m) => m.n === '1'), JSON.stringify(motion.slice(0, 3)));
const fadeOpacity = await rp.evaluate(() => [...document.querySelectorAll('.fade-in')].map((e) => getComputedStyle(e).opacity));
ok('reduced motion: faded-in content still ends visible (opacity 1)', fadeOpacity.length > 0 && fadeOpacity.every((o) => o === '1'), fadeOpacity.join(','));
const fullMotion = await page.evaluate(() => { const e = document.querySelector('.fade-in'); return e ? getComputedStyle(e).animationDuration : null; });
ok('control: without the setting, animations keep their duration', fullMotion && parseFloat(fullMotion) > 0.1, fullMotion);
await rm.close();

fs.writeFileSync(`${OUT}/walkthrough.json`, JSON.stringify({ steps, axe: axeResults }, null, 2));
const axeTotal = axeResults.reduce((s, r) => s + r.serious_critical_nodes, 0);
console.log(`\n${steps.filter((s) => s.pass).length}/${steps.length} steps passed; axe serious/critical nodes across ${axeResults.length} screens: ${axeTotal}`);
await browser.close();
process.exit(failures || axeTotal ? 1 : 0);
