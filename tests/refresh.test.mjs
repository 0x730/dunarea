import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const section = (a, b) => app.slice(app.indexOf(a), app.indexOf(b, app.indexOf(a)));

test('one complete refresh at a time, hidden tabs pause, failures release the lock', async () => {
  let calls = 0, finish;
  const context = vm.createContext({document: {hidden: false}, console: {error() {}},
    refreshPanels: () => { calls++; return new Promise(r => { finish = r; }); }});
  vm.runInContext(section('let refreshInFlight = null;', 'async function refreshPanels()'), context);
  const first = context.refreshData();
  assert.equal(context.refreshData(), first);
  assert.equal(calls, 1);
  finish(); await first;
  context.document.hidden = true;
  await context.refreshData(); assert.equal(calls, 1);
  context.document.hidden = false;
  const next = context.refreshData(); assert.equal(calls, 2);
  finish(); await next;
  context.refreshPanels = () => Promise.reject(new Error('failed'));
  await context.refreshData();
  assert.equal(vm.runInContext('refreshInFlight', context), null);
});

test('a cycle waits for independent slow panels and the core', async () => {
  let finish;
  const context = vm.createContext({safeRun: fn => fn(), refreshCore: () => Promise.resolve()});
  const renderers = ['renderPFChart','renderEntsoe','renderAnomalii','renderStatistici',
    'renderBilantApa','renderMvMChart','renderIstoric','renderMissingData',
    'renderEdo','renderRomania','renderApeMici'];
  for (const name of renderers) context[name] = () => Promise.resolve();
  context.renderRomania = () => new Promise(r => { finish = r; });
  vm.runInContext(section('async function refreshPanels()', 'async function refreshCore()'), context);
  let done = false;
  const cycle = context.refreshPanels().then(() => { done = true; });
  await Promise.resolve(); assert.equal(done, false);
  finish(); await cycle; assert.equal(done, true);
});

test('fetch aborts on its deadline and clears timers after success and failure', async () => {
  let timer, cleared = 0;
  const context = vm.createContext({AbortController, sanitize: x => x,
    setTimeout: fn => { timer = fn; return 1; }, clearTimeout: () => { cleared++; },
    fetch: (_url, {signal}) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')));
    })});
  vm.runInContext(section('async function jget(url)', '/* ------------------------------------------------------- status pills'), context);
  const call = context.jget('/test'); timer();
  await assert.rejects(call, /aborted/); assert.equal(cleared, 1);
  context.fetch = async () => ({ok: true, json: async () => ({value: 1})});
  assert.equal((await context.jget('/test')).value, 1); assert.equal(cleared, 2);
});

test('visibility return triggers refresh and warnings use text, not HTML', async () => {
  let callback, calls = 0;
  const context = vm.createContext({document: {hidden: true, addEventListener: (_name, fn) => {callback = fn;}}, refreshData: () => {calls++;}});
  const start = app.indexOf('  document.addEventListener("visibilitychange"');
  vm.runInContext(app.slice(start, app.indexOf('\n  });', start) + 6), context);
  callback(); assert.equal(calls, 0);
  context.document.hidden = false; callback(); assert.equal(calls, 1);
  const element = {};
  context.$ = () => element;
  vm.runInContext(section('const FRESHNESS_WARNINGS = {};', '/* --------------------------------------------------------- temă charts'), context);
  assert.equal(context.sourceState('DanubeHIS', {stale: false, observation_freshness: {
    status: 'partial_stale', counts: {}, problems: [{station: '<img src=x>', observed_at: '2026-08-13', status: 'stale'}]}}), 'limited');
  assert.match(element.textContent, /<img src=x>/);
  assert.equal(element.innerHTML, undefined);
});

test('a dated observation without its value is labelled as missing, not as an unverifiable date', () => {
  const context = vm.createContext({});
  vm.runInContext(section('function freshnessIssues(data)', 'function sourceState('), context);
  const inhga = {stale: false, observation_freshness: {status: 'unknown', problems: [
    {station: 'inhga', observed_at: '2026-09-25', status: 'unknown', missing: 'debit_bazias_m3s'}]}};
  assert.deepEqual([...context.freshnessIssues(inhga)], ['inhga: 2026-09-25 (valoarea măsurată lipsește)']);
  const undated = {observation_freshness: {status: 'unknown', problems: [
    {station: 'x', observed_at: 'invalid', status: 'unknown'}]}};
  assert.deepEqual([...context.freshnessIssues(undated)], ['x: invalid (dată neverificabilă)']);
});

function hero() {
  const elements = {};
  const context = vm.createContext({
    $: id => (elements[id] ||= {innerHTML: ''}),
    fmtN: new Intl.NumberFormat('ro-RO', {maximumFractionDigits: 0}),
  });
  vm.runInContext(section('function renderHero(', '/* ------------------------------------------------- profil'), context);
  return {context, elements};
}

test('the hero separates a fetched bulletin without a value from an unread one', () => {
  const url = 'https://www.hidro.ro/bulletin/diagnoza-si-prognoza-25-09-2026/';
  let {context, elements} = hero();
  context.renderHero({data_buletin: '2026-09-25', url, debit_bazias_m3s: null,
    text_oficial: ['Debitul (secțiunea Baziaș) a fost 1600 m³/s.']});
  assert.match(elements['hero-num'].innerHTML, /din 2026-09-25 a fost preluat/);
  assert.match(elements['hero-num'].innerHTML, /nu a putut fi extras automat/);
  assert.ok(elements['hero-num'].innerHTML.includes(`href="${url}"`));
  assert.match(elements['hero-text'].innerHTML, /a fost 1600 m³\/s/);

  ({context, elements} = hero());
  context.renderHero(null);
  assert.match(elements['hero-num'].innerHTML, /nu a putut fi citit acum/);
  assert.equal(elements['hero-text'], undefined);

  // O adresă din afara INHGA nu devine link.
  ({context, elements} = hero());
  context.renderHero({data_buletin: '2026-09-25', url: 'https://example.org/', debit_bazias_m3s: null});
  assert.match(elements['hero-num'].innerHTML, /nu a putut fi citit acum/);
});

test('a stationary trend is not rendered as "în staționar"', () => {
  const {context, elements} = hero();
  context.renderHero({data_buletin: '2026-09-24', url: 'https://www.hidro.ro/x/',
    debit_bazias_m3s: 1600, media_multianuala_m3s: 3800, tendinta: 'staționar'});
  assert.match(elements['hero-num'].innerHTML, / · staționar/);
  assert.doesNotMatch(elements['hero-num'].innerHTML, /în staționar/);
});

test('the ratio-break message follows the data', () => {
  const context = vm.createContext({});
  vm.runInContext(section('function ratioBreakMessage(', 'async function renderAnomalii()'), context);
  const wave = {in_timpul_variatiei_debitului: true, variatie_debit_pct: {oficial: 19.1, model: 37.8}};
  const during = context.ratioBreakMessage(wave, 'sever');
  assert.match(during, /în timpul unei variații de debit/);
  assert.match(during, /\+37,8%/);
  assert.match(during, /\+19,1%/);
  const jump = {in_timpul_variatiei_debitului: false, variatie_debit_pct: {oficial: 20.4, model: 0}};
  assert.match(context.ratioBreakMessage(jump, 'sever'), /s-a schimbat recent/);
  assert.match(context.ratioBreakMessage(wave, 'normal'), /consecvent/);
});
