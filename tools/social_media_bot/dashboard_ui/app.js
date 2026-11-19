(() => {
  "use strict";

  const ACTION_FIELDS = {
    follow: ["username"],
    post: ["text"],
    comment: ["url", "text"],
  };

  // Mirror of platforms/platform_catalog(); keeps the selector usable while
  // /api/status is still loading (or when an older server is running).
  const FALLBACK_PLATFORMS = [
    {
      key: "facebook",
      label: "Facebook",
      package: "com.facebook.katana",
      actions: {
        follow: ["username"],
        post: ["text"],
        comment: ["url", "text"],
      },
      notes: "Teks post didukung penuh.",
    },
    {
      key: "instagram",
      label: "Instagram",
      package: "com.instagram.android",
      actions: { follow: ["username"], comment: ["url", "text"] },
      notes: "Instagram hanya mendukung follow & comment; posting butuh media.",
    },
    {
      key: "threads",
      label: "Threads",
      package: "com.instagram.barcelona",
      actions: {
        follow: ["username"],
        post: ["text"],
        comment: ["url", "text"],
      },
      notes: "Threads mendukung posting teks, follow dan balasan.",
    },
    {
      key: "x",
      label: "X",
      package: "com.twitter.android",
      actions: {
        follow: ["username"],
        post: ["text"],
        comment: ["url", "text"],
      },
      notes: "Post teks maksimal 280 karakter (akun gratis).",
    },
    {
      key: "tiktok",
      label: "TikTok",
      package: "com.zhiliaoapp.musically",
      actions: { follow: ["username"], comment: ["url", "text"] },
      notes: "TikTok hanya mendukung follow & comment; posting butuh video.",
    },
  ];

  const state = {
    status: null,
    logs: [],
    lastLogId: 0,
    paused: false,
    busy: false,
    target: "all",
    online: true,
    liveStrip: new Map(),
    liveUdid: null,
    liveMuted: false,
    liveMuteBusy: false,
    platform: null,
    platformsSignature: null,
    mode: "single",
    screenSize: "fit",
    setupLoaded: false,
    setupDirty: false,
    notifiedJobs: new Set(),
    jobsInitialized: false,
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    offline: $("offline-banner"),
    rackName: $("rack-name"),
    rackHost: $("rack-host"),
    rackSize: $("rack-size"),
    connPill: $("conn-pill"),
    themeButton: $("btn-theme"),
    statConnected: $("stat-connected"),
    statTotal: $("stat-total"),
    statRunning: $("stat-running"),
    statQueued: $("stat-queued"),
    statLast: $("stat-last"),
    jobs: $("jobs-list"),
    logs: $("logs-list"),
    actionForm: $("action-form"),
    platformSelect: $("platform-select"),
    platformNote: $("platform-note"),
    actionSelect: $("action-select"),
    actionSchedule: $("action-schedule"),
    campaignText: $("campaign-text"),
    campaignTarget: $("campaign-target"),
    campaignPlatforms: $("campaign-platforms"),
    campaignInterval: $("campaign-interval"),
    campaignSchedule: $("campaign-schedule"),
    campaignSubmit: $("campaign-submit"),
    campaignHint: $("campaign-hint"),
    targetSelect: $("target-select"),
    usernameWrap: $("field-username-wrap"),
    username: $("field-username"),
    urlWrap: $("field-url-wrap"),
    url: $("field-url"),
    textWrap: $("field-text-wrap"),
    textLabel: $("field-text-label"),
    text: $("field-text"),
    submit: $("action-submit"),
    hint: $("action-hint"),
    pause: $("log-pause"),
    clear: $("log-clear"),
    clock: $("server-clock"),
    toast: $("toast"),
    livePanel: $("screen-panel"),
    liveTitle: $("screen-panel-title"),
    liveSubtitle: $("screen-panel-subtitle"),
    liveImg: $("screen-panel-img"),
    liveEmpty: $("screen-panel-empty"),
    liveStatus: $("screen-panel-status"),
    liveClose: $("screen-panel-close"),
    liveRetry: $("screen-panel-retry"),
    panelSlot: $("screen-panel-slot"),
    panelDevice: $("screen-panel-device"),
    panelSession: $("screen-panel-session"),
    panelPort: $("screen-panel-port"),
    screenSize: $("screen-size"),
    screenResolution: $("screen-resolution"),
    liveDeviceList: $("live-device-list"),
    liveStripCount: $("live-strip-count"),
    liveStripPrev: $("live-strip-prev"),
    liveStripNext: $("live-strip-next"),
    setupSummary: $("setup-summary"),
    setupRackName: $("setup-rack-name"),
    setupHost: $("setup-host"),
    setupDevices: $("setup-devices"),
    setupAdd: $("setup-add"),
    setupScan: $("setup-scan"),
    setupSave: $("setup-save"),
    setupHint: $("setup-hint"),
    limitFollow: $("limit-follow"),
    limitPost: $("limit-post"),
    limitComment: $("limit-comment"),
    limitLike: $("limit-like"),
    limitSave: $("limit-save"),
    limitReply: $("limit-reply"),
    limitsSave: $("limits-save"),
    healthCheck: $("health-check"),
    notifyEnable: $("notify-enable"),
    analyticsOk: $("analytics-ok"),
    analyticsFailed: $("analytics-failed"),
    analyticsDetail: $("analytics-detail"),
    automationHint: $("automation-hint"),
    screenBack: $("screen-back"),
    screenMute: $("screen-mute"),
    screenHome: $("screen-home"),
    screenEnter: $("screen-enter"),
    screenText: $("screen-text"),
    screenSendText: $("screen-send-text"),
    screenInputStatus: $("screen-input-status"),
    modeSingle: $("mode-single"),
    modeBulk: $("mode-bulk"),
    bulkFields: $("bulk-fields"),
    bulkItems: $("bulk-items"),
    bulkInterval: $("bulk-interval"),
    bulkRetries: $("bulk-retries"),
    bulkFile: $("bulk-file"),
    bulkHint: $("bulk-hint"),
  };

  const THEME_KEY = "gads.theme";
  const THEME_MEDIA = window.matchMedia("(prefers-color-scheme: light)");
  const THEME_COLORS = { dark: "#12100e", light: "#f4efe6" };

  function savedTheme() {
    try {
      const value = localStorage.getItem(THEME_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      /* localStorage can be unavailable in private mode */
      return null;
    }
  }

  function applyTheme(theme, persist) {
    const normalized = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = normalized;
    if (persist) {
      try {
        localStorage.setItem(THEME_KEY, normalized);
      } catch (error) {
        /* localStorage can be unavailable in private mode */
      }
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = THEME_COLORS[normalized];
    if (els.themeButton) {
      const light = normalized === "light";
      els.themeButton.setAttribute("aria-pressed", String(light));
      els.themeButton.title = light
        ? "Aktifkan mode gelap"
        : "Aktifkan mode terang";
    }
  }

  function restoreTheme() {
    applyTheme(
      savedTheme() || (THEME_MEDIA.matches ? "light" : "dark"),
      false,
    );
  }

  function toggleTheme() {
    const current =
      document.documentElement.dataset.theme === "light" ? "light" : "dark";
    applyTheme(current === "light" ? "dark" : "light", true);
  }

  async function api(path, options) {
    const response = await fetch(path, options || {});
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "btn-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", `#icon-${name}`);
    svg.append(use);
    return svg;
  }

  function setButtonLabel(button, text) {
    const label = button.querySelector("span");
    if (label) label.textContent = text;
  }

  function fmtTime(epoch) {
    if (!epoch) return "—";
    return new Date(epoch * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function toneFor(status) {
    return (
      {
        succeeded: "ok",
        failed: "bad",
        running: "warn",
        queued: "muted",
        scheduled: "warn",
        cancelled: "muted",
      }[status] || "muted"
    );
  }

  let toastTimer = null;
  function toast(message, tone) {
    els.toast.textContent = message;
    els.toast.className = "toast" + (tone ? ` toast-${tone}` : "");
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 4500);
  }

  function setOnline(online) {
    if (state.online === online) return;
    state.online = online;
    els.offline.hidden = online;
  }

  function render() {
    const status = state.status;
    if (!status) return;

    els.rackName.textContent = status.rack.name;
    els.rackHost.textContent = status.rack.host || "—";
    els.rackSize.textContent = status.rack.size;

    const connected = status.summary.connected;
    const total = status.summary.total;
    els.connPill.textContent = `${connected}/${total} terhubung`;
    els.connPill.className =
      "pill " +
      (total > 0 && connected === total
        ? "pill-ok"
        : connected
          ? "pill-warn"
          : "pill-muted");

    els.statConnected.textContent = connected;
    els.statTotal.textContent = `dari ${total}`;
    els.statRunning.textContent = status.summary.running_jobs;
    els.statQueued.textContent = status.summary.queued_jobs;

    const last = status.jobs[0];
    els.statLast.textContent = last ? last.label : "Belum ada";
    els.statLast.className =
      "stat-value stat-value-small " + (last ? `job-${last.status}` : "");

    els.clock.textContent = new Date(status.server_time * 1000).toLocaleString();

    renderLiveStrip(status.devices);
    renderSetup(status);
    renderPlatforms(status.platforms || [], status.default_platform);
    renderTargets(status.devices);
    renderCampaignPlatforms(status.platforms || []);
    renderJobs(status.jobs);
    updateLivePanel();
  }

  function createLiveChip(device) {
    const button = element("button", "live-device");
    button.type = "button";
    const top = element("span", "live-device-top");
    const dot = element("span", "dot");
    const slot = element("span", "live-device-slot");
    const badge = element("span", "live-device-badge");
    top.append(dot, slot, badge);
    const udid = element("span", "live-device-udid");
    const meta = element("span", "live-device-meta");
    const port = element("span", "live-device-port");
    const session = element("span", "live-device-session");
    meta.append(port, session);
    button.append(top, udid, meta);

    const entry = { button, dot, slot, badge, udid, port, session, device };
    button.addEventListener("click", () => openScreen(entry.device.udid));
    return entry;
  }

  function updateLiveChip(entry, device) {
    entry.device = device;
    entry.button.dataset.udid = device.udid;
    entry.button.title =
      `${device.slot} · ${device.udid} · port ${device.appium_port}`;
    entry.dot.className =
      "dot " + (device.connected ? "dot-on" : "dot-off");
    entry.slot.textContent = device.slot;
    entry.badge.textContent = device.connected ? "Online" : "Offline";
    entry.badge.className =
      "live-device-badge " + (device.connected ? "is-online" : "is-offline");
    entry.udid.textContent = device.udid;
    entry.port.textContent = `Port ${device.appium_port}`;
    entry.session.textContent = device.session_id
      ? `Sess ${device.session_id.slice(0, 8)}`
      : "No session";
    const active = state.liveUdid === device.udid;
    entry.button.className = [
      "live-device",
      device.connected ? "is-connected" : "is-offline",
      active ? "is-active" : "",
    ].filter(Boolean).join(" ");
    entry.button.setAttribute("aria-pressed", String(active));
  }

  function renderLiveStrip(devices) {
    const seen = new Set();
    for (const device of devices) {
      seen.add(device.udid);
      let entry = state.liveStrip.get(device.udid);
      if (!entry) {
        entry = createLiveChip(device);
        state.liveStrip.set(device.udid, entry);
      }
      updateLiveChip(entry, device);
      if (!entry.button.parentNode) els.liveDeviceList.append(entry.button);
    }

    for (const [udid, entry] of state.liveStrip) {
      if (!seen.has(udid)) {
        entry.button.remove();
        state.liveStrip.delete(udid);
      }
    }
    const online = devices.filter((device) => device.connected).length;
    els.liveStripCount.textContent =
      `${devices.length} slot · ${online} online`;
  }

  function scrollLiveStrip(direction) {
    const list = els.liveDeviceList;
    const amount = Math.max(180, Math.round(list.clientWidth * 0.7));
    list.scrollBy({ left: direction * amount, behavior: "smooth" });
  }

  function centerLiveChip(entry) {
    const list = els.liveDeviceList;
    const listRect = list.getBoundingClientRect();
    const chipRect = entry.button.getBoundingClientRect();
    const target =
      list.scrollLeft +
      (chipRect.left - listRect.left) -
      (listRect.width - chipRect.width) / 2;
    list.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
  }

  function setSetupHint(text, error = false) {
    els.setupHint.textContent = text;
    els.setupHint.classList.toggle("hint-error", error);
  }

  function setupRows() {
    return [...els.setupDevices.querySelectorAll(".setup-device")];
  }

  function setupValues(row) {
    return {
      slot: row.querySelector(".setup-slot").value.trim(),
      udid: row.querySelector(".setup-udid").value.trim(),
      port: row.querySelector(".setup-port").value,
    };
  }

  function nextSetupSlot() {
    let maximum = 0;
    for (const row of setupRows()) {
      const match = setupValues(row).slot.match(/^slot-(\d+)$/i);
      if (match) maximum = Math.max(maximum, Number(match[1]));
    }
    return `slot-${String(maximum + 1).padStart(2, "0")}`;
  }

  function nextSetupPort() {
    const ports = setupRows()
      .map((row) => Number(setupValues(row).port))
      .filter((value) => Number.isInteger(value) && value > 0);
    return ports.length ? Math.max(...ports) + 1 : 4723;
  }

  function markSetupDirty() {
    state.setupDirty = true;
  }

  function updateSetupSummary() {
    const count = setupRows().filter(
      (row) => setupValues(row).udid,
    ).length;
    els.setupSummary.textContent = `${count} device`;
  }

  function createSetupRow(device = {}) {
    const row = element("div", "setup-device");
    const slot = element("input", "setup-slot");
    slot.type = "text";
    slot.placeholder = "slot-01";
    slot.value = device.slot || "";
    slot.setAttribute("aria-label", "Nama slot device");

    const udid = element("input", "setup-udid");
    udid.type = "text";
    udid.placeholder = "UDID / serial ADB";
    udid.value = device.udid || "";
    udid.autocomplete = "off";
    udid.spellcheck = false;
    udid.setAttribute("aria-label", "UDID atau serial ADB");

    const port = element("input", "setup-port");
    port.type = "number";
    port.min = "1";
    port.max = "65535";
    port.value = device.appium_port || nextSetupPort();
    port.setAttribute("aria-label", "Port Appium");

    const remove = element("button", "btn btn-ghost btn-small setup-remove", "");
    remove.type = "button";
    remove.title = "Hapus device";
    remove.setAttribute("aria-label", "Hapus device");
    remove.append(icon("x"));

    row.append(slot, udid, port, remove);
    row.addEventListener("input", () => {
      markSetupDirty();
      updateSetupSummary();
    });
    remove.addEventListener("click", () => {
      row.remove();
      markSetupDirty();
      if (!setupRows().length) createSetupRow();
      updateSetupSummary();
    });
    els.setupDevices.append(row);
    return row;
  }

  function renderSetup(status) {
    if (state.setupLoaded) return;
    els.setupRackName.value = status.rack.name || "rack-01";
    els.setupHost.value = status.rack.host || "127.0.0.1";
    els.setupDevices.replaceChildren();
    for (const device of status.devices) {
      createSetupRow({
        slot: device.slot,
        udid: device.udid,
        appium_port: device.appium_port,
      });
    }
    if (!status.devices.length) createSetupRow();
    updateSetupSummary();
    state.setupLoaded = true;
    state.setupDirty = false;
  }


  function collectSetupConfig() {
    const devices = [];
    for (const row of setupRows()) {
      const values = setupValues(row);
      if (!values.slot && !values.udid && !values.port) continue;
      if (!values.udid) {
        throw new Error("UDID / serial ADB tidak boleh kosong");
      }
      if (!values.slot) {
        throw new Error("Nama slot tidak boleh kosong");
      }
      const port = Number(values.port);
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new Error(`Port untuk ${values.slot} harus 1–65535`);
      }
      devices.push({
        udid: values.udid,
        device_name: values.slot,
        appium_port: port,
      });
    }
    if (!devices.length) {
      throw new Error("Tambahkan minimal satu device");
    }
    const rack = els.setupRackName.value.trim();
    const host = els.setupHost.value.trim();
    if (!rack) throw new Error("Nama rack tidak boleh kosong");
    if (!host) throw new Error("Appium host tidak boleh kosong");
    return { rack, host, devices };
  }

  async function scanSetup() {
    const label = els.setupScan.querySelector("span").textContent;
    els.setupScan.disabled = true;
    setButtonLabel(els.setupScan, "Scanning…");
    setSetupHint("Mencari HP lewat adb devices…");
    try {
      const data = await api("/api/config/scan");
      for (const row of setupRows()) {
        const values = setupValues(row);
        if (!values.slot && !values.udid && !values.port) row.remove();
      }
      const existing = new Set(
        setupRows()
          .map((row) => setupValues(row).udid)
          .filter(Boolean),
      );
      let added = 0;
      for (const device of data.devices || []) {
        if (existing.has(device.udid)) continue;
        createSetupRow({
          slot: nextSetupSlot(),
          udid: device.udid,
          appium_port: nextSetupPort(),
        });
        existing.add(device.udid);
        added += 1;
      }
      if (!setupRows().length) createSetupRow();
      updateSetupSummary();
      if (added) markSetupDirty();
      let message = `${data.count || 0} HP siap${added ? `, ${added} baru ditambahkan` : ""}.`;
      if (data.skipped && data.skipped.length) {
        message +=
          " Dilewati: " +
          data.skipped
            .map((item) => `${item.udid} (${item.status})`)
            .join(", ");
      }
      setSetupHint(message);
    } catch (error) {
      setSetupHint(`Scan gagal: ${error.message}`, true);
    } finally {
      els.setupScan.disabled = false;
      setButtonLabel(els.setupScan, label);
    }
  }

  async function saveSetup() {
    let config;
    try {
      config = collectSetupConfig();
    } catch (error) {
      setSetupHint(error.message, true);
      return;
    }

    const label = els.setupSave.querySelector("span").textContent;
    els.setupSave.disabled = true;
    setButtonLabel(els.setupSave, "Menyimpan…");
    setSetupHint("Menyimpan manifest dan menerapkan konfigurasi…");
    try {
      await api("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      state.setupLoaded = false;
      state.setupDirty = false;
      setSetupHint("Konfigurasi tersimpan. Rack siap di-connect.");
      toast("Konfigurasi rack berhasil disimpan", "ok");
      await refreshStatus();
    } catch (error) {
      setSetupHint(`Gagal menyimpan: ${error.message}`, true);
    } finally {
      els.setupSave.disabled = false;
      setButtonLabel(els.setupSave, label);
    }
  }


  function renderTargets(devices) {
    const known = new Set(devices.map((device) => device.udid));
    if (state.target !== "all" && !known.has(state.target)) {
      state.target = "all";
    }

    els.targetSelect.replaceChildren();
    els.targetSelect.append(new Option("All connected devices", "all"));
    for (const device of devices) {
      els.targetSelect.append(
        new Option(`${device.slot} · ${device.udid}`, device.udid),
      );
    }
    els.targetSelect.value = state.target;

    const campaignValue = els.campaignTarget.value || "all";
    els.campaignTarget.replaceChildren();
    els.campaignTarget.append(new Option("All connected devices", "all"));
    for (const device of devices) {
      els.campaignTarget.append(
        new Option(`${device.slot} · ${device.udid}`, device.udid),
      );
    }
    els.campaignTarget.value = known.has(campaignValue) ? campaignValue : "all";
  }

  function renderCampaignPlatforms(platforms) {
    const catalog = Array.isArray(platforms) ? platforms : [];
    const checked = new Set(
      [...els.campaignPlatforms.querySelectorAll("input:checked")].map(
        (input) => input.value,
      ),
    );
    const supported = catalog.filter(
      (platform) => platform.actions && platform.actions.post,
    );
    els.campaignPlatforms.replaceChildren();
    if (!supported.length) {
      els.campaignPlatforms.append(
        element("p", "hint", "Belum ada platform dengan text post."),
      );
      return;
    }
    for (const platform of supported) {
      const label = element("label", "campaign-option");
      const input = element("input");
      input.type = "checkbox";
      input.value = platform.key;
      input.checked = checked.has(platform.key);
      label.append(input, element("span", "", platform.label));
      els.campaignPlatforms.append(label);
    }
  }

  function scheduleValue(input) {
    if (!input.value) return null;
    const date = new Date(input.value);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }

  function renderAutomation(data) {
    const limits = data.limits || {};
    els.limitFollow.value = limits.follow ?? 0;
    els.limitPost.value = limits.post ?? 0;
    els.limitComment.value = limits.comment ?? 0;
    els.limitLike.value = limits.like ?? 0;
    els.limitSave.value = limits.save ?? 0;
    els.limitReply.value = limits.reply ?? 0;

    const totals = data.totals || {};
    els.analyticsOk.textContent = String(totals.ok || 0);
    els.analyticsFailed.textContent = String(totals.failed || 0);
    els.analyticsDetail.replaceChildren();
    const analytics = data.analytics || {};
    for (const [platform, actions] of Object.entries(analytics)) {
      for (const [action, counters] of Object.entries(actions)) {
        const row = element("div", "analytics-row");
        row.append(
          element("span", "", `${platform} · ${action}`),
          element(
            "span",
            "",
            `${counters.ok || 0} ok / ${counters.failed || 0} gagal`,
          ),
        );
        els.analyticsDetail.append(row);
      }
    }
    if (!els.analyticsDetail.childNodes.length) {
      els.analyticsDetail.append(
        element("span", "hint", "Belum ada aktivitas hari ini."),
      );
    }
  }

  async function refreshAutomation() {
    try {
      renderAutomation(await api("/api/automation"));
    } catch (error) {
      els.automationHint.textContent =
        `Gagal memuat automation: ${error.message}`;
      els.automationHint.classList.add("hint-error");
    }
  }

  function maybeNotify(job) {
    if (!job || !["succeeded", "failed", "cancelled"].includes(job.status)) {
      return;
    }
    if (state.notifiedJobs.has(job.id)) return;
    state.notifiedJobs.add(job.id);
    if (!("Notification" in window) || Notification.permission !== "granted") {
      return;
    }
    try {
      new Notification(`Job ${job.status}`, {
        body: job.label || job.message || job.id,
      });
    } catch (error) {
      /* notification can fail in restricted browsers */
    }
  }

  function renderJobs(jobs) {
    els.jobs.replaceChildren();
    if (!jobs.length) {
      if (!state.jobsInitialized) state.jobsInitialized = true;
      els.jobs.append(element("li", "empty", "Belum ada job."));
      return;
    }
    for (const job of jobs.slice(0, 8)) {
      const item = element("li", "job");
      const top = element("div", "job-top");
      top.append(element("span", "job-label", job.label));
      top.append(
        element("span", `badge badge-${toneFor(job.status)}`, job.status),
      );
      item.append(top);
      item.append(element("p", "job-message", job.message || ""));

      if (job.progress && job.progress.total) {
        const progress = element("div", "job-progress");
        const track = element("div", "progress-track");
        const bar = element("div", "progress-bar");
        const percent = Math.round(
          (job.progress.done / job.progress.total) * 100,
        );
        bar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
        track.append(bar);
        progress.append(track);
        progress.append(
          element(
            "small",
            "muted",
            `${job.progress.done}/${job.progress.total} item · ` +
              `${job.progress.ok} ok · ${job.progress.failed} gagal`,
          ),
        );
        if (job.progress.current) {
          progress.append(
            element("small", "muted", `→ ${job.progress.current}`),
          );
        }
        item.append(progress);
      }

      item.append(element("small", "muted", fmtTime(job.created_at)));

      const actions = element("div", "job-actions");
      if (job.cancellable) {
        const cancel = element("button", "btn btn-danger btn-small", "");
        cancel.type = "button";
        cancel.append(icon("x"), element("span", null, "Cancel"));
        cancel.addEventListener("click", () =>
          jobCommand(`/api/jobs/${job.id}/cancel`, "Cancel"),
        );
        actions.append(cancel);
      }
      if (job.retryable) {
        const retry = element("button", "btn btn-ghost btn-small", "");
        retry.type = "button";
        retry.append(icon("refresh"), element("span", null, "Retry"));
        retry.addEventListener("click", () =>
          jobCommand(`/api/jobs/${job.id}/retry`, "Retry"),
        );
        actions.append(retry);
      }
      if (actions.childNodes.length) item.append(actions);

      if (!state.jobsInitialized) {
        for (const current of jobs) {
          if (["succeeded", "failed", "cancelled"].includes(current.status)) {
            state.notifiedJobs.add(current.id);
          }
        }
        state.jobsInitialized = true;
      } else {
        maybeNotify(job);
      }
      els.jobs.append(item);
    }
  }

  function renderLogs() {
    els.logs.replaceChildren();
    if (!state.logs.length) {
      els.logs.append(element("li", "empty", "Menunggu aktivitas…"));
      return;
    }
    for (const entry of state.logs.slice(-300)) {
      const item = element("li", "log log-" + String(entry.level).toLowerCase());
      item.append(element("span", "log-time", fmtTime(entry.ts)));
      item.append(element("span", "log-level", entry.level));
      item.append(element("span", "log-message", entry.message));
      els.logs.append(item);
    }
    els.logs.scrollTop = els.logs.scrollHeight;
  }

  const ACTION_LABELS = {
    follow: "Follow user",
    post: "Create post",
    comment: "Comment on post",
    like: "Like post",
    save: "Save / bookmark post",
    reply: "Reply to post",
  };

  function allPlatforms() {
    const catalog = state.status && state.status.platforms;
    return Array.isArray(catalog) && catalog.length
      ? catalog
      : FALLBACK_PLATFORMS;
  }

  function currentPlatform() {
    return (
      allPlatforms().find((platform) => platform.key === state.platform) ||
      null
    );
  }

  function renderPlatforms(platforms, defaultKey) {
    const catalog =
      Array.isArray(platforms) && platforms.length
        ? platforms
        : FALLBACK_PLATFORMS;
    const keys = catalog.map((platform) => platform.key);
    if (!state.platform || !keys.includes(state.platform)) {
      state.platform = keys.includes(defaultKey) ? defaultKey : keys[0];
    }

    const signature = catalog
      .map(
        (platform) =>
          `${platform.key}:${platform.label}:${Object.keys(
            platform.actions,
          ).join(",")}:${platform.notes || ""}`,
      )
      .join("|");

    if (state.platformsSignature !== signature) {
      state.platformsSignature = signature;
      els.platformSelect.replaceChildren();
      for (const platform of catalog) {
        els.platformSelect.append(new Option(platform.label, platform.key));
      }
      renderActionOptions();
    }

    els.platformSelect.value = state.platform;
  }

  function renderActionOptions() {
    const platform = currentPlatform();
    const actions = platform ? platform.actions : ACTION_FIELDS;
    const previous = els.actionSelect.value;

    els.actionSelect.replaceChildren();
    for (const action of Object.keys(actions)) {
      els.actionSelect.append(
        new Option(ACTION_LABELS[action] || action, action),
      );
    }
    if (actions[previous]) els.actionSelect.value = previous;

    els.platformNote.textContent = platform ? platform.notes || "" : "";
    renderActionFields();
  }

  function setMode(mode) {
    state.mode = mode;
    const bulk = mode === "bulk";
    els.modeSingle.classList.toggle("is-active", !bulk);
    els.modeBulk.classList.toggle("is-active", bulk);
    renderActionFields();
  }

  function bulkHintFor(action) {
    if (action === "comment" || action === "reply") {
      return "Format: https://post-url | teks (satu per baris, maks 200).";
    }
    if (action === "like" || action === "save") {
      return "Satu URL postingan per baris (maks 200 item).";
    }
    if (action === "post") {
      return "Satu teks post per baris (maks 200 item).";
    }
    return "Satu username per baris (maks 200 item).";
  }

  function renderActionFields() {
    const action = els.actionSelect.value;
    const platform = currentPlatform();
    const fields =
      (platform && platform.actions && platform.actions[action]) ||
      ACTION_FIELDS[action] ||
      [];
    const bulk = state.mode === "bulk";
    const singleField = (field) => !bulk && fields.includes(field);

    els.usernameWrap.hidden = !singleField("username");
    els.urlWrap.hidden = !singleField("url");
    els.textWrap.hidden = !singleField("text");
    els.bulkFields.hidden = !bulk;
    els.bulkHint.textContent = bulkHintFor(action);

    els.textLabel.textContent =
      action === "comment"
        ? "Comment text"
        : action === "reply"
          ? "Reply text"
          : "Post text";
    els.text.placeholder =
      action === "comment"
        ? "Tulis komentar…"
        : action === "reply"
          ? "Tulis balasan…"
          : "Tulis teks postingan…";
  }

  function currentDevice(udid) {
    if (!state.status) return null;
    return state.status.devices.find((device) => device.udid === udid) || null;
  }

  function screenPointFromEvent(event) {
    const img = els.liveImg;
    const rect = img.getBoundingClientRect();
    const naturalWidth = img.naturalWidth || rect.width;
    const naturalHeight = img.naturalHeight || rect.height;
    if (!rect.width || !rect.height || !naturalWidth || !naturalHeight) {
      return null;
    }
    const scale = Math.min(
      rect.width / naturalWidth,
      rect.height / naturalHeight,
    );
    const width = naturalWidth * scale;
    const height = naturalHeight * scale;
    const left = rect.left + (rect.width - width) / 2;
    const top = rect.top + (rect.height - height) / 2;
    const x = (event.clientX - left) / width;
    const y = (event.clientY - top) / height;
    if (![x, y].every(Number.isFinite) || x < 0 || x > 1 || y < 0 || y > 1) {
      return null;
    }
    return { x, y };
  }

  function showRipple(clientX, clientY) {
    const frame = els.liveImg.parentElement;
    const rect = frame.getBoundingClientRect();
    const ripple = element("span", "tap-ripple");
    ripple.style.left = `${clientX - rect.left}px`;
    ripple.style.top = `${clientY - rect.top}px`;
    frame.append(ripple);
    setTimeout(() => ripple.remove(), 500);
  }

  async function sendScreenInput(payload, label) {
    if (!state.liveUdid) return;
    els.screenInputStatus.textContent = `Mengirim ${label}…`;
    try {
      const result = await api(
        `/api/devices/${encodeURIComponent(state.liveUdid)}/input`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const coords =
        result.x !== undefined ? ` (${result.x}, ${result.y})` : "";
      els.screenInputStatus.textContent = `${label} terkirim${coords}`;
      return true;
    } catch (error) {
      els.screenInputStatus.textContent = `Gagal ${label}: ${error.message}`;
      return false;
    }
  }

  function sendScreenKey(key) {
    sendScreenInput({ type: "key", key }, `tombol ${key}`);
  }

  function sendScreenText() {
    const text = els.screenText.value;
    if (!text || !state.liveUdid) return;
    sendScreenInput({ type: "text", text }, "teks").then((sent) => {
      if (sent) els.screenText.value = "";
    });
  }

  function setLiveStatus(text, tone) {
    els.liveStatus.textContent = text;
    els.liveStatus.className = `pill pill-${tone}`;
  }

  const SCREEN_SIZE_KEY = "gads.liveScreenSize";
  const SCREEN_SIZES = new Set(["fit", "compact", "medium", "large", "actual"]);

  function applyScreenSize(value) {
    const size = SCREEN_SIZES.has(value) ? value : "fit";
    state.screenSize = size;
    els.livePanel.dataset.screenSize = size;
    els.screenSize.value = size;
    try {
      localStorage.setItem(SCREEN_SIZE_KEY, size);
    } catch (error) {
      /* localStorage can be unavailable in private mode */
    }
  }

  function restoreScreenSize() {
    let saved = "fit";
    try {
      saved = localStorage.getItem(SCREEN_SIZE_KEY) || "fit";
    } catch (error) {
      /* use default */
    }
    applyScreenSize(saved);
  }

  function markLiveCard(udid) {
    for (const [chipUdid, entry] of state.liveStrip) {
      const active = chipUdid === udid;
      entry.button.classList.toggle("is-active", active);
      entry.button.setAttribute("aria-pressed", String(active));
    }
    if (udid) {
      const entry = state.liveStrip.get(udid);
      if (entry) centerLiveChip(entry);
    }
  }

  function renderMuteButton(device) {
    const muted = Boolean(device && device.muted);
    state.liveMuted = muted;
    els.screenMute.classList.toggle("is-active", muted);
    els.screenMute.setAttribute("aria-pressed", String(muted));
    setButtonLabel(els.screenMute, muted ? "Unmute" : "Mute");
    els.screenMute.title = muted ? "Aktifkan suara HP" : "Mute suara HP";
    const use = els.screenMute.querySelector("use");
    if (use) use.setAttribute("href", muted ? "#icon-volume-off" : "#icon-volume");
    els.screenMute.disabled =
      !device || !device.connected || state.liveMuteBusy;
  }

  async function toggleLiveMute() {
    const device = currentDevice(state.liveUdid);
    if (!device || !device.connected || state.liveMuteBusy) return;
    const next = !state.liveMuted;
    state.liveMuteBusy = true;
    els.screenMute.disabled = true;
    els.screenInputStatus.textContent = next
      ? "Mute suara HP…"
      : "Mengaktifkan suara HP…";
    try {
      const result = await api(
        `/api/devices/${encodeURIComponent(device.udid)}/audio`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ muted: next }),
        },
      );
      device.muted = Boolean(result.muted);
      els.screenInputStatus.textContent = result.muted
        ? "Suara HP di-mute."
        : "Suara HP aktif.";
    } catch (error) {
      els.screenInputStatus.textContent = `Gagal ubah suara: ${error.message}`;
    } finally {
      state.liveMuteBusy = false;
      renderMuteButton(device);
    }
  }

  function fillLiveMeta(device) {
    if (!device) {
      els.panelSlot.textContent = "—";
      els.panelDevice.textContent = "—";
      els.panelSession.textContent = "—";
      els.panelPort.textContent = "—";
      return;
    }
    els.panelSlot.textContent = device.slot;
    els.panelDevice.textContent = device.udid;
    els.panelSession.textContent = device.session_id || "—";
    els.panelPort.textContent = device.appium_port;
  }

  function openScreen(udid) {
    const device = currentDevice(udid);
    if (!device) return;
    state.liveUdid = udid;
    els.liveRetry.disabled = !device.connected;
    els.liveTitle.textContent = `Live · ${device.slot}`;
    els.liveSubtitle.textContent = `${device.udid} · port ${device.appium_port}`;
    fillLiveMeta(device);
    renderMuteButton(device);
    markLiveCard(udid);
    els.screenText.value = "";
    els.screenInputStatus.textContent = device.connected
      ? "Klik = tap · drag = swipe"
      : "Device offline.";
    startLiveStream(device);
    requestAnimationFrame(() => {
      els.livePanel.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function updateLivePanel() {
    if (!state.liveUdid) return;
    const device = currentDevice(state.liveUdid);
    if (!device) {
      stopLiveScreen();
      return;
    }
    els.liveTitle.textContent = `Live · ${device.slot}`;
    els.liveSubtitle.textContent = `${device.udid} · port ${device.appium_port}`;
    fillLiveMeta(device);
    renderMuteButton(device);
    if (!device.connected) {
      stopLiveStream();
      els.liveEmpty.hidden = false;
      els.liveEmpty.textContent = "Device offline.";
      setLiveStatus("Offline", "muted");
      els.screenInputStatus.textContent = "Device offline.";
      els.liveRetry.disabled = true;
    } else if (!els.liveImg.getAttribute("src")) {
      els.liveRetry.disabled = false;
      startLiveStream(device);
    }
  }

  function startLiveStream(device) {
    els.liveImg.hidden = true;
    els.liveImg.removeAttribute("src");
    els.liveEmpty.hidden = false;
    els.screenResolution.textContent = "—";
    if (!device.connected) {
      els.liveEmpty.textContent = "Device offline. Connect dulu.";
      setLiveStatus("Offline", "muted");
      return;
    }
    els.liveEmpty.textContent = "Menghubungkan live screen…";
    setLiveStatus("Connecting", "warn");
    els.liveImg.src =
      `/api/devices/${encodeURIComponent(device.udid)}/stream` +
      `?interval=1&t=${Date.now()}`;
  }

  function stopLiveStream() {
    els.liveImg.removeAttribute("src");
    els.liveImg.hidden = true;
  }

  function stopLiveScreen() {
    stopLiveStream();
    state.liveUdid = null;
    markLiveCard(null);
    els.liveTitle.textContent = "Live screen";
    els.liveSubtitle.textContent = "Pilih device dari daftar di bawah";
    fillLiveMeta(null);
    renderMuteButton(null);
    setLiveStatus("—", "muted");
    els.liveRetry.disabled = true;
    els.liveEmpty.hidden = false;
    els.liveEmpty.textContent = "Pilih device dari daftar di bawah.";
    els.screenResolution.textContent = "—";
    els.screenText.value = "";
    els.screenInputStatus.textContent = "Klik = tap · drag = swipe";
  }

  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
      setOnline(true);
      render();
    } catch (error) {
      console.error("Dashboard status poll failed:", error);
      setOnline(false);
    }
  }

  async function refreshLogs() {
    if (state.paused) return;
    try {
      const data = await api(`/api/logs?since=${state.lastLogId}`);
      if (Array.isArray(data.logs) && data.logs.length) {
        state.logs = state.logs.concat(data.logs).slice(-300);
        state.lastLogId = data.last_id || state.lastLogId;
        renderLogs();
      }
    } catch (error) {
      /* the status poll drives the offline banner */
    }
  }

  async function tick() {
    await refreshStatus();
    await refreshLogs();
  }

  async function submitJob(path, payload, button) {
    button.disabled = true;
    try {
      const job = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      els.hint.classList.remove("hint-error");
      els.hint.textContent = `Job ${job.id} masuk antrean.`;
      toast(`Job ${job.id}: ${job.label}`, "ok");
      await refreshStatus();
    } catch (error) {
      toast(`Gagal: ${error.message}`, "bad");
    } finally {
      button.disabled = false;
    }
  }

  async function jobCommand(path, label) {
    try {
      const result = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const targetLabel =
        result.label || (result.job && result.job.label) || "ok";
      toast(`${label}: ${targetLabel}`, "ok");
      await refreshStatus();
    } catch (error) {
      toast(`${label} gagal: ${error.message}`, "bad");
    }
  }

  els.actionSelect.addEventListener("change", renderActionFields);
  els.platformSelect.addEventListener("change", () => {
    state.platform = els.platformSelect.value;
    renderActionOptions();
  });
  els.targetSelect.addEventListener("change", () => {
    state.target = els.targetSelect.value;
  });

  els.modeSingle.addEventListener("click", () => setMode("single"));
  els.modeBulk.addEventListener("click", () => setMode("bulk"));

  els.bulkFile.addEventListener("change", async () => {
    const file = els.bulkFile.files && els.bulkFile.files[0];
    if (!file) return;
    try {
      const text = (await file.text()).replace(/^\uFEFF/, "");
      const lines = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      els.bulkItems.value = lines.join("\n");
      els.bulkHint.textContent = `${lines.length} item dimuat dari ${file.name}.`;
    } catch (error) {
      toast(`Gagal membaca file: ${error.message}`, "bad");
    } finally {
      els.bulkFile.value = "";
    }
  });

  els.actionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;

    const action = els.actionSelect.value;
    let endpoint = "/api/action";
    let payload;

    if (state.mode === "bulk") {
      const items = els.bulkItems.value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      if (!items.length) {
        els.hint.textContent =
          "Bulk: isi minimal satu item (satu per baris).";
        els.hint.classList.add("hint-error");
        return;
      }
      endpoint = "/api/bulk";
      payload = {
        platform: state.platform,
        action,
        target: els.targetSelect.value,
        items,
        interval: Number(els.bulkInterval.value || 0),
        retries: Number(els.bulkRetries.value || 0),
      };
    } else {
      const params = {};
      if (action === "follow") params.username = els.username.value.trim();
      if (action === "post") params.text = els.text.value;
      if (action === "comment" || action === "reply") {
        params.url = els.url.value.trim();
        params.text = els.text.value;
      }
      if (action === "like" || action === "save") {
        params.url = els.url.value.trim();
      }
      payload = {
        platform: state.platform,
        action,
        target: els.targetSelect.value,
        params,
      };
    }

    const scheduledAt = scheduleValue(els.actionSchedule);
    if (scheduledAt) payload.schedule_at = scheduledAt;

    state.busy = true;
    els.submit.disabled = true;
    els.hint.classList.remove("hint-error");
    els.hint.textContent = "Mengirim job…";
    try {
      const job = await api(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      els.hint.textContent =
        job.status === "scheduled"
          ? `Job ${job.id} dijadwalkan (${job.message}).`
          : `Job ${job.id} masuk antrean (${job.label}).`;
      toast(
        job.status === "scheduled"
          ? `Job ${job.id} dijadwalkan`
          : `Job ${job.id}: ${job.label}`,
        "ok",
      );
      await refreshStatus();
    } catch (error) {
      els.hint.textContent = `Gagal: ${error.message}`;
      els.hint.classList.add("hint-error");
    } finally {
      state.busy = false;
      els.submit.disabled = false;
    }
  });

  els.campaignSubmit.addEventListener("click", async () => {
    const text = els.campaignText.value;
    const platforms = [
      ...els.campaignPlatforms.querySelectorAll("input:checked"),
    ].map((input) => input.value);
    if (!text.trim()) {
      els.campaignHint.textContent = "Teks campaign tidak boleh kosong.";
      els.campaignHint.classList.add("hint-error");
      return;
    }
    if (!platforms.length) {
      els.campaignHint.textContent = "Pilih minimal satu platform.";
      els.campaignHint.classList.add("hint-error");
      return;
    }
    const payload = {
      text,
      platforms,
      target: els.campaignTarget.value,
      interval: Number(els.campaignInterval.value || 0),
    };
    const scheduledAt = scheduleValue(els.campaignSchedule);
    if (scheduledAt) payload.schedule_at = scheduledAt;

    els.campaignSubmit.disabled = true;
    els.campaignHint.classList.remove("hint-error");
    els.campaignHint.textContent = "Mengirim campaign…";
    try {
      const job = await api("/api/campaign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      els.campaignHint.textContent =
        job.status === "scheduled"
          ? `Campaign ${job.id} dijadwalkan.`
          : `Campaign ${job.id} masuk antrean.`;
      toast(
        job.status === "scheduled"
          ? `Campaign ${job.id} dijadwalkan`
          : `Campaign ${job.id}: ${job.label}`,
        "ok",
      );
      els.campaignText.value = "";
      await refreshStatus();
    } catch (error) {
      els.campaignHint.textContent = `Gagal: ${error.message}`;
      els.campaignHint.classList.add("hint-error");
    } finally {
      els.campaignSubmit.disabled = false;
    }
  });

  els.liveImg.addEventListener("load", () => {
    if (!state.liveUdid) return;
    els.liveImg.hidden = false;
    els.liveEmpty.hidden = true;
    els.screenResolution.textContent =
      `${els.liveImg.naturalWidth} × ${els.liveImg.naturalHeight}`;
    setLiveStatus("Live", "ok");
  });
  els.liveImg.addEventListener("error", () => {
    if (!state.liveUdid) return;
    els.liveImg.hidden = true;
    els.liveEmpty.hidden = false;
    els.liveEmpty.textContent = "Stream berhenti. Coba lagi.";
    setLiveStatus("Terputus", "warn");
  });

  let screenPointerStart = null;

  els.liveImg.addEventListener("pointerdown", (event) => {
    if (!state.liveUdid) return;
    const point = screenPointFromEvent(event);
    if (!point) return;
    event.preventDefault();
    if (els.liveImg.setPointerCapture) {
      try {
        els.liveImg.setPointerCapture(event.pointerId);
      } catch (error) {
        /* ignore */
      }
    }
    screenPointerStart = { point, time: performance.now() };
  });

  els.liveImg.addEventListener("pointerup", (event) => {
    if (!screenPointerStart) return;
    const start = screenPointerStart;
    screenPointerStart = null;
    const end = screenPointFromEvent(event);
    if (!end) return;
    event.preventDefault();
    showRipple(event.clientX, event.clientY);

    const distance = Math.hypot(
      end.x - start.point.x,
      end.y - start.point.y,
    );
    if (distance < 0.02) {
      sendScreenInput({ type: "tap", x: end.x, y: end.y }, "tap");
    } else {
      const duration = Math.min(
        1500,
        Math.max(120, Math.round(performance.now() - start.time)),
      );
      sendScreenInput(
        { type: "swipe", from: start.point, to: end, duration },
        "swipe",
      );
    }
  });

  els.liveImg.addEventListener("pointercancel", () => {
    screenPointerStart = null;
  });

  els.screenBack.addEventListener("click", () => sendScreenKey("back"));
  els.screenHome.addEventListener("click", () => sendScreenKey("home"));
  els.screenEnter.addEventListener("click", () => sendScreenKey("enter"));
  els.screenMute.addEventListener("click", toggleLiveMute);
  els.screenSendText.addEventListener("click", sendScreenText);
  els.screenText.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendScreenText();
    }
  });

  els.liveClose.addEventListener("click", stopLiveScreen);
  els.liveRetry.addEventListener("click", () => {
    const device = currentDevice(state.liveUdid);
    if (device) startLiveStream(device);
  });
  els.liveStripPrev.addEventListener("click", () => scrollLiveStrip(-1));
  els.liveStripNext.addEventListener("click", () => scrollLiveStrip(1));
  els.liveDeviceList.addEventListener(
    "wheel",
    (event) => {
      const list = els.liveDeviceList;
      if (list.scrollWidth <= list.clientWidth) return;
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      list.scrollLeft += event.deltaY;
    },
    { passive: false },
  );
  els.screenSize.addEventListener("change", () =>
    applyScreenSize(els.screenSize.value),
  );

  els.setupAdd.addEventListener("click", () => {
    createSetupRow({
      slot: nextSetupSlot(),
      appium_port: nextSetupPort(),
    });
    markSetupDirty();
    updateSetupSummary();
    setSetupHint("Isi UDID / serial ADB, lalu simpan.");
  });
  els.setupScan.addEventListener("click", scanSetup);
  els.setupSave.addEventListener("click", saveSetup);
  els.setupRackName.addEventListener("input", markSetupDirty);
  els.setupHost.addEventListener("input", markSetupDirty);

  els.limitsSave.addEventListener("click", async () => {
    const limits = {
      follow: Number(els.limitFollow.value || 0),
      post: Number(els.limitPost.value || 0),
      comment: Number(els.limitComment.value || 0),
      like: Number(els.limitLike.value || 0),
      save: Number(els.limitSave.value || 0),
      reply: Number(els.limitReply.value || 0),
    };
    els.limitsSave.disabled = true;
    els.automationHint.classList.remove("hint-error");
    try {
      renderAutomation(await api("/api/automation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limits }),
      }));
      els.automationHint.textContent = "Batas harian disimpan.";
      toast("Batas harian disimpan", "ok");
    } catch (error) {
      els.automationHint.textContent = `Gagal: ${error.message}`;
      els.automationHint.classList.add("hint-error");
    } finally {
      els.limitsSave.disabled = false;
    }
  });

  els.healthCheck.addEventListener("click", async () => {
    els.healthCheck.disabled = true;
    try {
      const job = await api("/api/health", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: "all" }),
      });
      els.automationHint.textContent = `Health check ${job.id} masuk antrean.`;
      toast(`Health check ${job.id}`, "ok");
      await refreshStatus();
    } catch (error) {
      els.automationHint.textContent = `Gagal: ${error.message}`;
      els.automationHint.classList.add("hint-error");
    } finally {
      els.healthCheck.disabled = false;
    }
  });

  els.notifyEnable.addEventListener("click", async () => {
    if (!("Notification" in window)) {
      els.automationHint.textContent =
        "Browser ini tidak mendukung notifikasi.";
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      setButtonLabel(
        els.notifyEnable,
        permission === "granted" ? "Notifikasi aktif" : "Notifikasi diblokir",
      );
    } catch (error) {
      els.automationHint.textContent =
        `Notifikasi tidak tersedia: ${error.message}`;
    }
  });

  const controlAccordion = document.getElementById("control-accordion");
  const accordionButtons = controlAccordion
    ? [...controlAccordion.querySelectorAll(".accordion-toggle")]
    : [...document.querySelectorAll(".accordion-toggle")];

  function closeAccordion(button) {
    const body = document.getElementById(button.getAttribute("aria-controls"));
    button.setAttribute("aria-expanded", "false");
    if (body) body.hidden = true;
    const item = button.closest(".accordion-item");
    if (item) item.classList.remove("is-open");
  }

  accordionButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const body = document.getElementById(button.getAttribute("aria-controls"));
      if (!body) return;
      const expanded = button.getAttribute("aria-expanded") === "true";

      if (expanded) {
        closeAccordion(button);
        return;
      }

      for (const other of accordionButtons) {
        if (other !== button) closeAccordion(other);
      }
      button.setAttribute("aria-expanded", "true");
      body.hidden = false;
      const item = button.closest(".accordion-item");
      if (item) item.classList.add("is-open");
      button.scrollIntoView({ block: "nearest" });
      if (button.id === "accordion-automation") {
        refreshAutomation();
      }
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") stopLiveScreen();
  });

  $("btn-connect").addEventListener("click", () =>
    submitJob("/api/connect", { udids: null }, $("btn-connect")),
  );
  $("btn-disconnect").addEventListener("click", () =>
    submitJob("/api/disconnect", {}, $("btn-disconnect")),
  );
  $("btn-refresh").addEventListener("click", tick);

  if (els.themeButton) {
    els.themeButton.addEventListener("click", toggleTheme);
  }
  if (typeof THEME_MEDIA.addEventListener === "function") {
    THEME_MEDIA.addEventListener("change", () => {
      if (!savedTheme()) {
        applyTheme(THEME_MEDIA.matches ? "light" : "dark", false);
      }
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) {
      applyTheme(
        savedTheme() || (THEME_MEDIA.matches ? "light" : "dark"),
        false,
      );
    }
  });

  els.pause.addEventListener("click", () => {
    state.paused = !state.paused;
    els.pause.dataset.paused = String(state.paused);
    setButtonLabel(els.pause, state.paused ? "Resume" : "Pause");
    if (!state.paused) refreshLogs();
  });

  els.clear.addEventListener("click", () => {
    state.logs = [];
    renderLogs();
  });

  renderPlatforms(FALLBACK_PLATFORMS, "facebook");
  renderActionFields();
  restoreTheme();
  restoreScreenSize();
  refreshAutomation();
  tick();
  setInterval(tick, 2000);
})();
