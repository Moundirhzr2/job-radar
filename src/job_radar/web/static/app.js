/*
  Job Radar page.
  - State lives in the URL (town, radius, kinds, tab, place, search, open offer, view, language):
    every view can be bookmarked or opened in a new tab.
  - Data comes from this server's /api; only the map tiles come from IGN.
  - Text goes in through text nodes only: offers and Claude's answers are never parsed as HTML.
*/
import { LANGS, LOCALES, MESSAGES } from "./i18n.js";

const $ = (selector, root = document) => root.querySelector(selector);
const NB = " ";
const SPRITE = "/static/vendor/phosphor/sprite.svg";
const TILES =
  "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0" +
  "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png" +
  "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}";
const KINDS = ["internship", "apprenticeship", "job", "student_job"];
const STATUSES = ["to_apply", "sent", "followed_up", "interview", "offer", "rejected", "dropped"];
const DEFAULT_RADIUS = 30;
const LIST_STEP = 60;
const UNDO_MS = 6000;
const SKELETON_DELAY = 200; // a skeleton that flashes for 50 ms is worse than none
const KM_PER_DEGREE = 111.195; // along a meridian, on Leaflet's sphere
const WORLD = [[85, -180], [85, 180], [-85, 180], [-85, -180]];
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

// --- storage: conveniences only, the page works without it ------------------------------------

function store(area) {
  return {
    get(key) {
      try {
        return JSON.parse(area().getItem(`radar:${key}`));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        area().setItem(`radar:${key}`, JSON.stringify(value));
      } catch {
        // private browsing or a full quota: nothing is remembered, nothing breaks
      }
    },
  };
}
const local = store(() => localStorage);
const session = store(() => sessionStorage);

// --- state and URL -----------------------------------------------------------------------------

const state = {
  lang: "fr",
  town: null, // { name, lat, lon }
  radius: DEFAULT_RADIUS,
  kinds: [],
  view: "radar", // "radar" | "tracker"
  tab: "offers", // "offers" | "employers"
  place: "", // a town of the list, lower case
  q: "",
  rewrite: true,
  offer: null, // id of the offer open in the panel
  app: null, // application to bring into view in the tracker
};

function browserLang() {
  for (const tag of navigator.languages?.length ? navigator.languages : [navigator.language]) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (LANGS.includes(base)) return base;
  }
  return "en";
}

function defaultLang() {
  const chosen = local.get("lang");
  return LANGS.includes(chosen) ? chosen : browserLang();
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  const lang = params.get("lang");
  state.lang = LANGS.includes(lang) ? lang : defaultLang();
  const lat = Number.parseFloat(params.get("lat"));
  const lon = Number.parseFloat(params.get("lon"));
  const name = (params.get("town") || "").trim();
  state.town =
    name && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { name, lat, lon } : null;
  const radius = Number.parseInt(params.get("r") || "", 10);
  state.radius = Number.isFinite(radius) ? Math.min(100, Math.max(2, radius)) : DEFAULT_RADIUS;
  state.kinds = KINDS.filter((kind) => params.getAll("kind").includes(kind));
  state.view = params.get("view") === "tracker" ? "tracker" : "radar";
  state.tab = params.get("tab") === "employers" ? "employers" : "offers";
  state.place = (params.get("place") || "").toLocaleLowerCase("fr-FR");
  state.q = (params.get("q") || "").trim().slice(0, 300);
  state.rewrite = params.get("rw") !== "0";
  state.offer = positive(params.get("offer"));
  state.app = positive(params.get("app"));
}

function positive(value) {
  const n = Number.parseInt(value || "", 10);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function query(overrides = {}, { withLang = true } = {}) {
  const s = { ...state, ...overrides };
  const params = new URLSearchParams();
  if (s.view === "tracker") params.set("view", "tracker");
  if (s.town) {
    params.set("town", s.town.name);
    params.set("lat", s.town.lat.toFixed(5));
    params.set("lon", s.town.lon.toFixed(5));
  }
  if (s.radius !== DEFAULT_RADIUS) params.set("r", String(s.radius));
  for (const kind of s.kinds) params.append("kind", kind);
  if (s.tab !== "offers") params.set("tab", s.tab);
  if (s.place) params.set("place", s.place);
  if (s.q) params.set("q", s.q);
  if (!s.rewrite) params.set("rw", "0");
  if (s.offer) params.set("offer", String(s.offer));
  if (s.app) params.set("app", String(s.app));
  if (withLang && s.lang !== defaultLang()) params.set("lang", s.lang);
  return params;
}

function hrefFor(overrides) {
  const params = query(overrides).toString();
  return params ? `?${params}` : location.pathname;
}

function commit(push = false) {
  const url = new URL(hrefFor({}), location.href);
  if (url.href !== location.href) history[push ? "pushState" : "replaceState"](null, "", url);
  const home = { view: "radar", q: "", offer: null, place: "", app: null, rewrite: true };
  local.set("last", query(home, { withLang: false }).toString());
}

function restoreLast() {
  if (location.search) return;
  const last = local.get("last");
  if (typeof last === "string" && last) history.replaceState(null, "", `?${last}`);
}

function navigate(changes, { push = false } = {}) {
  const before = { ...state };
  Object.assign(state, changes);
  commit(push);
  render(before);
}

const sameTown = (a, b) => (!a && !b) || (Boolean(a) && Boolean(b) && a.lat === b.lat && a.lon === b.lon);

// --- words and numbers -------------------------------------------------------------------------

let fmt = null;

function setFormats() {
  const locale = LOCALES[state.lang];
  fmt = {
    number: new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }),
    percent: new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }),
    day: new Intl.DateTimeFormat(locale, { day: "numeric", month: "long" }),
    relative: new Intl.RelativeTimeFormat(locale, { numeric: "auto" }),
    plural: new Intl.PluralRules(locale),
    list: new Intl.ListFormat(locale, { type: "conjunction" }),
  };
}

function template(key, n) {
  let entry = MESSAGES[state.lang][key] ?? MESSAGES.fr[key];
  if (entry === undefined) return key;
  if (typeof entry === "object") entry = entry[fmt.plural.select(n ?? 0)] ?? entry.other;
  return entry;
}

function t(key, vars = {}) {
  return template(key, vars.n).replace(/\{(\w+)\}/g, (match, name) =>
    name in vars ? String(vars[name]) : match,
  );
}

/* Like t(), for sentences that hold data (a quote, a contact): the values stay DOM nodes,
   so they keep their own lang attribute and are never turned into HTML. */
function tn(key, vars = {}) {
  const text = template(key, vars.n);
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(/\{(\w+)\}/g)) {
    parts.push(text.slice(last, match.index));
    const value = vars[match[1]];
    parts.push(value instanceof Node ? value : String(value ?? match[0]));
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return parts.filter((part) => part !== "");
}

const known = (key) => MESSAGES[state.lang][key] !== undefined;
const num = (n) => fmt.number.format(n);
const km = (n) => `${num(n)}${NB}km`;
const counted = (key, n) => t(key, { n, count: num(n) });
const kindName = (kind) => (known(`kind.${kind}`) ? t(`kind.${kind}`) : kind);
const kindsText = (kinds) =>
  fmt.list.format(kinds.map((kind) => kindName(kind).toLocaleLowerCase(LOCALES[state.lang])));
const hours = (n) => t("fmt.hours", { n: num(n) });
const romeName = (code) => (known(`rome.${code}`) ? t(`rome.${code}`) : code);
const frenchSource = (item) => (item?.source === "france_travail" ? "fr" : null);

function localDate(iso) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d); // a calendar day, not midnight UTC
  }
  return new Date(iso);
}

function daysAgo(iso) {
  const day = localDate(iso);
  const today = new Date();
  const a = Date.UTC(day.getFullYear(), day.getMonth(), day.getDate());
  const b = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((b - a) / 86400000);
}

function published(iso) {
  if (!iso) return "";
  const days = daysAgo(iso);
  if (days < 0) return "";
  if (days <= 30) return t("fmt.publishedRelative", { when: fmt.relative.format(-days, "day") });
  return t("fmt.publishedOn", { date: fmt.day.format(localDate(iso)) });
}

/* Sources spell towns differently ("MULHOUSE", "Mulhouse"): one key, one readable name. */
const cityKey = (city) => (city || "").trim().toLocaleLowerCase("fr-FR");
function cityName(city) {
  const name = (city || "").trim();
  if (!name || name !== name.toLocaleUpperCase("fr-FR")) return name;
  return name
    .toLocaleLowerCase("fr-FR")
    .replace(/(^|[\s\-'’])(\p{L})/gu, (_, sep, letter) => sep + letter.toLocaleUpperCase("fr-FR"));
}

// --- DOM ---------------------------------------------------------------------------------------

const PROPERTIES = new Set(["value", "checked", "disabled", "hidden", "selected", "open"]);

function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") element.className = value;
    else if (name.startsWith("on") && typeof value === "function")
      element.addEventListener(name.slice(2), value);
    else if (PROPERTIES.has(name)) element[name] = value;
    else element.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    element.append(child instanceof Node ? child : String(child));
  }
  return element;
}

const classes = (...names) => names.filter(Boolean).join(" ");

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `${SPRITE}#${name}`);
  svg.append(use);
  return svg;
}

const plainClick = (event) =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

/* A real link (new tab, copy link) that changes the page in place on a plain click. */
function appLink(changes, label, attributes = {}, { push = true } = {}) {
  return h(
    "a",
    {
      ...attributes,
      href: hrefFor(changes),
      onclick: (event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        if (changes.offer && changes.offer !== state.offer) focusTitleNext = true;
        navigate(changes, { push });
      },
    },
    label,
  );
}

function safeUrl(value) {
  try {
    const url = new URL(value, location.origin);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function externalLink(url, label, className = "ext") {
  const href = safeUrl(url);
  if (!href) return null;
  return h(
    "a",
    { class: className, href, target: "_blank", rel: "noopener noreferrer" },
    h("span", {}, label),
    icon("arrow-square-out"),
    h("span", { class: "sr-only" }, ` ${t("newTab")}`),
  );
}

const isEmail = (value) => /^[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+$/.test(value || "");
const isLink = (value) => /^https?:\/\//i.test(value || "") && Boolean(safeUrl(value));

function command(text) {
  return h("p", {}, h("code", { translate: "no" }, text));
}

function setBusy(button, on, label) {
  const text = button.querySelector("span:not(.sr-only)") ?? button;
  if (on) {
    button.dataset.label = text.textContent;
    text.textContent = label;
    button.setAttribute("aria-busy", "true");
    button.setAttribute("aria-disabled", "true");
  } else {
    if (button.dataset.label) text.textContent = button.dataset.label;
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-disabled");
  }
}
const isBusy = (button) => button.getAttribute("aria-busy") === "true";

function showError(element, field, message) {
  element.textContent = message;
  element.hidden = false;
  if (field) {
    field.setAttribute("aria-invalid", "true");
    field.focus();
  }
}

function hideError(element, field = null) {
  element.hidden = true;
  element.textContent = "";
  field?.removeAttribute("aria-invalid");
}

function announce(message) {
  const region = $("#announce");
  region.textContent = "";
  setTimeout(() => {
    region.textContent = message;
  }, 60);
}

let toastTimer = 0;
function toast(message) {
  const box = $("#toast");
  $("#toast-text").textContent = message;
  box.hidden = false;
  announce(message);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.hidden = true;
  }, 3500);
}

// --- server ------------------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(path, { method = "GET", body, signal, keepalive = false } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      signal,
      keepalive,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "network", "");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (response.ok) return data;
  const detail = data?.detail;
  if (Array.isArray(detail)) throw new ApiError(response.status, "validation", "");
  if (detail && typeof detail === "object")
    throw new ApiError(response.status, detail.code || "http", detail.message || "");
  throw new ApiError(response.status, "http", typeof detail === "string" ? detail : "");
}

function errorText(error) {
  if (!(error instanceof ApiError)) return t("err.http", { status: "?" });
  if (known(`err.${error.code}`)) return t(`err.${error.code}`, { message: error.message, status: error.status });
  return error.message || t("err.http", { status: error.status });
}

function resource() {
  return { key: null, valueKey: null, value: null, error: null, loading: false, started: 0, controller: null };
}

async function load(res, key, fetcher, done) {
  res.controller?.abort();
  const controller = new AbortController();
  Object.assign(res, { key, error: null, loading: true, started: performance.now(), controller });
  try {
    const value = await fetcher(controller.signal);
    if (res.key !== key) return;
    Object.assign(res, { value, valueKey: key });
  } catch (error) {
    if (error.name === "AbortError" || res.key !== key) return;
    res.error = error;
  }
  res.loading = false;
  res.controller = null;
  done?.();
}

function cancel(res) {
  res.controller?.abort();
  Object.assign(res, { key: null, loading: false, controller: null });
}

const data = {
  coverage: resource(),
  radar: resource(),
  employers: resource(),
  search: resource(),
  offer: resource(),
  apps: resource(),
};
const offerCache = new Map();
const fits = new Map(); // "id|lang" -> { status, value, error }
const drafts = new Map(); // id -> { status, subject, body, original, facts, contact, previous }
const openDescriptions = new Set();
let radarIndex = new Map();

const areaKey = () =>
  state.town ? `${state.town.lat.toFixed(5)},${state.town.lon.toFixed(5)},${state.radius}` : "";
const radarKey = () => `${areaKey()}|${state.kinds.join(",")}`;
const searchKey = () => `${radarKey()}|${state.rewrite ? 1 : 0}|${state.q}`;

function areaParams(withKinds = true) {
  const params = new URLSearchParams({
    lat: String(state.town.lat),
    lon: String(state.town.lon),
    radius_km: String(state.radius),
  });
  if (withKinds) for (const kind of state.kinds) params.append("kind", kind);
  return params;
}

function searchResults() {
  return data.search.valueKey === searchKey() ? data.search.value?.results ?? [] : [];
}

function listed(id) {
  return radarIndex.get(id) ?? searchResults().find((result) => result.id === id) ?? null;
}

function ensureData() {
  if (state.offer) ensureOffer(state.offer);
  if (!state.town) {
    loadCoverage();
    return;
  }
  if (data.radar.key !== radarKey()) loadRadar();
  if (data.employers.key !== areaKey()) loadEmployers();
  if (state.q && data.search.key !== searchKey()) runSearch();
}

function refresh() {
  if (state.view !== "radar") return;
  renderPanel();
  drawMarkers();
}

function retry(res) {
  return () => {
    res.key = null;
    res.error = null;
    ensureData();
    refresh();
  };
}

function loadCoverage() {
  if (data.coverage.key) return;
  load(data.coverage, "all", (signal) => api("/api/coverage", { signal }), () => {
    if (state.town || state.view !== "radar") return;
    renderPanel();
    drawMarkers();
    const towns = data.coverage.value ?? [];
    if (towns.length) {
      map.fitBounds(L.latLngBounds(towns.map((town) => [town.lat, town.lon])).pad(0.3), {
        maxZoom: 10,
        animate: !reducedMotion.matches,
      });
    }
  });
}

function loadRadar() {
  const key = radarKey();
  load(data.radar, key, (signal) => api(`/api/radar?${areaParams()}`, { signal }), () => {
    if (data.radar.valueKey === key) {
      radarIndex = new Map(data.radar.value.offers.map((offer) => [offer.id, offer]));
      if (!state.q && !state.offer && state.tab === "offers") {
        announce(
          t("announce.offers", {
            offers: counted("u.offer", data.radar.value.count),
            radius: km(state.radius),
            town: state.town.name,
          }),
        );
      }
    }
    refresh();
    panToOffer();
  });
}

function loadEmployers() {
  const key = areaKey();
  load(data.employers, key, (signal) => api(`/api/employers?${areaParams(false)}`, { signal }), refresh);
}

function runSearch(force = false) {
  const key = searchKey();
  const cached = force ? null : session.get(`search:${key}`);
  if (cached?.results) {
    Object.assign(data.search, { key, valueKey: key, value: cached, error: null, loading: false });
    searchDone(key);
    return;
  }
  const params = areaParams();
  params.set("q", state.q);
  params.set("rewrite", String(state.rewrite));
  setSearchBusy(true);
  load(data.search, key, (signal) => api(`/api/search?${params}`, { signal }), () => {
    setSearchBusy(false);
    if (data.search.valueKey === key) session.set(`search:${key}`, data.search.value);
    searchDone(key);
  });
}

function searchDone(key) {
  if (data.search.valueKey === key && state.q && !state.offer) {
    announce(t("announce.results", { results: counted("u.result", data.search.value.results.length) }));
  }
  refresh();
}

function setSearchBusy(on) {
  const button = $("#search-submit");
  button.querySelector("[data-i18n]").textContent = on ? t("search.busy") : t("search.submit");
  if (on) {
    button.setAttribute("aria-busy", "true");
    button.setAttribute("aria-disabled", "true");
  } else {
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-disabled");
  }
}

function ensureOffer(id) {
  if (data.offer.valueKey === id) return;
  const cached = offerCache.get(id);
  if (cached) {
    Object.assign(data.offer, { key: id, valueKey: id, value: cached, error: null, loading: false });
    return;
  }
  if (data.offer.key === id) return; // loading, or failed: the panel offers to retry
  load(data.offer, id, (signal) => api(`/api/offers/${id}`, { signal }), () => {
    if (data.offer.valueKey === id) offerCache.set(id, data.offer.value);
    if (state.offer === id) {
      updateTitle();
      refresh();
    }
  });
}

function loadApplications() {
  load(data.apps, "all", (signal) => api("/api/applications", { signal }), () => {
    updateDueBadge();
    if (state.view === "tracker") renderTracker();
    else if (state.offer || state.tab === "employers") renderPanel();
  });
}

const findApp = (id) => data.apps.value?.applications.find((app) => app.id === id) ?? null;

function addApplication(app) {
  if (data.apps.value) data.apps.value.applications.unshift(app);
  else loadApplications();
  updateDueBadge();
}

function replaceApp(updated) {
  const apps = data.apps.value?.applications;
  if (!apps) return;
  const index = apps.findIndex((app) => app.id === updated.id);
  if (index >= 0) apps[index] = updated;
}

function trackedOffers() {
  const tracked = new Map();
  for (const app of data.apps.value?.applications ?? []) if (app.offer_id) tracked.set(app.offer_id, app);
  return tracked;
}

const companyKey = (company) => company.siret || `name:${cityKey(company.company)}`;
function trackedCompanies() {
  const tracked = new Map();
  for (const app of data.apps.value?.applications ?? []) {
    if (app.offer_id) continue;
    tracked.set(app.siret || `name:${cityKey(app.company)}`, app);
  }
  return tracked;
}

// --- the map -----------------------------------------------------------------------------------

let map = null;
let zoomControl = null;
let needsFit = true;
let lastPing = 0;
let pannedTo = null;
const layers = {};
const pinElements = new Map(); // offer id -> its pin, to light it up from the list

function initMap() {
  const still = reducedMotion.matches;
  map = L.map("map", {
    zoomControl: false,
    minZoom: 5,
    maxZoom: 18,
    zoomSnap: 0.5,
    zoomAnimation: !still,
    fadeAnimation: !still,
    markerZoomAnimation: !still,
  });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" translate="no">Leaflet</a>');
  L.tileLayer(TILES, {
    maxZoom: 18,
    attribution: 'Plan IGN, <a href="https://geoservices.ign.fr/" translate="no">IGN</a>',
  }).addTo(map);
  map.createPane("labels"); // distance labels stay readable above the pins
  map.getPane("labels").style.zIndex = "640"; // above the pins (600), under tooltips (650)
  map.getPane("labels").style.pointerEvents = "none";
  for (const name of ["area", "hiring", "places", "results"]) layers[name] = L.layerGroup().addTo(map);
  addZoomControl();
  map.on("zoomend", () => {
    if (state.view === "radar" && state.town && !state.q) {
      layers.places.clearLayers();
      pinElements.clear();
      drawPlaces();
    }
  });
  map.on("popupopen", (event) => {
    const close = event.popup.getElement()?.querySelector(".leaflet-popup-close-button");
    close?.setAttribute("aria-label", t("map.close"));
    close?.setAttribute("title", t("map.close"));
  });
  map.setView([46.6, 2.5], 6);
}

function addZoomControl() {
  zoomControl?.remove();
  zoomControl = L.control
    .zoom({ position: "topright", zoomInTitle: t("map.zoomIn"), zoomOutTitle: t("map.zoomOut") })
    .addTo(map);
}

function circle(lat, lon, radiusKm, points = 120) {
  const dLat = radiusKm / KM_PER_DEGREE;
  const dLon = dLat / Math.cos((lat * Math.PI) / 180);
  return Array.from({ length: points }, (_, i) => {
    const angle = (2 * Math.PI * i) / points;
    return [lat + dLat * Math.sin(angle), lon + dLon * Math.cos(angle)];
  });
}

/* Rings every 1, 2, 5, 10, 20, 25 or 50 km, at most four, never crowding the radius. */
function ringSteps(radius) {
  const step = [1, 2, 5, 10, 20, 25, 50].find((s) => radius / s <= 4) ?? 50;
  const rings = [];
  for (let d = step; d < radius - step * 0.6; d += step) rings.push(d);
  rings.push(radius);
  return rings;
}

function drawArea({ radius = state.radius, fit = false, ping = false } = {}) {
  layers.area.clearLayers();
  if (!state.town) return;
  const { lat, lon } = state.town;
  const edge = circle(lat, lon, radius);
  L.polygon([WORLD, edge], { className: "outside", interactive: false, stroke: false }).addTo(layers.area);
  for (const ring of ringSteps(radius)) {
    const outer = ring === radius;
    L.polygon(outer ? edge : circle(lat, lon, ring), {
      className: outer ? "ring outer" : "ring",
      interactive: false,
      fill: false,
    }).addTo(layers.area);
    L.marker([lat + ring / KM_PER_DEGREE, lon], {
      icon: L.divIcon({ className: "ring-label", html: h("span", {}, km(ring)), iconSize: null }),
      pane: "labels",
      interactive: false,
      keyboard: false,
    }).addTo(layers.area);
  }
  L.marker([lat, lon], {
    icon: L.divIcon({ className: "centre-mark", iconSize: [12, 12] }),
    interactive: false,
    keyboard: false,
    zIndexOffset: -1000,
  }).addTo(layers.area);
  if (fit) {
    map.fitBounds(L.latLngBounds(circle(lat, lon, radius, 24)), {
      padding: [28, 28],
      animate: !reducedMotion.matches,
    });
  }
  if (ping && !reducedMotion.matches && performance.now() - lastPing > 1200) {
    lastPing = performance.now();
    afterMove(() => pingArea(lat, lon, radius));
  }
}

function afterMove(callback) {
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    map.off("moveend", run);
    callback();
  };
  map.once("moveend", run);
  setTimeout(run, 600);
}

/* One ring sweeping out to the new radius: the radar answering a change of area. */
function pingArea(lat, lon, radius) {
  const centre = map.latLngToLayerPoint([lat, lon]);
  const top = map.latLngToLayerPoint([lat + radius / KM_PER_DEGREE, lon]);
  const size = Math.round(2 * (centre.y - top.y));
  if (!(size > 0 && size < 4000)) return;
  const marker = L.marker([lat, lon], {
    icon: L.divIcon({ className: "ping", html: h("span"), iconSize: [size, size] }),
    interactive: false,
    keyboard: false,
    zIndexOffset: -2000,
  }).addTo(map);
  setTimeout(() => marker.remove(), 1300);
}

function drawMarkers() {
  if (!map) return;
  for (const name of ["hiring", "places", "results"]) layers[name].clearLayers();
  pinElements.clear();
  if (state.view !== "radar") return;
  if (!state.town) {
    drawCoverage();
    return;
  }
  if (state.q) {
    if (data.search.valueKey === searchKey()) drawResults(data.search.value.results);
    else drawPlaces({ faded: true });
    return;
  }
  drawPlaces();
  drawHiring();
}

function pinIcon(element, size) {
  return L.divIcon({ className: "pin-icon", html: element, iconSize: size });
}

/* The hover readout of a pin, built from a text node: place names come from the data. */
function tip(marker, text, height) {
  marker.bindTooltip(h("span", {}, text), { direction: "top", offset: [0, -Math.round(height / 2) - 2], opacity: 1 });
  return marker;
}

function drawCoverage() {
  for (const town of data.coverage.value ?? []) {
    const size = Math.round(Math.min(44, 20 + 6 * Math.log2(town.count)));
    const name = cityName(town.city);
    const marker = L.marker([town.lat, town.lon], {
      icon: pinIcon(h("span", { class: "pin" }, num(town.count)), [size, size]),
      keyboard: false,
      riseOnHover: true,
    });
    tip(marker, `${name}${NB}: ${counted("u.offer", town.count)}`, size);
    marker.on("click", () => chooseTown({ name, lat: town.lat, lon: town.lon }));
    marker.addTo(layers.places);
  }
}

/* Points closer than CLUSTER_PX on screen merge into one pin, recomputed at every zoom: the
   map stays readable at 100 km and shows each address up close. */
const CLUSTER_PX = 44;

function clusters(places) {
  const zoom = map.getZoom();
  const groups = [];
  for (const place of [...places].sort((a, b) => b.count - a.count)) {
    const point = map.project([place.lat, place.lon], zoom);
    const near = groups.find((group) => Math.hypot(group.x - point.x, group.y - point.y) < CLUSTER_PX);
    if (near) {
      const total = near.count + place.count;
      near.x = (near.x * near.count + point.x * place.count) / total;
      near.y = (near.y * near.count + point.y * place.count) / total;
      near.count = total;
      near.places.push(place);
    } else {
      groups.push({ x: point.x, y: point.y, count: place.count, places: [place] });
    }
  }
  return groups.map((group) => {
    const at = map.unproject([group.x, group.y], zoom);
    return {
      lat: at.lat,
      lon: at.lng,
      count: group.count,
      places: group.places,
      ids: group.places.flatMap((place) => place.ids),
      townCentre: group.places.every((place) => place.town_centre),
    };
  });
}

function clusterLabel(cluster) {
  const towns = [...new Set(cluster.places.map((place) => cityName(place.city)).filter(Boolean))];
  const offers = counted("u.offer", cluster.count);
  if (towns.length <= 1) {
    const where = cluster.townCentre ? t("place.centre") : t("place.here");
    return t("place.popup", { offers, where, town: towns[0] ?? "" });
  }
  const others = towns.length - 3;
  const named = others > 0 ? [...towns.slice(0, 3), counted("place.others", others)] : towns;
  return t("place.many", { offers, towns: fmt.list.format(named) });
}

function drawPlaces({ faded = false } = {}) {
  const radar = data.radar.value;
  if (!radar) return;
  for (const cluster of clusters(radar.places)) {
    const single = cluster.count === 1;
    const size = single ? 22 : Math.round(Math.min(40, 20 + 5 * Math.log2(cluster.count)));
    const selected = state.offer
      ? cluster.ids.includes(state.offer)
      : Boolean(state.place) && cluster.places.some((place) => cityKey(place.city) === state.place);
    const element = h(
      "span",
      { class: classes("pin", single && "dot", cluster.townCentre && "approx", selected && "selected", faded && "faded") },
      single ? "" : num(cluster.count),
    );
    const label = clusterLabel(cluster);
    const marker = L.marker([cluster.lat, cluster.lon], {
      icon: pinIcon(element, [size, size]),
      keyboard: false,
      riseOnHover: true,
      zIndexOffset: selected ? 1000 : 0,
    });
    tip(marker, label, single ? 12 : size);
    marker.on("click", () => openCluster(cluster, label, marker));
    marker.addTo(layers.places);
    for (const id of cluster.ids) pinElements.set(id, element);
  }
}

function openCluster(cluster, label, marker) {
  if (cluster.count === 1) {
    openOffer(cluster.ids[0]);
    return;
  }
  if (cluster.places.length > 1 && map.getZoom() < map.getMaxZoom() - 1) {
    const bounds = L.latLngBounds(cluster.places.map((place) => [place.lat, place.lon]));
    map.fitBounds(bounds, { padding: [60, 60], animate: !reducedMotion.matches });
    return;
  }
  marker.bindPopup(() => clusterPopup(cluster, label), { maxWidth: 300 }).openPopup();
}

function clusterPopup(cluster, label) {
  const offers = cluster.ids.map((id) => radarIndex.get(id)).filter(Boolean).slice(0, 8);
  const towns = [...new Set(cluster.places.map((place) => cityKey(place.city)).filter(Boolean))];
  return h(
    "div",
    { class: "popup" },
    h("p", { class: "t" }, label),
    h(
      "ul",
      {},
      offers.map((offer) =>
        h(
          "li",
          {},
          h(
            "a",
            {
              href: hrefFor({ offer: offer.id }),
              lang: frenchSource(offer),
              onclick: (event) => {
                if (!plainClick(event)) return;
                event.preventDefault();
                map.closePopup();
                openOffer(offer.id);
              },
            },
            offer.title,
          ),
        ),
      ),
    ),
    towns.length === 1
      ? h(
          "button",
          {
            type: "button",
            class: "btn",
            onclick: () => {
              map.closePopup();
              navigate({ place: towns[0], tab: "offers" });
            },
          },
          t("place.inList", { town: cityName(cluster.places[0].city) }),
        )
      : null,
  );
}

function drawHiring() {
  for (const employer of data.employers.value?.hiring ?? []) {
    if (employer.lat == null || employer.lon == null) continue;
    const marker = L.marker([employer.lat, employer.lon], {
      icon: pinIcon(h("span", { class: "hiring-pin" }), [13, 13]),
      keyboard: false,
    });
    tip(marker, `${employer.name}${NB}: ${t("legend.hiring").toLocaleLowerCase(LOCALES[state.lang])}`, 13);
    marker.bindPopup(() => hiringPopup(employer), { maxWidth: 280 });
    marker.addTo(layers.hiring);
  }
}

function hiringPopup(employer) {
  return h(
    "div",
    { class: "popup" },
    h("p", { class: "t", translate: "no" }, employer.name),
    h("p", {}, `${cityName(employer.city)}, ${km(employer.distance_km)}`),
    h("p", {}, t("employers.hiresIn", { field: romeName(employer.rome) })),
    h(
      "p",
      { class: "row-actions" },
      externalLink(employer.fiche, t("employers.page")),
      trackCompanyControl(companyOf(employer), trackedCompanies()),
    ),
  );
}

function drawResults(results) {
  const groups = new Map();
  results.forEach((result, index) => {
    if (result.lat == null || result.lon == null) return;
    const key = `${result.lat.toFixed(4)},${result.lon.toFixed(4)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ result, rank: index + 1 });
  });
  for (const items of groups.values()) {
    const ranks = items.map((item) => num(item.rank));
    const label = ranks.length <= 2 ? ranks.join(", ") : `${ranks[0]}, ${ranks[1]} +${num(ranks.length - 2)}`;
    const selected = items.some((item) => item.result.id === state.offer);
    const weak = items.every((item) => item.result.weak);
    const element = h("span", { class: classes("result-pin", weak && "weak", selected && "selected") }, label);
    const { result } = items[0];
    const marker = L.marker([result.lat, result.lon], {
      icon: pinIcon(element, [Math.max(24, 14 + 7 * label.length), 24]),
      keyboard: false,
      zIndexOffset: 2000 - items[0].rank,
    });
    const first = `${num(items[0].rank)}. ${items[0].result.title}`;
    tip(marker, items.length > 1 ? `${first} +${num(items.length - 1)}` : first, 24);
    if (items.length === 1) marker.on("click", () => openOffer(result.id));
    else marker.bindPopup(() => resultsPopup(items), { maxWidth: 300 });
    marker.addTo(layers.results);
    for (const item of items) pinElements.set(item.result.id, element);
  }
}

function resultsPopup(items) {
  return h(
    "div",
    { class: "popup" },
    h(
      "ul",
      {},
      items.map(({ result, rank }) =>
        h(
          "li",
          {},
          `${num(rank)}. `,
          h(
            "a",
            {
              href: hrefFor({ offer: result.id }),
              lang: frenchSource(result),
              onclick: (event) => {
                if (!plainClick(event)) return;
                event.preventDefault();
                map.closePopup();
                openOffer(result.id);
              },
            },
            result.title,
          ),
        ),
      ),
    ),
  );
}

function hoverPin(id, on) {
  pinElements.get(id)?.classList.toggle("hover", on);
}

function panToOffer() {
  if (!state.offer || pannedTo === state.offer || state.view !== "radar") return;
  const offer = listed(state.offer);
  if (offer?.lat == null) return;
  pannedTo = state.offer;
  const point = L.latLng(offer.lat, offer.lon);
  if (!map.getBounds().pad(-0.1).contains(point)) map.panTo(point, { animate: !reducedMotion.matches });
}

// --- the panel ---------------------------------------------------------------------------------

let panelMode = "";
let panelTimer = 0;
let focusTitleNext = false;
const panelScroll = new Map();

function renderPanel() {
  if (state.view !== "radar") return;
  clearTimeout(panelTimer);
  let mode;
  let nodes;
  let busy = false;
  if (state.offer) {
    mode = `offer:${state.offer}`;
    nodes = detailView();
    busy = data.offer.loading;
  } else if (!state.town) {
    mode = "start";
    nodes = startView();
  } else if (state.q) {
    mode = `search:${searchKey()}`;
    nodes = searchView();
    busy = data.search.loading;
  } else if (state.tab === "employers") {
    mode = `employers:${areaKey()}`;
    nodes = employersView();
    busy = data.employers.loading;
  } else {
    mode = `offers:${radarKey()}:${state.place}`;
    nodes = offersView();
    busy = data.radar.loading;
  }
  setPanel(mode, nodes, busy);
}

function setPanel(mode, nodes, busy) {
  const body = $("#panel-body");
  const active = document.activeElement;
  const focusKey = body.contains(active) ? active.dataset.key : null;
  const previous = panelMode;
  const changed = mode !== previous;
  if (changed) panelScroll.set(previous, body.scrollTop);
  body.setAttribute("aria-busy", String(busy));
  body.replaceChildren(...[nodes].flat(Infinity).filter(Boolean));
  if (changed) {
    panelMode = mode;
    body.scrollTop = mode.startsWith("offer:") ? 0 : panelScroll.get(mode) ?? 0;
  }
  const title = $("#detail-title");
  if (!mode.startsWith("offer:")) focusTitleNext = false;
  if (focusTitleNext && title) {
    focusTitleNext = false;
    title.focus({ preventScroll: true });
  } else if (changed && previous.startsWith("offer:") && !mode.startsWith("offer:")) {
    body.querySelector(`a.row[data-id="${previous.slice(6)}"]`)?.focus({ preventScroll: true });
  } else if (focusKey) {
    body.querySelector(`[data-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
  }
}

function pending(res, label) {
  const elapsed = performance.now() - res.started;
  if (res.loading && elapsed < SKELETON_DELAY) {
    panelTimer = setTimeout(renderPanel, SKELETON_DELAY - elapsed + 10);
    return [];
  }
  return skeleton(label);
}

function skeleton(label) {
  return [
    h("p", { class: "busy-note", role: "status" }, label),
    h(
      "ul",
      { class: "skeleton", "aria-hidden": "true" },
      Array.from({ length: 6 }, () => h("li", {}, h("span"), h("span"))),
    ),
  ];
}

function errorBox(error, title, onRetry) {
  return h(
    "div",
    { class: "notice bad", role: "alert" },
    title ? h("p", {}, h("strong", {}, title)) : null,
    h("p", {}, errorText(error)),
    onRetry
      ? h("button", { type: "button", class: "btn", "data-key": "retry", onclick: onRetry }, icon("arrow-clockwise"), h("span", {}, t("retry")))
      : null,
  );
}

function startView() {
  const towns = data.coverage.value ?? [];
  return h(
    "div",
    { class: "empty enter" },
    icon("map-pin"),
    h("h2", {}, t("onboarding.title")),
    h("p", {}, t("onboarding.text")),
    towns.length
      ? [
          h("p", {}, t("onboarding.loaded")),
          h(
            "ul",
            { class: "towns-loaded" },
            towns.map((town) =>
              h(
                "li",
                {},
                h(
                  "button",
                  {
                    type: "button",
                    class: "btn",
                    "data-key": `start-${cityKey(town.city)}`,
                    onclick: () => chooseTown({ name: cityName(town.city), lat: town.lat, lon: town.lon }),
                  },
                  h("span", {}, cityName(town.city)),
                  h("span", { class: "facts", "aria-hidden": "true" }, num(town.count)),
                  h("span", { class: "sr-only" }, `, ${counted("u.offer", town.count)}`),
                ),
              ),
            ),
          ),
        ]
      : data.coverage.value
        ? [h("p", {}, t("onboarding.none")), command("radar francetravail --town Mulhouse")]
        : null,
  );
}

function areaMeta(withKindsKey, plainKey) {
  const vars = { town: state.town.name, radius: km(state.radius) };
  return state.kinds.length ? t(withKindsKey, { ...vars, kinds: kindsText(state.kinds) }) : t(plainKey, vars);
}

function offersHead() {
  const radar = data.radar.value;
  return [
    h(
      "div",
      { class: "panel-head" },
      h(
        "div",
        {},
        h(
          "h2",
          {},
          radar
            ? t("offers.heading", { offers: counted("u.offer", radar.count), radius: km(state.radius) })
            : t("loading.offers"),
        ),
        h("p", { class: "panel-meta" }, areaMeta("offers.metaKinds", "offers.meta")),
      ),
    ),
    radar && data.radar.loading ? h("p", { class: "busy-note" }, t("busy.update")) : null,
  ];
}

function tabs() {
  const employers = data.employers.value;
  const tab = (name, label, n) =>
    h(
      "button",
      {
        type: "button",
        "aria-pressed": String(state.tab === name),
        "data-key": `tab-${name}`,
        onclick: () => {
          if (state.tab !== name) navigate({ tab: name, place: "" });
        },
      },
      label,
      n === null || n === undefined ? null : h("span", { class: "n" }, num(n)),
    );
  return h(
    "div",
    { class: "segmented", role: "group", "aria-label": t("tabs.label") },
    tab("offers", t("tab.offers"), data.radar.value?.count),
    tab("employers", t("tab.employers"), employers ? employers.hiring.length + employers.digital.length : null),
  );
}

function offersView() {
  const res = data.radar;
  const radar = res.value;
  if (!radar) {
    if (res.error) return [offersHead(), errorBox(res.error, t("error.offers"), retry(res))];
    return pending(res, t("loading.offers"));
  }
  const offers = radar.offers;
  const cities = citiesOf(offers);
  const place = cities.has(state.place) ? state.place : "";
  const shown = place ? offers.filter((offer) => cityKey(offer.city) === place) : offers;
  return [
    offersHead(),
    tabs(),
    offers.length === 0
      ? noOffers()
      : [cities.size > 1 ? placeFilter(cities, place, offers.length) : null, batched(shown, offerRow)],
    h("p", { class: "sources" }, t("sources")),
  ];
}

function citiesOf(offers) {
  const cities = new Map();
  for (const offer of offers) {
    const key = cityKey(offer.city);
    if (!key) continue;
    const entry = cities.get(key) ?? { name: cityName(offer.city), count: 0 };
    entry.count += 1;
    cities.set(key, entry);
  }
  return cities;
}

function placeFilter(cities, current, total) {
  const sorted = [...cities.entries()].sort(
    (a, b) => b[1].count - a[1].count || a[1].name.localeCompare(b[1].name, LOCALES[state.lang]),
  );
  const select = h(
    "select",
    {
      id: "place",
      name: "place",
      "data-key": "place",
      onchange: (event) => {
        const city = cities.get(event.target.value);
        navigate({ place: event.target.value });
        if (city) announce(t("announce.place", { offers: counted("u.offer", city.count), town: city.name }));
      },
    },
    h("option", { value: "" }, t("place.all", { count: num(total) })),
    sorted.map(([key, city]) => h("option", { value: key }, `${city.name} (${num(city.count)})`)),
  );
  select.value = current;
  return h("div", { class: "place-filter" }, h("label", { for: "place" }, t("place.label")), select);
}

/* Long lists arrive LIST_STEP rows at a time; the button appends the next rows in place and
   moves focus to the first of them. */
function batched(items, row) {
  const list = h("ul", { class: "rows" });
  const more = h("button", { type: "button", class: "btn more", "data-key": "more" });
  let shown = 0;
  const showMore = (focus) => {
    const rows = items.slice(shown, shown + LIST_STEP).map((item) => row(item));
    list.append(...rows);
    shown += rows.length;
    const left = items.length - shown;
    more.hidden = left <= 0;
    more.textContent = t("more", { step: num(Math.min(LIST_STEP, left)), left: counted("u.left", left) });
    if (focus) rows[0]?.querySelector("a, button")?.focus();
  };
  more.addEventListener("click", () => showMore(true));
  showMore(false);
  return [list, more];
}

function tag(kind) {
  return h("span", { class: `tag k-${kind}` }, kindName(kind));
}

function offerRow(offer, { rank = null, weak = false } = {}) {
  const meta = [
    offer.company ? h("span", { translate: "no" }, offer.company) : null,
    offer.company && offer.city ? ", " : null,
    cityName(offer.city),
  ];
  return h(
    "li",
    {},
    h(
      "a",
      {
        class: "row",
        href: hrefFor({ offer: offer.id }),
        "data-id": offer.id,
        "aria-current": state.offer === offer.id ? "true" : null,
        onclick: (event) => {
          if (!plainClick(event)) return;
          event.preventDefault();
          openOffer(offer.id);
        },
        onmouseenter: () => hoverPin(offer.id, true),
        onmouseleave: () => hoverPin(offer.id, false),
        onfocus: () => hoverPin(offer.id, true),
        onblur: () => hoverPin(offer.id, false),
      },
      h(
        "span",
        { class: "t" },
        rank ? h("span", { class: classes("rank", weak && "weak"), "aria-hidden": "true" }, num(rank)) : null,
        h("span", { lang: frenchSource(offer) }, offer.title),
      ),
      h("span", { class: "m" }, meta),
      h(
        "span",
        { class: "line" },
        offer.kinds.map(tag),
        h("span", { class: "facts" }, km(offer.distance_km)),
        offer.weekly_hours ? h("span", { class: "facts" }, hours(offer.weekly_hours)) : null,
        offer.published_at ? h("span", { class: "facts" }, published(offer.published_at)) : null,
      ),
    ),
  );
}

function widerButton() {
  const wider = Math.min(100, state.radius * 2);
  return state.radius < 100
    ? h("button", { type: "button", class: "btn", onclick: () => navigate({ radius: wider }) }, t("empty.offers.wider", { radius: km(wider) }))
    : null;
}

function noOffers() {
  return h(
    "div",
    { class: "empty" },
    icon("map-pin"),
    h("h3", {}, t("empty.offers.title", { radius: km(state.radius) })),
    h("p", {}, t("empty.offers.text")),
    command(`radar francetravail --town "${state.town.name}"`),
    h(
      "div",
      { class: "row-actions" },
      widerButton(),
      state.kinds.length
        ? h("button", { type: "button", class: "btn", onclick: () => navigate({ kinds: [] }) }, t("empty.offers.allKinds"))
        : null,
    ),
  );
}

const companyOf = (employer) => ({ siret: employer.siret ?? null, company: employer.name, url: employer.fiche });

function employersView() {
  const res = data.employers;
  const value = res.value;
  const head = [offersHead(), tabs()];
  if (!value) {
    if (res.error) return [head, errorBox(res.error, t("error.employers"), retry(res))];
    return [head, pending(res, t("loading.employers"))];
  }
  const { hiring, digital } = value;
  if (!hiring.length && !digital.length) {
    return [
      head,
      h(
        "div",
        { class: "empty" },
        icon("buildings"),
        h("h3", {}, t("employers.empty.title")),
        h("p", {}, t("employers.empty.text")),
        command(`radar hiring --town "${state.town.name}"`),
        command(`radar companies --town "${state.town.name}" --sections J`),
      ),
    ];
  }
  const tracked = trackedCompanies();
  return [
    head,
    hiring.length
      ? [
          h("h3", { class: "list-title" }, t("employers.hiring.title")),
          h("p", { class: "hint" }, t("employers.hiring.hint")),
          batched(hiring, (employer) => employerRow(employer, tracked, true)),
        ]
      : null,
    digital.length
      ? [
          h("h3", { class: "list-title" }, t("employers.digital.title")),
          h("p", { class: "hint" }, t("employers.digital.hint")),
          batched(digital, (company) => employerRow(company, tracked, false)),
        ]
      : null,
    h("p", { class: "sources" }, t("sources")),
  ];
}

function employerRow(employer, tracked, hiring) {
  return h(
    "li",
    {},
    h(
      "div",
      { class: "row" },
      h("span", { class: "t" }, h("span", { translate: "no" }, employer.name)),
      h("span", { class: "m" }, `${cityName(employer.city)}, ${km(employer.distance_km)}`),
      h(
        "span",
        { class: "line" },
        hiring ? h("span", { class: "facts" }, t("employers.hiresIn", { field: romeName(employer.rome) })) : null,
        hiring && employer.high_potential ? h("span", { class: "tag good" }, t("employers.high")) : null,
        hiring && employer.accepts_email ? h("span", { class: "facts" }, t("employers.email")) : null,
        !hiring && employer.naf_code ? h("span", { class: "facts", translate: "no" }, `NAF ${employer.naf_code}`) : null,
      ),
      h(
        "span",
        { class: "row-actions" },
        externalLink(employer.fiche, t("employers.page")),
        trackCompanyControl(companyOf(employer), tracked),
      ),
    ),
  );
}

function trackCompanyControl(company, tracked) {
  const key = companyKey(company);
  const app = tracked.get(key);
  if (app) {
    return appLink({ view: "tracker", app: app.id }, [icon("check"), h("span", {}, t("track.done"))], {
      class: "btn quiet",
      "data-key": `track-${key}`,
    });
  }
  const button = h(
    "button",
    { type: "button", class: "btn", "data-key": `track-${key}`, onclick: () => trackCompany(company, button) },
    icon("plus"),
    h("span", {}, t("track.add")),
  );
  return button;
}

async function trackCompany(company, button) {
  if (isBusy(button)) return;
  setBusy(button, true, t("track.busy"));
  try {
    const app = await api("/api/applications", {
      method: "POST",
      body: { siret: company.siret, company: company.company, url: company.url || "", channel: t("channel.spontaneous") },
    });
    addApplication(app);
    toast(t("toast.tracked"));
  } catch (error) {
    setBusy(button, false);
    toast(t("toast.trackFailed", { message: errorText(error) }));
    return;
  }
  map.closePopup();
  refresh();
}

function searchHead(value) {
  return h(
    "div",
    { class: "panel-head" },
    h(
      "div",
      {},
      h(
        "h2",
        {},
        value
          ? t("search.heading", { results: counted("u.result", value.results.length), q: state.q })
          : t("search.headingLoading", { q: state.q }),
      ),
      h("p", { class: "panel-meta" }, areaMeta("search.metaKinds", "search.meta")),
    ),
    h(
      "button",
      { type: "button", class: "btn quiet", "data-key": "clear-search", "aria-label": t("search.clearFull"), onclick: clearSearch },
      icon("x"),
      h("span", { "aria-hidden": "true" }, t("search.clear")),
    ),
  );
}

function searchView() {
  const res = data.search;
  const value = res.valueKey === searchKey() ? res.value : null;
  if (!value) {
    if (res.error && res.key === searchKey()) {
      return [searchHead(null), errorBox(res.error, t("search.error"), () => runSearch(true))];
    }
    return [searchHead(null), skeleton(state.rewrite ? t("search.loadingRewrite") : t("search.loading"))];
  }
  const nodes = [searchHead(value)];
  if (value.notice) {
    nodes.push(h("div", { class: "notice warn" }, h("p", {}, t(`notice.${value.notice.code}`, { error: value.notice.error }))));
  }
  if (value.query.rewritten) {
    nodes.push(h("p", { class: "hint" }, tn("search.understood", { text: h("span", { lang: "fr" }, value.query.text) })));
    if (value.query.keywords.length) {
      nodes.push(h("ul", { class: "terms", "aria-label": t("search.terms") }, value.query.keywords.map((word) => h("li", {}, word))));
    }
    if (value.query.kinds.length) nodes.push(h("p", { class: "hint" }, t("search.kinds", { kinds: kindsText(value.query.kinds) })));
  }
  if (!value.results.length) {
    nodes.push(
      h(
        "div",
        { class: "empty" },
        icon("magnifying-glass"),
        h("h3", {}, t("search.none.title")),
        h("p", {}, t("search.none.text")),
        h(
          "div",
          { class: "row-actions" },
          widerButton(),
          h("button", { type: "button", class: "btn", onclick: clearSearch }, t("search.clearFull")),
        ),
      ),
    );
    return nodes;
  }
  if (!value.confident) nodes.push(h("div", { class: "notice" }, h("p", {}, t("search.notConfident"))));
  const ranked = value.results.map((result, index) => ({ result, rank: index + 1 }));
  const strong = ranked.filter((item) => !item.result.weak);
  const weak = ranked.filter((item) => item.result.weak);
  if (strong.length) nodes.push(h("ul", { class: "rows" }, strong.map((item) => offerRow(item.result, { rank: item.rank }))));
  if (weak.length) {
    nodes.push(
      h("p", { class: "divider-label" }, t("search.weak")),
      h("ul", { class: "rows" }, weak.map((item) => offerRow(item.result, { rank: item.rank, weak: true }))),
    );
  }
  return nodes;
}

function detailView() {
  const id = state.offer;
  const res = data.offer;
  const back = appLink(
    { offer: null },
    [icon("arrow-left"), h("span", {}, state.q ? t("detail.backResults") : t("detail.backList"))],
    { class: "btn quiet back", "data-key": "back" },
  );
  const offer = res.valueKey === id ? res.value : null;
  if (!offer) {
    if (res.error && res.key === id) {
      const again = () => {
        res.key = null;
        res.error = null;
        ensureOffer(id);
        refresh();
      };
      return [back, errorBox(res.error, t("error.offer"), again)];
    }
    return [back, pending(res, t("loading.offer"))];
  }
  const where = listed(id);
  const french = frenchSource(offer);
  return [
    back,
    h(
      "article",
      { class: "detail", "aria-labelledby": "detail-title" },
      h("h2", { id: "detail-title", tabindex: "-1", lang: french }, offer.title),
      h(
        "p",
        { class: "m" },
        offer.company ? h("span", { translate: "no" }, offer.company) : null,
        offer.company && offer.city ? ", " : null,
        cityName(offer.city),
      ),
      h(
        "p",
        { class: "line" },
        offer.kinds.map(tag),
        offer.weekly_hours ? h("span", { class: "facts" }, hours(offer.weekly_hours)) : null,
        where?.distance_km != null ? h("span", { class: "facts" }, km(where.distance_km)) : null,
        where?.published_at ? h("span", { class: "facts" }, published(where.published_at)) : null,
        where?.precision === "town" ? h("span", { class: "facts" }, t("detail.approx")) : null,
        offer.closed ? h("span", { class: "tag closed" }, t("detail.closed")) : null,
      ),
      offer.closed ? h("div", { class: "notice warn" }, h("p", {}, t("detail.closedNotice"))) : null,
      h("div", { class: "actions" }, sourceLink(offer), trackOfferControl(offer)),
      descriptionSection(offer, french),
      fitSection(offer),
      draftSection(offer),
    ),
  ];
}

function sourceLink(offer) {
  let label = t("detail.sourceSite");
  if (offer.closed) label = t("detail.sourceOld");
  else if (offer.source === "france_travail") label = t("detail.sourceFt");
  return externalLink(offer.url, label, offer.closed ? "btn" : "btn primary");
}

function trackOfferControl(offer) {
  const appId = offer.application_id ?? trackedOffers().get(offer.id)?.id;
  if (appId) {
    return appLink({ view: "tracker", app: appId }, [icon("check"), h("span", {}, t("track.done"))], {
      class: "btn",
      "data-key": "track",
    });
  }
  const button = h(
    "button",
    { type: "button", class: "btn", "data-key": "track", onclick: () => trackOffer(offer, button) },
    icon("plus"),
    h("span", {}, t("track.add")),
  );
  return button;
}

async function trackOffer(offer, button) {
  if (isBusy(button)) return;
  setBusy(button, true, t("track.busy"));
  try {
    const app = await api("/api/applications", { method: "POST", body: { offer_id: offer.id } });
    offer.application_id = app.id;
    addApplication(app);
    toast(t("toast.tracked"));
  } catch (error) {
    setBusy(button, false);
    toast(t("toast.trackFailed", { message: errorText(error) }));
    return;
  }
  if (state.offer === offer.id) renderPanel();
}

function descriptionSection(offer, french) {
  if (!offer.description) return null;
  const long = offer.description.length > 900;
  const open = openDescriptions.has(offer.id);
  const text = h("div", { class: classes("desc", long && !open && "clamped"), id: "offer-desc", lang: french }, offer.description);
  let toggle = null;
  if (long) {
    const label = h("span", {}, open ? t("detail.less") : t("detail.more"));
    toggle = h(
      "button",
      {
        type: "button",
        class: "btn quiet",
        "aria-expanded": String(open),
        "aria-controls": "offer-desc",
        "data-key": "desc-toggle",
        onclick: () => {
          const nowOpen = text.classList.toggle("clamped") === false;
          if (nowOpen) openDescriptions.add(offer.id);
          else openDescriptions.delete(offer.id);
          toggle.setAttribute("aria-expanded", String(nowOpen));
          label.textContent = nowOpen ? t("detail.less") : t("detail.more");
        },
      },
      label,
    );
  }
  return h(
    "section",
    { class: "section", "aria-labelledby": "desc-title" },
    h("h3", { id: "desc-title" }, t("detail.description")),
    french && state.lang !== "fr" ? h("p", { class: "hint" }, t("detail.asPublished")) : null,
    text,
    toggle,
  );
}

function fitSection(offer) {
  const entry = fits.get(`${offer.id}|${state.lang}`);
  const nodes = [h("h3", { id: "fit-title", tabindex: "-1" }, t("fit.title"))];
  if (!entry || entry.status === "error") {
    nodes.push(h("p", { class: "hint" }, t("fit.hint")));
    if (entry) nodes.push(errorBox(entry.error, null, null));
    nodes.push(
      h(
        "button",
        { type: "button", class: "btn", "data-key": "fit", onclick: () => runFit(offer) },
        icon("list-checks"),
        h("span", {}, entry ? t("fit.retry") : t("fit.run")),
      ),
    );
  } else if (entry.status === "loading") {
    nodes.push(
      h("p", { class: "busy-note", role: "status" }, t("fit.wait")),
      h(
        "button",
        { type: "button", class: "btn", "data-key": "fit", "aria-busy": "true", "aria-disabled": "true" },
        icon("list-checks"),
        h("span", {}, t("fit.busy")),
      ),
    );
  } else {
    nodes.push(fitResult(entry.value, offer));
  }
  return h("section", { class: "section", "aria-labelledby": "fit-title" }, nodes);
}

async function runFit(offer, again = false) {
  const lang = state.lang;
  const key = `${offer.id}|${lang}`;
  if (fits.get(key)?.status === "loading") return;
  const hadFocus = $("#panel-body").contains(document.activeElement);
  fits.set(key, { status: "loading" });
  if (state.offer === offer.id) renderPanel();
  try {
    const params = new URLSearchParams({ again: String(again), lang });
    const value = await api(`/api/offers/${offer.id}/fit?${params}`, { method: "POST" });
    fits.set(key, { status: "done", value });
    announce(t("announce.fit"));
  } catch (error) {
    fits.set(key, { status: "error", error });
  }
  if (state.offer !== offer.id || state.view !== "radar") return;
  renderPanel();
  if (hadFocus && !$("#panel-body").contains(document.activeElement)) $("#fit-title")?.focus();
}

function fitResult(fit, offer) {
  const french = frenchSource(offer);
  const groups = [
    ["covered", "check"],
    ["to_confirm", "question"],
    ["missing", "x"],
  ];
  const nodes = [h("p", { class: "summary-text" }, fit.summary)];
  for (const [status, iconName] of groups) {
    const items = fit.requirements.filter((requirement) => requirement.status === status);
    if (!items.length) continue;
    nodes.push(
      h("h4", { class: "group-title" }, `${t(`fit.${status}`)} (${num(items.length)})`),
      h("ul", { class: "reqs" }, items.map((requirement) => requirementItem(requirement, iconName, french))),
    );
  }
  if (fit.projects.length) {
    nodes.push(h("h4", { class: "group-title" }, t("fit.projects")));
    for (const project of fit.projects) {
      nodes.push(
        h(
          "div",
          { class: "project" },
          h("h5", {}, project.title),
          h("p", {}, project.goal),
          h("ol", {}, project.steps.map((step) => h("li", {}, step))),
          h("p", { class: "facts" }, t("fit.shows", { duration: project.duration, shows: project.shows })),
        ),
      );
    }
  }
  nodes.push(
    h("p", { class: "hint" }, fit.cached ? t("fit.cached") : t("fit.saved")),
    h(
      "button",
      { type: "button", class: "btn quiet", "data-key": "fit-again", onclick: () => runFit(offer, true) },
      icon("arrow-clockwise"),
      h("span", {}, t("fit.again")),
    ),
  );
  return nodes;
}

function requirementItem(requirement, iconName, french) {
  const r = requirement;
  return h(
    "li",
    { class: `req ${r.status}` },
    icon(iconName),
    h(
      "span",
      {},
      h("span", { class: "skill" }, r.skill),
      " ",
      h("span", { class: "level" }, r.level === "required" ? t("fit.required") : t("fit.nice")),
    ),
    r.evidence ? h("span", { class: "q" }, tn("fit.offerQuote", { q: h("span", { lang: french }, r.evidence) })) : null,
    r.evidence && !r.evidence_found ? h("span", { class: "warn-line" }, t("fit.offerQuoteMissing")) : null,
    r.profile_evidence ? h("span", { class: "q" }, tn("fit.profileQuote", { q: h("span", {}, r.profile_evidence) })) : null,
    r.profile_evidence && r.profile_evidence_found === false
      ? h("span", { class: "warn-line" }, t("fit.profileQuoteMissing"))
      : null,
    r.suggestion ? h("span", { class: "add" }, tn("fit.suggestion", { s: h("span", {}, r.suggestion) })) : null,
  );
}

function draftSection(offer) {
  const entry = drafts.get(offer.id);
  const nodes = [h("h3", { id: "draft-title", tabindex: "-1" }, t("draft.title"))];
  if (offer.closed) {
    nodes.push(h("p", { class: "hint" }, t("draft.closed")));
  } else if (!entry || entry.status === "error") {
    let hint = t("draft.hint");
    if (isLink(offer.contact)) hint = t("draft.hintLink");
    else if (offer.contact) hint = tn("draft.hintContact", { contact: h("span", { translate: "no" }, offer.contact) });
    nodes.push(h("p", { class: "hint" }, hint));
    if (entry) nodes.push(errorBox(entry.error, null, null));
    nodes.push(
      h(
        "button",
        { type: "button", class: "btn", "data-key": "draft", onclick: () => runDraft(offer) },
        icon("envelope-simple"),
        h("span", {}, entry ? t("draft.retry") : t("draft.run")),
      ),
    );
  } else if (entry.status === "loading") {
    nodes.push(
      h("p", { class: "busy-note", role: "status" }, t("draft.wait")),
      h(
        "button",
        { type: "button", class: "btn", "data-key": "draft", "aria-busy": "true", "aria-disabled": "true" },
        icon("envelope-simple"),
        h("span", {}, t("draft.busy")),
      ),
    );
  } else {
    nodes.push(draftEditor(entry, offer));
  }
  return h("section", { class: "section", "aria-labelledby": "draft-title" }, nodes);
}

async function runDraft(offer) {
  const current = drafts.get(offer.id);
  if (current?.status === "loading") return;
  const previous = current?.status === "done" ? current : null;
  const hadFocus = $("#panel-body").contains(document.activeElement);
  drafts.set(offer.id, { status: "loading" });
  if (state.offer === offer.id) renderPanel();
  try {
    const value = await api(`/api/offers/${offer.id}/draft`, { method: "POST" });
    drafts.set(offer.id, {
      status: "done",
      subject: value.subject,
      body: value.body,
      original: { subject: value.subject, body: value.body },
      facts: value.facts,
      contact: value.contact,
      previous,
    });
    setDirty(`draft-${offer.id}`, false);
    announce(t("announce.draft"));
  } catch (error) {
    if (previous) {
      drafts.set(offer.id, previous);
      toast(errorText(error));
    } else {
      drafts.set(offer.id, { status: "error", error });
    }
  }
  if (state.offer !== offer.id || state.view !== "radar") return;
  renderPanel();
  if (hadFocus && !$("#panel-body").contains(document.activeElement)) $("#draft-title")?.focus();
}

function draftEditor(entry, offer) {
  const dirtyKey = `draft-${offer.id}`;
  const mail = isEmail(entry.contact)
    ? h("a", { class: "btn", "data-key": "draft-mail" }, icon("envelope-simple"), h("span", {}, t("draft.mail")))
    : null;
  const changed = () => {
    setDirty(dirtyKey, entry.subject !== entry.original.subject || entry.body !== entry.original.body);
    if (mail) {
      mail.href = `mailto:${entry.contact}?subject=${encodeURIComponent(entry.subject)}&body=${encodeURIComponent(entry.body)}`;
    }
  };
  const subject = h("input", {
    type: "text",
    name: "subject",
    value: entry.subject,
    autocomplete: "off",
    "data-key": "draft-subject",
    oninput: (event) => {
      entry.subject = event.target.value;
      changed();
    },
  });
  const body = h("textarea", {
    name: "body",
    rows: "12",
    value: entry.body,
    autocomplete: "off",
    "data-key": "draft-body",
    oninput: (event) => {
      entry.body = event.target.value;
      changed();
    },
  });
  changed();
  const french = frenchSource(offer);
  return [
    h("p", { class: "hint" }, contactLine(entry.contact)),
    h("label", { class: "draft-field" }, t("draft.subject"), subject),
    h("label", { class: "draft-field" }, t("draft.body"), body),
    h(
      "div",
      { class: "actions" },
      h(
        "button",
        { type: "button", class: "btn primary", "data-key": "draft-copy", onclick: () => copyDraft(entry) },
        icon("copy"),
        h("span", {}, t("draft.copy")),
      ),
      mail,
      h(
        "button",
        { type: "button", class: "btn quiet", "data-key": "draft-again", onclick: () => runDraft(offer) },
        icon("arrow-clockwise"),
        h("span", {}, t("draft.again")),
      ),
      entry.previous
        ? h(
            "button",
            {
              type: "button",
              class: "btn quiet",
              "data-key": "draft-restore",
              onclick: () => {
                const previous = entry.previous;
                drafts.set(offer.id, { ...previous, previous: null });
                setDirty(dirtyKey, previous.subject !== previous.original.subject || previous.body !== previous.original.body);
                renderPanel();
                $('[data-key="draft-body"]')?.focus();
              },
            },
            h("span", {}, t("draft.restore")),
          )
        : null,
    ),
    h("h4", { class: "group-title" }, t("draft.facts")),
    h(
      "ul",
      { class: "facts-list" },
      entry.facts.map((fact) =>
        h(
          "li",
          { class: classes("fact", fact.found ? "ok" : "ko") },
          icon(fact.found ? "check" : "warning"),
          h(
            "span",
            {},
            h("span", { lang: french }, fact.claim),
            " ",
            h(
              "span",
              { class: "facts" },
              "(",
              fact.found ? tn("draft.factFound", { q: h("span", {}, fact.profile_quote) }) : t("draft.factMissing"),
              ")",
            ),
          ),
        ),
      ),
    ),
  ];
}

function contactLine(contact) {
  if (isLink(contact)) return [t("draft.toLink"), " ", externalLink(contact, t("draft.applyLink"))];
  if (contact) return tn("draft.to", { contact: h("span", { translate: "no" }, contact) });
  return t("draft.noContact");
}

async function copyDraft(entry) {
  try {
    await navigator.clipboard.writeText(`${entry.subject}\n\n${entry.body}`);
    toast(t("draft.copied"));
  } catch {
    toast(t("draft.copyFailed"));
  }
}

function openOffer(id) {
  focusTitleNext = true;
  navigate({ offer: id }, { push: true });
}

function closeOffer() {
  navigate({ offer: null }, { push: true });
}

function chooseTown(town) {
  const input = $("#town");
  hideError($("#town-error"), input);
  input.value = town.name;
  navigate({ town: { name: town.name, lat: town.lat, lon: town.lon }, place: "", offer: null }, { push: true });
}

function clearSearch() {
  cancel(data.search);
  setSearchBusy(false);
  const input = $("#q");
  input.value = "";
  hideError($("#search-error"), input);
  navigate({ q: "", offer: null }, { push: true });
  input.focus();
}

// --- the tracker -------------------------------------------------------------------------------

const unsaved = new Map(); // "notes-12" -> what is typed but not saved yet
const deleting = new Map(); // application id -> timer of its undo window
const dirty = new Set();
let closedGroupOpen = false;
let highlighted = null;

function setDirty(key, on) {
  if (on) dirty.add(key);
  else dirty.delete(key);
}

function isDue(app) {
  return ["sent", "followed_up"].includes(app.status) && Boolean(app.follow_up_on) && daysAgo(app.follow_up_on) >= 0;
}

function statsOf(apps) {
  const by = Object.fromEntries(STATUSES.map((status) => [status, 0]));
  for (const app of apps) by[app.status] += 1;
  const sent = by.sent + by.followed_up + by.interview + by.offer + by.rejected;
  const answered = by.interview + by.offer + by.rejected;
  return { by, sent, answered, rate: sent ? answered / sent : null, due: apps.filter(isDue).length };
}

const liveApps = () => (data.apps.value?.applications ?? []).filter((app) => !deleting.has(app.id));

function updateDueBadge() {
  const due = statsOf(liveApps()).due;
  const badge = $("#due-badge");
  badge.hidden = due === 0;
  badge.replaceChildren(num(due), h("span", { class: "sr-only" }, ` ${t("u.followUpWord", { n: due })}`));
}

function renderSummary() {
  const element = $("#summary");
  const apps = liveApps();
  if (!apps.length) {
    element.replaceChildren();
    return;
  }
  const s = statsOf(apps);
  const parts = [h("strong", {}, counted("u.sent", s.sent))];
  if (s.sent) parts.push(", ", counted("u.answer", s.answered), s.rate === null ? "" : ` (${fmt.percent.format(s.rate)})`);
  parts.push(".");
  if (s.by.to_apply) parts.push(" ", counted("u.toSend", s.by.to_apply), ".");
  if (s.due) parts.push(" ", h("span", { class: "alert" }, counted("u.followUp", s.due)), ".");
  element.replaceChildren(...parts);
}

function renderTracker() {
  renderSummary();
  const root = $("#applications");
  const active = document.activeElement;
  const focusKey = root.contains(active) ? active.dataset.key : null;
  const res = data.apps;
  let nodes;
  if (!res.value) {
    nodes = res.error ? [errorBox(res.error, t("tracker.error"), loadApplications)] : skeleton(t("tracker.loading"));
  } else if (!res.value.applications.length) {
    nodes = [trackerEmpty()];
  } else {
    nodes = trackerGroups(res.value.applications);
  }
  root.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  if (focusKey) root.querySelector(`[data-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
  highlightApp();
}

function trackerEmpty() {
  return h(
    "div",
    { class: "empty" },
    icon("list-checks"),
    h("h3", {}, t("tracker.empty.title")),
    h("p", {}, t("tracker.empty.text")),
    h(
      "div",
      { class: "row-actions" },
      appLink({ view: "radar", app: null }, [icon("map-pin"), h("span", {}, t("tracker.toRadar"))], { class: "btn" }),
    ),
  );
}

function trackerGroups(apps) {
  const groups = [
    ["due", (app) => isDue(app)],
    ["to_apply", (app) => app.status === "to_apply"],
    ["waiting", (app) => app.status === "sent" || app.status === "followed_up"],
    ["answers", (app) => app.status === "interview" || app.status === "offer"],
    ["closed", (app) => app.status === "rejected" || app.status === "dropped"],
  ];
  const placed = new Set();
  const nodes = [];
  for (const [name, test] of groups) {
    const items = apps.filter((app) => !placed.has(app.id) && test(app));
    if (!items.length) continue;
    for (const app of items) placed.add(app.id);
    const title = h("h3", { id: `group-${name}` }, t(`group.${name}`), " ", h("span", { class: "n" }, `(${num(items.length)})`));
    const list = h("ul", { class: "apps" }, items.map((app) => (deleting.has(app.id) ? deletedRow(app) : appRow(app))));
    if (name === "closed") {
      const details = h(
        "details",
        { class: "group", open: closedGroupOpen || items.some((app) => app.id === state.app) },
        h("summary", {}, title),
        list,
      );
      details.addEventListener("toggle", () => {
        closedGroupOpen = details.open;
      });
      nodes.push(details);
    } else {
      nodes.push(h("section", { class: classes("group", name === "due" && "due"), "aria-labelledby": `group-${name}` }, title, list));
    }
  }
  return nodes;
}

function appRow(app) {
  const id = app.id;
  const saved = h("span", { class: "saved", "aria-hidden": "true" });
  const field = (label, control, wide = false) => h("label", { class: wide ? "wide" : null }, label, control);
  const status = h(
    "select",
    { name: "status", "data-key": `status-${id}`, onchange: (event) => saveApp(id, { status: event.target.value }, saved, true) },
    STATUSES.map((value) => h("option", { value }, t(`status.${value}`))),
  );
  status.value = app.status;
  const date = (name, key) =>
    h("input", {
      type: "date",
      name,
      value: app[name] ?? "",
      "data-key": `${key}-${id}`,
      onchange: (event) => saveApp(id, { [name]: event.target.value || null }, saved, true),
    });
  const text = (name, placeholderKey) =>
    h("input", {
      type: "text",
      name,
      value: app[name],
      autocomplete: "off",
      spellcheck: name === "contact" ? "false" : null,
      placeholder: t(placeholderKey),
      "data-key": `${name}-${id}`,
      onchange: (event) => saveApp(id, { [name]: event.target.value.trim() }, saved),
    });
  const notesKey = `notes-${id}`;
  let notesTimer = 0;
  const notes = h("textarea", {
    name: "notes",
    rows: "1",
    value: unsaved.get(notesKey) ?? app.notes,
    autocomplete: "off",
    placeholder: t("placeholder.notes"),
    "data-key": notesKey,
    oninput: (event) => {
      unsaved.set(notesKey, event.target.value);
      setDirty(notesKey, event.target.value !== findApp(id)?.notes);
      clearTimeout(notesTimer);
      notesTimer = setTimeout(() => saveNotes(id, event.target, saved), 1200);
    },
    onchange: (event) => {
      clearTimeout(notesTimer);
      saveNotes(id, event.target, saved);
    },
  });
  const company = app.company || t("app.unknownCompany");
  return h(
    "li",
    { class: "app", id: `app-${id}`, "data-id": id },
    h(
      "div",
      { class: "who" },
      h("div", { class: "t", translate: "no" }, company),
      app.title ? h("div", { class: "m" }, app.title) : null,
      h(
        "div",
        { class: "app-tags" },
        app.offer_id && app.offer_open === false ? h("span", { class: "tag closed" }, t("app.closedOffer")) : null,
        app.offer_id ? null : h("span", { class: "tag" }, t("app.spontaneous")),
      ),
      h(
        "div",
        { class: "app-links" },
        app.offer_id ? appLink({ view: "radar", offer: app.offer_id, app: null }, t("app.openOffer")) : null,
        externalLink(app.url, app.offer_id ? t("app.sourcePage") : t("employers.page")),
      ),
    ),
    h(
      "div",
      { class: "app-fields" },
      field(t("field.status"), status),
      field(t("app.applied"), date("applied_on", "applied")),
      field(t("app.followUp"), date("follow_up_on", "follow")),
      field(t("app.channel"), text("channel", "placeholder.channel")),
      field(t("app.contact"), text("contact", "placeholder.contact")),
      field(t("app.notes"), notes, true),
    ),
    h(
      "div",
      { class: "app-side" },
      saved,
      h(
        "button",
        {
          type: "button",
          class: "btn quiet icon-only",
          "aria-label": t("app.delete", { company: app.company || t("app.thisCompany") }),
          title: t("app.deleteTitle"),
          "data-key": `delete-${id}`,
          onclick: () => removeApplication(id),
        },
        icon("trash"),
      ),
    ),
  );
}

function showSaved(element, message, bad = false) {
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("bad", bad);
  clearTimeout(element.timer);
  if (!bad && message === t("app.saved")) {
    element.timer = setTimeout(() => {
      element.textContent = "";
    }, 2500);
  }
}

async function saveApp(id, patch, saved, regroup = false) {
  showSaved(saved, t("app.saving"));
  try {
    const updated = await api(`/api/applications/${id}`, { method: "PATCH", body: patch });
    replaceApp(updated);
    if (regroup) renderTracker();
    else renderSummary();
    showSaved($(`#app-${id} .saved`) ?? saved, t("app.saved"));
    announce(t("app.saved"));
    updateDueBadge();
  } catch (error) {
    const message = t("app.saveFailed", { message: errorText(error) });
    showSaved($(`#app-${id} .saved`) ?? saved, message, true);
    announce(message);
  }
}

async function saveNotes(id, textarea, saved) {
  const key = `notes-${id}`;
  const value = textarea.value;
  if (value === findApp(id)?.notes) {
    unsaved.delete(key);
    setDirty(key, false);
    return;
  }
  await saveApp(id, { notes: value }, saved);
  if (findApp(id)?.notes === value && unsaved.get(key) === value) {
    unsaved.delete(key);
    setDirty(key, false);
  }
}

/* Deleting is never immediate: the row turns into an undo button for a few seconds, and the
   window stays open while that button has focus or the pointer. */
function removeApplication(id) {
  const app = findApp(id);
  if (!app) return;
  armDelete(id, UNDO_MS);
  renderTracker();
  updateDueBadge();
  $(`[data-key="undo-${id}"]`)?.focus({ preventScroll: true });
  announce(t("app.deleted", { company: app.company || t("app.thisCompany") }));
}

function armDelete(id, ms) {
  clearTimeout(deleting.get(id));
  deleting.set(id, setTimeout(() => commitDelete(id), ms));
}

function pauseDelete(id) {
  if (deleting.has(id)) {
    clearTimeout(deleting.get(id));
    deleting.set(id, 0);
  }
}

function deletedRow(app) {
  const id = app.id;
  return h(
    "li",
    { class: "app deleted", id: `app-${id}`, "data-id": id },
    h("p", {}, t("app.deleted", { company: app.company || t("app.thisCompany") })),
    h(
      "button",
      {
        type: "button",
        class: "btn",
        "data-key": `undo-${id}`,
        onclick: () => undoDelete(id),
        onfocus: () => pauseDelete(id),
        onmouseenter: () => pauseDelete(id),
        onblur: () => deleting.has(id) && armDelete(id, 3000),
        onmouseleave: () => deleting.has(id) && document.activeElement?.dataset.key !== `undo-${id}` && armDelete(id, 3000),
      },
      icon("arrow-clockwise"),
      h("span", {}, t("undo")),
    ),
  );
}

function undoDelete(id) {
  clearTimeout(deleting.get(id));
  deleting.delete(id);
  renderTracker();
  updateDueBadge();
  $(`[data-key="status-${id}"]`)?.focus();
  announce(t("app.restored"));
}

async function commitDelete(id, keepalive = false) {
  if (!deleting.has(id)) return;
  clearTimeout(deleting.get(id));
  deleting.delete(id);
  const apps = data.apps.value?.applications ?? [];
  const index = apps.findIndex((app) => app.id === id);
  if (index < 0) return;
  const [app] = apps.splice(index, 1);
  const hadFocus = document.activeElement?.dataset.key === `undo-${id}`;
  if (!keepalive) {
    renderTracker();
    if (hadFocus) $("#tracker-title").focus({ preventScroll: true });
  }
  try {
    await api(`/api/applications/${id}`, { method: "DELETE", keepalive });
    for (const offer of offerCache.values()) if (offer.application_id === id) offer.application_id = null;
  } catch (error) {
    if (error.code === "application_not_found") return;
    apps.splice(Math.min(index, apps.length), 0, app);
    renderTracker();
    updateDueBadge();
    toast(t("app.deleteFailed", { message: errorText(error) }));
  }
}

function highlightApp() {
  if (!state.app || highlighted === state.app) return;
  const row = document.getElementById(`app-${state.app}`);
  if (!row) return;
  highlighted = state.app;
  row.scrollIntoView({ block: "center", behavior: reducedMotion.matches ? "auto" : "smooth" });
  row.classList.add("flash");
  setTimeout(() => row.classList.remove("flash"), 1800);
}

// --- the whole page ----------------------------------------------------------------------------

function applyStatic() {
  document.documentElement.lang = state.lang;
  for (const element of document.querySelectorAll("[data-i18n]")) element.textContent = t(element.dataset.i18n);
  for (const element of document.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of element.dataset.i18nAttr.split(",")) {
      const [attribute, key] = pair.split("=").map((part) => part.trim());
      element.setAttribute(attribute, t(key));
    }
  }
}

function applyLanguage() {
  setFormats();
  applyStatic();
  if (map) addZoomControl();
  if (data.search.loading) setSearchBusy(true);
}

function syncControls() {
  document.body.dataset.view = state.view;
  const town = $("#town");
  if (document.activeElement !== town) town.value = state.town?.name ?? "";
  $("#radius").value = String(state.radius);
  showRadius(state.radius);
  for (const chip of document.querySelectorAll(".chip")) {
    chip.setAttribute("aria-pressed", String(state.kinds.includes(chip.dataset.kind)));
  }
  $("#rewrite").checked = state.rewrite;
  const q = $("#q");
  if (document.activeElement !== q) q.value = state.q;
  $("#tab-radar").href = hrefFor({ view: "radar", app: null });
  $("#tab-tracker").href = hrefFor({ view: "tracker" });
  for (const [selector, current] of [["#tab-radar", state.view === "radar"], ["#tab-tracker", state.view === "tracker"]]) {
    if (current) $(selector).setAttribute("aria-current", "page");
    else $(selector).removeAttribute("aria-current");
  }
  const other = t("lang.other");
  const link = $("#lang-link");
  link.href = hrefFor({ lang: other });
  link.hreflang = other;
  link.lang = other;
  link.textContent = t("lang.otherName");
}

function showRadius(radius) {
  $("#radius-out").textContent = km(radius);
  $("#radius").setAttribute("aria-valuetext", t("radius.valuetext", { n: num(radius) }));
}

function updateTitle() {
  if (state.view === "tracker") document.title = t("title.tracker");
  else if (state.offer && data.offer.valueKey === state.offer) document.title = `${data.offer.value.title} | Job Radar`;
  else if (state.town) document.title = `${state.town.name} | Job Radar`;
  else document.title = "Job Radar";
}

function render(before = {}) {
  const languageChanged = before.lang !== state.lang;
  if (languageChanged) applyLanguage();
  syncControls();
  updateTitle();
  const tracker = state.view === "tracker";
  $("#view-radar").hidden = tracker;
  $("#view-tracker").hidden = !tracker;
  if (tracker) {
    if (before.view !== "tracker" && !data.apps.loading) loadApplications();
    renderTracker();
    return;
  }
  map.invalidateSize({ pan: false });
  const areaChanged = !sameTown(before.town, state.town) || before.radius !== state.radius;
  if (areaChanged || needsFit || languageChanged || before.view === "tracker") {
    drawArea({ fit: areaChanged || needsFit, ping: areaChanged });
    needsFit = false;
  }
  ensureData();
  renderPanel();
  drawMarkers();
  if (before.offer !== state.offer) pannedTo = null;
  panToOffer();
}

function wireControls() {
  const townInput = $("#town");
  let townOptions = [];
  let townTimer = 0;
  let townController = null;
  const townLabel = (town) => (town.postal_codes?.length ? `${town.name} (${town.postal_codes[0]})` : town.name);
  const fetchTowns = async (text) => {
    townController?.abort();
    townController = new AbortController();
    try {
      return await api(`/api/towns?${new URLSearchParams({ q: text })}`, { signal: townController.signal });
    } catch (error) {
      if (error.name !== "AbortError") showError($("#town-error"), null, errorText(error));
      return null;
    }
  };
  townInput.addEventListener("input", () => {
    hideError($("#town-error"), townInput);
    clearTimeout(townTimer);
    const picked = townOptions.find((town) => townLabel(town) === townInput.value);
    if (picked) {
      chooseTown(picked);
      return;
    }
    townTimer = setTimeout(async () => {
      const text = townInput.value.trim();
      if (text.length < 2) return;
      const found = await fetchTowns(text);
      if (!found) return;
      townOptions = found;
      $("#town-list").replaceChildren(...found.map((town) => h("option", { value: townLabel(town) })));
    }, 250);
  });
  townInput.addEventListener("focus", () => townInput.select());
  $("#town-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTimeout(townTimer);
    const text = townInput.value.trim();
    if (text.length < 2) {
      showError($("#town-error"), townInput, t("town.short"));
      return;
    }
    const lower = text.toLocaleLowerCase("fr-FR");
    const exact = townOptions.find((town) => townLabel(town) === text || town.name.toLocaleLowerCase("fr-FR") === lower);
    if (exact) {
      chooseTown(exact);
      return;
    }
    const found = await fetchTowns(text);
    if (!found) return;
    if (found.length) chooseTown(found[0]);
    else showError($("#town-error"), townInput, t("town.notFound", { q: text }));
  });

  const radius = $("#radius");
  radius.addEventListener("input", () => {
    const value = Number(radius.value);
    showRadius(value);
    drawArea({ radius: value });
  });
  radius.addEventListener("change", () => navigate({ radius: Number(radius.value) }));

  $("#kinds").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const kind = chip.dataset.kind;
    const kinds = state.kinds.includes(kind) ? state.kinds.filter((k) => k !== kind) : [...state.kinds, kind];
    navigate({ kinds: KINDS.filter((k) => kinds.includes(k)), place: "" });
  });

  $("#rewrite").addEventListener("change", (event) => navigate({ rewrite: event.target.checked }));

  const q = $("#q");
  q.addEventListener("input", () => hideError($("#search-error"), q));
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (isBusy($("#search-submit"))) return;
    const text = q.value.trim();
    hideError($("#search-error"), q);
    if (!text) {
      if (state.q) clearSearch();
      else showError($("#search-error"), q, t("search.empty"));
      return;
    }
    if (text.length < 2) {
      showError($("#search-error"), q, t("search.short"));
      return;
    }
    if (!state.town) {
      showError($("#search-error"), null, t("search.needTown"));
      townInput.focus();
      return;
    }
    if (text === state.q && !state.offer) return;
    navigate({ q: text, offer: null }, { push: true });
  });

  for (const [selector, view] of [["#tab-radar", "radar"], ["#tab-tracker", "tracker"]]) {
    $(selector).addEventListener("click", (event) => {
      if (!plainClick(event)) return;
      event.preventDefault();
      if (state.view !== view) navigate({ view, app: null }, { push: true });
    });
  }

  $("#lang-link").addEventListener("click", (event) => {
    if (!plainClick(event)) return;
    event.preventDefault();
    const lang = t("lang.other");
    local.set("lang", lang);
    navigate({ lang });
    announce(t("lang.name"));
  });

  wireSpontaneousForm();

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (event.key === "/" && !typing && state.view === "radar") {
      event.preventDefault();
      q.focus();
    } else if (event.key === "Escape" && !typing && state.offer && state.view === "radar" && !$(".leaflet-popup")) {
      event.preventDefault();
      closeOffer();
    }
  });

  window.addEventListener("popstate", () => {
    const before = { ...state };
    readUrl();
    render(before);
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty.size === 0) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pagehide", () => {
    for (const id of [...deleting.keys()]) commitDelete(id, true);
  });
}

function wireSpontaneousForm() {
  const form = $("#spontaneous-form");
  const company = form.elements.company;
  company.addEventListener("input", () => hideError($("#company-error"), company));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    if (isBusy(submit)) return;
    hideError($("#spontaneous-error"));
    const name = company.value.trim();
    if (!name) {
      showError($("#company-error"), company, t("form.companyRequired"));
      return;
    }
    setBusy(submit, true, t("form.busy"));
    try {
      const app = await api("/api/applications", {
        method: "POST",
        body: {
          company: name,
          status: form.elements.status.value,
          channel: form.elements.channel.value.trim(),
          contact: form.elements.contact.value.trim(),
          notes: form.elements.notes.value.trim(),
        },
      });
      addApplication(app);
      form.reset();
      renderTracker();
      toast(t("app.added"));
      company.focus();
    } catch (error) {
      showError($("#spontaneous-error"), null, errorText(error));
    } finally {
      setBusy(submit, false);
    }
  });
}

function init() {
  restoreLast();
  readUrl();
  setFormats();
  applyStatic();
  initMap();
  wireControls();
  commit(false);
  loadApplications();
  render({ lang: state.lang });
}

init();
