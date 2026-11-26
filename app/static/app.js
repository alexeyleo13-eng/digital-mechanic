"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_RU = { normal: "Норма", attention: "Требует внимания", quarantine: "Карантин" };
const URG_RU = { high: "высокий", medium: "средний", low: "низкий" };
const Q_RU = { good: "хорошее", warning: "с помехами", bad: "непригодна" };
const fmtDate = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

const state = {
  vehicles: [], vehicle: null, detail: null, conversationId: null, busy: false,
  retrieved: new Set(), manuals: {}, lastDiag: null, fleetFilter: null,
};

// ---------- API ----------
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function init() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $("#vehicle-picker").addEventListener("click", openPicker);
  $("#btn-diagnose").addEventListener("click", () => send("Получить результат диагностики" + sessionSuffix()));
  $("#v-trend").addEventListener("click", () => state.vehicle && openHistory(state.vehicle));
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#input").value); } });
  $("#input").addEventListener("input", autosize);
  $("#sheet").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheet(); });
  $("#chat").addEventListener("click", onChatClick);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  try {
    const cfg = await api("/api/config");
    $("#mode").textContent = cfg.provider === "offline" ? "демо без LLM" : (cfg.model || cfg.provider);
    if (!cfg.services.ml || !cfg.services.toir) $("#mode").textContent += " · сервисы недоступны";
  } catch { $("#mode").textContent = "нет связи"; }
  $("#mode2").textContent = $("#mode").textContent;
  window.addEventListener("hashchange", () => { const id = decodeURIComponent(location.hash.slice(1)); if (id && id !== state.vehicle) selectVehicle(id); });
  state.vehicles = await api("/api/vehicles");
  const saved = (() => { try { return localStorage.getItem("vehicle"); } catch { return null; } })();
  const fromHash = decodeURIComponent(location.hash.slice(1));
  const pick = state.vehicles.find((v) => v.vehicle_id === fromHash) || state.vehicles.find((v) => v.vehicle_id === saved);
  if (pick) selectVehicle(pick.vehicle_id);
  renderChips();
}

function showView(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  if (name === "fleet") loadFleet();
}

// ---------- vehicle ----------
function openPicker() {
  const body = el("div");
  body.innerHTML = `<input class="search" placeholder="Госномер, гаражный номер, марка, двигатель" autofocus><div class="list"></div>`;
  const list = $(".list", body);
  const draw = (q) => {
    q = q.trim().toLowerCase();
    const groups = {};
    state.vehicles.filter((v) => !q || [v.vehicle_id, v.garage_no, v.make, v.model, v.engine_model].join(" ").toLowerCase().includes(q))
      .forEach((v) => (groups[v.engine_model] ||= []).push(v));
    list.innerHTML = Object.entries(groups).map(([eng, vs]) => `<div class="group-h">${esc(eng)}</div><ul class="vlist">${vs.map((v) => `
      <li data-id="${esc(v.vehicle_id)}"><span class="dot ${v.status}"></span><span style="flex:1"><b>${esc(v.vehicle_id)}</b>
      <small>${esc(v.garage_no)} · ${esc(v.make)} ${esc(v.model || "")} · ${STATUS_RU[v.status]}</small></span></li>`).join("")}</ul>`).join("")
      || `<p class="muted">Ничего не найдено</p>`;
  };
  draw("");
  $("input", body).addEventListener("input", (e) => draw(e.target.value));
  list.addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) { closeSheet(); selectVehicle(li.dataset.id); } });
  openSheet("Выбор машины", body);
  setTimeout(() => $("input", body).focus(), 50);
}

async function selectVehicle(id) {
  const d = await api("/api/vehicles/" + encodeURIComponent(id));
  state.vehicle = id; state.detail = d; state.conversationId = null; state.retrieved = new Set(); state.lastDiag = null;
  state.manuals = Object.fromEntries(d.manuals.map((m) => [m.manual_id, m]));
  try { localStorage.setItem("vehicle", id); } catch {}
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  const v = d.vehicle;
  $("#v-dot").className = "dot " + d.status;
  $("#v-plate").textContent = v.vehicle_id;
  $("#v-sub").textContent = `${v.garage_no} · ${v.make} ${v.model || ""} · ${v.engine_model}`;
  const sel = $("#session-select");
  sel.innerHTML = d.sessions.slice().reverse().map((s, i) => `<option value="${s.session_id}">${i === 0 ? "посл. " : ""}${s.session_id}</option>`).join("");
  sel.disabled = false;
  ["#btn-diagnose", "#input", "#send"].forEach((s) => ($(s).disabled = false));
  drawTrendButton(d);
  $("#chat").innerHTML = "";
  addAgentNote(`Машина <b>${esc(v.vehicle_id)}</b> (${esc(v.make)} ${esc(v.model || "")}, двигатель <b>${esc(v.engine_model)}</b>). ` +
    `Ответы — по руководствам: ${d.manuals.map((m) => esc(m.designation)).join("; ")}.`);
  renderChips();
}

function sessionSuffix() {
  const sel = $("#session-select");
  return sel.selectedIndex > 0 ? ` (проверка ${sel.value})` : "";
}

function sparkline(points, w = 80, h = 34) {
  if (!points.length) return "";
  const xs = points.map((_, i) => (points.length === 1 ? w / 2 : (i * (w - 8)) / (points.length - 1) + 4));
  const ys = points.map((p) => h - 4 - p * (h - 8));
  const path = xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const color = last >= 0.8 ? "var(--bad)" : last >= 0.5 ? "var(--warn)" : "var(--ok)";
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="0" x2="${w}" y1="${h - 4 - 0.5 * (h - 8)}" y2="${h - 4 - 0.5 * (h - 8)}" stroke="var(--line)" stroke-dasharray="2 2"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>${xs.map((x, i) => `<circle cx="${x}" cy="${ys[i]}" r="2.5" fill="${color}"/>`).join("")}</svg>`;
}

function drawTrendButton(d) {
  const pts = d.trend.points.map((p) => p[1]);
  const b = $("#v-trend");
  b.hidden = !pts.length;
  b.innerHTML = sparkline(pts) + `<small>${esc(d.trend.direction)}</small>`;
}

// ---------- chat ----------
function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function scrollDown() { window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }); }
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; }
function addAgentNote(html) { $("#chat").append(el("div", "msg agent", `<p>${html}</p>`)); }

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy || !state.vehicle) return;
  state.busy = true; setBusy(true);
  $("#input").value = ""; autosize();
  $("#empty")?.remove();
  $("#chat").append(el("div", "msg user", esc(text)));
  const turn = el("div", "turn"); $("#chat").append(turn);
  const steps = el("div", "steps"); turn.append(steps);
  let bubble = null, raw = "";
  const ensureBubble = () => { if (!bubble) { bubble = el("div", "msg agent"); turn.append(bubble); } return bubble; };
  scrollDown();
  try {
    const resp = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vehicle_id: state.vehicle, message: text, conversation_id: state.conversationId }) });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.statusText);
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "conversation") state.conversationId = ev.id;
        else if (ev.type === "status") steps.append(el("div", "step", esc(ev.text)));
        else if (ev.type === "tool") {
          steps.querySelectorAll(".run").forEach((s) => s.classList.remove("run"));
          const q = ev.args?.query ? `: «${esc(ev.args.query)}»` : ev.args?.pdf_page ? `: стр. ${ev.args.pdf_page}` : "";
          steps.append(el("div", "step run", esc(ev.label) + q));
          if (bubble && raw.trim()) { bubble = null; raw = ""; }
        } else if (ev.type === "card") { steps.querySelectorAll(".run").forEach((s) => s.classList.remove("run")); renderCard(turn, ev); }
        else if (ev.type === "delta") {
          raw += ev.text;
          if (raw.trim()) { ensureBubble().innerHTML = md(raw) + '<span class="typing"></span>'; }
        } else if (ev.type === "error") turn.append(el("div", "msg error", esc(ev.text)));
        else if (ev.type === "done") steps.querySelectorAll(".run").forEach((s) => s.classList.remove("run"));
        scrollDown();
      }
    }
  } catch (e) {
    turn.append(el("div", "msg error", "Ошибка связи: " + esc(e.message)));
  } finally {
    if (bubble) bubble.innerHTML = md(raw);
    state.busy = false; setBusy(false); renderChips(); scrollDown();
  }
}

function setBusy(b) { ["#btn-diagnose", "#send"].forEach((s) => ($(s).disabled = b || !state.vehicle)); }

function renderCard(turn, ev) {
  const d = ev.data;
  if (ev.kind === "search") { d.hits.forEach((h) => state.retrieved.add(`${h.manual_id}:${h.pdf_page}`)); return; }
  if (ev.kind === "read") { state.retrieved.add(`${d.manual_id}:${d.pdf_page}`); return; }
  if (ev.kind === "diagnosis") { state.lastDiag = d; turn.append(diagnosisCard(d)); return; }
  if (ev.kind === "ticket_draft") { turn.append(ticketCard(d)); return; }
  if (ev.kind === "ticket_created") { turn.append(createdCard(d)); refreshVehicle(); return; }
  if (ev.kind === "unverified") {
    turn.append(el("div", "msg error", `⚠️ Не подтверждено руководством: <b>${d.values.map(esc).join(", ")}</b>. Не используйте эти значения без проверки по документации.`));
    return;
  }
  if (ev.kind === "history") return;
}

function diagnosisCard(d) {
  const r = d.ml_result, a = d.assessment;
  const cls = r.status === "anomaly" ? `anomaly ${a.urgency}` : r.status;
  const label = { normal: "Норма", anomaly: "Отклонение", check_failed: "Сбой проверки" }[r.status];
  const title = r.status === "anomaly" ? `${r.anomaly_title}` : r.status === "normal" ? "Значимых отклонений не обнаружено" : "Запись непригодна — повторите проверку";
  const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
  const metric = (name, val, frac) => `<div class="metric"><small>${name}</small><b>${val}</b>${frac != null ? `<div class="bar"><i style="width:${Math.round(frac * 100)}%"></i></div>` : ""}</div>`;
  const c = el("div", "card verdict " + cls);
  c.innerHTML = `<div class="label">${label}${r.status === "anomaly" ? ` · ${esc(r.anomaly_type)} · риск ${esc(a.risk)}` : ""}</div>
    <div class="title">${esc(title)}</div>
    <div>${esc(r.description)}</div>
    ${r.status !== "check_failed" ? `<div class="metrics">${metric("Отклонение", r.anomaly_score?.toFixed(2) ?? "—", r.anomaly_score)}${metric("Уверенность", pct(r.confidence), r.confidence)}${metric("Запись", Q_RU[r.signal_quality] || r.signal_quality)}</div>` : ""}
    <div class="action">${esc(a.action)}</div>
    ${(a.notes || []).length ? `<ul class="notes">${a.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    ${a.compatibility && !a.compatibility.compatible ? `<p class="muted">${esc(a.compatibility.reason)}</p>` : ""}
    ${a.basis ? `<details class="basis"><summary>Приоритет: ${URG_RU[a.urgency]} — на чём основан</summary><ul>${a.basis.map((b) => `<li>${esc(b)}</li>`).join("")}</ul></details>` : ""}
    <div class="meta">${esc(d.vehicle.vehicle_id)} · ${esc(d.vehicle.engine_model)} · проверка ${esc(r.session_id)} · модель ${esc(r.model_version)}</div>`;
  return c;
}

function ticketCard(d) {
  const t = d.ticket;
  const c = el("div", "card ticket");
  c.innerHTML = `<h4>Заявка на дефектовку — черновик</h4>
    ${d.existing?.length ? `<p class="msg error">По этой проверке уже есть заявка: ${d.existing.map(esc).join(", ")}</p>` : ""}
    <dl><dt>ТС</dt><dd><b>${esc(t.vehicle_id)}</b>, проверка ${esc(t.session_id)}</dd>
    <dt>Неисправность</dt><dd>${esc(t.anomaly_type)} · ${esc(d.anomaly_title)}</dd>
    <dt>Диагноз</dt><dd>${esc(t.conclusion)}</dd>
    <dt>Приоритет</dt><dd><span class="pill ${t.urgency}">${URG_RU[t.urgency]}</span></dd>
    <dt>Работы</dt><dd><ul>${t.operations.map((o) => `<li>${esc(o)}</li>`).join("")}</ul></dd>
    <dt>Запчасти</dt><dd>${t.spare_parts.length ? `<ul>${t.spare_parts.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : "—"}</dd>
    <dt>Источники</dt><dd>${t.references.map((r, i) => citeChip(d.ref_ids[i], r.pdf_page)).join(" ")}</dd></dl>
    <div class="row-btns"><button class="edit">Изменить</button><button class="go">Отправить в ТОиР</button></div>`;
  $(".go", c).addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Отправляю…";
    try {
      const out = await api("/api/ticket/confirm", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: state.conversationId, draft_id: d.draft_id }) });
      $(".row-btns", c).replaceWith(createdCard(out)); refreshVehicle();
    } catch (err) { e.target.disabled = false; e.target.textContent = "Отправить в ТОиР"; c.append(el("div", "msg error", esc(err.message))); }
  });
  $(".edit", c).addEventListener("click", () => { $("#input").value = "Измени заявку: "; $("#input").focus(); });
  return c;
}

function createdCard(d) { return el("div", "card created", `✅ Заявка <b>${esc(d.ticket_id)}</b> создана в ТОиР`); }

async function refreshVehicle() {
  if (!state.vehicle) return;
  state.detail = await api("/api/vehicles/" + encodeURIComponent(state.vehicle));
}

// ---------- markdown + citations ----------
function citeChip(mid, page) {
  const m = state.manuals[mid];
  const ok = state.retrieved.has(`${mid}:${page}`);
  const name = m ? m.short : mid;
  return `<button class="cite${ok ? "" : " unverified"}" data-m="${esc(mid)}" data-p="${page}" title="${esc(m ? m.designation : mid)}${ok ? "" : " — страница не подтверждена поиском"}">📄 ${esc(name)}, стр. ${page}</button>`;
}

function inline(s) {
  return esc(s)
    .replace(/\[{1,2}\s*([a-z][a-z0-9]*_[a-z0-9_]+)\s*:\s*([\d,;\s]+?)\s*\]{1,2}/g, (_, m, ps) => ps.split(/[,;\s]+/).filter(Boolean).map((p) => citeChip(m, +p)).join(""))
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])_(.+?)_(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function md(text) {
  const lines = text.replace(/\r/g, "").split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const ln of lines) {
    const t = ln.trim();
    let m;
    if (!t) { flushPara(); flushList(); continue; }
    if ((m = t.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); html += `<h3>${inline(m[1])}</h3>`; continue; }
    if ((m = t.match(/^[-*•]\s+(.*)/)) || (m = t.match(/^\d+[.)]\s+(.*)/))) {
      flushPara();
      const kind = /^\d/.test(t) ? "ol" : "ul";
      if (list !== kind) { flushList(); html += `<${kind}>`; list = kind; }
      html += `<li>${inline(m[1])}</li>`; continue;
    }
    if (/^\|/.test(t)) { flushList(); if (/^\|[\s:-]+\|/.test(t.replace(/[^|:\s-]/g, "x")) && !/[a-zа-я0-9]/i.test(t)) continue;
      para.push(t.split("|").filter(Boolean).map((c) => c.trim()).join(" · ")); flushPara(); continue; }
    flushList(); para.push(t);
  }
  flushPara(); flushList();
  return html;
}

async function onChatClick(e) {
  const b = e.target.closest(".cite");
  if (!b) return;
  const mid = b.dataset.m, page = b.dataset.p;
  const body = el("div", "", `<p class="muted">Загрузка…</p>`);
  openSheet(`${state.manuals[mid]?.designation || mid} — стр. ${page}`, body);
  try {
    const s = await api(`/api/source/${encodeURIComponent(mid)}/${page}`);
    body.innerHTML = `${b.classList.contains("unverified") ? `<p class="msg error">Агент сослался на страницу, которую не получал поиском. Проверьте по руководству.</p>` : ""}
      <p class="muted">${esc(s.title)}. Номер страницы — по PDF.</p>
      ${s.pdf_available ? `<a class="link-btn" href="/manuals/${encodeURIComponent(mid)}.pdf#page=${page}" target="_blank" rel="noopener">Открыть PDF на странице ${page}</a>` : ""}
      <div class="page-text">${esc(s.text || "Текст страницы не извлечён (рисунок).")}</div>`;
  } catch (err) { body.innerHTML = `<p class="msg error">${esc(err.message)}</p>`; }
}

// ---------- chips ----------
function renderChips() {
  const box = $("#chips");
  if (!state.vehicle) { box.innerHTML = ""; return; }
  const d = state.lastDiag;
  let chips;
  if (!d) chips = [["Получить результат диагностики", true], ["Покажи историю проверок"], ["Какое давление масла должно быть?"]];
  else if (d.ml_result.status === "anomaly") chips = [["Создать заявку на дефектовку", true], ["Почему это опасно?"], ["Можно ли своим ходом перегнать на базу?"], ["Как проверить по руководству?"], ["Какие запчасти могут понадобиться?"]];
  else if (d.ml_result.status === "check_failed") chips = [["Как правильно сделать запись?"], ["Покажи историю проверок"]];
  else chips = [["Покажи историю проверок"], ["Что проверить на ближайшем ТО?"]];
  box.innerHTML = chips.map(([t, strong]) => `<button class="chip${strong ? " strong" : ""}">${esc(t)}</button>`).join("");
  box.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => send(c.textContent + (c.textContent.startsWith("Получить") ? sessionSuffix() : ""))));
}

// ---------- sheet ----------
function openSheet(title, body) {
  $("#sheet-title").textContent = title;
  const b = $("#sheet-body"); b.innerHTML = ""; b.append(body);
  $("#sheet").hidden = false; document.body.style.overflow = "hidden";
}
function closeSheet() { $("#sheet").hidden = true; document.body.style.overflow = ""; }

// ---------- fleet (главный механик) ----------
async function loadFleet() {
  const f = await api("/api/fleet");
  const tiles = [["quarantine", "Карантин"], ["attention", "Внимание"], ["normal", "Норма"]];
  $("#tiles").innerHTML = tiles.map(([k, n]) => `<button class="tile ${k}${state.fleetFilter === k ? " sel" : ""}" data-k="${k}"><b>${f.counts[k]}</b><small>${n}</small></button>`).join("");
  $("#tiles").querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => { state.fleetFilter = state.fleetFilter === t.dataset.k ? null : t.dataset.k; loadFleet(); }));
  const rows = f.vehicles.filter((v) => !state.fleetFilter || v.status === state.fleetFilter);
  $("#fleet-list").innerHTML = rows.map((v) => {
    const l = v.last || {};
    const what = l.status === "anomaly" ? `${l.anomaly_title}` : l.status === "check_failed" ? "сбой записи — повторить" : "норма";
    return `<li class="fleet-item" data-id="${esc(v.vehicle_id)}"><span class="dot ${v.status}"></span>
      <div class="grow"><b>${esc(v.vehicle_id)}</b> <span class="muted">${esc(v.garage_no)}</span>
      <small>${esc(v.make)} ${esc(v.model || "")} · ${esc(v.engine_model)}</small><small>${esc(what)}${l.checked_at ? " · " + fmtDate(l.checked_at) : ""}${v.tickets ? ` · заявок: ${v.tickets}` : ""}</small></div>
      <div class="score"><b>${l.anomaly_score != null ? l.anomaly_score.toFixed(2) : "—"}</b>${esc(v.trend)}</div></li>`;
  }).join("");
  $("#fleet-list").querySelectorAll(".fleet-item").forEach((li) => li.addEventListener("click", () => openHistory(li.dataset.id)));
}

function bigChart(sessions) {
  const pts = sessions.filter((s) => s.anomaly_score != null);
  if (pts.length < 2) return `<p class="muted">Недостаточно проверок для графика.</p>`;
  const w = 320, h = 150, px = 28, py = 14;
  const x = (i) => px + (i * (w - px - 10)) / (pts.length - 1), y = (v) => h - py - v * (h - 2 * py);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.anomaly_score)}`).join(" ");
  const grid = [0, 0.5, 0.8, 1].map((g) => `<line x1="${px}" x2="${w - 10}" y1="${y(g)}" y2="${y(g)}" stroke="var(--line)" ${g === 0.5 || g === 0.8 ? 'stroke-dasharray="3 3"' : ""}/><text x="2" y="${y(g) + 4}" font-size="10" fill="var(--ink-3)">${g.toFixed(1)}</text>`).join("");
  const dots = pts.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.anomaly_score)}" r="4" fill="${p.anomaly_score >= 0.8 ? "var(--bad)" : p.anomaly_score >= 0.5 ? "var(--warn)" : "var(--ok)"}"/><text x="${x(i)}" y="${h - 1}" font-size="9" text-anchor="middle" fill="var(--ink-3)">${p.session_id}</text>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Динамика anomaly_score">${grid}<path d="${path}" fill="none" stroke="var(--ink-2)" stroke-width="2"/>${dots}</svg></div>`;
}

async function openHistory(id) {
  const d = await api("/api/vehicles/" + encodeURIComponent(id));
  const v = d.vehicle;
  const body = el("div");
  const delta = d.trend.delta != null ? ` (${d.trend.delta > 0 ? "+" : ""}${d.trend.delta})` : "";
  body.innerHTML = `<p><span class="dot ${d.status}" style="display:inline-block"></span> <b>${STATUS_RU[d.status]}</b> · ${esc(v.make)} ${esc(v.model || "")} · ${esc(v.engine_model)}</p>
    <h3>Динамика anomaly_score: ${esc(d.trend.direction)}${delta}</h3>${bigChart(d.sessions)}
    <h3>Проверки</h3><ul class="hist">${d.sessions.slice().reverse().map((s) => `<li><span class="when">${fmtDate(s.checked_at)}<br>${esc(s.session_id)}</span>
      <span>${s.status === "anomaly" ? `<b>${esc(s.anomaly_title)}</b> · ${s.anomaly_score.toFixed(2)} · <span class="pill ${s.urgency}">${URG_RU[s.urgency]}</span>` : s.status === "normal" ? "Норма" : "Сбой записи"}<br><span class="muted">${esc(s.description)}</span></span></li>`).join("")}</ul>
    <h3>Заявки</h3>${d.tickets.length ? `<ul class="hist">${d.tickets.map((t) => `<li><span class="when">${fmtDate(t.created_at)}</span><span><b>${esc(t.ticket_id)}</b> · ${esc(t.anomaly_type)} · <span class="pill ${t.urgency}">${URG_RU[t.urgency]}</span><br><span class="muted">${esc(t.conclusion)}</span></span></li>`).join("")}</ul>` : `<p class="muted">Заявок нет</p>`}
    <button class="link-btn" id="to-chat">Открыть в чате механика</button>`;
  $("#to-chat", body).addEventListener("click", () => { closeSheet(); showView("mechanic"); if (state.vehicle !== id) selectVehicle(id); });
  openSheet(`${v.vehicle_id} · ${v.garage_no}`, body);
}

init();
