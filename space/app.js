/* Formal-MAS static Space: plays back the recorded runs built by scripts/build_space.py.
 *
 * Nothing is decided here. Every step sentence, status, technical detail, card and result was
 * produced by the demo's own Python code (demo/steps.py, timeline.py, catalog.py) for every
 * frame of every replay, in both languages; this file only chooses which frame to show and
 * when (the recorded gaps divided by the speed, each capped at max_gap_s, as demo/replay.py).
 * Texts of the page come from data/i18n.json (exported from demo/i18n.py).
 */
"use strict";

(() => {
  const DATA = "data/";
  const S = {
    lang: "es", texts: null, site: null, replays: {}, tab: "examples",
    example: null, speed: { single: "5", pair: "5" },
    single: { rep: null, pos: -1 }, pair: { reps: null, pos: [-1, -1] },
  };
  const $ = (id) => document.getElementById(id);

  // -- texts ---------------------------------------------------------------------------------
  function t(key, fields) {
    const texts = S.texts.texts;
    let s = (texts[S.lang] || {})[key];
    if (s === undefined) s = texts[S.texts.default][key];
    if (s === undefined) return key;
    return fields ? s.replace(/\{(\w+)\}/g, (m, k) => (k in fields ? String(fields[k]) : m)) : s;
  }

  function storedLang() {
    try {
      const q = new URLSearchParams(location.search).get("lang");
      if (q) return q;
      return localStorage.getItem("fm-lang");
    } catch (e) {
      return null;
    }
  }

  function saveLang(lang) {
    try { localStorage.setItem("fm-lang", lang); } catch (e) { /* private mode: not remembered */ }
  }

  function renderMath(root) {
    if (!window.katex) return; // KaTeX did not load: the TeX source stays readable
    root.querySelectorAll(".tex:not([data-done])").forEach((el) => {
      try {
        window.katex.render(el.textContent, el, { displayMode: el.dataset.display === "1", throwOnError: false });
      } catch (e) { /* leave the source */ }
      el.dataset.done = "1";
    });
  }

  function setHTML(el, html) {
    el.innerHTML = html;
    el.querySelectorAll("table").forEach((table) => {
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      table.replaceWith(wrap);
      wrap.appendChild(table);
    });
    renderMath(el);
  }

  // -- static texts, language, tabs --------------------------------------------------------------
  function applyTexts() {
    document.documentElement.lang = S.lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === S.lang)));
    document.querySelectorAll(".speed button").forEach((b) => { b.textContent = t("speed." + b.dataset.speed); });
    document.querySelectorAll("#ex-list button").forEach((b) => {
      const ex = S.site.examples.find((x) => x.key === b.dataset.key);
      b.querySelector(".title").textContent = ex.title[S.lang] || ex.key;
    });
  }

  function setLang(lang) {
    if (!S.texts.langs.includes(lang)) lang = S.texts.default;
    S.lang = lang;
    saveLang(lang);
    applyTexts();
    setHTML($("results"), S.site.results[lang] || "");
    renderExampleCard();
    views.single.reset();
    views.a.reset();
    views.b.reset();
    renderSingle();
    renderPair();
  }

  function setTab(name) {
    S.tab = name;
    document.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    $("panel-examples").hidden = name !== "examples";
    $("panel-results").hidden = name !== "results";
    try { history.replaceState(null, "", name === "results" ? "#results" : location.pathname + location.search); } catch (e) { /* file:// */ }
  }

  // -- one steps + result panel -----------------------------------------------------------------
  class StepsView {
    constructor(stepsEl, resultEl, idleKey) {
      this.stepsEl = stepsEl;
      this.resultEl = resultEl;
      this.idleKey = idleKey;
      this.reset();
    }

    reset() {
      this.key = null;
      this.rows = [];
      this.shownResult = null;
    }

    build(rep) {
      this.stepsEl.textContent = "";
      this.rows = rep.steps.map((step) => {
        const row = document.createElement("div");
        row.innerHTML = '<span class="fm-icon"></span><div class="fm-body"><div class="fm-name"></div>' +
          '<div class="fm-text"></div><details class="fm-tech" hidden><summary></summary><div class="tech"></div></details></div>';
        this.stepsEl.appendChild(row);
        return { step, el: row, icon: row.querySelector(".fm-icon"), name: row.querySelector(".fm-name"),
                 text: row.querySelector(".fm-text"), details: row.querySelector("details"),
                 summary: row.querySelector("summary"), tech: row.querySelector(".tech"), techCount: -1 };
      });
      this.key = rep.key + "|" + S.lang;
    }

    render(rep, pos) {
      if (!rep || pos < 0) {
        this.reset();
        this.stepsEl.innerHTML = "";
        const p = document.createElement("p");
        p.className = "idle";
        p.textContent = t(this.idleKey);
        this.stepsEl.appendChild(p);
        this.resultEl.innerHTML = '<div class="fm-result fm-idle"></div>';
        this.resultEl.firstChild.textContent = t("result.idle");
        return;
      }
      if (this.key !== rep.key + "|" + S.lang) this.build(rep);
      const n = rep.t.length;
      const frame = rep.frames[S.lang][pos];
      const shown = Math.min(pos, n);
      frame.forEach(([status, sentence], k) => {
        const r = this.rows[k];
        r.el.className = "fm-step fm-" + status;
        r.icon.textContent = S.site.icons[status] || "○";
        r.name.textContent = t("step." + r.step);
        r.text.textContent = sentence;
        const parts = [];
        for (let i = 0; i < shown; i++) if (rep.owner[i] === k && rep.tech[i]) parts.push(rep.tech[i]);
        if (parts.length !== r.techCount) {
          r.techCount = parts.length;
          r.details.hidden = parts.length === 0;
          r.summary.textContent = t("steps.details");
          setHTML(r.tech, parts.join("\n"));
        }
      });
      const final = pos === n + 1;
      const resultKey = rep.key + "|" + S.lang + "|" + (final ? "final" : "running");
      if (this.shownResult !== resultKey) {
        this.shownResult = resultKey;
        if (final) {
          setHTML(this.resultEl, rep.result[S.lang]);
        } else {
          this.resultEl.innerHTML = '<div class="fm-result fm-idle"></div>';
          this.resultEl.firstChild.textContent = t("result.running");
        }
      }
    }
  }

  const views = {};

  // -- playback ----------------------------------------------------------------------------------
  /* A schedule is a list of [delay in ms, apply()]; one Player per section. */
  class Player {
    constructor(playBtn, stopBtn, bar) {
      this.playBtn = playBtn;
      this.stopBtn = stopBtn;
      this.bar = bar;
      this.timer = null;
    }

    start(schedule) {
      this.stop();
      this.playBtn.disabled = true;
      this.stopBtn.disabled = false;
      let i = 0;
      const next = () => {
        if (i >= schedule.length) {
          this.timer = null;
          this.finish();
          return;
        }
        const [delay, apply] = schedule[i];
        this.timer = setTimeout(() => {
          apply();
          i += 1;
          this.bar.style.width = (100 * i / schedule.length).toFixed(1) + "%";
          next();
        }, delay);
      };
      this.bar.style.width = "0%";
      next();
    }

    stop() {
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
      this.finish();
    }

    finish() {
      this.playBtn.disabled = false;
      this.stopBtn.disabled = true;
    }
  }

  function gapMs(dt, speed) {
    const sp = Number(speed);
    if (!(sp > 0)) return 0;
    return Math.round(1000 * Math.min(Math.max(dt, 0) / sp, S.site.max_gap_s));
  }

  function singleSchedule(rep, speed) {
    const n = rep.t.length;
    const show = (p) => () => { S.single.pos = p; renderSingle(); };
    if (!(Number(speed) > 0)) return [[0, show(n + 1)]];
    const out = [[0, show(0)]];
    let last = 0;
    for (let i = 0; i < n; i++) {
      out.push([gapMs(rep.t[i] - last, speed), show(i + 1)]);
      last = rep.t[i];
    }
    out.push([0, show(n + 1)]); // the result right after the last event
    return out;
  }

  function pairSchedule(reps, speed) {
    const ns = reps.map((r) => r.t.length);
    const finalPos = () => [ns[0] + 1, ns[1] + 1];
    if (!(Number(speed) > 0)) return [[0, () => { S.pair.pos = finalPos(); renderPair(); }]];
    // both runs on one clock (seconds since each problem_start), as demo/replay.pair_frames
    const merged = [];
    reps.forEach((r, k) => r.t.forEach((tt, i) => merged.push([tt, k, i])));
    merged.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2]);
    const counts = [0, 0];
    const out = [[0, () => { S.pair.pos = [0, 0]; renderPair(); }]];
    let last = 0;
    merged.forEach(([tt, k]) => {
      out.push([gapMs(tt - last, speed), () => {
        counts[k] += 1;
        S.pair.pos = counts.map((c, j) => (c === ns[j] ? ns[j] + 1 : c)); // a side shows its result once its events end
        renderPair();
      }]);
      last = tt;
    });
    return out;
  }

  // -- examples ----------------------------------------------------------------------------------
  function loadReplay(key) {
    if (!S.replays[key]) {
      S.replays[key] = fetch(DATA + "replays/" + encodeURIComponent(key) + ".json").then((r) => {
        if (!r.ok) throw new Error(r.status + " " + key);
        return r.json();
      });
    }
    return S.replays[key];
  }

  function renderExampleCard() {
    const rep = S.single.rep;
    if (rep) setHTML($("ex-card"), rep.card[S.lang]);
  }

  function renderSingle() { views.single.render(S.single.rep, S.single.pos); }

  function renderPair() {
    if (!S.pair.reps) return;
    const [a, b] = S.pair.reps;
    $("pair-title-a").textContent = a.side[S.lang];
    $("pair-title-b").textContent = b.side[S.lang];
    const body = $("pair-problem-body");
    if (body.dataset.lang !== S.lang) { // once per language, not on every frame
      setHTML(body, b.problem[S.lang]);
      body.dataset.lang = S.lang;
    }
    views.a.render(a, S.pair.pos[0]);
    views.b.render(b, S.pair.pos[1]);
  }

  function selectExample(key) {
    players.single.stop();
    S.example = key;
    document.querySelectorAll("#ex-list button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.key === key)));
    return loadReplay(key).then((rep) => {
      if (S.example !== key) return;
      S.single = { rep, pos: -1 };
      $("ex-progress").style.width = "0%";
      renderExampleCard();
      renderSingle();
    }).catch(showLoadError);
  }

  function buildSpeeds(which) {
    const box = document.querySelector(`[data-speed-for="${which}"]`);
    S.site.speeds.forEach((sp) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.speed = sp;
      b.setAttribute("aria-checked", String(sp === S.speed[which]));
      b.addEventListener("click", () => {
        S.speed[which] = sp;
        box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      });
      box.appendChild(b);
    });
  }

  function buildExampleList() {
    const list = $("ex-list");
    S.site.examples.forEach((ex) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.key = ex.key;
      b.innerHTML = '<span class="dot">●</span><span class="title"></span>';
      b.addEventListener("click", () => selectExample(ex.key));
      list.appendChild(b);
    });
  }

  function showLoadError() {
    const el = $("loading");
    el.hidden = false;
    el.textContent = S.texts ? t("space.load_error") : "Error";
  }

  const players = {};

  function init([texts, site]) {
    S.texts = texts;
    S.site = site;
    S.speed.single = S.speed.pair = site.default_speed;
    const wanted = storedLang();
    S.lang = texts.langs.includes(wanted) ? wanted : texts.default;

    document.querySelectorAll("[data-link]").forEach((a) => { a.href = site.links[a.dataset.link]; });
    buildExampleList();
    buildSpeeds("single");
    buildSpeeds("pair");
    views.single = new StepsView($("ex-steps"), $("ex-result"), "steps.idle_replay");
    views.a = new StepsView($("pair-steps-a"), $("pair-result-a"), "steps.idle_pair");
    views.b = new StepsView($("pair-steps-b"), $("pair-result-b"), "steps.idle_pair");
    players.single = new Player($("ex-play"), $("ex-stop"), $("ex-progress"));
    players.pair = new Player($("pair-play"), $("pair-stop"), $("pair-progress"));

    document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
    document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
    $("ex-play").addEventListener("click", () => {
      if (S.single.rep) players.single.start(singleSchedule(S.single.rep, S.speed.single));
    });
    $("ex-stop").addEventListener("click", () => players.single.stop());
    $("pair-play").addEventListener("click", () => {
      if (S.pair.reps) players.pair.start(pairSchedule(S.pair.reps, S.speed.pair));
    });
    $("pair-stop").addEventListener("click", () => players.pair.stop());

    $("loading").hidden = true;
    setTab(location.hash === "#results" ? "results" : "examples");
    setLang(S.lang);
    if (site.examples.length) selectExample(site.examples[0].key);
    if (site.pairs.length) { // the first pair (the recorded examples have one: C4 / C5)
      Promise.all(site.pairs[0].map(loadReplay)).then((reps) => {
        S.pair = { reps, pos: [-1, -1] };
        $("pair").hidden = false;
        renderPair();
      }).catch(showLoadError);
    }
  }

  function getJSON(name) {
    return fetch(DATA + name).then((r) => {
      if (!r.ok) throw new Error(r.status + " " + name);
      return r.json();
    });
  }

  Promise.all([getJSON("i18n.json"), getJSON("site.json")]).then(init).catch(showLoadError);
})();
