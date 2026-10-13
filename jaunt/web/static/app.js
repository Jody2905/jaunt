// Jaunt's front end: plain JavaScript, no framework, no build step.
// It talks only to the JSON API (/api/...). Every piece of text from the server is
// put on the page with textContent, never as HTML, so nothing a user types can run as code.

const view = document.getElementById("view");
const state = { user: null, csrf: null, places: null };
let ticker = null;                     // the one-second timer for hold countdowns

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;
    else if (key in el && key !== "list" && key !== "form") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const ICONS = {
  stay: "M3 11l9-7 9 7 M5 9.5V20h14V9.5 M10 20v-5h4v5",
  flight: "M10.5 13.5L3 11l1.5-1.5 8 1 4.5-5c1-1 2.5-1.2 3-.6.6.5.4 2-.6 3l-5 4.5 1 8L14 22l-2.5-7.5z",
  tour: "M12 3v13 M12 4l7 11h-7 M11 7l-6 9h6 M3 19c3 2 15 2 18 0",
  car: "M3 16v-3l2-5h14l2 5v3z M6 16v2.5 M18 16v2.5 M7 12.5h.01 M17 12.5h.01",
  booking: "M4 7h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z",
  food: "M3 11h18a9 9 0 0 1-18 0z M9 7c0-1.5 1.5-1.5 1.5-3 M14 7c0-1.5 1.5-1.5 1.5-3",
  activity: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v2 M12 20v2 M2 12h2 M20 12h2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  transport: "M3 14h15l3-4H6z M3 18c3 1.5 6-1.5 9 0s6 1.5 9 0",
  note: "M6 3h9l4 4v14H6z M9 12h7 M9 16h5",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
  bin: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
  pause: "M8 5v14 M16 5v14",
  play: "M7 4l13 8-13 8z",
};

function icon(name, label) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i");
  if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); }
  else svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.note);
  svg.append(path);
  return svg;
}

const money = (cents) =>
  "US$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const parseDay = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const isoDay = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const addDays = (iso, n) => { const d = parseDay(iso); d.setDate(d.getDate() + n); return isoDay(d); };
const dayLabel = (iso) => {
  const d = parseDay(iso);
  return `${d.toLocaleDateString("en-GB", { weekday: "short" })} ${d.getDate()} ${d.toLocaleDateString("en-GB", { month: "short" })}`;
};
const clock = (iso) => iso.slice(11, 16);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const PAINT = { SVD: "var(--lagoon)", BQU: "var(--hibiscus)", BGI: "var(--mango)", UVF: "var(--shutter)", GND: "var(--lagoon-deep)" };

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(method, path, body) {
  const options = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (method !== "GET" && state.csrf) options.headers["X-CSRF-Token"] = state.csrf;
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError("Can't reach Jaunt. Is the server still running?", 0);
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/me" && !path.startsWith("/api/auth/")) {
      signedOut();
      go("#/login");
    }
    throw new ApiError(data.detail || `Something went wrong (error ${response.status})`, response.status);
  }
  return data;
}

function toast(message, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, message);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 4000);
}

/** Ask a yes/no question in the confirm box. Resolves true for yes. */
function ask(title, lines, yes, no) {
  const dialog = document.getElementById("ask");
  document.getElementById("ask-title").textContent = title;
  document.getElementById("ask-body").replaceChildren(...lines.map((line) => h("p", {}, line)));
  document.getElementById("ask-yes").textContent = yes;
  document.getElementById("ask-no").textContent = no;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true }));
}

/** Disable a button while its action runs, so a double click can't do it twice. */
async function busy(button, action) {
  if (button) button.disabled = true;
  try { return await action(); }
  catch (e) { toast(e.message, true); }
  finally { if (button && button.isConnected) button.disabled = false; }
}

async function places() {
  if (!state.places) state.places = await api("GET", "/api/places");
  return state.places;
}

// ---------------------------------------------------------------------------
// Signing in and the menu
// ---------------------------------------------------------------------------

function signedIn(me) { state.user = me.user; state.csrf = me.csrf_token; nav(); }
function signedOut() { state.user = null; state.csrf = null; nav(); }

function nav() {
  const links = state.user
    ? [h("a", { href: "#/trips" }, "Your trips"),
       h("button", { class: "link", type: "button", onclick: logOut }, "Log out")]
    : [h("a", { href: "#/login" }, "Log in"), h("a", { class: "btn primary small", href: "#/register" }, "Sign up")];
  document.getElementById("nav").replaceChildren(...links);
}

async function logOut() {
  try { await api("POST", "/api/auth/logout"); } catch { /* the session may already be gone */ }
  signedOut();
  toast("Logged out. See you on the next one.");
  go("#/");
}

function go(hash) {
  if (location.hash === hash) route(); else location.hash = hash;
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

const ROUTES = [
  [/^#?\/?$/, home],
  [/^#\/login$/, loginScreen],
  [/^#\/register$/, registerScreen],
  [/^#\/trips$/, tripsScreen, true],
  [/^#\/trips\/(\d+)$/, tripScreen, true],
];

async function route() {
  clearInterval(ticker);
  const hash = location.hash || "#/";
  let found = null;
  for (const [pattern, screenFn, isPrivate] of ROUTES) {
    const m = hash.match(pattern);
    if (m) { found = { screen: screenFn, private_: isPrivate, params: m.slice(1) }; break; }
  }
  const { screen, private_, params } = found || { screen: home, private_: false, params: [] };
  document.body.classList.toggle("on-hero", screen === home);
  if (private_ && !state.user) {
    state.afterLogin = hash;
    return go("#/login");
  }
  try {
    await screen(...params);
  } catch (e) {
    if (e.status === 404) view.replaceChildren(page(h("h1", {}, "That trip isn't here"),
      h("p", { class: "muted" }, "It may have been deleted, or it belongs to someone else."),
      h("p", {}, h("a", { href: "#/trips" }, "Back to your trips"))));
    else if (e.status !== 401) toast(e.message, true);
  }
  if (screen !== home) view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

const page = (...children) => h("div", { class: "page" }, ...children);

// ---- Home, with the video header ----------------------------------------------

async function home() {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const video = h("video", { muted: true, loop: true, playsInline: true, preload: reduce ? "none" : "auto",
                             poster: "/static/video/hero.jpg", "aria-hidden": "true", tabindex: "-1" },
    h("source", { src: "/static/video/hero.webm", type: "video/webm" }),
    h("source", { src: "/static/video/hero.mp4", type: "video/mp4" }));
  video.muted = true;                                  // must be set as a property for autoplay to be allowed

  const label = h("span", {});
  const toggle = h("button", { class: "motion-toggle", type: "button" });
  const show = (playing) => {
    toggle.replaceChildren(icon(playing ? "pause" : "play"), label);
    label.textContent = playing ? "Pause animation" : "Play animation";
    toggle.setAttribute("aria-pressed", String(!playing));
  };
  toggle.addEventListener("click", () => {
    if (video.paused) video.play().then(() => show(true)).catch(() => show(false));
    else { video.pause(); show(false); }
  });
  show(false);
  if (!reduce) video.play().then(() => show(true)).catch(() => show(false));

  const start = state.user ? "#/trips" : "#/register";
  const hero = h("section", { class: "hero", "aria-labelledby": "hero-title" },
    video,
    h("div", { class: "hero-copy" },
      h("h1", { class: "hero-title", id: "hero-title" }, h("span", {}, "Go on,"), h("span", {}, "take a jaunt.")),
      h("p", { class: "hero-lede" }, "Plan your island days, then book the stays, hops, tours and wheels to go with them."),
      h("div", { class: "hero-actions" },
        h("a", { class: "btn primary", href: start }, state.user ? "Open your trips" : "Start planning"),
        !state.user && h("a", { class: "btn", href: "#/login" }, "Log in"))),
    toggle);

  const islands = h("section", { class: "islands", "aria-labelledby": "islands-title" },
    h("h2", { id: "islands-title" }, "Five islands to start with"),
    h("p", {}, "Each one has its own guesthouses, island-hopper flights, boat trips and little hire cars."),
    h("ul", { class: "island-row" }));
  view.replaceChildren(hero, islands,
    h("p", { class: "demo-note" }, "Jaunt is a portfolio project. Every hotel, flight, tour and price here is invented, and no payment is ever taken."));
  const list = islands.querySelector("ul");
  for (const p of await places()) {
    list.append(h("li", { class: "island", style: `--paint: ${PAINT[p.code] || "var(--lagoon)"}` },
      h("div", { class: "island-wall" },
        h("h3", {}, p.name), h("p", { class: "country" }, p.country), h("p", {}, p.blurb))));
  }
}

// ---- Log in and sign up ------------------------------------------------------------

function field(label, input, hint) {
  input.id ||= "f-" + Math.random().toString(36).slice(2, 8);
  return h("div", { class: "field" }, h("label", { for: input.id }, label), input, hint && h("span", { class: "hint" }, hint));
}

function loginScreen() {
  if (state.user) return go("#/trips");
  const email = h("input", { type: "email", name: "email", autocomplete: "username", required: true });
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const error = h("p", { class: "error", role: "alert" });
  const form = h("form", { class: "card narrow", novalidate: false },
    h("h1", {}, "Welcome back"),
    field("Email", email), field("Password", password), error,
    h("div", { class: "form-actions" }, h("button", { class: "btn primary", type: "submit" }, "Log in"),
      h("a", { href: "#/register" }, "New here? Create an account")),
    h("div", { class: "demo-hint" },
      "Just looking? Use the demo account: ", h("code", {}, "jo@example.test"), " with password ", h("code", {}, "Jaunt-2026-demo"), ". ",
      h("button", { class: "btn quiet small", type: "button", onclick: () => {
        email.value = "jo@example.test"; password.value = "Jaunt-2026-demo"; form.requestSubmit();
      } }, "Log in as Jo")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        signedIn(await api("POST", "/api/auth/login", { email: email.value, password: password.value }));
        go(state.afterLogin || "#/trips");
        state.afterLogin = null;
      } catch (err) { error.textContent = err.message; password.select(); }
    });
  });
  view.replaceChildren(page(form));
  email.focus();
}

function registerScreen() {
  if (state.user) return go("#/trips");
  const name = h("input", { name: "name", autocomplete: "given-name", required: true, maxLength: 80 });
  const email = h("input", { type: "email", name: "email", autocomplete: "email", required: true });
  const password = h("input", { type: "password", name: "password", autocomplete: "new-password", required: true, minLength: 10 });
  const error = h("p", { class: "error", role: "alert" });
  const form = h("form", { class: "card narrow" },
    h("h1", {}, "Start planning"),
    field("Your name", name), field("Email", email), field("Password", password, "At least 10 characters. A short phrase works well."),
    error,
    h("div", { class: "form-actions" }, h("button", { class: "btn primary", type: "submit" }, "Create account"),
      h("a", { href: "#/login" }, "Already have one? Log in")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        signedIn(await api("POST", "/api/auth/register", { name: name.value, email: email.value, password: password.value }));
        toast(`Welcome aboard, ${state.user.name}.`);
        go("#/trips");
      } catch (err) { error.textContent = err.message; }
    });
  });
  view.replaceChildren(page(form));
  name.focus();
}

// ---- Your trips -----------------------------------------------------------------------

async function tripsScreen() {
  const [trips, islands] = await Promise.all([api("GET", "/api/trips"), places()]);
  const tomorrow = isoDay(new Date(Date.now() + 86400000));
  const title = h("input", { required: true, maxLength: 100, placeholder: "Bequia birthday week" });
  const island = h("select", { required: true }, islands.map((p) => h("option", { value: p.id }, p.name)));
  const first = h("input", { type: "date", required: true, min: isoDay(new Date()), value: tomorrow });
  const last = h("input", { type: "date", required: true, min: isoDay(new Date()), value: addDays(tomorrow, 4) });
  const travellers = h("input", { type: "number", min: 1, max: 9, value: 2, required: true });
  const budget = h("input", { type: "number", min: 0, step: "0.01", value: 2000, inputMode: "decimal" });
  const error = h("p", { class: "error", role: "alert" });
  first.addEventListener("change", () => { if (last.value < first.value) last.value = addDays(first.value, 3); });

  const form = h("form", { class: "card", hidden: trips.length > 0 },
    h("h2", {}, "Plan a new trip"),
    field("Trip name", title),
    h("div", { class: "grid-2" }, field("Island", island), field("Travellers", travellers, "1 to 9")),
    h("div", { class: "grid-2" }, field("First day", first), field("Last day", last)),
    field("Budget (US$)", budget, "Bookings are counted against this. Leave at 0 for no budget."),
    error,
    h("div", { class: "form-actions" }, h("button", { class: "btn primary", type: "submit" }, "Create trip")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        const trip = await api("POST", "/api/trips", {
          title: title.value, place_id: Number(island.value), start_date: first.value, end_date: last.value,
          travellers: Number(travellers.value), budget_cents: Math.round(Number(budget.value || 0) * 100) });
        toast(`${trip.title} created. Now fill in the days.`);
        go(`#/trips/${trip.id}`);
      } catch (err) { error.textContent = err.message; }
    });
  });

  const newTrip = h("button", { class: "btn primary", type: "button", hidden: trips.length === 0,
    onclick: () => { form.hidden = false; newTrip.hidden = true; title.focus(); } }, "Plan a new trip");

  const list = trips.length
    ? h("ul", { class: "tags" }, trips.map((t) => h("li", {},
        h("a", { class: "tag", href: `#/trips/${t.id}`, style: `--paint: ${PAINT[t.place.code] || "var(--lagoon)"}` },
          h("div", { class: "where" }, t.place.name),
          h("div", { class: "what" }, t.title),
          h("div", { class: "when" }, `${dayLabel(t.start_date)} to ${dayLabel(t.end_date)}`),
          h("div", { class: "when" }, `${plural(t.days, "day")}, ${plural(t.travellers, "traveller")}`)))))
    : h("div", { class: "card empty-state" }, h("h2", {}, "No trips yet"),
        h("p", {}, "Pick an island and some dates below, and Jaunt lays out the days for you to fill."));

  view.replaceChildren(page(
    h("div", { class: "page-head" }, h("h1", {}, "Your trips"), newTrip),
    trips.length ? list : null, form, trips.length ? null : list));
}

// ---- One trip ------------------------------------------------------------------------------

async function tripScreen(id) {
  const [trip, islands] = await Promise.all([api("GET", `/api/trips/${id}`), places()]);
  const parts = {
    head: h("div", { class: "trip-head" }),
    days: h("ol", { class: "days", "aria-label": "Days of the trip" }),
    bookings: h("div", {}),
  };
  const screen = { trip, parts, islands, research: null };

  const deleteButton = h("button", { class: "btn danger", type: "button" }, "Delete this trip");
  deleteButton.addEventListener("click", async () => {
    if (!(await ask("Delete this trip?", [`${screen.trip.title} and its plan will be gone for good.`], "Delete trip", "Keep it"))) return;
    await busy(deleteButton, async () => {
      await api("DELETE", `/api/trips/${id}`);
      toast("Trip deleted.");
      go("#/trips");
    });
  });

  view.replaceChildren(page(
    h("a", { class: "back", href: "#/trips" }, "Your trips"),
    parts.head,
    h("div", { class: "trip-layout" },
      h("section", { "aria-labelledby": "plan-title" }, h("h2", { id: "plan-title" }, "Day by day"), parts.days),
      h("div", { class: "side" },
        bookPanel(screen),
        h("section", { "aria-labelledby": "bookings-title" }, h("h2", { id: "bookings-title" }, "Your bookings"), parts.bookings))),
    h("div", { class: "danger-zone" }, deleteButton)));
  paint(screen);
  ticker = setInterval(() => tick(screen), 1000);
}

/** Draw everything that depends on the trip's current data. */
function paint(screen, focus) {
  const { trip, parts } = screen;
  parts.head.replaceChildren(
    h("div", {},
      h("p", { class: "island-name" }, `${trip.place.name}, ${trip.place.country}`),
      h("h1", {}, trip.title),
      h("p", { class: "meta" }, `${dayLabel(trip.start_date)} to ${dayLabel(trip.end_date)}. ${plural(trip.days, "day")}, ${plural(trip.travellers, "traveller")}.`)),
    budgetMeter(trip.budget));
  const kinds = Object.fromEntries(trip.bookings.map((b) => [b.id, b.kind]));
  parts.days.replaceChildren(...trip.itinerary.map((day, n) => dayCard(screen, day, n, kinds)));
  parts.bookings.replaceChildren(bookingList(screen));
  if (focus) {                              // keep keyboard focus on the item that just moved
    const target = document.querySelector(focus);
    if (target && !target.disabled) target.focus();
    else document.querySelector(`${focus.split(" ")[0]} button:not([disabled])`)?.focus();
  }
}

function budgetMeter(b) {
  if (!b.budget_cents) {
    return h("div", { class: "budget" }, h("div", { class: "budget-top" }, h("span", {}, "No budget set"),
      h("span", {}, `${money(b.committed_cents)} booked`)));
  }
  const pct = (c) => `${Math.min(100, (c / b.budget_cents) * 100)}%`;
  const left = b.over_budget ? `${money(-b.remaining_cents)} over budget` : `${money(b.remaining_cents)} left`;
  return h("div", { class: "budget" + (b.over_budget ? " over" : "") },
    h("div", { class: "budget-top" }, h("span", { class: "left" }, left), h("span", {}, `of ${money(b.budget_cents)}`)),
    h("div", { class: "meter", role: "img",
      "aria-label": `${money(b.committed_cents)} booked and ${money(b.on_hold_cents)} on hold, of a ${money(b.budget_cents)} budget` },
      h("span", { class: "booked", style: `width: ${pct(b.committed_cents)}` }),
      h("span", { class: "held", style: `width: ${pct(b.on_hold_cents)}` })),
    h("div", { class: "budget-key" },
      h("span", {}, h("i", { style: "background: var(--lagoon)" }), `Booked ${money(b.committed_cents)}`),
      h("span", {}, h("i", { style: "background: var(--mango)" }), `On hold ${money(b.on_hold_cents)}`)));
}

// ---- The itinerary, with drag and drop --------------------------------------------------------

let dragging = null;                      // { id, fromDay } while an item is being dragged

function dayCard(screen, day, n, kinds) {
  const items = h("ol", { class: "items", "data-day": day.date, "aria-label": `Plans for ${dayLabel(day.date)}` });
  if (!day.items.length) items.append(h("li", { class: "nothing" }, "Nothing planned yet."));
  day.items.forEach((item, i) => items.append(itemRow(screen, item, i, day, n, kinds)));

  items.addEventListener("dragover", (e) => {
    if (!dragging) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    items.classList.add("drop-target");
    const at = dropIndex(items, e.clientY);
    const marker = document.querySelector(".drop-marker") || h("li", { class: "drop-marker", "aria-hidden": "true" });
    const rows = [...items.querySelectorAll(".item:not(.dragging)")];
    if (rows[at]) items.insertBefore(marker, rows[at]); else items.append(marker);
  });
  items.addEventListener("dragleave", (e) => {
    if (!items.contains(e.relatedTarget)) { items.classList.remove("drop-target"); document.querySelector(".drop-marker")?.remove(); }
  });
  items.addEventListener("drop", (e) => {
    e.preventDefault();
    if (!dragging) return;
    const at = dropIndex(items, e.clientY);
    const { id } = dragging;
    cleanUpDrag();
    move(screen, id, day.date, at, null);
  });

  return h("li", { class: "day" },
    h("div", { class: "day-stub" }, h("span", { class: "day-num" }, `Day ${n + 1}`), h("span", { class: "day-date" }, dayLabel(day.date))),
    items,
    addForm(screen, day.date));
}

function dropIndex(list, y) {
  const rows = [...list.querySelectorAll(".item:not(.dragging)")];
  const i = rows.findIndex((row) => { const r = row.getBoundingClientRect(); return y < r.top + r.height / 2; });
  return i === -1 ? rows.length : i;
}

function cleanUpDrag() {
  dragging = null;
  document.querySelector(".drop-marker")?.remove();
  document.querySelectorAll(".drop-target, .dragging").forEach((el) => el.classList.remove("drop-target", "dragging"));
}

function itemRow(screen, item, i, day, n, kinds) {
  const days = screen.trip.itinerary;
  const kind = item.kind === "booking" ? kinds[item.booking_id] || "booking" : item.kind;
  const isBooking = item.kind === "booking";
  const firstOverall = n === 0 && i === 0;
  const lastOverall = n === days.length - 1 && i === day.items.length - 1;

  // Up/down buttons do the same job as dragging, for keyboards and touch screens.
  // At the top of a day, "up" moves the item to the end of the day before.
  const up = h("button", { class: "icon-btn up", type: "button", disabled: firstOverall || (isBooking && i === 0),
    "aria-label": `Move ${item.title} earlier`, onclick: () =>
      i > 0 ? move(screen, item.id, day.date, i - 1, `[data-id="${item.id}"] .up`)
            : move(screen, item.id, days[n - 1].date, days[n - 1].items.length, `[data-id="${item.id}"] .up`) },
    icon("up"));
  const down = h("button", { class: "icon-btn down", type: "button", disabled: lastOverall || (isBooking && i === day.items.length - 1),
    "aria-label": `Move ${item.title} later`, onclick: () =>
      i < day.items.length - 1 ? move(screen, item.id, day.date, i + 1, `[data-id="${item.id}"] .down`)
                               : move(screen, item.id, days[n + 1].date, 0, `[data-id="${item.id}"] .down`) },
    icon("down"));
  const remove = !isBooking && h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${item.title}`,
    onclick: async (e) => busy(e.currentTarget, async () => {
      screen.trip = await api("DELETE", `/api/items/${item.id}`);
      paint(screen);
      toast(`Removed ${item.title}.`);
    }) }, icon("bin"));

  const row = h("li", { class: `item${isBooking ? " booked k-" + kind : ""}`, draggable: "true", "data-id": item.id },
    h("span", { class: "time" + (item.time ? "" : " none") }, item.time || "Any time"),
    h("span", { class: "kind" }, icon(kind, kindName(kind))),
    h("div", {}, h("div", { class: "item-title" }, item.title), item.notes && h("div", { class: "item-notes" }, item.notes)),
    h("div", { class: "item-actions" }, up, down, remove));
  row.addEventListener("dragstart", (e) => {
    dragging = { id: item.id };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(item.id));
    requestAnimationFrame(() => row.classList.add("dragging"));
  });
  row.addEventListener("dragend", cleanUpDrag);
  return row;
}

const kindName = (k) => ({ stay: "Stay", flight: "Flight", tour: "Tour", car: "Car hire", food: "Food",
                           activity: "Activity", transport: "Getting around", note: "Note", booking: "Booking" }[k] || k);

async function move(screen, id, day, position, focus) {
  try {
    screen.trip = await api("POST", `/api/items/${id}/move`, { day, position });
    paint(screen, focus);
  } catch (e) {
    toast(e.message, true);
    paint(screen);                          // put things back where they were
  }
}

function addForm(screen, day) {
  const title = h("input", { required: true, maxLength: 120, placeholder: "Snorkel at Moonhole" });
  const time = h("input", { type: "time" });
  const kind = h("select", {}, ["activity", "food", "transport", "note"].map((k) => h("option", { value: k }, kindName(k))));
  const form = h("form", {},
    field("What", title), field("Time (optional)", time), field("Kind", kind),
    h("button", { class: "btn primary small", type: "submit" }, "Add"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await busy(form.querySelector("button"), async () => {
      screen.trip = await api("POST", `/api/trips/${screen.trip.id}/items`,
        { day, title: title.value, kind: kind.value, time: time.value || null });
      paint(screen);
      toast(`Added ${title.value} to ${dayLabel(day)}.`);
    });
  });
  return h("details", { class: "add" }, h("summary", {}, "Add to this day"), form);
}

// ---- Booking: search, hold, confirm, cancel ------------------------------------------------------

function bookPanel(screen) {
  const trip = screen.trip;
  const results = h("div", { "aria-live": "polite" });
  const body = h("div", { role: "tabpanel", id: "book-panel" });
  const tabs = ["stay", "flight", "tour", "car"];
  const names = { stay: "Stays", flight: "Flights", tour: "Tours", car: "Cars" };
  const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "What to book" });
  const select = (kind) => {
    tablist.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.kind === kind)));
    body.replaceChildren(SEARCHES[kind](screen, results), results);
    results.replaceChildren();
  };
  for (const kind of tabs) {
    tablist.append(h("button", { class: "tab", type: "button", role: "tab", "data-kind": kind, "aria-controls": "book-panel",
      onclick: () => select(kind) }, icon(kind), names[kind]));
  }
  tablist.addEventListener("keydown", (e) => {          // arrow keys move between tabs
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const current = tabs.indexOf(document.activeElement?.dataset?.kind);
    if (current < 0) return;
    const next = tabs[(current + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    select(next);
    tablist.querySelector(`[data-kind="${next}"]`).focus();
  });
  select("stay");
  return h("section", { class: "card book", "aria-labelledby": "book-title" },
    h("h2", { id: "book-title" }, `Book something for ${trip.place.name}`), tablist, body);
}

function searchForm(screen, results, fields, run) {
  const error = h("p", { class: "error", role: "alert" });
  const form = h("form", {}, fields, error,
    h("div", { class: "form-actions" }, h("button", { class: "btn primary small", type: "submit" }, "Search")));
  const search = async () => {
    error.textContent = "";
    try {
      const options = await run();
      results.replaceChildren(options.length
        ? h("ul", { class: "options" }, options)
        : h("p", { class: "muted" }, "Nothing free for that. Try other days."));
    } catch (e) { results.replaceChildren(); error.textContent = e.message; }
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); search(); });
  screen.research = search;                             // re-run after a hold, so the counts stay right
  return form;
}

function option(title, sub, priceMain, priceSub, left, unit, onHold) {
  const button = h("button", { class: "btn small", type: "button" }, "Hold for 15 minutes");
  button.addEventListener("click", () => busy(button, onHold));
  return h("li", { class: "option" },
    h("div", {}, h("h3", {}, title), sub.map((s) => h("div", { class: "sub" }, s)),
      h("div", { class: "left-count" + (left <= 2 ? " few" : "") }, `${plural(left, unit)} left`)),
    h("div", { class: "price" }, priceMain, h("small", {}, priceSub)),
    button);
}

async function hold(screen, kind, body) {
  const b = await api("POST", `/api/trips/${screen.trip.id}/holds/${kind}`, body);
  screen.trip = await api("GET", `/api/trips/${screen.trip.id}`);
  screen.fresh = b.id;
  paint(screen);
  const covered = kind === "stay" && screen.trip.bookings.some((o) => o.kind === "stay" && o.status === "confirmed"
    && o.start_date < b.end_date && o.end_date > b.start_date);
  toast(covered
    ? `Held ${b.title} until ${clock(b.held_until)}. You already have a stay confirmed for some of these nights.`
    : `Held ${b.title} until ${clock(b.held_until)}. Confirm it under Your bookings.`);
  screen.research?.();
}

const dateInput = (value, min, max) => h("input", { type: "date", value, min, max, required: true });
const numberInput = (value, max) => h("input", { type: "number", value, min: 1, max, required: true });

const SEARCHES = {
  stay(screen, results) {
    const t = screen.trip;
    const checkIn = dateInput(t.start_date, t.start_date, addDays(t.end_date, -1));
    const checkOut = dateInput(t.end_date, addDays(t.start_date, 1), t.end_date);
    const rooms = numberInput(1, 5);
    return searchForm(screen, results,
      [h("div", { class: "grid-2" }, field("Check in", checkIn), field("Check out", checkOut)), field("Rooms", rooms)],
      async () => {
        const q = new URLSearchParams({ place_id: t.place.id, check_in: checkIn.value, check_out: checkOut.value, rooms: rooms.value });
        return (await api("GET", `/api/search/stays?${q}`)).map((o) => option(
          `${o.stay}, ${o.room}`,
          [`${o.stars}-star ${o.stay_kind}, sleeps ${o.sleeps}`, `Free cancellation until ${o.free_cancel_hours} hours before check-in`],
          money(o.total_cents), `${money(o.nightly_cents)} a night, ${plural(o.nights, "night")}`, o.rooms_left, "room",
          () => hold(screen, "stay", { room_type_id: o.room_type_id, check_in: checkIn.value, check_out: checkOut.value,
                                       rooms: Number(rooms.value) })));
      });
  },

  flight(screen, results) {
    const t = screen.trip;
    const choices = () => screen.islands.map((p) => h("option", { value: p.id }, `${p.name} (${p.code})`));
    const from = h("select", {}, choices());
    const to = h("select", {}, choices());
    from.value = String((screen.islands.find((p) => p.code === "SVD" && p.id !== t.place.id) || screen.islands.find((p) => p.id !== t.place.id)).id);
    to.value = String(t.place.id);
    const day = dateInput(t.start_date, t.start_date, t.end_date);
    const passengers = numberInput(t.travellers, t.travellers);
    return searchForm(screen, results,
      [h("div", { class: "grid-2" }, field("From", from), field("To", to)),
       h("div", { class: "grid-2" }, field("Day", day), field("Passengers", passengers))],
      async () => {
        const q = new URLSearchParams({ origin_id: from.value, destination_id: to.value, day: day.value, passengers: passengers.value });
        return (await api("GET", `/api/search/flights?${q}`)).map((o) => option(
          `${o.number}: ${o.origin} to ${o.destination}`,
          [`${o.airline}, departs ${clock(o.departs_at)}, lands ${clock(o.arrives_at)}`, "Free cancellation for 24 hours after booking"],
          money(o.total_cents), `${money(o.fare_cents)} each`, o.seats_left, "seat",
          () => hold(screen, "flight", { flight_id: o.flight_id, passengers: Number(passengers.value) })));
      });
  },

  tour(screen, results) {
    const t = screen.trip;
    const day = dateInput(t.start_date, t.start_date, t.end_date);
    const people = numberInput(t.travellers, t.travellers);
    return searchForm(screen, results,
      [h("div", { class: "grid-2" }, field("Day", day), field("People", people))],
      async () => {
        const q = new URLSearchParams({ place_id: t.place.id, day: day.value, people: people.value });
        return (await api("GET", `/api/search/tours?${q}`)).map((o) => option(
          o.tour,
          [`${o.operator}, starts ${clock(o.starts_at)}, ${duration(o.minutes)}`, o.blurb],
          money(o.total_cents), `${money(o.price_cents)} a person`, o.places_left, "place",
          () => hold(screen, "tour", { slot_id: o.slot_id, people: Number(people.value) })));
      });
  },

  car(screen, results) {
    const t = screen.trip;
    const pickup = dateInput(t.start_date, t.start_date, t.end_date);
    const dropoff = dateInput(addDays(t.start_date, 1) > t.end_date ? t.end_date : addDays(t.start_date, 1), t.start_date, t.end_date);
    const cars = numberInput(1, 3);
    return searchForm(screen, results,
      [h("div", { class: "grid-2" }, field("Pick up", pickup), field("Drop off", dropoff)), field("Cars", cars)],
      async () => {
        const q = new URLSearchParams({ place_id: t.place.id, pickup: pickup.value, dropoff: dropoff.value, cars: cars.value });
        return (await api("GET", `/api/search/cars?${q}`)).map((o) => option(
          `${o.company} ${o.name}`,
          [`Seats ${o.seats}. Pick up 09:00, drop off 17:00`, "Free cancellation any time before pick-up"],
          money(o.total_cents), `${money(o.daily_cents)} a day, ${plural(o.days, "day")}`, o.cars_left, "car",
          () => hold(screen, "car", { car_class_id: o.car_class_id, pickup: pickup.value, dropoff: dropoff.value,
                                      cars: Number(cars.value) })));
      });
  },
};

function duration(minutes) {
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (!rest) return plural(hours, "hour");
  return hours ? `${hours} h ${rest} min` : `${rest} min`;
}

function bookingList(screen) {
  const bookings = [...screen.trip.bookings].sort((a, b) =>
    ["held", "confirmed", "cancelled", "expired"].indexOf(a.status) - ["held", "confirmed", "cancelled", "expired"].indexOf(b.status));
  if (!bookings.length) return h("p", { class: "muted" }, "Nothing booked yet. Search above, hold what you like, then confirm it here.");
  return h("ul", { class: "booking-list" }, bookings.map((b) => bookingRow(screen, b)));
}

function bookingRow(screen, b) {
  const when = b.kind === "stay" ? `${dayLabel(b.start_date)} to ${dayLabel(b.end_date)}`
    : b.kind === "car" ? `${dayLabel(b.start_date)} to ${dayLabel(b.end_date)}`
    : `${dayLabel(b.start_date)}, ${clock(b.starts_at)}`;
  const count = { stay: "room", flight: "passenger", tour: "person", car: "car" }[b.kind];
  const who = b.kind === "tour" ? (b.quantity === 1 ? "1 person" : `${b.quantity} people`) : plural(b.quantity, count);
  let status;
  const actions = h("div", { class: "actions" });
  let note = null;
  if (b.status === "held") {
    const deadline = Date.now() + (b.hold_seconds_left || 0) * 1000;
    status = h("span", { class: "state" }, "On hold, ", h("span", { "data-deadline": deadline }, countdown(deadline)), " left");
    // Only one confirmed stay a night: say so before the traveller tries
    const clash = b.kind === "stay" && screen.trip.bookings.find((o) => o.kind === "stay" && o.status === "confirmed"
      && o.start_date < b.end_date && o.end_date > b.start_date);
    if (clash) note = h("p", { class: "clash" }, `Some of these nights are covered by ${clash.title}. Cancel that one to confirm this instead.`);
    actions.append(
      h("button", { class: "btn primary small", type: "button", disabled: Boolean(clash),
        onclick: (e) => confirmBooking(screen, b, e.currentTarget) }, "Confirm booking"),
      h("button", { class: "btn quiet small", type: "button", onclick: (e) => cancelBooking(screen, b, e.currentTarget) }, "Let it go"));
  } else if (b.status === "confirmed") {
    status = h("span", { class: "state" }, "Confirmed");
    actions.append(h("button", { class: "btn small", type: "button", onclick: (e) => cancelBooking(screen, b, e.currentTarget) }, "Cancel booking"));
  } else if (b.status === "cancelled") {
    status = h("span", { class: "state" }, b.refund_cents ? `Cancelled, ${money(b.refund_cents)} refunded` : "Cancelled");
  } else {
    status = h("span", { class: "state" }, "Hold ran out");
  }
  return h("li", { class: `booking k-${b.kind} ${b.status}${screen.fresh === b.id ? " fresh" : ""}` },
    h("div", {}, h("h3", {}, b.title), h("div", { class: "ref" }, `${b.reference}. ${when}. ${who}.`), status),
    h("div", { class: "total" }, money(b.total_cents)),
    note,
    actions.children.length ? actions : null);
}

function countdown(deadline) {
  const s = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function tick(screen) {
  let lapsed = false;
  document.querySelectorAll("[data-deadline]").forEach((el) => {
    el.textContent = countdown(Number(el.dataset.deadline));
    if (Number(el.dataset.deadline) <= Date.now()) lapsed = true;
  });
  if (lapsed && !screen.refreshing) {
    screen.refreshing = true;
    api("GET", `/api/trips/${screen.trip.id}`).then((trip) => {
      screen.trip = trip; paint(screen); toast("A hold ran out, so that space went back on sale.", true);
    }).catch(() => {}).finally(() => { screen.refreshing = false; });
  }
}

async function confirmBooking(screen, b, button) {
  const yes = await ask(`Confirm ${b.title}?`,
    [`Total ${money(b.total_cents)}.`, "This is a demo, so no payment is taken."], "Confirm booking", "Not yet");
  if (!yes) return;
  await busy(button, async () => {
    try {
      const done = await api("POST", `/api/bookings/${b.id}/confirm`);
      toast(`Confirmed. Your reference is ${done.reference}. It's in your day-by-day plan now.`);
    } finally {
      screen.trip = await api("GET", `/api/trips/${screen.trip.id}`);
      paint(screen);
    }
  });
}

async function cancelBooking(screen, b, button) {
  await busy(button, async () => {
    const quote = b.status === "held" ? null : await api("GET", `/api/bookings/${b.id}/refund`);
    const lines = quote
      ? [`You'd get back ${money(quote.refund_cents)} of ${money(b.total_cents)}.`, quote.reason]
      : ["Nothing has been charged for a hold, so there's nothing to refund."];
    const yes = await ask(b.status === "held" ? `Let go of ${b.title}?` : `Cancel ${b.title}?`, lines,
      b.status === "held" ? "Let it go" : "Cancel booking", "Keep it");
    if (!yes) return;
    const done = await api("POST", `/api/bookings/${b.id}/cancel`);
    toast(b.status === "held" ? "Let go. The space is back on sale." : `Cancelled. ${money(done.refund_cents)} refunded.`);
    screen.trip = await api("GET", `/api/trips/${screen.trip.id}`);
    paint(screen);
    screen.research?.();
  });
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function start() {
  try { signedIn(await api("GET", "/api/me")); } catch { signedOut(); }
  window.addEventListener("hashchange", route);
  route();
}

start();
