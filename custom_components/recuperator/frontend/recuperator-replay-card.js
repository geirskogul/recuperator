/*
 * Recuperator Replay card.
 *
 * Pick a period with Home Assistant's own date and time range picker (the one
 * on the History page), press Create, and the card shows the animated replay:
 * the pipe, and under it a history graph of the inside and outside probes with
 * a cursor sweeping across in step with the animation. With a synced
 * recuperator, its own pipe and graph can be drawn underneath. Each new replay
 * can also be saved as a GIF; the card links to the files it made.
 *
 *   type: custom:recuperator-replay-card
 *   entity: image.basement_breather_replay_animation
 *   # title: Replay           optional card title
 *   # hours: 24               length of the period picked at first
 *   # playback_seconds: 60    optional; saved like the action's value
 *   # frames: 0               optional; saved like the action's value
 */

const CARD = "recuperator-replay-card";
const PICKER = "ha-date-range-picker";
const PICKER_WAIT_MS = 5000;

const STYLE = `
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 0 16px 8px; }
  .picker { flex: 1 1 260px; min-width: 0; }
  .picker input { font: inherit; padding: 6px; margin: 2px 0; color: var(--primary-text-color);
    background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 4px; }
  button { font: inherit; font-weight: 500; cursor: pointer; padding: 8px 16px; border: none; border-radius: 18px;
    color: var(--text-primary-color, #fff); background: var(--primary-color); }
  button[disabled] { opacity: 0.5; cursor: default; }
  .options { display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 0 16px 8px; }
  .options label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .links { display: flex; gap: 16px; padding: 0 16px 8px; }
  .links a { color: var(--primary-color); }
  .status { padding: 0 16px 8px; color: var(--secondary-text-color); font-size: 0.9em; min-height: 1.2em; }
  .status.error { color: var(--error-color, #db4437); }
  img { display: block; width: 100%; height: auto; }
  [hidden] { display: none !important; }
  .empty { padding: 16px; color: var(--secondary-text-color); }
`;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* Load the History page's code (which holds the picker) through the panel resolver.
 * The resolver's method is getRoutes in older releases, _getRoutes in newer ones. */
async function loadHistoryPanel() {
  await customElements.whenDefined("partial-panel-resolver");
  const resolver = document.createElement("partial-panel-resolver");
  const getRoutes = (resolver._getRoutes || resolver.getRoutes)?.bind(resolver);
  const routes = getRoutes?.([{ component_name: "history", url_path: "recuperator-tmp" }]);
  await routes?.routes?.["recuperator-tmp"]?.load?.();
}

/* Or through the Energy date selection card, which uses the same picker. */
async function loadEnergyPeriodCard() {
  const helpers = await window.loadCardHelpers?.();
  helpers?.createCardElement({ type: "energy-date-selection" });
}

/* The date range picker is part of the frontend's code that is only loaded
 * when needed: have it loaded, and wait a little for it. */
async function loadDatePicker() {
  if (customElements.get(PICKER)) return true;
  for (const load of [loadHistoryPanel, loadEnergyPeriodCard]) {
    try {
      await load();
    } catch (err) {
      console.debug(`${CARD}: ${load.name} failed`, err);
    }
    if (customElements.get(PICKER)) return true;
  }
  const defined = customElements.whenDefined(PICKER).then(() => true);
  return Promise.race([defined, sleep(PICKER_WAIT_MS).then(() => false)]);
}

/* A datetime-local input's value for a Date (local time, to the minute). */
function toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/* The picker's event carries the range as detail.value (newer) or detail (older). */
function rangeFromEvent(ev) {
  const detail = ev.detail?.value ?? ev.detail ?? {};
  const start = detail.startDate ?? detail.start;
  const end = detail.endDate ?? detail.end;
  return start && end ? { start: new Date(start), end: new Date(end) } : null;
}

class RecuperatorReplayCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Choose the replay's image entity (entity: image.…_replay_animation)");
    }
    this._config = config;
    const end = new Date();
    end.setSeconds(0, 0);
    const hours = Number(config.hours) > 0 ? Number(config.hours) : 24;
    this._range = { start: new Date(end.getTime() - hours * 3600 * 1000), end };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (this._picker) this._picker.hass = hass;
    this._updateOptions();
    this._updateImage();
  }

  getCardSize() {
    return 8;
  }

  static getStubConfig(hass) {
    const replay = Object.values(hass.entities || {}).find(
      (e) => e.platform === "recuperator" && e.translation_key === "replay"
    );
    return { entity: replay ? replay.entity_id : "" };
  }

  _build() {
    this._built = true;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = STYLE;
    const card = document.createElement("ha-card");
    if (this._config.title) card.header = this._config.title;
    card.append(this._buildControls(), this._buildOptions(), this._buildStatus(), this._buildLinks(), this._buildImage());
    root.append(style, card);
  }

  _buildControls() {
    const controls = document.createElement("div");
    controls.className = "controls";
    this._pickerSlot = document.createElement("div");
    this._pickerSlot.className = "picker";
    this._button = document.createElement("button");
    this._button.textContent = "Create";
    this._button.addEventListener("click", () => this._create());
    controls.append(this._pickerSlot, this._button);
    this._addPicker();
    return controls;
  }

  /* Checkboxes for the Replay device's switches: also a GIF, and the synced recuperator. */
  _buildOptions() {
    const options = document.createElement("div");
    options.className = "options";
    this._options = {};
    for (const [key, text] of [["replay_linked", "Include synced recuperator"], ["replay_gif", "Also save a GIF"]]) {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      label.append(box, document.createTextNode(text));
      label.hidden = true; // until its switch is found
      options.append(label);
      this._options[key] = { label, box, synced: false };
    }
    return options;
  }

  _buildLinks() {
    this._links = document.createElement("div");
    this._links.className = "links";
    return this._links;
  }

  /* The replay device's switch for `key` (replay_gif, replay_linked), if it exists. */
  _switchFor(key) {
    const image = this._hass.entities?.[this._config.entity];
    if (!image) return undefined;
    return Object.values(this._hass.entities).find(
      (e) => e.platform === "recuperator" && e.translation_key === key && e.device_id === image.device_id
    );
  }

  /* Show a checkbox once its switch is known, ticked as the switch is (once: then it is the user's). */
  _updateOptions() {
    for (const [key, option] of Object.entries(this._options)) {
      const entity = this._switchFor(key);
      option.label.hidden = !entity;
      if (entity && !option.synced) {
        option.box.checked = this._hass.states[entity.entity_id]?.state === "on";
        option.synced = true;
      }
    }
  }

  _buildStatus() {
    this._status = document.createElement("div");
    this._status.className = "status";
    return this._status;
  }

  _buildImage() {
    const wrap = document.createElement("div");
    this._img = document.createElement("img");
    this._img.alt = "Recuperator replay";
    this._empty = document.createElement("div");
    this._empty.className = "empty";
    wrap.append(this._img, this._empty);
    return wrap;
  }

  async _addPicker() {
    const ready = await loadDatePicker();
    this._pickerSlot.innerHTML = "";
    this._pickerSlot.append(ready ? this._stockPicker() : this._fallbackPicker());
  }

  /* Home Assistant's own picker, as on the History page (presets, calendar, times). */
  _stockPicker() {
    const picker = document.createElement(PICKER);
    picker.hass = this._hass;
    picker.startDate = this._range.start;
    picker.endDate = this._range.end;
    picker.timePicker = true;
    picker.setAttribute("time-picker", "");
    picker.setAttribute("extended-presets", "");
    // Like the History page, give the chosen range back to the picker so it shows it.
    const changed = (ev) => {
      const range = rangeFromEvent(ev);
      if (!range) return;
      this._range = range;
      picker.startDate = range.start;
      picker.endDate = range.end;
    };
    picker.addEventListener("value-changed", changed);
    picker.addEventListener("change", changed);
    this._picker = picker;
    return picker;
  }

  /* Plain date and time boxes, if the stock picker could not be loaded. */
  _fallbackPicker() {
    const box = document.createElement("div");
    const input = (value, key) => {
      const el = document.createElement("input");
      el.type = "datetime-local";
      el.value = toLocalInput(value);
      el.addEventListener("change", () => {
        if (el.value) this._range = { ...this._range, [key]: new Date(el.value) };
      });
      return el;
    };
    box.append(input(this._range.start, "start"), document.createTextNode(" – "), input(this._range.end, "end"));
    return box;
  }

  /* The recuperator the replay image belongs to (its device's config entry). */
  _configEntryId() {
    const entity = this._hass.entities?.[this._config.entity];
    const device = entity && this._hass.devices?.[entity.device_id];
    return device?.config_entries?.[0];
  }

  _serviceData() {
    const data = { start: this._range.start.toISOString(), end: this._range.end.toISOString() };
    const entryId = this._configEntryId();
    if (entryId) data.config_entry_id = entryId;
    if (this._config.playback_seconds !== undefined) data.playback_seconds = this._config.playback_seconds;
    if (this._config.frames !== undefined) data.frames = this._config.frames;
    if (!this._options.replay_gif.label.hidden) data.gif = this._options.replay_gif.box.checked;
    if (!this._options.replay_linked.label.hidden) data.include_linked = this._options.replay_linked.box.checked;
    return data;
  }

  async _create() {
    this._button.disabled = true;
    this._links.innerHTML = "";
    this._setStatus(this._options.replay_gif.box.checked && !this._options.replay_gif.label.hidden
      ? "Creating the replay and its GIF…" : "Creating the replay…");
    try {
      const result = await this._hass.callService(
        "recuperator", "create_replay", this._serviceData(), undefined, false, true
      );
      const response = result?.response ?? {};
      const parts = [response.frames ? `${response.frames} frames` : "", response.linked ? `with ${response.linked}` : ""];
      this._setStatus(parts.filter(Boolean).join(", "));
      this._showLinks(response);
    } catch (err) {
      this._setStatus(err?.message || String(err), true);
    } finally {
      this._button.disabled = false;
    }
  }

  /* Links to the saved files, to open or download (a time in the address skips the browser's cache). */
  _showLinks(response) {
    this._links.innerHTML = "";
    for (const [url, text] of [[response.url, "SVG"], [response.gif_url, "GIF"]]) {
      if (!url) continue;
      const link = document.createElement("a");
      link.href = `${url}?v=${Date.now()}`;
      link.download = url.split("/").pop();
      link.target = "_blank";
      link.textContent = `Download ${text}`;
      this._links.append(link);
    }
  }

  _setStatus(text, error = false) {
    this._status.textContent = text;
    this._status.classList.toggle("error", error);
  }

  /* Show the replay image; its state (the time it was made) changes with each new replay. */
  _updateImage() {
    const state = this._hass.states[this._config.entity];
    const picture = state?.attributes?.entity_picture;
    if (!picture) {
      this._img.hidden = true;
      this._empty.textContent = state ? "No replay yet: pick a period and press Create." : `${this._config.entity} not found`;
      return;
    }
    const url = new URL(picture, window.location.origin);
    url.searchParams.set("v", state.state);
    if (this._img.getAttribute("src") !== url.pathname + url.search) {
      this._img.src = url.pathname + url.search;
    }
    this._img.hidden = false;
    this._empty.textContent = "";
  }
}

if (!customElements.get(CARD)) {
  customElements.define(CARD, RecuperatorReplayCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD,
    name: "Recuperator replay",
    description: "Pick a period with the History page's date picker and watch the recuperator breathe.",
    preview: false,
  });
}
