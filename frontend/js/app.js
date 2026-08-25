"use strict";

// ---------- tiny helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const api = async (path, opts = {}) => {
  const res = await fetch(`/api${path}`, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
};
const mediaURL = (rel) => (rel ? `/media/${rel}` : "");
const CAT_ICON = { top: "👕", bottom: "👖", dress: "👗", outerwear: "🧥", shoes: "👟", bag: "👜", accessory: "🧣" };

let META = { categories: [], seasons: [], occasions: [], laundry_statuses: [], item_statuses: [] };
let pendingImage = null; // { image, thumbnail, primary_color_hex }

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

// ---------- navigation ----------
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    $(`#view-${tab.dataset.view}`).classList.add("active");
    if (tab.dataset.view === "wardrobe") loadWardrobe();
    if (tab.dataset.view === "saved") loadSaved();
    if (tab.dataset.view === "insights") loadInsights();
  });
});

// ---------- bootstrap ----------
async function init() {
  META = await api("/meta");
  buildDropdowns();
  buildChips();
  loadWardrobe();
  wireAddForm();
  wireBuild();
}

function fillSelect(sel, values, { includeAll = false, allLabel = "All" } = {}) {
  sel.innerHTML = "";
  if (includeAll) sel.appendChild(new Option(allLabel, ""));
  values.forEach((v) => sel.appendChild(new Option(v, v)));
}

function buildDropdowns() {
  fillSelect($("#filter-category"), META.categories, { includeAll: true, allLabel: "All categories" });
  fillSelect($("#filter-laundry"), META.laundry_statuses, { includeAll: true, allLabel: "Any status" });
  fillSelect($('#item-form [name="category"]'), META.categories);
  fillSelect($('#item-form [name="laundry_status"]'), META.laundry_statuses);
  fillSelect($("#rec-occasion"), META.occasions, { includeAll: true, allLabel: "Any" });
  fillSelect($("#rec-season"), META.seasons, { includeAll: true, allLabel: "Any" });
}

function buildChips() {
  const mk = (container, values) => {
    container.innerHTML = "";
    values.forEach((v) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = v;
      chip.dataset.value = v;
      chip.addEventListener("click", () => chip.classList.toggle("on"));
      container.appendChild(chip);
    });
  };
  mk($("#seasons-chips"), META.seasons);
  mk($("#occasions-chips"), META.occasions);
}
const selectedChips = (container) => $$(".chip.on", container).map((c) => c.dataset.value);

// ---------- WARDROBE ----------
async function loadWardrobe() {
  const params = new URLSearchParams();
  const cat = $("#filter-category").value;
  const laundry = $("#filter-laundry").value;
  const q = $("#search").value.trim();
  if (cat) params.set("category", cat);
  if (laundry) params.set("laundry_status", laundry);
  if (q) params.set("q", q);
  const items = await api(`/items?${params}`);
  const grid = $("#wardrobe-grid");
  grid.innerHTML = "";
  $("#wardrobe-empty").classList.toggle("hidden", items.length > 0);
  items.forEach((it) => grid.appendChild(itemCard(it)));
}

function itemCard(it) {
  const el = document.createElement("div");
  el.className = "card";
  const thumbStyle = it.thumbnail ? `style="background-image:url('${mediaURL(it.thumbnail)}')"` : "";
  const laundryBadge = it.laundry_status !== "available"
    ? `<span class="badge warn">${it.laundry_status}</span>` : "";
  const swatch = it.primary_color_hex
    ? `<span class="swatch" style="background:${it.primary_color_hex}"></span>` : "";
  el.innerHTML = `
    <div class="thumb" ${thumbStyle}>${it.thumbnail ? "" : (CAT_ICON[it.category] || "👚")}</div>
    <div class="body">
      <p class="name">${escapeHTML(it.name)}</p>
      <div class="meta">
        <span class="badge">${it.category}</span>
        ${swatch}
        <span class="badge">worn ${it.wear_count}×</span>
        ${laundryBadge}
      </div>
    </div>
    <div class="actions">
      <button data-act="wear">Wear</button>
      <button data-act="laundry">${it.laundry_status === "available" ? "→ Laundry" : "→ Clean"}</button>
      <button data-act="delete" class="danger">✕</button>
    </div>`;
  $('[data-act="wear"]', el).onclick = async () => {
    await api(`/items/${it.id}/wear`, { method: "POST" });
    toast(`Logged wear for ${it.name}`);
    loadWardrobe();
  };
  $('[data-act="laundry"]', el).onclick = async () => {
    const next = it.laundry_status === "available" ? "laundry" : "available";
    await api(`/items/${it.id}`, { method: "PUT", body: JSON.stringify({ laundry_status: next }) });
    loadWardrobe();
  };
  $('[data-act="delete"]', el).onclick = async () => {
    if (!confirm(`Delete "${it.name}"?`)) return;
    await api(`/items/${it.id}`, { method: "DELETE" });
    toast("Deleted");
    loadWardrobe();
  };
  return el;
}

["#search", "#filter-category", "#filter-laundry"].forEach((s) =>
  $(s).addEventListener("input", debounce(loadWardrobe, 250))
);

// ---------- ADD ITEM ----------
function wireAddForm() {
  const fileInput = $("#file-input");
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    const status = $("#analyze-status");
    status.textContent = "Analyzing photo…";
    const preview = $("#preview");
    preview.classList.remove("placeholder");
    preview.textContent = "";
    preview.style.backgroundImage = `url('${URL.createObjectURL(file)}')`;
    try {
      const fd = new FormData();
      fd.append("image", file);
      const result = await api("/analyze", { method: "POST", body: fd });
      pendingImage = { image: result.image, thumbnail: result.thumbnail, primary_color_hex: result.primary_color_hex };
      if (result.primary_color_hex) {
        $('[name="primary_color_hex"]').value = result.primary_color_hex;
        $("#color-name").textContent = result.color_name ? `detected: ${result.color_name}` : "";
      }
      if (result.suggestions) {
        applySuggestions(result.suggestions);
        status.textContent = "✨ AI pre-filled fields below — please confirm";
      } else {
        status.textContent = "Photo ready ✓";
      }
    } catch (e) {
      status.textContent = "Analysis failed: " + e.message;
    }
  });

  // Live range value labels.
  [["formality_score", "formality-out"], ["comfort_score", "comfort-out"], ["warmth", "warmth-out"]]
    .forEach(([field, outId]) => {
      const inp = $(`#view-add [name="${field}"]`);
      const out = $(`#${outId}`);
      if (inp && out) inp.addEventListener("input", () => (out.textContent = inp.value));
    });
  $('[name="primary_color_hex"]').addEventListener("input", (e) => {
    $("#color-name").textContent = e.target.value;
  });

  $("#item-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const payload = {
      name: f.name.value.trim(),
      category: f.category.value,
      subcategory: f.subcategory.value.trim() || null,
      material: f.material.value.trim() || null,
      primary_color_hex: f.primary_color_hex.value,
      colors: [],
      seasons: selectedChips($("#seasons-chips")),
      occasions: selectedChips($("#occasions-chips")),
      formality_score: +f.formality_score.value,
      comfort_score: +f.comfort_score.value,
      warmth: +f.warmth.value,
      rain_safe: f.rain_safe.checked,
      purchase_price: f.purchase_price.value ? +f.purchase_price.value : null,
      laundry_status: f.laundry_status.value,
      notes: f.notes.value.trim() || null,
    };
    if (pendingImage) {
      payload.image = pendingImage.image;
      payload.thumbnail = pendingImage.thumbnail;
    }
    try {
      await api("/items", { method: "POST", body: JSON.stringify(payload) });
      toast(`Added ${payload.name}`);
      resetAddForm();
      switchTo("wardrobe");
    } catch (err) {
      toast("Error: " + err.message);
    }
  });

  $("#add-reset").addEventListener("click", resetAddForm);
}

// Pre-fill the add-item form from AI suggestions. Everything stays editable.
function applySuggestions(s) {
  const f = $("#item-form");
  if (s.category && META.categories.includes(s.category)) f.category.value = s.category;
  if (s.subcategory && !f.subcategory.value) f.subcategory.value = s.subcategory;
  if (s.formality_score) {
    f.formality_score.value = s.formality_score;
    $("#formality-out").textContent = s.formality_score;
  }
  if (Array.isArray(s.seasons)) {
    $$("#seasons-chips .chip").forEach((chip) => {
      chip.classList.toggle("on", s.seasons.includes(chip.dataset.value));
    });
  }
  if (s.notes && !f.notes.value) f.notes.value = s.notes;
}

function resetAddForm() {
  $("#item-form").reset();
  pendingImage = null;
  const p = $("#preview");
  p.style.backgroundImage = "";
  p.classList.add("placeholder");
  p.textContent = "Take or choose a photo";
  $("#analyze-status").textContent = "";
  $("#color-name").textContent = "";
  $$(".chip.on").forEach((c) => c.classList.remove("on"));
  ["formality-out", "comfort-out", "warmth-out"].forEach((id) => {
    const map = { "formality-out": 5, "comfort-out": 7, "warmth-out": 5 };
    $("#" + id).textContent = map[id];
  });
}

function switchTo(view) {
  $(`.tab[data-view="${view}"]`).click();
}

// ---------- BUILD / RECOMMEND ----------
function wireBuild() {
  $("#rec-go").addEventListener("click", async () => {
    const body = {
      occasion: $("#rec-occasion").value || null,
      season: $("#rec-season").value || null,
      temperature_c: $("#rec-temp").value ? +$("#rec-temp").value : null,
      raining: $("#rec-rain").checked,
      limit: 8,
    };
    const results = $("#rec-results");
    results.innerHTML = `<p class="muted">Thinking…</p>`;
    try {
      const outfits = await api("/recommend", { method: "POST", body: JSON.stringify(body) });
      results.innerHTML = "";
      if (!outfits.length) {
        results.innerHTML = `<p class="empty">No complete outfits found. Add more items (you need at least a top, bottom &amp; shoes, or a dress &amp; shoes) or relax the filters.</p>`;
        return;
      }
      outfits.forEach((o) => results.appendChild(outfitCard(o, { fromRec: true })));
    } catch (e) {
      results.innerHTML = `<p class="empty">Error: ${escapeHTML(e.message)}</p>`;
    }
  });
}

function barRow(label, value) {
  return `<div class="bar-row"><span class="label">${label}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${Math.round(value * 100)}%"></span></span>
    <span>${Math.round(value * 100)}</span></div>`;
}

function outfitCard(o, { fromRec = false } = {}) {
  const el = document.createElement("div");
  el.className = "outfit";
  const items = o.items || [];
  const itemsHTML = items.map((it) => {
    const style = it.thumbnail ? `style="background-image:url('${mediaURL(it.thumbnail)}')"` : "";
    return `<div class="outfit-item">
      <div class="thumb" ${style}>${it.thumbnail ? "" : (CAT_ICON[it.category] || "👚")}</div>
      <div class="cap">${escapeHTML(it.name)}</div></div>`;
  }).join("");

  const scorePct = o.score != null ? Math.round(o.score * 100) : null;
  const breakdown = o.breakdown || o.score_breakdown || {};
  const barsHTML = Object.keys(breakdown).length
    ? `<div class="bars">${["occasion", "weather", "color", "comfort", "preference", "rotation"]
        .filter((k) => k in breakdown)
        .map((k) => barRow(k, breakdown[k])).join("")}</div>` : "";

  el.innerHTML = `
    <div class="outfit-head">
      <strong>${o.name ? escapeHTML(o.name) : (o.occasion ? cap(o.occasion) + " look" : "Outfit")}</strong>
      ${scorePct != null ? `<span class="score-pill">${scorePct}</span>` : ""}
    </div>
    <div class="outfit-items">${itemsHTML}</div>
    ${o.rationale ? `<p class="rationale">${escapeHTML(o.rationale)}</p>` : ""}
    ${barsHTML}
    <div class="outfit-actions"></div>`;

  const actions = $(".outfit-actions", el);
  if (fromRec) {
    const save = mkBtn("💾 Save", "btn", async () => {
      await api("/outfits", { method: "POST", body: JSON.stringify({
        item_ids: o.item_ids, occasion: o.occasion || $("#rec-occasion").value || null,
        score: o.score, score_breakdown: o.breakdown,
      }) });
      toast("Outfit saved");
    });
    const wear = mkBtn("👟 Wear today", "btn primary", async () => {
      const saved = await api("/outfits", { method: "POST", body: JSON.stringify({
        item_ids: o.item_ids, occasion: o.occasion, score: o.score, score_breakdown: o.breakdown,
      }) });
      await api(`/outfits/${saved.id}/wear`, { method: "POST" });
      toast("Logged — wear counts updated");
    });
    actions.append(save, wear);
  } else {
    const wear = mkBtn("👟 Wear today", "btn primary", async () => {
      await api(`/outfits/${o.id}/wear`, { method: "POST" });
      toast("Logged — wear counts updated");
    });
    const fav = mkBtn(o.favorite ? "★ Favorited" : "☆ Favorite", "btn", async () => {
      await api(`/outfits/${o.id}/favorite`, { method: "POST" });
      loadSaved();
    });
    const del = mkBtn("Delete", "btn danger", async () => {
      if (!confirm("Delete this outfit?")) return;
      await api(`/outfits/${o.id}`, { method: "DELETE" });
      loadSaved();
    });
    actions.append(wear, fav, del);
  }
  return el;
}

function mkBtn(label, cls, onclick) {
  const b = document.createElement("button");
  b.className = cls;
  b.textContent = label;
  b.onclick = onclick;
  return b;
}

// ---------- SAVED ----------
async function loadSaved() {
  const outfits = await api("/outfits");
  const wrap = $("#saved-results");
  wrap.innerHTML = "";
  $("#saved-empty").classList.toggle("hidden", outfits.length > 0);
  outfits.sort((a, b) => (b.favorite - a.favorite));
  outfits.forEach((o) => wrap.appendChild(outfitCard(o, { fromRec: false })));
}

// ---------- INSIGHTS ----------
async function loadInsights() {
  const d = await api("/analytics");
  const el = $("#insights");
  const stat = (num, lbl) => `<div class="stat"><div class="num">${num}</div><div class="lbl">${lbl}</div></div>`;
  const list = (arr, fmt) => arr.length ? `<ul>${arr.map(fmt).join("")}</ul>` : `<p class="muted">Nothing here yet.</p>`;

  el.innerHTML = `
    <div class="stat-grid">
      ${stat(d.item_count, "items owned")}
      ${stat(d.avg_wears_per_item, "avg wears / item")}
      ${stat(d.pct_worn_last_90_days + "%", "worn last 90 days")}
      ${stat("$" + d.total_spend, "total spend")}
    </div>
    <div class="panel">
      <h3>By category</h3>
      ${list(Object.entries(d.by_category), ([c, n]) => `<li>${cap(c)}: ${n}</li>`)}
    </div>
    <div class="panel">
      <h3>Never worn ${d.never_worn.length ? `(${d.never_worn.length})` : ""}</h3>
      ${list(d.never_worn, (i) => `<li>${escapeHTML(i.name)} — ${i.category}</li>`)}
    </div>
    <div class="panel">
      <h3>Highest cost per wear</h3>
      ${list(d.highest_cost_per_wear, (i) => `<li>${escapeHTML(i.name)} — $${i.cost_per_wear} (worn ${i.wear_count}×)</li>`)}
    </div>
    <div class="panel">
      <h3>Most worn</h3>
      ${list(d.most_worn, (i) => `<li>${escapeHTML(i.name)} — ${i.wear_count}×</li>`)}
    </div>
    ${d.capsule_gaps.length ? `<div class="panel"><h3>Capsule gaps</h3>
      ${list(d.capsule_gaps, (g) => `<li>${cap(g.category)}: have ${g.have} / target ${g.target} (need ${g.gap})</li>`)}</div>` : ""}
  `;
}

// ---------- utils ----------
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

init().catch((e) => toast("Startup error: " + e.message));
