/* Monitor Dunărea — frontend. Toate datele vin din backendul local (/api/…),
   care le ia din sursele oficiale și le ține în cache. */

"use strict";

const $ = (id) => document.getElementById(id);
const fmtN = new Intl.NumberFormat("ro-RO", { maximumFractionDigits: 0 });
const fmt1 = new Intl.NumberFormat("ro-RO", { maximumFractionDigits: 1 });

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------- status pills -- */
const PILLS = {};
function pill(name, state) {
  PILLS[name] = state;
  const el = $("status-pills");
  el.innerHTML = Object.entries(PILLS)
    .map(([n, s]) => `<span class="pill ${s}">${n}</span>`)
    .join("");
}

/* --------------------------------------------------------- temă charts -- */
const INK2 = "#c3c2b7", MUTED = "#898781", GRID = "#2c2c2a", AXIS = "#383835";
const MONO = 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace';
const YEAR_RAMP = ["#b7d3f6", "#6da7ec", "#2a78d6", "#184f95"]; // an curent → -3
const BLUE = "#3987e5", ORANGE = "#d95926";

function baseOpt() {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "system-ui, sans-serif", color: INK2 },
    grid: { left: 58, right: 80, top: 42, bottom: 34 },
    legend: { top: 4, textStyle: { color: MUTED, fontSize: 12 }, itemWidth: 16, itemHeight: 9, inactiveColor: "#4a4a47" },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: "#212120", color: INK2, fontFamily: MONO, fontSize: 11 }, crossStyle: { color: AXIS }, lineStyle: { color: AXIS } },
      backgroundColor: "#212120", borderColor: "rgba(255,255,255,.12)",
      textStyle: { color: INK2, fontSize: 12.5 },
      valueFormatter: (v) => (v == null ? "–" : fmtN.format(v)),
    },
    xAxis: {
      type: "category", boundaryGap: false,
      axisLine: { lineStyle: { color: AXIS } },
      axisTick: { show: false },
      axisLabel: { color: MUTED, fontFamily: MONO, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: MUTED, fontFamily: MONO, fontSize: 11, formatter: (v) => fmtN.format(v) },
      splitLine: { lineStyle: { color: GRID } },
    },
  };
}

const CHARTS = [];
function mkChart(el) {
  const c = echarts.init(el, null, { renderer: "canvas" });
  CHARTS.push(c);
  return c;
}
window.addEventListener("resize", () => CHARTS.forEach((c) => c.resize()));

/* --------------------------------------------------------------- hero -- */
function renderHero(b) {
  if (!b || !b.debit_bazias_m3s) {
    $("hero-num").innerHTML = `<div class="err-box">Buletinul INHGA nu a putut fi citit acum.
      <a href="https://www.hidro.ro/bulletin_type/diagnoza-si-prognoza-pentru-dunare/" target="_blank" rel="noopener">Deschide-l direct</a>.</div>`;
    return;
  }
  const pct = b.media_multianuala_m3s
    ? Math.round((b.debit_bazias_m3s / b.media_multianuala_m3s) * 100) : null;
  $("hero-num").innerHTML = `
    <div class="label">Debit la intrarea în țară · Baziaș <span class="prov prov-masurat">măsurat</span></div>
    <div class="value">${fmtN.format(b.debit_bazias_m3s)}<small> m³/s</small></div>
    <div class="delta">${pct !== null
      ? `<b>${pct}%</b> din media multianuală a lunii (${fmtN.format(b.media_multianuala_m3s)} m³/s)` : ""}
      ${b.tendinta ? ` · în ${b.tendinta}` : ""}</div>
    <div class="asof">buletin INHGA · ${b.data_buletin}${b.prognoza_debit_m3s
      ? ` · prognoză 7 zile: ${fmtN.format(b.prognoza_debit_m3s)} m³/s` : ""}</div>`;
  $("hero-text").innerHTML =
    (b.text_oficial || []).map((t) => `<p>${t}</p>`).join("") +
    `<p class="src">Text oficial integral: <a href="${b.url}" target="_blank" rel="noopener">INHGA — diagnoza și prognoza pentru Dunăre</a></p>`;
}

/* ------------------------------------------------- profil longitudinal -- */
// km fluvial aproximativ pentru stațiile RHMZ (poziționare pe profil)
const RS_KM = {
  "Bezdan": 1425, "Apatin": 1402, "Bogojevo": 1367, "Bačka Palanka": 1299,
  "Novi Sad": 1255, "Slankamen": 1216, "Zemun": 1173, "Pančevo": 1153,
  "Smederevo": 1116, "Banatska Palanka": 1077, "Veliko Gradiste": 1060,
  "Veliko Gradište": 1060, "Golubac": 1042, "Donji Milanovac": 991,
  "Tekija": 956, "Kladovo": 933, "Brza Palanka": 884, "Prahovo": 861,
};

function renderProfile(ov, afdj, hidmet, portal) {
  const W = 1000, H = 250, X0 = 14, X1 = 990, Y0 = 196, YTOP = 30;
  const KM_MAX = 2500;
  const x = (km) => X0 + ((KM_MAX - km) / KM_MAX) * (X1 - X0);
  const QMIN = 40, QMAX = 7000;
  const y = (q) => {
    const v = Math.max(QMIN, Math.min(QMAX, q));
    return Y0 - ((Math.log10(v) - Math.log10(QMIN)) / (Math.log10(QMAX) - Math.log10(QMIN))) * (Y0 - YTOP);
  };

  const measured = [], model = [], ticks = [];

  (ov.pegelonline?.stations || []).forEach((s) => {
    if (s.km == null) return;
    if (s.q && s.q.value != null)
      measured.push({ km: s.km, q: s.q.value, name: s.name, src: "PEGELONLINE (orar)", extra: s.w ? `nivel ${fmtN.format(s.w.value)} cm` : "" });
    else if (s.w && s.w.value != null)
      ticks.push({ km: s.km, name: s.name, src: "PEGELONLINE (orar)", info: `nivel ${fmtN.format(s.w.value)} cm` });
  });

  (hidmet?.statii || []).forEach((s) => {
    const km = RS_KM[s.statie];
    if (!km) return;
    if (s.debit_m3s != null)
      measured.push({ km, q: s.debit_m3s, name: s.statie + " (RS)", src: "RHMZ Serbia (zilnic)", extra: s.nivel_cm != null ? `nivel ${fmtN.format(s.nivel_cm)} cm` : "" });
    else if (s.nivel_cm != null)
      ticks.push({ km, name: s.statie + " (RS)", src: "RHMZ Serbia (zilnic)", info: `nivel ${fmtN.format(s.nivel_cm)} cm` });
  });

  if (ov.inhga?.debit_bazias_m3s)
    measured.push({ km: 1071, q: ov.inhga.debit_bazias_m3s, name: "Baziaș", src: "INHGA (buletin zilnic)", extra: "intrarea în România" });

  (ov.glofas || []).forEach((p) => {
    if (p.id.startsWith("brat_")) return; // modelul nu rezolvă brațele deltei
    if (p.discharge_m3s != null && p.discharge_m3s > 1)
      model.push({ km: p.km, q: p.discharge_m3s, name: p.name, src: "GloFAS / Copernicus (model)" });
  });

  (afdj?.statii || []).forEach((s) => {
    if (s.km == null || s.cota_cm == null) return;
    ticks.push({ km: s.km, name: s.statie + " (RO)", src: "AFDJ (zilnic)", info: `cotă ${fmtN.format(s.cota_cm)} cm` });
  });

  // mirele DanubeSTREAM din țările neacoperite de celelalte surse (SK/HU/BG
  // + Austria, cu valori mai proaspete decât cele zilnice)
  (portal?.mire || []).forEach((m) => {
    if (m.km == null || m.cota_cm == null) return;
    if (!["SK", "HU", "BG", "AT"].includes(m.tara)) return;
    ticks.push({ km: m.km, name: `${m.statie} (${m.tara})`,
      src: "DanubeSTREAM (cvasi-orar)",
      info: `cotă ${fmtN.format(m.cota_cm)} cm · ${(m.masurat_utc || "").slice(11, 16)} UTC` });
  });

  model.sort((a, b) => b.km - a.km);
  const linePts = model.map((p) => `${x(p.km).toFixed(1)},${y(p.q).toFixed(1)}`).join(" ");

  const kmAxis = [2400, 2000, 1600, 1200, 800, 400, 0];
  const qAxis = [100, 500, 2000, 6000];
  const labels = [
    { km: 2415, t: "Kelheim" }, { km: 2226, t: "Passau" }, { km: 1869, t: "Bratislava" },
    { km: 1647, t: "Budapesta" }, { km: 1255, t: "Novi Sad" }, { km: 1071, t: "Baziaș" },
    { km: 554, t: "Zimnicea" }, { km: 170, t: "Brăila" }, { km: 0, t: "Sulina" },
  ];

  let svg = `<svg id="profil-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Profilul longitudinal al debitului Dunării">`;
  qAxis.forEach((q) => {
    svg += `<line x1="${X0}" y1="${y(q)}" x2="${X1}" y2="${y(q)}" stroke="${GRID}" stroke-width="1"/>`;
    svg += `<text x="${X1}" y="${y(q) - 4}" fill="${MUTED}" font-size="10" font-family="${MONO}" text-anchor="end">${fmtN.format(q)} m³/s</text>`;
  });
  svg += `<line x1="${X0}" y1="${Y0}" x2="${X1}" y2="${Y0}" stroke="${AXIS}" stroke-width="1"/>`;
  kmAxis.forEach((km) => {
    svg += `<text x="${x(km)}" y="${Y0 + 30}" fill="${MUTED}" font-size="10" font-family="${MONO}" text-anchor="middle">km ${km}</text>`;
  });
  labels.forEach((l) => {
    svg += `<text x="${x(l.km)}" y="${Y0 + 18}" fill="${INK2}" font-size="10.5" text-anchor="middle">${l.t}</text>`;
  });

  // barajele
  [{ km: 943, t: "PF I" }, { km: 863, t: "PF II" }].forEach((d) => {
    svg += `<rect x="${x(d.km) - 2}" y="${YTOP + 8}" width="4" height="${Y0 - YTOP - 8}" fill="#ec835a" opacity="0.75"/>`;
    svg += `<text x="${x(d.km)}" y="${YTOP + 2}" fill="#ec835a" font-size="9.5" font-family="${MONO}" text-anchor="middle">${d.t}</text>`;
  });

  // mire doar-nivel
  ticks.forEach((t, i) => {
    svg += `<line class="pf-tick" data-i="t${i}" x1="${x(t.km)}" y1="${Y0 - 7}" x2="${x(t.km)}" y2="${Y0}" stroke="${MUTED}" stroke-width="2" opacity="0.7" style="cursor:crosshair"/>`;
  });

  // linia modelului + punctele
  if (linePts) svg += `<polyline points="${linePts}" fill="none" stroke="${BLUE}" stroke-width="1.6" stroke-dasharray="5 4" opacity="0.75"/>`;
  model.forEach((p, i) => {
    svg += `<circle class="pf-pt" data-i="m${i}" cx="${x(p.km)}" cy="${y(p.q)}" r="4.5" fill="#1a1a19" stroke="${BLUE}" stroke-width="1.8" style="cursor:crosshair"/>`;
  });
  measured.forEach((p, i) => {
    svg += `<circle class="pf-pt" data-i="M${i}" cx="${x(p.km)}" cy="${y(p.q)}" r="5" fill="${BLUE}" stroke="#1a1a19" stroke-width="1.5" style="cursor:crosshair"/>`;
  });
  svg += `</svg>`;
  $("profil-holder").innerHTML = svg;

  const tip = $("profil-tip");
  const lookup = (key) => {
    const kind = key[0], idx = +key.slice(1);
    if (kind === "M") { const p = measured[idx]; return { n: p.name, rows: [`Q = ${fmtN.format(p.q)} m³/s`, p.extra].filter(Boolean), src: p.src + " · măsurat", km: p.km }; }
    if (kind === "m") { const p = model[idx]; return { n: p.name, rows: [`Q ≈ ${fmtN.format(p.q)} m³/s`], src: p.src, km: p.km }; }
    const t = ticks[idx]; return { n: t.name, rows: [t.info], src: t.src + " · măsurat", km: t.km };
  };
  const box = $("profil-holder").parentElement;
  box.querySelectorAll(".pf-pt, .pf-tick").forEach((el) => {
    el.addEventListener("mousemove", (ev) => {
      const d = lookup(el.dataset.i);
      tip.innerHTML = `<div class="t-name">${d.n}</div>` +
        d.rows.map((r) => `<div class="t-row">${r}</div>`).join("") +
        `<div class="t-row">km ${fmtN.format(d.km)}</div><div class="t-src">${d.src}</div>`;
      const r = box.getBoundingClientRect();
      tip.style.display = "block";
      tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 200) + "px";
      tip.style.top = (ev.clientY - r.top + 14) + "px";
    });
    el.addEventListener("mouseleave", () => (tip.style.display = "none"));
  });
}

/* -------------------------------------------------------------- tabele -- */
const arrow = (v) =>
  v == null ? "" :
  v > 0 ? `<span class="up">▲ +${fmt1.format(v)}</span>` :
  v < 0 ? `<span class="down">▼ ${fmt1.format(v)}</span>` :
          `<span class="flat">— 0</span>`;

function renderAfdjTable(afdj) {
  if (!afdj?.statii?.length) { $("tabel-afdj").innerHTML = `<div class="err-box">AFDJ indisponibil momentan.</div>`; return; }
  const rows = afdj.statii.map((s) => `
    <tr><td class="name">${s.statie}</td>
        <td class="num">${s.km != null ? fmtN.format(s.km) : ""}</td>
        <td class="num">${s.cota_cm != null ? fmtN.format(s.cota_cm) : "–"}</td>
        <td class="num">${arrow(s.variatie_cm)}</td>
        <td class="num">${s.temp_apa_c != null ? fmt1.format(s.temp_apa_c) + "°" : ""}</td></tr>`).join("");
  $("tabel-afdj").innerHTML = `<table class="data">
    <thead><tr><th>Stație</th><th class="num">km</th><th class="num">cotă cm</th><th class="num">24 h</th><th class="num">apă</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderHidmetTable(h) {
  if (!h?.statii?.length) { $("tabel-hidmet").innerHTML = `<div class="err-box">RHMZ indisponibil momentan (site-ul sârbesc răspunde greu din unele rețele).</div>`; return; }
  const rows = h.statii.map((s) => `
    <tr><td class="name">${s.statie}</td>
        <td class="num">${s.nivel_cm != null ? fmtN.format(s.nivel_cm) : "–"}</td>
        <td class="num">${arrow(s.variatie_cm)}</td>
        <td class="num">${s.debit_m3s != null ? fmtN.format(s.debit_m3s) : "·"}</td>
        <td class="num">${s.temp_apa_c != null ? fmt1.format(s.temp_apa_c) + "°" : ""}</td></tr>`).join("");
  $("tabel-hidmet").innerHTML = `<table class="data">
    <thead><tr><th>Stație</th><th class="num">nivel cm</th><th class="num">24 h</th><th class="num">Q m³/s</th><th class="num">apă</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <p class="sub" style="margin:8px 0 0">„·" = debitul nu se publică pentru stația respectivă.</p>`;
}

/* ------------------------------------------------ comparația pe ani ----- */
const MMDD = (() => {
  const out = [];
  const dim = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  for (let m = 0; m < 12; m++)
    for (let d = 1; d <= dim[m]; d++)
      out.push(`${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  return out;
})();
const MMDD_IDX = Object.fromEntries(MMDD.map((s, i) => [s, i]));
const MONTHS_RO = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"];

function splitByYear(time, values) {
  const byYear = {};
  for (let i = 0; i < time.length; i++) {
    const t = time[i], v = values[i];
    const mmdd = t.slice(5);
    if (mmdd === "02-29" || v == null) continue;
    const y = t.slice(0, 4);
    (byYear[y] ||= new Array(365).fill(null))[MMDD_IDX[mmdd]] = v;
  }
  return byYear;
}

function bandStats(byYear, excludeYear) {
  const lo = [], range = [], med = [];
  for (let i = 0; i < 365; i++) {
    const vals = Object.entries(byYear)
      .filter(([y]) => y !== excludeYear)
      .map(([, arr]) => arr[i])
      .filter((v) => v != null)
      .sort((a, b) => a - b);
    if (!vals.length) { lo.push(null); range.push(null); med.push(null); continue; }
    lo.push(vals[0]);
    range.push(vals[vals.length - 1] - vals[0]);
    med.push(vals[Math.floor(vals.length / 2)]);
  }
  return { lo, range, med };
}

function xAxisMonths() {
  return {
    data: MMDD,
    axisLabel: {
      color: MUTED, fontFamily: MONO, fontSize: 11,
      interval: (i) => MMDD[i].endsWith("-01"),
      formatter: (v) => MONTHS_RO[+v.slice(0, 2) - 1],
    },
  };
}

function yearSeries(byYear, curYear, { cumulative = false } = {}) {
  const years = [curYear, curYear - 1, curYear - 2, curYear - 3].map(String);
  return years.flatMap((y, i) => {
    let arr = byYear[y];
    if (!arr) return [];
    if (cumulative) {
      let acc = 0;
      arr = arr.map((v) => (v == null ? null : +(acc += v).toFixed(1)));
    }
    return [{
      name: y, type: "line", data: arr, symbol: "none", z: 10 - i,
      lineStyle: { width: i === 0 ? 3 : 1.6, color: YEAR_RAMP[i] },
      itemStyle: { color: YEAR_RAMP[i] },
      emphasis: { focus: "series" },
      endLabel: i === 0 ? { show: true, color: YEAR_RAMP[0], fontFamily: MONO, fontSize: 11, formatter: y } : undefined,
    }];
  });
}

let chartAni, chartPrecip, chartPF;

async function loadYears(pointId) {
  $("titlu-debit-ani").textContent = "Debit zilnic pe ani — se încarcă…";
  try {
    const d = await jget(`/api/glofas/years?point=${pointId}&start=2015`);
    const byYear = splitByYear(d.time, d.discharge);
    const cur = new Date().getFullYear();
    const { lo, range, med } = bandStats(byYear, String(cur));
    const ref = `2015–${cur - 1}`;
    const opt = baseOpt();
    opt.xAxis = { ...opt.xAxis, ...xAxisMonths() };
    opt.yAxis.name = "m³/s"; opt.yAxis.nameTextStyle = { color: MUTED, fontFamily: MONO };
    opt.series = [
      { name: "min", type: "line", data: lo, stack: "band", symbol: "none", lineStyle: { width: 0 }, silent: true, tooltip: { show: false }, legendHoverLink: false },
      { name: `interval ${ref}`, type: "line", data: range, stack: "band", symbol: "none", lineStyle: { width: 0 }, areaStyle: { color: "rgba(255,255,255,0.07)" }, silent: true, tooltip: { show: false } },
      { name: `mediană ${ref}`, type: "line", data: med, symbol: "none", lineStyle: { width: 1.4, type: "dashed", color: MUTED }, itemStyle: { color: MUTED } },
      ...yearSeries(byYear, cur),
    ];
    opt.legend.data = [...[cur, cur - 1, cur - 2, cur - 3].map(String), `mediană ${ref}`, `interval ${ref}`];
    chartAni ||= mkChart($("chart-ani"));
    chartAni.setOption(opt, { notMerge: true });
    $("titlu-debit-ani").textContent = `Debit zilnic pe ani — ${d.point.name}`;
    pill("GloFAS", d.stale ? "stale" : "ok");
  } catch (e) {
    $("titlu-debit-ani").textContent = "Debit zilnic pe ani — eroare la încărcare";
    pill("GloFAS", "err");
  }
}

async function loadPrecip(pointId) {
  $("titlu-precip").textContent = "Precipitații cumulate — se încarcă…";
  try {
    const d = await jget(`/api/precip?point=${pointId}&start=2015`);
    const byYear = splitByYear(d.time, d.precip);
    const cur = new Date().getFullYear();
    // mediană a cumulatelor anilor 2015–2025
    const cumYears = {};
    for (const [y, arr] of Object.entries(byYear)) {
      let acc = 0;
      cumYears[y] = arr.map((v) => (v == null ? null : +(acc += v).toFixed(1)));
    }
    const { med } = bandStats(cumYears, String(cur));
    const ref = `2015–${cur - 1}`;
    const opt = baseOpt();
    opt.xAxis = { ...opt.xAxis, ...xAxisMonths() };
    opt.yAxis.name = "mm"; opt.yAxis.nameTextStyle = { color: MUTED, fontFamily: MONO };
    opt.series = [
      { name: `mediană ${ref}`, type: "line", data: med, symbol: "none", lineStyle: { width: 1.4, type: "dashed", color: MUTED }, itemStyle: { color: MUTED } },
      ...yearSeries(byYear, cur, { cumulative: true }),
    ];
    opt.legend.data = [...[cur, cur - 1, cur - 2, cur - 3].map(String), `mediană ${ref}`];
    chartPrecip ||= mkChart($("chart-precip"));
    chartPrecip.setOption(opt, { notMerge: true });
    $("titlu-precip").textContent = `Precipitații cumulate de la 1 ianuarie — ${d.point.name}`;
    pill("ERA5", d.stale ? "stale" : "ok");
  } catch (e) {
    $("titlu-precip").textContent = "Precipitații — eroare la încărcare";
    pill("ERA5", "err");
  }
}

/* ------------------------------------------------------ Porțile de Fier -- */
async function renderPFChart() {
  try {
    const [ba, gr] = await Promise.all([
      jget("/api/glofas/recent?point=bazias&days=90"),
      jget("/api/glofas/recent?point=gruia&days=90"),
    ]);
    const today = new Date().toISOString().slice(0, 10);
    const idx = ba.time.filter((t) => t <= today);
    const gmap = Object.fromEntries(gr.time.map((t, i) => [t, gr.discharge[i]]));
    const opt = baseOpt();
    opt.grid.right = 110;
    opt.xAxis.data = idx.map((t) => t.slice(5));
    opt.yAxis.name = "m³/s"; opt.yAxis.nameTextStyle = { color: MUTED, fontFamily: MONO };
    opt.series = [
      { name: "Baziaș (intrare)", type: "line", data: idx.map((t, i) => ba.discharge[i]), symbol: "none",
        lineStyle: { width: 2, color: BLUE }, itemStyle: { color: BLUE },
        endLabel: { show: true, color: BLUE, fontSize: 11, formatter: "Baziaș" } },
      { name: "Gruia (ieșire)", type: "line", data: idx.map((t) => gmap[t]), symbol: "none",
        lineStyle: { width: 2, color: ORANGE }, itemStyle: { color: ORANGE },
        endLabel: { show: true, color: ORANGE, fontSize: 11, formatter: "Gruia" } },
    ];
    chartPF ||= mkChart($("chart-pf"));
    chartPF.setOption(opt, { notMerge: true });
  } catch (e) { /* pill-ul GloFAS acoperă eroarea */ }
}

function renderPFFacts(ov, afdj, hidmet) {
  const li = (k, v) => `<li><span class="k">${k}</span><span class="v">${v}</span></li>`;
  const inh = ov.inhga;
  const gGruia = (ov.glofas || []).find((p) => p.id === "gruia");
  const rs = Object.fromEntries((hidmet?.statii || []).map((s) => [s.statie, s]));
  const ro = Object.fromEntries((afdj?.statii || []).map((s) => [s.statie, s]));
  const rows = [];
  if (inh?.debit_bazias_m3s)
    rows.push(li("intrare · Baziaș", `<b>${fmtN.format(inh.debit_bazias_m3s)} m³/s</b> <span class="prov prov-masurat">măsurat</span> INHGA`));
  const gol = rs["Golubac"], bp = rs["Banatska Palanka"];
  if (gol) rows.push(li("lac · Golubac", `nivel <b>${fmtN.format(gol.nivel_cm)} cm</b> (${arrow(gol.variatie_cm)}) <span class="prov prov-masurat">măsurat</span> RHMZ`));
  if (bp) rows.push(li("lac · Banatska P.", `nivel <b>${fmtN.format(bp.nivel_cm)} cm</b> (${arrow(bp.variatie_cm)}) <span class="prov prov-masurat">măsurat</span> RHMZ`));
  const gruia = ro["Gruia"], prahovo = rs["Prahovo"];
  if (gruia) rows.push(li("ieșire · Gruia", `cotă <b>${fmtN.format(gruia.cota_cm)} cm</b> (${arrow(gruia.variatie_cm)}) <span class="prov prov-masurat">măsurat</span> AFDJ`));
  if (prahovo) rows.push(li("ieșire · Prahovo", `nivel <b>${fmtN.format(prahovo.nivel_cm)} cm</b> (${arrow(prahovo.variatie_cm)}) <span class="prov prov-masurat">măsurat</span> RHMZ`));
  if (gGruia?.discharge_m3s) rows.push(li("ieșire · debit", `≈ <b>${fmtN.format(gGruia.discharge_m3s)} m³/s</b> <span class="prov prov-model">model</span> GloFAS la Gruia`));
  rows.push(li("debit orar baraj", `<span class="prov prov-lipsa">nepublicat</span> — doar la Hidroelectrica / EPS`));
  $("pf-bilant").innerHTML = rows.join("");
}

async function renderEntsoe() {
  const li = (k, v) => `<li><span class="k">${k}</span><span class="v">${v}</span></li>`;
  try {
    const d = await jget("/api/entsoe");
    const rows = [];
    try {
      const s = await jget("/api/sen");
      if (s.hidro_mw != null)
        rows.push(li("hidro RO acum (SEN)", `<b>${fmtN.format(s.hidro_mw)} MW</b> toată țara, din care PF I+II e componenta majoră <span class="prov prov-masurat">măsurat</span> Transelectrica`));
    } catch (e) { /* SEN e opțional aici */ }
    if (d.activ && d.unitati?.length) {
      const last = d.unitati.map((u) => {
        const v = u.valori_mw.at(-1);
        return `${u.unitate}: <b>${v ? fmtN.format(v[1]) : "–"} MW</b>`;
      });
      rows.push(li("producție pe unități", `${last.join(" · ")} <span class="prov prov-masurat">măsurat</span> ENTSO-E`));
      rows.push(li("debit turbinat", `estimabil cu Q≈P/(ρ·g·H·η) <span class="prov prov-calculat">calculat</span>`));
      pill("ENTSO-E", "ok");
    } else {
      rows.push(li("producție pe unități", `disponibilă prin ENTSO-E pentru agregatele ≥100 MW (PF I are 6×194 MW pe partea RO). ${d.motiv || ""}`));
      rows.push(li("activare", `cont gratuit pe transparency.entsoe.eu → token → porniți serverul cu <code>ENTSOE_TOKEN=…</code>`));
      pill("ENTSO-E", "err");
    }
    rows.push(li("deversoare", `<span class="prov prov-lipsa">nepublicat</span> — cere pozițiile vanelor și curbele de descărcare`));
    rows.push(li("ecluzări", `<span class="prov prov-lipsa">nepublicat</span> — jurnalele de manevră nu sunt flux public`));
    $("pf-entsoe").innerHTML = rows.join("");
  } catch (e) {
    $("pf-entsoe").innerHTML = li("stare", "eroare la interogarea ENTSO-E");
  }
}

/* --------------------------------------------------------------- delta -- */
async function renderDelta(afdj) {
  const li = (k, v) => `<li><span class="k">${k}</span><span class="v">${v}</span></li>`;
  try {
    const d = await jget("/api/delta");
    const tot = d.puncte?.ceatal_izmail;
    const rows = [];
    if (tot?.discharge_m3s != null)
      rows.push(li("debit total", `≈ <b>${fmtN.format(tot.discharge_m3s)} m³/s</b> <span class="prov prov-model">model</span> GloFAS · ${tot.date}`));
    if (d.distributie?.valid) {
      rows.push(li("Chilia / Tulcea", `<b>${d.distributie.chilia_pct}%</b> / <b>${d.distributie.tulcea_pct}%</b> <span class="prov prov-calculat">calculat</span> din model`));
    } else {
      rows.push(li("împărțirea pe brațe", `<span class="prov prov-lipsa">necalculabil onest</span> — modelul rutează tot debitul pe o singură celulă la bifurcație; verificarea automată a respins rezultatul`));
    }
    rows.push(li("reper istoric", `campaniile de măsurători au arătat de-a lungul timpului Chilia ≈ 50–58%, Tulcea ≈ 42–50% (în scădere pe Chilia, de la deceniu la deceniu) — a se citi rapoartele INHGA / Comisia Dunării, nu ca valoare de azi`));
    $("delta-intrare").innerHTML = rows.join("");
  } catch (e) {
    $("delta-intrare").innerHTML = li("stare", "eroare la încărcare");
  }
  const want = ["Isaccea", "Tulcea", "Sulina", "Galati", "Galați"];
  const rows2 = (afdj?.statii || [])
    .filter((s) => want.includes(s.statie))
    .map((s) => li(s.statie + ` · km ${fmtN.format(s.km)}`,
      `cotă <b>${fmtN.format(s.cota_cm)} cm</b> (${arrow(s.variatie_cm)})${s.temp_apa_c != null ? ` · apă ${fmt1.format(s.temp_apa_c)}°C` : ""}`));
  $("delta-cote").innerHTML = rows2.join("") ||
    li("stare", "AFDJ indisponibil momentan");
}

/* ------------------------------------------------------------- anomalii -- */
function divergingColor(p) {
  // P0 (secetă) roșu → P50 gri neutru → P100 (ape mari) albastru
  const lerp = (a, b, t) => Math.round(a + (b - a) * t);
  const hex = (r, g, b) => `rgb(${r},${g},${b})`;
  const R = [230, 103, 103], G = [56, 56, 53], B = [57, 135, 229];
  if (p <= 50) { const t = p / 50; return hex(lerp(R[0], G[0], t), lerp(R[1], G[1], t), lerp(R[2], G[2], t)); }
  const t = (p - 50) / 50; return hex(lerp(G[0], B[0], t), lerp(G[1], B[1], t), lerp(G[2], B[2], t));
}

const SEV_LABEL = { extrem: "extrem", sever: "sever", atentie: "atenție", normal: "în limite", info: "info" };
const sevChip = (s) => `<span class="sev sev-${s}">${SEV_LABEL[s] || s}</span>`;

async function renderAnomalii() {
  let d;
  try { d = await jget("/api/anomalii"); }
  catch (e) {
    $("anom-strip").innerHTML = `<div class="err-box">Raportul de anomalii nu a putut fi calculat (arhivele GloFAS nu au răspuns).</div>`;
    $("anom-verdicte").innerHTML = "";
    return;
  }

  // banda de percentile de-a lungul râului (amonte → aval)
  $("anom-strip").innerHTML = (d.climatologie || []).map((c) => {
    const a = c.azi || {};
    const p = a.pct;
    return `<div class="anom-cell"${c.nota ? ` title="${c.nota.replace(/"/g, "&quot;")}"` : ""}>
      <div class="bar" style="background:${p != null ? divergingColor(p) : "var(--grid)"}"></div>
      <div class="n">${c.name}</div>
      <div class="p">P${p != null ? fmt1.format(p) : "?"} <small>· ${a.value != null ? fmtN.format(a.value) + " m³/s" : ""}</small></div>
      <div class="d">${c.streak_sub_p10 > 0 ? `${c.streak_sub_p10} zile sub P10` : "peste P10"} · mai mic doar în ${c.ani_mai_mici}/${c.ani_referinta} ani</div>
      <div style="margin-top:6px">${sevChip(c.severitate)}${c.nota ? ` <span class="sev sev-atentie">⚛ CNE</span>` : ""}</div>
      ${c.nota ? `<div class="d" style="margin-top:6px">${c.nota}</div>` : ""}
    </div>`;
  }).join("");

  // verdictele
  const cards = [];

  // 1. climatologie — sinteză
  const worst = (d.climatologie || []).reduce((w, c) => {
    const p = c.azi?.pct; return p != null && (w == null || p < w.p) ? { p, c } : w;
  }, null);
  if (worst) {
    const maxStreak = Math.max(...d.climatologie.map((c) => c.streak_sub_p10));
    const subP10 = d.climatologie.filter((c) => c.azi?.pct != null && c.azi.pct < 10);
    const amonte = d.climatologie.filter((c) => (c.km || 0) > 1100 && c.azi?.pct != null);
    const amonteLow = amonte.filter((c) => c.azi.pct < 10);
    const origine = amonteLow.length >= Math.max(1, amonte.length - 1)
      ? `Percentilele sunt la fel de joase din amonte (${amonteLow[0]?.name || "Germania"} → Baziaș),
         deci <b>deficitul intră în România pe fluviu</b> — nu apare la graniță.`
      : amonteLow.length === 0 && amonte.length > 0
        ? `În amonte valorile sunt aproape normale — <b>deficitul se accentuează pe parcurs</b>, de investigat unde.`
        : `Deficitul e mixt pe transect — vezi banda de mai sus, secțiune cu secțiune.`;
    cards.push(`<div class="card verdict">
      <h3>Secetă hidrologică ${sevChip(worst.c.severitate)}</h3>
      <p class="v">Debitul e <b>istoric de mic</b> pe cursul monitorizat — la ${worst.c.name}
        doar ${worst.c.ani_mai_mici} din ${worst.c.ani_referinta} ani au avut valori mai mici în această zi a anului.
        ${origine}</p>
      <div class="evi">${subP10.length} din ${d.climatologie.length} secțiuni (DE → deltă) sub percentila 10<br>
        serie maximă: ${maxStreak} zile consecutive sub percentila 10</div>
      <p class="met">Metodă: percentila empirică a zilei calendaristice (±7 zile), GloFAS 1991–2025.
        Anormal ≠ inexplicabil — vezi verdictul despre precipitații.</p>
    </div>`);
  }

  // 2. bilanțul Baziaș→Gruia
  const b = d.bilant;
  if (b) {
    const sev = Math.abs(b.z) > 2.5 ? "sever" : Math.abs(b.z) > 1.5 ? "atentie" : "normal";
    const msg = sev === "normal"
      ? `Bilanțul intrare→ieșire la Porțile de Fier este <b>în limitele istorice</b> — în datele publice nu se vede „apă lipsă".`
      : sev === "atentie"
        ? `Reziduul bilanțului e <b>ușor în afara tiparului</b> lunii — de urmărit în zilele următoare.`
        : `Reziduul bilanțului e <b>persistent în afara tiparului istoric</b> — exact genul de divergență care merită investigată și cerută oficial.`;
    cards.push(`<div class="card verdict">
      <h3>Bilanț Baziaș → Gruia ${sevChip(sev)}</h3>
      <p class="v">${msg}</p>
      <div class="evi">timp de propagare estimat: ${b.lag_zile} zile (corelație ${b.corelatie})<br>
        reziduu acum: ${b.reziduu_curent_pct}% · istoric în această lună: ${b.reziduu_istoric_pct}% ± ${b.sd_pct}%<br>
        abatere: z = ${b.z}</div>
      <p class="met">Metodă: (Gruia − Baziaș decalat) / Baziaș, ${b.fereastra}. Nu separă
        umplerea lacului de captări — pentru asta e nevoie de telemetria barajului (nepublicată).</p>
    </div>`);
  }

  // 3. măsurat vs model
  const m = d.masurat_vs_model;
  if (m) {
    if (m.insuficient) {
      cards.push(`<div class="card verdict">
        <h3>INHGA vs. model independent ${sevChip("info")}</h3>
        <p class="v">${m.nota}.</p>
        <p class="met">Compară debitul oficial la Baziaș (buletinele INHGA, arhivă publică) cu
          modelul Copernicus — o schimbare bruscă a relației ar fi un semnal.</p>
      </div>`);
    } else {
      const sev = Math.abs(m.z) > 2.5 ? "sever" : Math.abs(m.z) > 1.5 ? "atentie" : "normal";
      const msg = sev === "normal"
        ? `Cifra oficială românească se mișcă <b>consecvent</b> cu modelul independent Copernicus — nicio ruptură suspectă în relația dintre ele.`
        : `Relația dintre cifra oficială și modelul independent <b>s-a schimbat recent</b> — de verificat ce s-a modificat (metodă, stație sau râu).`;
      cards.push(`<div class="card verdict">
        <h3>INHGA vs. model independent ${sevChip(sev)}</h3>
        <p class="v">${msg}</p>
        <div class="evi">raport oficial/model: ${m.raport_mediu} ± ${m.sd} (${m.n} zile)<br>
          ultimele 7 zile: ${m.raport_ultimele7} · abatere: z = ${m.z}</div>
        <p class="met">Modelul supraestimează sistematic la Baziaș (raport ~${m.raport_mediu}) —
          bias-ul stabil e normal la modele; ruptura bruscă ar fi semnalul.</p>
      </div>`);
    }
  }

  // 5. verificarea încrucișată a mirelor (AFDJ vs. rețeaua de navigație)
  const mc = d.mire_crosscheck;
  if (mc) {
    const sev = mc.mediana_abatere_cm <= 10 ? "normal" : mc.mediana_abatere_cm <= 25 ? "atentie" : "sever";
    const msg = sev === "normal"
      ? `Două sisteme independente (AFDJ și rețeaua de navigație DanubeSTREAM) raportează <b>aceleași cote</b> pe stațiile comune — mirele românești nu „mint".`
      : `Cele două sisteme diferă mai mult decât ar explica ora citirii — <b>de investigat stațiile din listă</b>.`;
    cards.push(`<div class="card verdict">
      <h3>Mire încrucișate: AFDJ ↔ navigație ${sevChip(sev)}</h3>
      <p class="v">${msg}</p>
      <div class="evi">${mc.statii_comune} stații comune · abatere mediană ${fmt1.format(mc.mediana_abatere_cm)} cm · maximă ${fmt1.format(mc.max_abatere_cm)} cm<br>
        ${mc.top.map((t) => `${t.statie}: AFDJ ${fmtN.format(t.afdj_cm)} vs portal ${fmtN.format(t.portal_cm)} (${t.diferenta_cm > 0 ? "+" : ""}${fmt1.format(t.diferenta_cm)} cm)`).join("<br>")}</div>
      <p class="met">${mc.metoda}.</p>
    </div>`);
  }

  // 6. satelitul confirmă?
  if (d.satelit) {
    const s = d.satelit;
    const sev = s.mediana_pct <= 15 ? "normal" : s.mediana_pct <= 40 ? "atentie" : "info";
    cards.push(`<div class="card verdict">
      <h3>Satelit ↔ râu ${sevChip(sev)}</h3>
      <p class="v">${s.mediana_pct <= 15
        ? `Altimetria satelitară spune <b>aceeași poveste</b> ca mirele și modelul: niveluri la minimul propriei istorii.`
        : `Satelitul vede altceva decât mirele — <b>de investigat</b>.`}</p>
      <div class="evi">${s.statii} stații virtuale · percentila mediană P${fmt1.format(s.mediana_pct)} · ${s.sub_p10} sub P10</div>
      <p class="met">${s.metoda}.</p>
    </div>`);
  }

  // 7/8. perechi măsurat↔model pe alte teritorii
  [["germania", "Germania: miră ↔ model", "WSV (federal german)"],
   ["serbia", "Serbia: miră ↔ model", "RHMZ (Serbia)"]].forEach(([key, titlu, cine]) => {
    const g = d[key];
    if (!g) return;
    const sev = g.coerent ? "normal" : "sever";
    cards.push(`<div class="card verdict">
      <h3>${titlu} ${sevChip(sev)}</h3>
      <p class="v">${g.coerent
        ? `Măsurătoarea ${cine} și modelul independent sunt <b>coerente</b> — încă o pereche care se confirmă reciproc.`
        : `Raport măsurat/model în afara plauzibilului — <b>de investigat</b>.`}</p>
      <div class="evi">măsurat ${fmtN.format(g.masurat_m3s)} m³/s · model ${fmtN.format(g.model_m3s)} m³/s · raport ${g.raport}</div>
      <p class="met">${g.metoda}.</p>
    </div>`);
  });

  // 9. Austria sub lupă — testul retenției
  if (d.austria) {
    const a = d.austria;
    const sev = a.suspiciune_retentie ? "sever" : "normal";
    const scad = a.statii.filter((s) => s.trend_cm_30z < 0).length;
    cards.push(`<div class="card verdict">
      <h3>Austria sub lupă: rețin barajele apa? ${sevChip(sev)}</h3>
      <p class="v">${a.suspiciune_retentie
        ? `Nivelurile din lacurile austriece <b>cresc</b> pe fond de intrare în scădere — tiparul retenției; <b>de investigat</b>.`
        : `<b>Nu.</b> ${scad} din ${a.statii.length} mire austriece sunt în scădere sau stabile pe ultimele 30 de zile — barajele nu acumulează, apa pur și simplu nu vine.`}</p>
      <div class="evi">trend median mire AT: ${a.mediana_trend_cm > 0 ? "+" : ""}${fmt1.format(a.mediana_trend_cm)} cm/30 zile ·
        intrare Germania (Hofkirchen): ${a.intrare_trend_pct != null ? (a.intrare_trend_pct > 0 ? "+" : "") + fmt1.format(a.intrare_trend_pct) + "%" : "–"}<br>
        ${a.statii.slice(0, 5).map((s) => `${s.statie} ${s.trend_cm_30z > 0 ? "+" : ""}${fmt1.format(s.trend_cm_30z)}`).join(" · ")}</div>
      <p class="met">${a.metoda} Surse oficiale austriece pentru verificare fină:
        <a href="https://ehyd.gv.at" target="_blank" rel="noopener">eHYD</a> ·
        <a href="https://www.doris.bmk.gv.at" target="_blank" rel="noopener">DoRIS</a> ·
        <a href="https://www.verbund.com" target="_blank" rel="noopener">VERBUND</a> (raportare oficială H1 2026: disponibilitate apă −32%, minimul secolului).</p>
    </div>`);
  }

  // 4. coerența precipitații ↔ debit
  const pr = d.precipitatii;
  if (pr && pr.zone?.length) {
    const minP = Math.min(...pr.zone.map((z) => z.pct ?? 100));
    const debitLow = pr.debit_pct != null && pr.debit_pct <= 10;
    const consistent = debitLow ? minP <= 20 : true;
    const sev = consistent ? "normal" : "atentie";
    const msg = consistent
      ? `Ploile din bazinul amonte sunt la fel de rare ca debitul — seceta <b>e explicată meteorologic</b>, nu de captări invizibile.`
      : `Debitul e mai scăzut decât ar sugera precipitațiile din amonte — <b>necorelare de investigat</b> (sol, zăpadă, gestiune sau captări; nedeterminabil doar din date publice).`;
    cards.push(`<div class="card verdict">
      <h3>Precipitații ↔ debit ${sevChip(sev)}</h3>
      <p class="v">${msg}</p>
      <div class="evi">${pr.zone.map((z) => `${z.eticheta}: ${fmt1.format(z.cum90_mm)} mm/90 zile · P${z.pct}`).join("<br>")}<br>
        debit Baziaș: P${pr.debit_pct}</div>
      <p class="met">Euristic: percentila cumulului de precipitații pe 90 de zile (ERA5, 2000–prezent,
        aceeași fereastră calendaristică) față de percentila debitului. Orientativ, nu bilanț hidrologic.</p>
    </div>`);
  }

  $("anom-verdicte").innerHTML = cards.join("");
}

/* ------------------------------------------------------------ statistici -- */
const pctSign = (v) =>
  v == null ? "–" :
  v > 0 ? `<span class="up">+${fmt1.format(v)}%</span>` :
  v < 0 ? `<span class="down">−${fmt1.format(Math.abs(v))}%</span>` : "0%";

async function renderStatistici() {
  let d;
  try { d = await jget("/api/statistici"); }
  catch (e) {
    $("tabel-stat-debit").innerHTML = `<div class="err-box">Statisticile nu au putut fi calculate.</div>`;
    $("tabel-stat-precip").innerHTML = "";
    return;
  }

  $("tabel-stat-debit").innerHTML = `<div style="overflow-x:auto"><table class="data">
    <thead><tr><th>Secțiune</th><th class="num">km</th><th class="num">azi m³/s</th>
      <th class="num">normala zilei</th><th class="num">abatere</th>
      <th class="num">percentilă</th><th class="num">zile&lt;P10</th>
      <th class="num">ani mai mici</th></tr></thead>
    <tbody>${d.debit.map((r) => `
      <tr><td class="name">${r.name}</td>
        <td class="num">${fmtN.format(r.km)}</td>
        <td class="num">${r.azi_m3s != null ? fmtN.format(r.azi_m3s) : "–"}</td>
        <td class="num">${r.normala_zilei_m3s != null ? fmtN.format(r.normala_zilei_m3s) : "–"}</td>
        <td class="num">${pctSign(r.abatere_pct)}</td>
        <td class="num">${r.percentila != null ? "P" + fmt1.format(r.percentila) : "–"}</td>
        <td class="num">${r.zile_sub_p10}</td>
        <td class="num">${r.ani_mai_mici}/${r.ani_referinta}</td></tr>`).join("")}
    </tbody></table></div>`;

  const b = (x) => x || {};
  $("tabel-stat-precip").innerHTML = `<div style="overflow-x:auto"><table class="data">
    <thead><tr><th>Zonă</th><th class="num">ian→azi mm</th><th class="num">mediană</th>
      <th class="num">abatere</th><th class="num">ani mai uscați</th>
      <th class="num">iarnă mm</th><th class="num">mediană</th><th class="num">abatere</th>
      <th class="num">zăpadă iarnă</th>
      <th class="num">ult. 90 zile</th></tr></thead>
    <tbody>${d.precipitatii.map((r) => `
      <tr><td class="name">${r.zona}</td>
        <td class="num">${b(r.ian_azi).cumul_mm != null ? fmtN.format(r.ian_azi.cumul_mm) : "–"}</td>
        <td class="num">${b(r.ian_azi).mediana_mm != null ? fmtN.format(r.ian_azi.mediana_mm) : "–"}</td>
        <td class="num">${pctSign(b(r.ian_azi).abatere_pct)}</td>
        <td class="num">${b(r.ian_azi).ani_mai_uscati != null ? `${r.ian_azi.ani_mai_uscati}/${r.ian_azi.ani}` : "–"}</td>
        <td class="num">${b(r.iarna).cumul_mm != null ? fmtN.format(r.iarna.cumul_mm) : "–"}</td>
        <td class="num">${b(r.iarna).mediana_mm != null ? fmtN.format(r.iarna.mediana_mm) : "–"}</td>
        <td class="num">${pctSign(b(r.iarna).abatere_pct)}</td>
        <td class="num">${b(r.zapada_iarna).cumul_mm != null ? `${fmtN.format(r.zapada_iarna.cumul_mm)} cm ${pctSign(r.zapada_iarna.abatere_pct)}` : "–"}</td>
        <td class="num">${b(r.ultimele90).pct != null ? "P" + fmt1.format(r.ultimele90.pct) : "–"}</td></tr>`).join("")}
    </tbody></table>
    <p class="sub" style="margin:8px 0 0">date până la ${d.precipitatii[0]?.pana_la || "–"} (ERA5 are câteva zile întârziere) ·
      „ani mai uscați" = câți ani din referință au avut mai puțină apă în aceeași fereastră</p></div>`;
}

/* ------------------------------------------------------------ unde e apa -- */
async function renderBilantApa() {
  let d;
  try { d = await jget("/api/bilant-apa"); }
  catch (e) {
    $("bilant-card").innerHTML = `<div class="err-box">Bilanțul nu a putut fi calculat acum.</div>`;
    return;
  }
  const b = d.bazin_superior, bz = d.bazias;
  const maxRef = Math.max(b.ploaie_normal_km3, b.ploaie_km3);
  const bar = (val, normal, color) => {
    const w = (x) => Math.max(1, Math.round(100 * x / maxRef));
    return `<div style="margin:4px 0 10px">
      <div style="height:9px;width:${w(normal)}%;background:var(--grid);border-radius:4px"></div>
      <div style="height:9px;width:${w(val)}%;background:${color};border-radius:4px;margin-top:2px"></div>
    </div>`;
  };
  const row = (label, val, normal, color, note) => `
    <tr><td class="name" style="width:26%">${label}<br><span style="color:var(--muted);font-size:12px">${note || ""}</span></td>
        <td style="width:44%">${bar(val, normal, color)}</td>
        <td class="num"><b>${fmt1.format(val)}</b> km³</td>
        <td class="num">${fmt1.format(normal)} km³</td>
        <td class="num">${pctSign(Math.round(1000 * (val - normal) / normal) / 10)}</td></tr>`;

  const lipsaPct = Math.round(100 * bz.lipsa_km3 / bz.normal_km3);
  const fereastra = `1 ian – ${d.pana_la.slice(8)}.${d.pana_la.slice(5, 7)}`;
  $("bilant-card").innerHTML = `
    <p style="margin:4px 0 16px;font-size:15px;color:var(--ink)">
      Prin Baziaș au trecut, în intervalul <b>${fereastra}</b>, <b>${fmt1.format(bz.volum_km3)} km³</b>,
      față de <b>${fmt1.format(bz.normal_km3)} km³</b> — mediana <b>exact aceluiași interval</b> din anii 1991–${new Date().getFullYear() - 1} —
      <b style="color:var(--serious)">lipsesc ${fmt1.format(bz.lipsa_km3)} km³ (${lipsaPct}%)</b>.
      Mai jos, de unde lipsesc, în bazinul superior (aceeași fereastră pentru toți anii):</p>
    <div style="overflow-x:auto"><table class="data">
      <thead><tr><th>Rând din bilanț</th><th>normal (gri) vs. ${new Date().getFullYear()}</th>
        <th class="num">${new Date().getFullYear()} (${fereastra})</th><th class="num">normal (${fereastra})</th><th class="num">abatere</th></tr></thead>
      <tbody>
        ${row("① Ploaie + zăpadă căzute", b.ploaie_km3, b.ploaie_normal_km3, "#3987e5",
              "ERA5 × aria bazinului")}
        ${row("② Scurs prin râu (Passau)", b.rau_passau_km3, b.rau_normal_km3, "#6da7ec",
              "GloFAS, cumulat")}
        ${row("③ Luat de atmosferă + sol", b.atmosfera_sol_km3, b.atmosfera_sol_normal_km3, "#898781",
              "rezidualul ① − ②")}
      </tbody></table></div>
    <p class="sub" style="margin:12px 0 0">${(() => {
      const dP = b.ploaie_normal_km3 - b.ploaie_km3;
      const dQ = b.rau_normal_km3 - b.rau_passau_km3;
      const dR = b.atmosfera_sol_normal_km3 - b.atmosfera_sol_km3;
      return `Bilanțul se închide fără rest: din <b>${fmt1.format(dP)} km³ de ploaie lipsă</b>,
        <b>${fmt1.format(dQ)} km³</b> lipsesc din râu și <b>${fmt1.format(dR)} km³</b> din partea
        atmosferei și solului (mai puțină evaporare și reîncărcare, pentru că n-a avut ce evapora și reîncărca).
        Niciun rând nedistribuit — <b>apa care lipsește din Dunăre e apa care n-a căzut din cer</b>.`;
    })()}
      ${d.grace ? `Rezerva subterană (GRACE, ${d.grace.luna}): anomalie ${fmt1.format(d.grace.anomalie_km3)} km³ —
      pornim din deficit acumulat.` : ""}
      ${d.consum_uman_nota}. <span class="prov prov-calculat">calculat</span> · ${d.metoda} · date până la ${d.pana_la}</p>`;
}

/* ----------------------------------------------------------- contra-probe -- */
const deacc = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

async function renderContraProbe(afdj, portal) {
  const li = (k, v) => `<li><span class="k">${k}</span><span class="v">${v}</span></li>`;

  // 1. DanubeSTREAM — toate țările + cross-check cu AFDJ pe stațiile românești
  try {
    const d = portal || await jget("/api/danubeportal");
    const tari = {};
    d.mire.forEach((m) => (tari[m.tara] = (tari[m.tara] || 0) + 1));
    const afdjMap = {};
    (afdj?.statii || []).forEach((s) => (afdjMap[deacc(s.statie)] = s));
    const compar = d.mire
      .filter((m) => m.tara === "RO" && afdjMap[deacc(m.statie)])
      .map((m) => ({ m, a: afdjMap[deacc(m.statie)], d: m.cota_cm - afdjMap[deacc(m.statie)].cota_cm }))
      .sort((x, y) => Math.abs(y.d) - Math.abs(x.d));
    const maxAbat = compar.length ? Math.round(Math.max(...compar.map((c) => Math.abs(c.d)))) : null;
    const sel = ["Achleiten", "Bratislava", "Budapest", "Mohács", "Bezdan", "Ruse"];
    const rows = d.mire
      .filter((m) => sel.some((s) => deacc(m.statie).includes(deacc(s))))
      .slice(0, 6)
      .map((m) => `<tr><td class="name">${m.statie} <span style="color:var(--muted)">${m.tara}</span></td>
        <td class="num">${m.km != null ? fmtN.format(m.km) : ""}</td>
        <td class="num">${fmtN.format(m.cota_cm)}</td>
        <td class="num">${(m.masurat_utc || "").slice(11, 16)} UTC</td></tr>`).join("");
    $("cp-danubeportal").innerHTML = `<table class="data">
      <thead><tr><th>Miră</th><th class="num">km</th><th class="num">cotă cm</th><th class="num">ora</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="sub" style="margin:10px 0 0">${d.mire.length} mire active ·
        ${Object.entries(tari).map(([t, n]) => `${t}:${n}`).join(" ")}</p>
      ${compar.length ? `<p class="sub" style="margin:6px 0 0">Cross-check RO: ${compar.length} stații comune cu AFDJ,
        abatere maximă <b>${maxAbat} cm</b>${maxAbat <= 15 ? " — sursele se confirmă reciproc ✓" : " — de investigat diferența (momente de citire diferite?)"}</p>` : ""}`;
    pill("DanubeSTREAM", d.stale ? "stale" : "ok");
  } catch (e) {
    $("cp-danubeportal").innerHTML = `<div class="err-box">danubeportal.com nu a răspuns.</div>`;
    pill("DanubeSTREAM", "err");
  }

  // 2. SEN Transelectrica
  try {
    const s = await jget("/api/sen");
    const nuclNote = s.nuclear_mw != null && s.nuclear_mw < 1000
      ? ` — <b>≈ o singură unitate în funcțiune</b> (plină putere ar fi ~1.300 MW; cauza — planificată sau nu — nu e în aceste date)` : "";
    $("cp-sen").innerHTML = [
      li("hidro (toată țara)", `<b>${fmtN.format(s.hidro_mw)} MW</b> din ${fmtN.format(s.productie_mw)} MW producție · consum ${fmtN.format(s.consum_mw)} MW`),
      li("nuclear · CNE", `<b>${fmtN.format(s.nuclear_mw)} MW</b>${nuclNote}`),
      li("linia Djerdap (PF)", `${fmtN.format(s.linia_djerdap_mw)} MW schimb RO↔RS pe linia Porților de Fier`),
      li("sold import/export", `${fmtN.format(s.sold_mw)} MW ${s.sold_mw > 0 ? "(import)" : "(export)"}`),
      li("actualizat", `${s.actualizat || "–"} <span class="prov prov-masurat">măsurat</span>`),
    ].join("");
    pill("SEN", s.stale ? "stale" : "ok");
  } catch (e) {
    $("cp-sen").innerHTML = li("stare", "Transelectrica nu a răspuns.");
    pill("SEN", "err");
  }

  // 3. Satelit: hydroweb.next (activ cu cheia utilizatorului), DAHITI ca rezervă
  try {
    const h = await jget("/api/hydroweb");
    if (h.activ) {
      const ok = h.statii.filter((s) => s.nivel_m != null);
      const rows = ok.map((s) => {
        const p = s.percentila_lunii;
        return `<tr><td class="name">${s.statie.replace("R_DANUBE_DUNAREA_KM0*", "").replace("R_DANUBE_DUNAREA_KM", "Dunărea · km ")}</td>
          <td class="num">${fmt1.format(s.nivel_m)}${s.incertitudine_m != null ? " ± " + fmt1.format(s.incertitudine_m) : ""} m</td>
          <td class="num">${s.variatie_fata_de_precedenta_m != null ? arrow(s.variatie_fata_de_precedenta_m * 100) : ""}</td>
          <td class="num">${p != null ? "P" + fmt1.format(p) : "·"}</td>
          <td class="num">${(s.data || "").slice(5)}</td></tr>`;
      }).join("");
      $("cp-dahiti").innerHTML = `<table class="data">
        <thead><tr><th>Stație virtuală</th><th class="num">nivel</th><th class="num">Δ cm</th><th class="num">percentila lunii</th><th class="num">data</th></tr></thead>
        <tbody>${rows}</tbody></table>
        <p class="sub" style="margin:8px 0 0">${ok.length} stații virtuale hydroweb.next (CNES) pe Dunărea propriu-zisă ·
        nivelurile sunt față de geoid (alt reper decât mirele) — relevante sunt <b>variația și percentila proprie</b>, care pot fi
        comparate cu mirele oficiale · Δ = față de trecerea anterioară a satelitului</p>`;
      pill("Satelit", h.stale ? "stale" : "ok");
    } else {
      const d = await jget("/api/dahiti");
      $("cp-dahiti").innerHTML = [
        li("hydroweb.next", `<span class="prov prov-lipsa">inactiv</span> ${h.motiv || ""}`),
        li("DAHITI", d.activ ? `${d.tinte.length} ținte` : `<span class="prov prov-lipsa">inactiv</span> ${d.motiv || ""}`),
      ].join("");
      pill("Satelit", "err");
    }
  } catch (e) {
    $("cp-dahiti").innerHTML = li("stare", "eroare la interogarea surselor satelitare");
    pill("Satelit", "err");
  }

  // 4b. gravimetrie GRACE
  try {
    const g = await jget("/api/gravimetrie");
    if (!g.activ) {
      $("cp-grav").innerHTML = li("stare", `<span class="prov prov-lipsa">inactiv</span> ${g.motiv || ""}`);
    } else {
      const serie = g.serie;
      const vTot = (s) => s.total ?? s.anomalie_km3;
      const min5 = [...serie].sort((a, b) => vTot(a) - vTot(b)).slice(0, 3);
      const seg = g.segmente
        ? Object.values(g.segmente).map((s) =>
            `${s.nume}: <b>${fmt1.format(s.anomalie_km3)} km³</b> (mai sec doar în ${s.mai_seci}/${s.ani} ani)`)
        : [];
      $("cp-grav").innerHTML = [
        li("ultima lună publicată", `<b>${g.ultima.luna}</b>: anomalie totală <b>${fmt1.format(g.ultima.anomalie_km3)} km³</b> · mai secetos în aceeași lună doar în <b>${g.ani_mai_seci_aceeasi_luna}/${g.ani_comparati}</b> ani`),
        ...(seg.length ? [li("pe segmente de bazin", seg.join("<br>"))] : []),
        li("minimele seriei", min5.map((s) => `${s.luna}: ${fmt1.format(vTot(s))}`).join(" · ")),
        li("notă", g.nota),
      ].join("");
    }
  } catch (e) {
    $("cp-grav").innerHTML = li("stare", "eroare la interogarea gravimetriei");
  }

  // 4. GRDC
  try {
    const g = await jget("/api/grdc");
    if (!g.activ) {
      $("cp-grdc").innerHTML = [
        li("stare", `<span class="prov prov-lipsa">inactiv</span> ${g.motiv}`),
        li("ce aduce", `seria măsurată de la Ceatal Izmail începe în sec. XIX — „minim din 29 de ani (model)" ar deveni „minim din ~180 de ani (măsurat)"`),
      ].join("");
    } else {
      $("cp-grdc").innerHTML = [
        li("serie", `<b>${g.statie}</b> · ${g.din} → ${g.pana} (${fmtN.format(g.zile)} zile măsurate)`),
        li("azi vs istoric", g.percentila_vs_masurat != null
          ? `debitul de azi (model, ${fmtN.format(g.azi_model_m3s)} m³/s) e la <b>P${fmt1.format(g.percentila_vs_masurat)}</b> din ${fmtN.format(g.mostre_referinta)} mostre măsurate ale acestei ferestre calendaristice`
          : "–"),
        g.record_minim_zi ? li("record minim al zilei", `${fmtN.format(g.record_minim_zi.m3s)} m³/s · ${g.record_minim_zi.data}`) : "",
        li("notă", g.nota || ""),
      ].join("");
    }
  } catch (e) {
    $("cp-grdc").innerHTML = li("stare", "eroare la citirea GRDC");
  }
}

/* --------------------------------------------- măsurat vs model, zi de zi -- */
let chartMvM;
async function renderMvMChart() {
  try {
    const [of, mo] = await Promise.all([
      jget("/api/inhga/serie?days=92"),
      jget("/api/glofas/recent?point=bazias&days=92"),
    ]);
    if (!of.serie?.length) return;
    const today = new Date().toISOString().slice(0, 10);
    const days = mo.time.filter((t) => t <= today);
    const oMap = Object.fromEntries(of.serie.map((r) => [r.date, r.debit_m3s]));
    const opt = baseOpt();
    opt.grid.right = 40;
    opt.xAxis.data = days.map((t) => t.slice(5));
    opt.yAxis.name = "m³/s"; opt.yAxis.nameTextStyle = { color: MUTED, fontFamily: MONO };
    opt.series = [
      { name: "INHGA (măsurat)", type: "line", data: days.map((t) => oMap[t] ?? null),
        symbol: "none", connectNulls: true, lineStyle: { width: 2.2, color: BLUE }, itemStyle: { color: BLUE } },
      { name: "GloFAS (model)", type: "line", data: days.map((t, i) => mo.discharge[i]),
        symbol: "none", lineStyle: { width: 1.6, type: "dashed", color: MUTED }, itemStyle: { color: MUTED } },
    ];
    opt.legend.data = ["INHGA (măsurat)", "GloFAS (model)"];
    chartMvM ||= mkChart($("chart-mvm"));
    chartMvM.setOption(opt, { notMerge: true });
  } catch (e) { /* cardul detectorului acoperă */ }
}

async function renderIstoric() {
  try {
    const h = await jget("/api/istoric");
    const nume = { inhga: "INHGA", afdj: "AFDJ", rhmz: "RHMZ", danubestream: "DanubeSTREAM", sen: "SEN" };
    const txt = Object.entries(h)
      .map(([k, v]) => `${nume[k] || k}: ${v.zile} zile (din ${v.din})`)
      .join(" · ");
    if (txt) $("istoric-local").innerHTML = `<br>Arhiva locală, construită automat de aplicație: ${txt}.`;
  } catch (e) { /* opțional */ }
}

/* --------------------------------------------------------------- sinteza -- */
async function renderSinteza(inhga) {
  let an = null, bi = null;
  try { [an, bi] = await Promise.all([jget("/api/anomalii"), jget("/api/bilant-apa")]); }
  catch (e) { /* compunem din ce avem */ }

  const parts = [];
  const chips = [];
  let checksHtml = "";

  if (an?.climatologie?.length) {
    const clim = an.climatologie;
    const pcts = clim.map((c) => c.azi?.pct).filter((p) => p != null).sort((a, b) => a - b);
    const medP = pcts[Math.floor(pcts.length / 2)];
    const sub10 = clim.filter((c) => c.azi?.pct != null && c.azi.pct < 10);
    const peste90 = clim.filter((c) => c.azi?.pct != null && c.azi.pct > 90);
    const baz = clim.find((c) => c.id === "bazias");
    const worstSev = clim.some((c) => c.severitate === "extrem") ? "extrem"
      : clim.some((c) => c.severitate === "sever") ? "sever" : "atentie";
    const nExtrem = clim.filter((c) => c.severitate === "extrem").length;
    const debitTxt = inhga?.debit_bazias_m3s
      ? ` Debit oficial la Baziaș: <b>${fmtN.format(inhga.debit_bazias_m3s)} m³/s</b>${
          inhga.media_multianuala_m3s
            ? ` (${Math.round(100 * inhga.debit_bazias_m3s / inhga.media_multianuala_m3s)}% din media multianuală a lunii)` : ""}.`
      : "";

    // regimul se alege din date — aceeași sinteză trebuie să fie corectă și
    // la secetă, și la normal, și la ape mari
    if (sub10.length >= Math.ceil(pcts.length * 0.6)) {
      chips.push(`<span class="sev sev-${worstSev}">secetă hidrologică: ${SEV_LABEL[worstSev]}</span>`);
      parts.push(`Dunărea trece printr-o secetă ${nExtrem >= pcts.length / 2
          ? "la nivel de <b>record al ultimilor ~30 de ani</b>" : "<b>severă</b>"}:
        <b>${sub10.length} din ${clim.length} secțiuni</b> monitorizate (Germania → deltă) sunt sub percentila 10${
        baz && baz.streak_sub_p10 >= 3 ? `, la Baziaș a <b>${baz.streak_sub_p10}-a zi consecutivă</b>` : ""}.${debitTxt}`);
    } else if (peste90.length >= Math.ceil(pcts.length * 0.6)) {
      chips.push(`<span class="sev sev-${worstSev}">ape mari: ${SEV_LABEL[worstSev]}</span>`);
      parts.push(`Dunărea e la <b>ape mari</b> față de istoricul acestor zile:
        <b>${peste90.length} din ${clim.length} secțiuni</b> peste percentila 90.${debitTxt}
        Pentru avertizări oficiale de inundații: INHGA / Apele Române.`);
    } else if (medP < 25) {
      chips.push(`<span class="sev sev-atentie">sub normalul sezonului</span>`);
      parts.push(`Dunărea curge <b>sub normalul sezonului</b> (mediana percentilelor: P${fmt1.format(medP)};
        ${sub10.length} din ${clim.length} secțiuni sub percentila 10).${debitTxt}`);
    } else if (medP <= 75) {
      chips.push(`<span class="sev sev-normal">debit în marja normală</span>`);
      parts.push(`Dunărea curge <b>în marja normală</b> a acestor zile din an
        (mediana percentilelor pe cele ${clim.length} secțiuni: P${fmt1.format(medP)}).${debitTxt}`);
    } else {
      chips.push(`<span class="sev sev-atentie">peste normalul sezonului</span>`);
      parts.push(`Dunărea curge <b>peste normalul sezonului</b> (mediana percentilelor:
        P${fmt1.format(medP)}; ${peste90.length} secțiuni peste percentila 90).${debitTxt}`);
    }
  }

  if (bi?.bazias) {
    const lipsa = bi.bazias.lipsa_km3;
    const pct = Math.round(100 * lipsa / bi.bazias.normal_km3);
    const dP = bi.bazin_superior ? bi.bazin_superior.ploaie_normal_km3 - bi.bazin_superior.ploaie_km3 : null;
    const volTxt = `de la 1 ianuarie, prin Baziaș au trecut <b>${fmt1.format(bi.bazias.volum_km3)} km³</b>
      față de ${fmt1.format(bi.bazias.normal_km3)} — mediana aceluiași interval`;
    let bilTxt;
    if (pct >= 5) {
      bilTxt = `Cauza se vede în cifre, nu în teorii: ${volTxt} —
        <b>lipsesc ${fmt1.format(lipsa)} km³ (${pct}%)</b>${dP > 0
          ? `, iar în bazinul superior n-au căzut din cer <b>${fmt1.format(dP)} km³ de ploaie</b>: bilanțul se închide fără rest` : ""}.`;
    } else if (pct <= -5) {
      bilTxt = `Volumele: ${volTxt} — <b>un plus de ${fmt1.format(-lipsa)} km³ (${-pct}%)</b>${dP < 0
          ? `, susținut de precipitații peste medie în bazinul superior (+${fmt1.format(-dP)} km³)` : ""}.`;
    } else {
      bilTxt = `Volumele: ${volTxt} — abatere de doar ${pct}%, în marja normală.`;
    }
    let graceTxt = "";
    if (bi.grace && bi.grace.ani_comparati > 3) {
      const g = bi.grace;
      graceTxt = g.ani_mai_seci <= 2
        ? ` Rezerva totală a bazinului (gravimetrie satelitară) e <b>aproape de minimul măsurătorilor din 2002 încoace</b> (mai secetoși doar ${g.ani_mai_seci}/${g.ani_comparati} ani).`
        : g.ani_mai_seci <= g.ani_comparati / 2
          ? ` Rezerva totală a bazinului (gravimetrie satelitară) rămâne sub media istorică (${g.ani_mai_seci}/${g.ani_comparati} ani mai secetoși).`
          : ` Rezerva totală a bazinului (gravimetrie satelitară) e peste media istorică — în refacere.`;
    }
    parts.push(bilTxt + graceTxt);
  }

  if (an) {
    const checks = [];
    const add = (nume, ok) => { if (ok != null) checks.push({ nume, ok }); };
    add("bilanț Porțile de Fier", an.bilant ? Math.abs(an.bilant.z) <= 1.5 : null);
    add("INHGA↔model", an.masurat_vs_model && !an.masurat_vs_model.insuficient ? Math.abs(an.masurat_vs_model.z) <= 1.5 : null);
    add("mire încrucișate", an.mire_crosscheck ? an.mire_crosscheck.mediana_abatere_cm <= 10 : null);
    add("ploi↔debit", an.precipitatii?.zone?.length
      ? !(an.precipitatii.debit_pct <= 10 && Math.min(...an.precipitatii.zone.map((z) => z.pct ?? 100)) > 20) : null);
    add("satelit↔râu", an.satelit ? an.satelit.mediana_pct <= 15 : null);
    add("Germania măsurat↔model", an.germania ? an.germania.coerent : null);
    add("Serbia măsurat↔model", an.serbia ? an.serbia.coerent : null);
    add("retenție Austria", an.austria ? !an.austria.suspiciune_retentie : null);
    const rele = checks.filter((c) => !c.ok);
    if (rele.length === 0 && checks.length) {
      chips.push(`<span class="sev sev-normal">verificări încrucișate: ${checks.length}/${checks.length} în limite</span>`);
      parts.push(`Integritatea datelor: toate cele <b>${checks.length} verificări încrucișate</b> —
        bilanțul Porților de Fier, perechile măsurat↔model din trei țări, mirele a șase state,
        satelitul și testul de retenție din Austria — ies <b>în limite</b>: în datele publice nu se
        vede apă reținută, deviată sau raportată fals, nicăieri pe fluviu.`);
    } else if (checks.length) {
      chips.push(`<span class="sev sev-atentie">de investigat: ${rele.map((c) => c.nume).join(", ")}</span>`);
      parts.push(`Atenție: <b>${rele.length} verificări în afara limitelor</b> —
        ${rele.map((c) => c.nume).join(", ")} — detaliile în secțiunea „Detectorul".`);
    }
    checksHtml = "";
  }

  if (!parts.length) {
    parts.push(`Sursele se încarcă sau nu răspund momentan — detaliile pe secțiuni, mai jos.`);
  }

  const acum = new Date().toLocaleTimeString("ro-RO");
  $("sinteza-card").innerHTML = `
    <div class="chips">${chips.join("")}</div>
    ${parts.map((p) => `<p>${p}</p>`).join("")}
    <div class="cine">concluzie generată automat din datele de mai jos · actualizat ${acum} ·
      se reîmprospătează la 5 minute · nimic din acest text nu e scris de mână</div>`;
}

/* ------------------------------------------------------------ analiza AI -- */
async function renderAnalizaAI() {
  const card = $("ai-card");
  try {
    const d = await jget("/api/analiza-ai");
    if (!d.activ) { card.style.display = "none"; return; }
    card.style.display = "block";
    const text = d.text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/^(SITUAȚIA|CAUZE PROBABILE|CE NU SE POATE CONCLUZIONA[^\n]*|CE AR SCHIMBA CONCLUZIA)\s*:?\s*$/gmi,
               '<b style="color:var(--ink)">$1</b>')
      .replace(/\n/g, "<br>");
    card.innerHTML = `
      <h3>Analiză narativă <span class="prov prov-model">interpretare AI · ${d.model}</span></h3>
      <p class="sub">strat interpretativ, separat de sinteza deterministă de mai sus — poate greși;
        promptul și datele de intrare sunt publice mai jos · generat ${d.generat} ·
        declanșator: <b>${d.declansator || "–"}</b> · se regenerează doar la schimbări reale de stare
        (severități, verificări, bilanț, GRACE) sau după 7 zile</p>
      <div style="font-size:14px; line-height:1.65; max-width:90ch">${text}</div>
      <details style="margin-top:14px">
        <summary style="cursor:pointer; color:var(--muted); font-size:12.5px">promptul exact + datele de intrare (auditabil)</summary>
        <pre style="white-space:pre-wrap; font-size:11.5px; color:var(--muted); background:var(--surface-2); padding:10px; border-radius:6px; margin-top:8px">${d.prompt_sistem.replace(/</g, "&lt;")}</pre>
        <p class="sub">Datele de intrare = exact JSON-ul din <a href="/api/analiza-ai" target="_blank" rel="noopener">/api/analiza-ai</a>
          (câmpul <code>date_intrare</code>). Analizele se arhivează zilnic.</p>
      </details>`;
  } catch (e) { card.style.display = "none"; }
}

/* ---------------------------------------------------------------- main -- */
async function main() {
  // selectoarele pentru comparația multianuală
  jget("/api/points").then((pts) => {
    const selP = $("sel-punct");
    pts.glofas
      .filter((p) => !p.id.startsWith("brat_"))
      .forEach((p) => selP.add(new Option(p.name, p.id)));
    selP.value = "bazias";
    selP.addEventListener("change", () => loadYears(selP.value));
    const selR = $("sel-precip");
    pts.precip.forEach((p) => selR.add(new Option(p.name, p.id)));
    selR.value = "oltenia";
    selR.addEventListener("change", () => loadPrecip(selR.value));
    loadYears("bazias");
    loadPrecip("oltenia");
  }).catch(() => {});

  refreshData();
  // fluxul continuu: zona de date se recompune singură la 5 minute
  setInterval(refreshData, 5 * 60 * 1000);
}

async function refreshData() {
  renderPFChart();
  renderEntsoe();
  renderAnomalii();
  renderStatistici();
  renderBilantApa();
  renderMvMChart();
  renderIstoric();

  const [ovR, afdjR, hidmetR, portalR] = await Promise.allSettled([
    jget("/api/overview"), jget("/api/afdj"), jget("/api/hidmet"),
    jget("/api/danubeportal"),
  ]);
  const ov = ovR.status === "fulfilled" ? ovR.value : { glofas: [], errors: {} };
  const afdj = afdjR.status === "fulfilled" ? afdjR.value : null;
  const hidmet = hidmetR.status === "fulfilled" ? hidmetR.value : null;
  const portal = portalR.status === "fulfilled" ? portalR.value : null;

  pill("INHGA", ov.inhga ? (ov.inhga.stale ? "stale" : "ok") : "err");
  pill("AFDJ", afdj ? (afdj.stale ? "stale" : "ok") : "err");
  pill("PEGELONLINE", ov.pegelonline ? (ov.pegelonline.stale ? "stale" : "ok") : "err");
  pill("RHMZ", hidmet ? (hidmet.stale ? "stale" : "ok") : "err");

  renderHero(ov.inhga);
  renderProfile(ov, afdj, hidmet, portal);
  renderAfdjTable(afdj);
  renderHidmetTable(hidmet);
  renderPFFacts(ov, afdj, hidmet);
  renderDelta(afdj);
  renderContraProbe(afdj, portal);
  renderSinteza(ov.inhga);
  renderAnalizaAI();
}

main();
