// === DOM ===
const $ = (id) => document.getElementById(id);

const urlInput      = $('url-input');
const btnChooseFile = $('btn-choose-file');
const fileLabel     = $('file-label');
const dropzone      = $('dropzone');
const whisperModelSelect = $('whisperModelSelect');
const llmModelSelect = $('llmModelSelect');
const computeType   = $('compute-type');
const language      = $('language');
const summaryLanguageSelect = $('summaryLanguageSelect');
const fastModeBadge = $('fastModeBadge');
const contextWindowSelect = $('contextWindowSelect');
const llmGpuLayersSelect = $('llm-gpu-layers');
const gpuInfoBadge  = $('gpuInfoBadge');
const outputDir     = $('output-dir');
const btnOutputDir  = $('btn-output-dir');
const btnStart      = $('btn-start');
const btnCancel     = $('btn-cancel');
const etaBox        = $('etaBox');
const etaPre        = $('etaPre');
const etaRuntime    = $('etaRuntime');
const progressPanel = $('progress-panel');
const progressBar   = $('progress-bar');
const progressPct   = $('progress-percent');
const progressMsg   = $('progress-message');
const errorEl       = $('error-message');
const resultPanel   = $('result-panel');
const codexBody     = resultPanel ? resultPanel.querySelector('.codex-body') : null;
const resultContent = $('result-content');
const resultPlaceholder = $('result-placeholder');
const resultActions = $('result-actions');
const headerStatus  = $('header-status');
const btnCopy       = $('btn-copy');
const btnOpenFile   = $('btn-open-file');
const btnOpenFolder = $('btn-open-folder');
const btnInqPromptHelp = $('btn-inq-prompt-help');
const btnInqPromptHelpClose = $('btn-inq-prompt-help-close');
const inqPromptHelpPanel = $('inq-prompt-help-panel');
const modeToggle    = $('mode-toggle');
const inquisitionPanel = $('inquisition-panel');
const topbarSubtitle = $('topbar-subtitle');
const etaLabel      = $('eta-label');
const codexTitle    = $('codex-title');
const illumText     = $('illum-text');
const illumCap      = $('illum-cap');
const resultMeta    = $('result-meta');
const inqGoal       = $('inq-goal');
const inqRequired   = $('inq-required');
const inqRedflags   = $('inq-redflags');
const inqCriteria   = $('inq-criteria');
const bardPanel     = $('bard-panel');
const bardFocus     = $('bard-focus');
const bardCount     = $('bard-count');
const bardTimestamps = $('bard-timestamps');
const bardQuotes    = $('bard-quotes');
const diarizeToggle = $('diarize-toggle');
const noSummaryToggle = $('no-summary-toggle');
const disableThinkingToggle = $('disable-thinking-toggle');

// === State ===
let selectedFile = null;
let outputPath = null;
let rawMarkdown = '';
let displayMarkdown = '';
let isProcessing = false;
let currentMode = 'chronicle';  // 'chronicle', 'inquisition', or 'bard'
let etaCountdownId = null;
let etaRemainingSec = 0;
let elapsedTimerId = null;
let elapsedSec = 0;
let pendingProgressMsg = null;
let progressRafId = null;
let inqPromptHelpTransitionId = 0;

const STAGES = ['download', 'extract', 'transcribe', 'summarize', 'format'];
const INQ_PROMPT_HELP_STATE_KEY = 'lvt_inq_prompt_help_open';

/** After codex scroll, keep #illum-text inside the visible codex-body rect (instant). */
function nudgeCodexScrollForIllumText() {
  const el = document.getElementById('illum-text');
  if (!el || !codexBody) return;
  const br = codexBody.getBoundingClientRect();
  const ir = el.getBoundingClientRect();
  if (ir.height < 2) return;
  const pad = 12;
  if (ir.bottom <= br.bottom - pad && ir.top >= br.top + pad) return;
  let delta = 0;
  if (ir.bottom > br.bottom - pad) delta = ir.bottom - br.bottom + pad;
  else if (ir.top < br.top + pad) delta = ir.top - br.top - pad;
  if (!delta) return;
  const maxScroll = codexBody.scrollHeight - codexBody.clientHeight;
  codexBody.scrollTop = Math.max(0, Math.min(maxScroll, Math.round(codexBody.scrollTop + delta)));
}

// === Helpers ===
function fmtEta(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/** llama.cpp n_gpu_layers from Rubrics (-1 = all on GPU). */
function readLlmGpuLayers() {
  if (!llmGpuLayersSelect) return -1;
  const n = parseInt(llmGpuLayersSelect.value, 10);
  return Number.isFinite(n) ? n : -1;
}

function startElapsedTimer() {
  stopElapsedTimer();
  elapsedSec = 0;
  elapsedTimerId = setInterval(() => {
    elapsedSec++;
    // Only update etaRuntime; etaPre is now reserved for ETA/remaining
    etaRuntime.textContent = `elapsed: ${fmtEta(elapsedSec)}`;
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimerId) { clearInterval(elapsedTimerId); elapsedTimerId = null; }
}

function startEtaCountdown(totalSec) {
  stopEtaCountdown();
  etaRemainingSec = Math.max(0, Math.round(Number(totalSec) || 0));
  etaPre.textContent = fmtEta(etaRemainingSec);
  etaCountdownId = setInterval(() => {
    etaRemainingSec = Math.max(0, etaRemainingSec - 1);
    etaPre.textContent = fmtEta(etaRemainingSec);
    // Do NOT auto-stop at 0 — keep the countdown alive so subsequent
    // backend runtime updates don't look like a fresh "hourglass flip"
    // (observed 2026-04-18: users perceived the elapsed/remaining display
    // as resetting whenever the countdown was torn down and rebuilt).
  }, 1000);
}

function stopEtaCountdown() {
  if (etaCountdownId) { clearInterval(etaCountdownId); etaCountdownId = null; }
}

function setHourglassRunning(running) {
  const svg = document.querySelector('.hourglass-svg');
  if (!svg) return;
  if (running) {
    svg.classList.remove('stopped');
  } else {
    svg.classList.add('stopped');
  }
}

function getSource() {
  return selectedFile || urlInput.value.trim();
}

function setProcessingIllum(on) {
  document.body.classList.toggle('processing-illum', Boolean(on));
}

function updateIllumCapSeal(mode) {
  if (!illumCap) return;
  const sealClass = {
    chronicle: 'illum-seal-quill',
    inquisition: 'illum-seal-mace',
    bard: 'illum-seal-lute',
  }[mode] || 'illum-seal-quill';
  illumCap.querySelectorAll('.illum-cap-seal').forEach((svg) => {
    if (svg.classList.contains(sealClass)) {
      svg.classList.remove('hidden');
    } else {
      svg.classList.add('hidden');
    }
  });
}

function setMode(mode) {
  currentMode = mode;
  localStorage.setItem('lvt_mode', mode);

  // Update button states
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.classList.remove('active');
    btn.setAttribute('aria-pressed', 'false');
  });
  const activeModeBtn = document.querySelector(`.mode-btn[data-mode="${mode}"]`);
  if (activeModeBtn) {
    activeModeBtn.classList.add('active');
    activeModeBtn.setAttribute('aria-pressed', 'true');
  }

  // Remove all mode classes
  document.body.classList.remove('inquisition-mode', 'bard-mode');

  // Hide all mode panels
  inquisitionPanel.classList.add('hidden');
  inquisitionPanel.classList.remove('visible');
  if (bardPanel) {
    bardPanel.classList.add('hidden');
    bardPanel.classList.remove('visible');
  }
  if (btnInqPromptHelp) btnInqPromptHelp.hidden = true;
  setInqPromptHelpOpen(false, { immediate: true });

  // Apply mode-specific settings
  if (mode === 'inquisition') {
    inquisitionPanel.classList.remove('hidden');
    inquisitionPanel.classList.add('visible');
    document.body.classList.add('inquisition-mode');
    topbarSubtitle.textContent = 'The Tribunal';
    if (btnInqPromptHelp) btnInqPromptHelp.hidden = false;
    const shouldOpenHelp = localStorage.getItem(INQ_PROMPT_HELP_STATE_KEY) === '1';
    setInqPromptHelpOpen(shouldOpenHelp, { immediate: true });
  } else if (mode === 'bard') {
    if (bardPanel) {
      bardPanel.classList.remove('hidden');
      bardPanel.classList.add('visible');
    }
    document.body.classList.add('bard-mode');
    topbarSubtitle.textContent = 'The Minstrel';
  } else {
    topbarSubtitle.textContent = 'The Chronicler';
  }

  // Update codex title in result
  if (codexTitle) {
    const titles = { chronicle: 'The Chronicle', inquisition: 'The Tribunal', bard: 'The Ballad' };
    codexTitle.textContent = titles[mode] || 'The Chronicle';
  }

  // Update placeholder text and illuminated seal
  if (illumText) {
    if (mode === 'inquisition') {
      illumText.innerHTML = 'Herein shall the inquisitor\'s verdicts appear,<br>once the voice hath been examined for heresy.';
    } else if (mode === 'bard') {
      illumText.innerHTML = 'Herein shall the bard\'s ballad unfold,<br>the finest moments plucked like strings of a lute.';
    } else {
      illumText.innerHTML = 'Herein shall the scribe\'s chronicle appear,<br>once the voice hath been committed to vellum.';
    }
  }
  updateIllumCapSeal(mode);

  // Update ETA label
  if (etaLabel) {
    etaLabel.innerHTML = (mode === 'inquisition' || mode === 'bard') ? 'IV. &nbsp;The hourglass' : 'III. &nbsp;The hourglass';
  }
}

function updateInqPromptHelpButton(open) {
  if (!btnInqPromptHelp) return;
  btnInqPromptHelp.textContent = open ? 'Hide guide' : 'Prompt guide';
  btnInqPromptHelp.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function ensureInqPromptHelpVisible() {
  if (!inqPromptHelpPanel || !codexBody || inqPromptHelpPanel.hidden) return;
  const panelRect = inqPromptHelpPanel.getBoundingClientRect();
  const bodyRect = codexBody.getBoundingClientRect();
  const topOverflow = panelRect.top < bodyRect.top + 8;
  const bottomOverflow = panelRect.bottom > bodyRect.bottom - 8;
  if (!topOverflow && !bottomOverflow) return;
  const targetTop = Math.max(0, inqPromptHelpPanel.offsetTop - 8);
  codexBody.scrollTo({ top: targetTop, behavior: 'auto' });
  nudgeCodexScrollForIllumText();
}

function setInqPromptHelpOpen(isOpen, opts = {}) {
  if (!inqPromptHelpPanel || !btnInqPromptHelp) return;
  const restoreFocus = Boolean(opts.restoreFocus);
  const immediate = Boolean(opts.immediate);
  const ensureVisible = Boolean(opts.ensureVisible);
  const open = Boolean(isOpen) && currentMode === 'inquisition';
  const transitionToken = ++inqPromptHelpTransitionId;

  localStorage.setItem(INQ_PROMPT_HELP_STATE_KEY, open ? '1' : '0');
  updateInqPromptHelpButton(open);

  const finishClose = () => {
    if (transitionToken !== inqPromptHelpTransitionId) return;
    inqPromptHelpPanel.hidden = true;
    inqPromptHelpPanel.classList.remove('is-open');
    inqPromptHelpPanel.style.maxHeight = '';
    if (restoreFocus) btnInqPromptHelp.focus();
  };

  if (immediate) {
    inqPromptHelpPanel.hidden = !open;
    inqPromptHelpPanel.classList.toggle('is-open', open);
    inqPromptHelpPanel.style.maxHeight = open ? '' : '';
    if (open && ensureVisible) {
      window.requestAnimationFrame(ensureInqPromptHelpVisible);
    }
    if (!open && restoreFocus) btnInqPromptHelp.focus();
    return;
  }

  if (open) {
    inqPromptHelpPanel.hidden = false;
    inqPromptHelpPanel.classList.add('is-open');
    inqPromptHelpPanel.style.maxHeight = '0px';
    const onOpenEnd = (event) => {
      if (event.propertyName !== 'max-height') return;
      inqPromptHelpPanel.removeEventListener('transitionend', onOpenEnd);
      if (transitionToken !== inqPromptHelpTransitionId) return;
      inqPromptHelpPanel.style.maxHeight = '';
    };
    inqPromptHelpPanel.addEventListener('transitionend', onOpenEnd);
    window.requestAnimationFrame(() => {
      if (transitionToken !== inqPromptHelpTransitionId) return;
      inqPromptHelpPanel.style.maxHeight = `${inqPromptHelpPanel.scrollHeight}px`;
    });
    if (ensureVisible) {
      window.requestAnimationFrame(() => window.requestAnimationFrame(ensureInqPromptHelpVisible));
      window.setTimeout(ensureInqPromptHelpVisible, 180);
      window.setTimeout(nudgeCodexScrollForIllumText, 220);
    }
    return;
  }

  inqPromptHelpPanel.style.maxHeight = `${inqPromptHelpPanel.scrollHeight}px`;
  inqPromptHelpPanel.classList.remove('is-open');
  window.requestAnimationFrame(() => {
    if (transitionToken !== inqPromptHelpTransitionId) return;
    inqPromptHelpPanel.style.maxHeight = '0px';
  });
  const onTransitionEnd = (event) => {
    if (event.propertyName !== 'max-height') return;
    inqPromptHelpPanel.removeEventListener('transitionend', onTransitionEnd);
    finishClose();
  };
  inqPromptHelpPanel.addEventListener('transitionend', onTransitionEnd);
  window.setTimeout(() => {
    inqPromptHelpPanel.removeEventListener('transitionend', onTransitionEnd);
    finishClose();
  }, 320);
}

function updateStartButton() {
  btnStart.disabled = !getSource() || isProcessing;
}

function parseMarkdownHeader(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  let idx = 0;
  let title = '';
  let source = '';
  let consumed = false;

  if (/^#\s+/.test(lines[idx] || '')) {
    title = (lines[idx] || '').replace(/^#\s+/, '').trim();
    idx++;
    consumed = true;
    while ((lines[idx] || '').trim() === '') idx++;
  }

  while (idx < lines.length) {
    const line = (lines[idx] || '').trim();
    if (!line) {
      idx++;
      consumed = true;
      continue;
    }

    const sourceMatch = line.match(/^\*\*Source:\*\*\s*`?(.+?)`?\s*$/i);
    const isMeta = /^\*\*(Source|Duration|Words|Segments|Videos processed|Total duration|Total words):\*\*/i.test(line);
    if (sourceMatch) source = sourceMatch[1].trim();

    if (isMeta) {
      idx++;
      consumed = true;
      continue;
    }

    if (line === '---') {
      idx++;
      consumed = true;
      while ((lines[idx] || '').trim() === '') idx++;
      continue;
    }

    break;
  }

  return {
    title,
    source,
    body: consumed ? lines.slice(idx).join('\n').trim() : md.trim(),
  };
}

function humanizeSource(source) {
  if (!source) return { label: '', detail: '' };
  const trimmed = source.trim();

  if (/^https?:\/\//i.test(trimmed)) {
    try {
      const parsed = new URL(trimmed);
      const host = parsed.hostname.replace(/^www\./i, '');
      const isYouTube = /(^|\.)youtube\.com$/i.test(host) || /(^|\.)youtu\.be$/i.test(host);
      return {
        label: isYouTube ? 'YouTube video' : host,
        detail: trimmed,
      };
    } catch (_) {
      return { label: 'Video link', detail: trimmed };
    }
  }

  const parts = trimmed.split(/[\\/]/);
  return {
    label: parts[parts.length - 1] || trimmed,
    detail: trimmed,
  };
}

function renderResultMeta(title, source) {
  if (!resultMeta) return;
  if (!title && !source) {
    resultMeta.dataset.hasContent = '0';
    resultMeta.classList.add('hidden');
    resultMeta.innerHTML = '';
    return;
  }

  const sourceInfo = humanizeSource(source);
  const titleHtml = title ? `<div class="meta-title">${escapeHtml(title)}</div>` : '';
  const sourceHtml = sourceInfo.label
    ? `<div class="meta-source"><span class="meta-label">Source</span><span class="meta-value">${escapeHtml(sourceInfo.label)}</span></div>`
    : '';
  const detailHtml = sourceInfo.detail ? `<div class="meta-detail">${escapeHtml(sourceInfo.detail)}</div>` : '';

  resultMeta.innerHTML = `<div class="meta-card">${titleHtml}${sourceHtml}${detailHtml}</div>`;
  resultMeta.dataset.hasContent = '1';
  resultMeta.classList.remove('hidden');
}

function setStage(activeStage) {
  const stageEls = progressPanel.querySelectorAll('.stage');
  const lineEls = progressPanel.querySelectorAll('.stage-line');
  const idx = STAGES.indexOf(activeStage);

  stageEls.forEach((el, i) => {
    el.classList.remove('active', 'done');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });

  lineEls.forEach((el, i) => {
    el.classList.toggle('done', i < idx);
    el.classList.toggle('active-flight', idx > 0 && i === idx - 1);
  });
}

function flushProgressUpdate() {
  progressRafId = null;
  if (!pendingProgressMsg) return;
  const msg = pendingProgressMsg;
  pendingProgressMsg = null;
  const pctRaw = Number(msg.percent);
  const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.round(pctRaw))) : 0;
  progressBar.style.width = pct + '%';
  progressPct.textContent = pct + ' %';
  progressMsg.textContent = msg.message || '';
  headerStatus.textContent = `${msg.stage} ${pct}%`;
  setStage(msg.stage);
}

function queueProgressUpdate(msg) {
  pendingProgressMsg = msg;
  if (progressRafId !== null) return;
  progressRafId = window.requestAnimationFrame(flushProgressUpdate);
}

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.classList.remove('hidden');
}

function clearError() {
  errorEl.classList.add('hidden');
}

// === Source handling ===
urlInput.addEventListener('input', () => {
  if (urlInput.value.trim()) {
    selectedFile = null;
    fileLabel.textContent = 'Choose or drag a file';
    fileLabel.classList.remove('has-file');
  }
  updateStartButton();
});

function selectLocalFile(filePath) {
  if (!filePath) return;
  selectedFile = filePath;
  const name = filePath.split(/[\\/]/).pop();
  fileLabel.textContent = name;
  fileLabel.classList.add('has-file');
  urlInput.value = '';
  updateStartButton();
}

btnChooseFile.addEventListener('click', async (e) => {
  e.stopPropagation();
  const filePath = await window.api.selectFile();
  selectLocalFile(filePath);
});

// Clicking the dropzone area also triggers file selection
dropzone.addEventListener('click', async (e) => {
  if (e.target === btnChooseFile || e.target.closest('.dropzone-btn')) return;
  const filePath = await window.api.selectFile();
  selectLocalFile(filePath);
});

// Drag & drop
dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('drag-over');
});

dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('drag-over');
});

dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) selectLocalFile(file.path);
});

btnOutputDir.addEventListener('click', async () => {
  const dir = await window.api.selectOutputDir();
  if (dir) outputDir.value = dir;
});

// === Model selection & persistence ===
function fmtSize(mb) {
  if (!mb && mb !== 0) return '?';
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function loadModels() {
  window.api.listModels().then((data) => {
    // Whisper — array of {id, label, size_mb, recommended_vram_gb}
    const whisperList = Array.isArray(data.whisper) ? data.whisper : [];
    if (whisperList.length) {
      whisperModelSelect.innerHTML = '';
      whisperList.forEach((m) => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = `${m.label} · ${fmtSize(m.size_mb)}`;
        whisperModelSelect.appendChild(opt);
      });
      const saved = localStorage.getItem('lvt_whisper_model');
      const ids = whisperList.map((m) => m.id);
      whisperModelSelect.value = saved && ids.includes(saved) ? saved : (ids.includes('small') ? 'small' : ids[0]);
    }

    // LLM — {local: [{id:"local:...", label, path, size_mb}], presets: [{id, label, repo, file, size_mb}]}
    const llmData = data.llm || {};
    const local = Array.isArray(llmData.local) ? llmData.local : [];
    const presets = Array.isArray(llmData.presets) ? llmData.presets : [];
    if (local.length || presets.length) {
      llmModelSelect.innerHTML = '';

      if (local.length) {
        const og = document.createElement('optgroup');
        og.label = 'Local .gguf files';
        local.forEach((m) => {
          const opt = document.createElement('option');
          opt.value = m.id; // already "local:filename.gguf"
          opt.textContent = `${m.label} · ${fmtSize(m.size_mb)}`;
          og.appendChild(opt);
        });
        llmModelSelect.appendChild(og);
      }

      if (presets.length) {
        const og = document.createElement('optgroup');
        og.label = 'Hugging Face presets';
        presets.forEach((p) => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.label} · ${fmtSize(p.size_mb)}`;
          og.appendChild(opt);
        });
        llmModelSelect.appendChild(og);
      }

      const saved = localStorage.getItem('lvt_llm_preset');
      const allIds = [...local.map((m) => m.id), ...presets.map((p) => p.id)];
      if (saved && allIds.includes(saved)) {
        llmModelSelect.value = saved;
      } else if (allIds.includes('qwen2.5-7b')) {
        llmModelSelect.value = 'qwen2.5-7b';
      } else if (allIds.length) {
        llmModelSelect.value = allIds[0];
      }
    }
  }).catch((err) => {
    console.error('Failed to load models:', err);
  });
}

function loadGpuInfo() {
  window.api.getGpuInfo().then((info) => {
    if (info.cuda && info.vram_mb > 0) {
      const vramGb = (info.vram_mb / 1024).toFixed(1);
      gpuInfoBadge.textContent = `${info.device} · ${vramGb} GB`;
      gpuInfoBadge.classList.add('badge-ok');
      gpuInfoBadge.classList.remove('badge-warn');
    } else {
      gpuInfoBadge.textContent = 'CPU only';
      gpuInfoBadge.classList.add('badge-warn');
      gpuInfoBadge.classList.remove('badge-ok');
    }
  }).catch(() => {
    gpuInfoBadge.textContent = 'unknown';
    gpuInfoBadge.classList.add('badge-warn');
    gpuInfoBadge.classList.remove('badge-ok');
  });
}

function updateFastModeBadge() {
  if (language.value && language.value !== '') {
    fastModeBadge.style.display = 'inline-block';
  } else {
    fastModeBadge.style.display = 'none';
  }
}

whisperModelSelect.addEventListener('change', () => {
  localStorage.setItem('lvt_whisper_model', whisperModelSelect.value);
});

llmModelSelect.addEventListener('change', () => {
  localStorage.setItem('lvt_llm_preset', llmModelSelect.value);
});

summaryLanguageSelect.addEventListener('change', () => {
  localStorage.setItem('lvt_summary_language', summaryLanguageSelect.value);
});

language.addEventListener('change', () => {
  localStorage.setItem('lvt_video_language', language.value);
  updateFastModeBadge();
});

contextWindowSelect.addEventListener('change', () => {
  localStorage.setItem('lvt_context_window', contextWindowSelect.value);
});

if (llmGpuLayersSelect) {
  llmGpuLayersSelect.addEventListener('change', () => {
    localStorage.setItem('lvt_llm_gpu_layers', llmGpuLayersSelect.value);
  });
}

diarizeToggle.addEventListener('change', () => {
  localStorage.setItem('lvt_diarize', diarizeToggle.checked ? '1' : '0');
});

noSummaryToggle.addEventListener('change', () => {
  localStorage.setItem('lvt_no_summary', noSummaryToggle.checked ? '1' : '0');
  // Disable Oracle / memory / chronicle-related selects when no_summary is active
  const disabled = noSummaryToggle.checked;
  llmModelSelect.disabled = disabled;
  contextWindowSelect.disabled = disabled;
  if (llmGpuLayersSelect) llmGpuLayersSelect.disabled = disabled;
  if (disableThinkingToggle) disableThinkingToggle.disabled = disabled;
  // Exit inquisition/bard mode if no_summary is enabled
  if (disabled && (currentMode === 'inquisition' || currentMode === 'bard')) {
    setMode('chronicle');
  }
});

if (disableThinkingToggle) {
  disableThinkingToggle.addEventListener('change', () => {
    localStorage.setItem('lvt_disable_thinking', disableThinkingToggle.checked ? '1' : '0');
  });
}

// === Mode switching ===
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.mode;
    setMode(mode);
  });
});

if (btnInqPromptHelp) {
  btnInqPromptHelp.addEventListener('click', () => {
    if (!inqPromptHelpPanel) return;
    const isOpen = !inqPromptHelpPanel.hidden;
    setInqPromptHelpOpen(!isOpen, { ensureVisible: true });
  });
}

if (btnInqPromptHelpClose) {
  btnInqPromptHelpClose.addEventListener('click', () => {
    setInqPromptHelpOpen(false, { restoreFocus: true });
  });
}

if (inqPromptHelpPanel) {
  inqPromptHelpPanel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      setInqPromptHelpOpen(false, { restoreFocus: true });
    }
  });
}

// === Collapsible panels ===
document.querySelectorAll('.panel-toggle').forEach(toggle => {
  toggle.addEventListener('click', () => {
    const panelId = toggle.dataset.panel;
    const panel = document.getElementById(panelId);
    if (!panel) return;
    panel.classList.toggle('collapsed');
    const states = JSON.parse(localStorage.getItem('lvt_collapsed') || '{}');
    states[panelId] = panel.classList.contains('collapsed');
    localStorage.setItem('lvt_collapsed', JSON.stringify(states));
  });
});

function restoreCollapsedState() {
  const states = JSON.parse(localStorage.getItem('lvt_collapsed') || '{}');
  document.querySelectorAll('.panel.collapsible').forEach(panel => {
    if (states[panel.id] === false) {
      panel.classList.remove('collapsed');
    }
  });
}
restoreCollapsedState();

// Persist Inquisition textareas
inqGoal.addEventListener('change', () => {
  localStorage.setItem('lvt_inq_goal', inqGoal.value);
});
inqRequired.addEventListener('change', () => {
  localStorage.setItem('lvt_inq_required', inqRequired.value);
});
inqRedflags.addEventListener('change', () => {
  localStorage.setItem('lvt_inq_redflags', inqRedflags.value);
});
inqCriteria.addEventListener('change', () => {
  localStorage.setItem('lvt_inq_criteria', inqCriteria.value);
});

// Persist Bard settings
if (bardFocus) bardFocus.addEventListener('change', () => {
  localStorage.setItem('lvt_bard_focus', bardFocus.value);
});
if (bardCount) bardCount.addEventListener('change', () => {
  localStorage.setItem('lvt_bard_count', bardCount.value);
});
if (bardTimestamps) bardTimestamps.addEventListener('change', () => {
  localStorage.setItem('lvt_bard_timestamps', bardTimestamps.checked ? '1' : '0');
});
if (bardQuotes) bardQuotes.addEventListener('change', () => {
  localStorage.setItem('lvt_bard_quotes', bardQuotes.checked ? '1' : '0');
});

// === Processing ===
async function estimateETA() {
  if (!selectedFile) {
    etaBox.hidden = true;
    return;
  }

  try {
    // Get file duration from backend (if available) or skip for now
    const config = {
      duration_sec: 0,  // Will be filled by backend if needed
      config: {
        whisper_model: whisperModelSelect.value,
        llm_model_preset: llmModelSelect.value,
        summary_language: summaryLanguageSelect.value,
        language: language.value || 'auto',
        llm_gpu_layers: readLlmGpuLayers(),
      },
    };

    const eta = await window.api.estimateEta(config);
    // rpcCall in main.js unwraps msg.eta, so we receive the inner dict directly
    if (eta && typeof eta.total_sec === 'number') {
      etaPre.textContent = eta.formatted_total || fmtEta(eta.total_sec);
      etaBox.hidden = false;
    }
  } catch (err) {
    console.error('Failed to estimate ETA:', err);
  }
}

btnStart.addEventListener('click', () => {
  const source = getSource();
  if (!source) return;

  isProcessing = true;
  btnStart.disabled = true;
  btnCancel.disabled = false;
  setProcessingIllum(true);

  // Reset UI
  progressPanel.classList.remove('hidden');
  etaBox.hidden = false;
  setHourglassRunning(true);
  startElapsedTimer();
  etaPre.textContent = '—';  // Placeholder until backend sends first ETA
  etaRuntime.textContent = '';
  clearError();
  resultContent.classList.add('hidden');
  resultPlaceholder.classList.remove('hidden');
  if (resultMeta) {
    resultMeta.dataset.hasContent = '0';
    resultMeta.classList.add('hidden');
    resultMeta.innerHTML = '';
  }
  resultActions.classList.remove('visible');
  progressBar.style.width = '0%';
  progressPct.textContent = '0 %';
  progressMsg.textContent = 'Starting...';
  headerStatus.textContent = 'processing...';
  setStage('');
  pendingProgressMsg = null;
  if (progressRafId !== null) {
    window.cancelAnimationFrame(progressRafId);
    progressRafId = null;
  }

  const params = {
    source,
    output_dir: outputDir.value,
    diarize: false,
    no_summary: noSummaryToggle.checked,
    config: {
      whisper_model: whisperModelSelect.value,
      compute_type: computeType.value,
      language: language.value || null,
      llm_model_preset: llmModelSelect.value,
      summary_language: summaryLanguageSelect.value,
      llm_context_length: parseInt(contextWindowSelect.value, 10) || 0,
      llm_gpu_layers: readLlmGpuLayers(),
      disable_thinking: disableThinkingToggle ? disableThinkingToggle.checked : true,
    },
  };

  // Add Inquisition mode config if active
  if (currentMode === 'inquisition') {
    params.config.summary_mode = 'inquisition';
    params.config.summary_mode_config = {
      EVALUATION_GOAL: inqGoal.value.trim(),
      REQUIRED_ITEMS: inqRequired.value
        .split('\n')
        .map(s => s.trim())
        .filter(s => s.length > 0),
      RED_FLAGS: inqRedflags.value
        .split('\n')
        .map(s => s.trim())
        .filter(s => s.length > 0),
      SCORING_CRITERIA: inqCriteria.value
        .split('\n')
        .map(s => s.trim())
        .filter(s => s.length > 0),
    };
  }

  // Add Bard mode config if active
  if (currentMode === 'bard') {
    params.config.summary_mode = 'bard';
    params.config.summary_mode_config = {
      HIGHLIGHT_FOCUS: bardFocus ? bardFocus.value.trim() : '',
      HIGHLIGHT_COUNT: bardCount ? bardCount.value : '10',
      INCLUDE_TIMESTAMPS: bardTimestamps ? (bardTimestamps.checked ? 'yes' : 'no') : 'yes',
      INCLUDE_QUOTES: bardQuotes ? (bardQuotes.checked ? 'yes' : 'no') : 'yes',
    };
  }

  window.api.startProcess(params);
});

btnCancel.addEventListener('click', () => {
  window.api.cancelProcess();
  btnCancel.disabled = true;
  setProcessingIllum(false);
  progressMsg.textContent = 'Cancelling...';
  headerStatus.textContent = 'cancelling...';
  progressPanel.querySelectorAll('.stage-line').forEach(el => {
    el.classList.remove('active-flight');
  });
  stopEtaCountdown();
  stopElapsedTimer();
  setHourglassRunning(false);
});

// === Backend messages ===
window.api.onMessage((msg) => {
  if (msg.type === 'progress') {
    queueProgressUpdate(msg);
  }

  if (msg.type === 'eta') {
    if (msg.stage === 'pre') {
      etaBox.hidden = false;
      startEtaCountdown(msg.total_sec || 0);
      // etaRuntime already shows elapsed and is managed independently
    } else if (msg.stage === 'runtime') {
      const remaining = Math.max(0, Math.round(Number(msg.remaining_sec) || 0));
      if (!etaCountdownId) {
        // First runtime ETA — boot the countdown from backend's estimate.
        startEtaCountdown(remaining);
      } else if (remaining > 0 && remaining < etaRemainingSec - 1) {
        // Backend has a smaller (more accurate) estimate than our local
        // tick — converge DOWN smoothly without tearing the interval
        // down and back up. Upward corrections are ignored on purpose:
        // letting the countdown jump up on every backend emit produced
        // the "hourglass keeps resetting" feel reported 2026-04-18.
        etaRemainingSec = remaining;
        etaPre.textContent = fmtEta(etaRemainingSec);
      }
    }
  }

  if (msg.type === 'result') {
    // Only pipeline completion carries `markdown`; RPC replies reuse type "result" without it.
    if (typeof msg.markdown !== 'string') {
      return;
    }

    isProcessing = false;
    setProcessingIllum(false);
    btnCancel.disabled = true;
    updateStartButton();

    stopEtaCountdown();
    stopElapsedTimer();
    setHourglassRunning(false);
    // Show completion time in etaPre (ETA display)
    etaPre.textContent = fmtEta(elapsedSec);
    etaRuntime.textContent = `completed in ${fmtEta(elapsedSec)}`;

    if (progressRafId !== null) {
      window.cancelAnimationFrame(progressRafId);
      progressRafId = null;
    }
    pendingProgressMsg = null;
    progressBar.style.width = '100%';
    progressPct.textContent = '100 %';
    progressMsg.textContent = 'Done';
    headerStatus.textContent = 'done';

    // Mark all stages done
    progressPanel.querySelectorAll('.stage').forEach(el => {
      el.classList.remove('active');
      el.classList.add('done');
    });
    progressPanel.querySelectorAll('.stage-line').forEach(el => {
      el.classList.add('done');
      el.classList.remove('active-flight');
    });

    rawMarkdown = msg.markdown || '';
    const parsed = parseMarkdownHeader(rawMarkdown);
    displayMarkdown = parsed.body || rawMarkdown;
    outputPath = msg.output_path || null;

    resultPlaceholder.classList.add('hidden');
    renderResultMeta(parsed.title, parsed.source);
    resultContent.classList.remove('hidden');
    resultContent.innerHTML = renderMarkdown(displayMarkdown);
    resultActions.classList.add('visible');
  }

  if (msg.type === 'error') {
    isProcessing = false;
    setProcessingIllum(false);
    btnCancel.disabled = true;
    updateStartButton();
    headerStatus.textContent = '';

    stopEtaCountdown();
    stopElapsedTimer();
    setHourglassRunning(false);
    pendingProgressMsg = null;
    if (progressRafId !== null) {
      window.cancelAnimationFrame(progressRafId);
      progressRafId = null;
    }
    progressPanel.querySelectorAll('.stage-line').forEach(el => {
      el.classList.remove('active-flight');
    });

    showError(msg.message || 'Unknown error');
  }
});

// === Result actions ===
btnCopy.addEventListener('click', () => {
  navigator.clipboard.writeText(displayMarkdown || rawMarkdown).then(() => {
    const origHTML = btnCopy.innerHTML;
    btnCopy.textContent = 'Copied!';
    setTimeout(() => { btnCopy.innerHTML = origHTML; }, 1500);
  });
});

btnOpenFile.addEventListener('click', () => {
  if (outputPath) window.api.openFile(outputPath);
});

btnOpenFolder.addEventListener('click', () => {
  if (outputPath) window.api.openFolder(outputPath);
});

// === Markdown renderer ===
function renderMarkdown(md) {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n');
  const htmlBlocks = [];
  let i = 0;

  const isHr = (line) => /^---+$/.test(line);
  const isHeading = (line) => /^#{1,3}\s+/.test(line);
  const isUnordered = (line) => /^[-*]\s+/.test(line);
  const isOrdered = (line) => /^\d+[.)]\s+/.test(line);
  const isBlockquote = (line) => /^>\s?/.test(line);
  const isBoldOnlyHeading = (line) => {
    const match = line.match(/^\*\*([^*\n]+)\*\*\s*$/);
    if (!match) return false;
    const text = match[1].trim();
    // Keep as heading only for short, label-like lines to avoid random paragraph promotion.
    // Comma-separated phrases (e.g. Russian topic-like lines) are not chapter titles — promoting
    // them to <h2> looked like a second «итог» after the real ## Итог block.
    if (text.includes(',')) return false;
    return text.length > 0 && text.length <= 80 && !/[.!?]$/.test(text);
  };

  while (i < lines.length) {
    const rawLine = lines[i];
    const line = rawLine.trim();

    if (!line) {
      i++;
      continue;
    }

    if (isHr(line)) {
      htmlBlocks.push('<hr>');
      i++;
      continue;
    }

    if (isHeading(line)) {
      const level = Math.min(3, Math.max(1, (line.match(/^#+/) || ['#'])[0].length));
      const text = line.replace(/^#{1,3}\s+/, '').trim();
      htmlBlocks.push(`<h${level}>${renderInlineMarkdown(text)}</h${level}>`);
      i++;
      continue;
    }

    if (isBoldOnlyHeading(line)) {
      const text = line.replace(/^\*\*([^*\n]+)\*\*\s*$/, '$1').trim();
      htmlBlocks.push(`<h2>${renderInlineMarkdown(text)}</h2>`);
      i++;
      continue;
    }

    if (isUnordered(line)) {
      const items = [];
      while (i < lines.length) {
        const liLine = (lines[i] || '').trim();
        if (!isUnordered(liLine)) break;
        items.push(liLine.replace(/^[-*]\s+/, '').trim());
        i++;
      }
      htmlBlocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ul>`);
      continue;
    }

    if (isOrdered(line)) {
      const items = [];
      while (i < lines.length) {
        const liLine = (lines[i] || '').trim();
        if (!isOrdered(liLine)) break;
        items.push(liLine.replace(/^\d+[.)]\s+/, '').trim());
        i++;
      }
      htmlBlocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ol>`);
      continue;
    }

    if (isBlockquote(line)) {
      const quoteLines = [];
      while (i < lines.length) {
        const quoteLine = (lines[i] || '').trim();
        if (!isBlockquote(quoteLine)) break;
        quoteLines.push(quoteLine.replace(/^>\s?/, '').trim());
        i++;
      }
      htmlBlocks.push(`<blockquote>${renderInlineMarkdown(quoteLines.join(' '))}</blockquote>`);
      continue;
    }

    const paragraphLines = [];
    while (i < lines.length) {
      const paraLine = (lines[i] || '').trim();
      if (!paraLine) break;
      if (isHr(paraLine) || isHeading(paraLine) || isBoldOnlyHeading(paraLine) || isUnordered(paraLine) || isOrdered(paraLine) || isBlockquote(paraLine)) {
        break;
      }
      paragraphLines.push(paraLine);
      i++;
    }

    if (paragraphLines.length > 0) {
      htmlBlocks.push(`<p>${renderInlineMarkdown(paragraphLines.join(' '))}</p>`);
      continue;
    }

    i++;
  }

  return htmlBlocks.join('\n');
}

function escapeHtml(text) {
  const el = document.createElement('div');
  el.textContent = text;
  return el.innerHTML;
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text || '');
  const codeTokens = [];

  html = html.replace(/`([^`]+)`/g, (_, code) => {
    const token = `__CODE_TOKEN_${codeTokens.length}__`;
    codeTokens.push(`<code>${code}</code>`);
    return token;
  });

  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  codeTokens.forEach((codeHtml, idx) => {
    html = html.replace(`__CODE_TOKEN_${idx}__`, codeHtml);
  });

  return html;
}

// === Init ===
function _initApp() {
  loadModels();

  const savedWhisper = localStorage.getItem('lvt_whisper_model');
  if (savedWhisper && whisperModelSelect.value) {
    whisperModelSelect.value = savedWhisper;
  }

  const savedLlm = localStorage.getItem('lvt_llm_preset');
  if (savedLlm && llmModelSelect.value) {
    llmModelSelect.value = savedLlm;
  }

  const savedSummaryLang = localStorage.getItem('lvt_summary_language');
  if (savedSummaryLang) {
    summaryLanguageSelect.value = savedSummaryLang;
  }

  const savedLang = localStorage.getItem('lvt_video_language');
  if (savedLang) {
    language.value = savedLang;
  }

  const savedMode = localStorage.getItem('lvt_mode') || 'chronicle';
  setMode(savedMode);

  const savedInqGoal = localStorage.getItem('lvt_inq_goal');
  if (savedInqGoal) {
    inqGoal.value = savedInqGoal;
  }

  const savedInqRequired = localStorage.getItem('lvt_inq_required');
  if (savedInqRequired) {
    inqRequired.value = savedInqRequired;
  }

  const savedInqRedflags = localStorage.getItem('lvt_inq_redflags');
  if (savedInqRedflags) {
    inqRedflags.value = savedInqRedflags;
  }

  const savedInqCriteria = localStorage.getItem('lvt_inq_criteria');
  if (savedInqCriteria) {
    inqCriteria.value = savedInqCriteria;
  }

  // Restore Bard settings
  const savedBardFocus = localStorage.getItem('lvt_bard_focus');
  if (savedBardFocus && bardFocus) bardFocus.value = savedBardFocus;
  const savedBardCount = localStorage.getItem('lvt_bard_count');
  if (savedBardCount && bardCount) bardCount.value = savedBardCount;
  const savedBardTimestamps = localStorage.getItem('lvt_bard_timestamps');
  if (savedBardTimestamps === '0' && bardTimestamps) bardTimestamps.checked = false;
  const savedBardQuotes = localStorage.getItem('lvt_bard_quotes');
  if (savedBardQuotes === '0' && bardQuotes) bardQuotes.checked = false;

  const savedCtx = localStorage.getItem('lvt_context_window');
  if (savedCtx) {
    contextWindowSelect.value = savedCtx;
  }

  const savedDiarize = localStorage.getItem('lvt_diarize');
  if (savedDiarize === '1') {
    diarizeToggle.checked = true;
  }

  const savedNoSummary = localStorage.getItem('lvt_no_summary');
  if (savedNoSummary === '1') {
    noSummaryToggle.checked = true;
    llmModelSelect.disabled = true;
    contextWindowSelect.disabled = true;
    if (llmGpuLayersSelect) llmGpuLayersSelect.disabled = true;
    if (disableThinkingToggle) disableThinkingToggle.disabled = true;
  }

  const savedGpuLayers = localStorage.getItem('lvt_llm_gpu_layers');
  if (savedGpuLayers && llmGpuLayersSelect) {
    const opt = llmGpuLayersSelect.querySelector(`option[value="${savedGpuLayers}"]`);
    if (opt) llmGpuLayersSelect.value = savedGpuLayers;
  }

  const savedDisableThinking = localStorage.getItem('lvt_disable_thinking');
  if (disableThinkingToggle) {
    disableThinkingToggle.checked = savedDisableThinking !== '0';
  }

  loadGpuInfo();
  updateFastModeBadge();
  updateStartButton();

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      nudgeCodexScrollForIllumText();
    });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initApp);
} else {
  _initApp();
}

// ============================================================
// Oracle — post-transcription chat
// CTA appears after a chronicle is ready; modal hosts the chat.
// Streaming tokens arrive as python messages of type "oracle_token".
// ============================================================

const oracleCta       = $('oracle-cta');
const oracleModal     = $('oracle-modal');
const oracleCloseBtn  = $('oracle-close-btn');
const oracleMessages  = $('oracle-messages');
const oracleForm      = $('oracle-form');
const oracleInput     = $('oracle-input');
const oracleSendBtn   = $('oracle-send');
const oracleStopBtn   = $('oracle-stop');

const oracleState = {
  // Active request id (a monotonic token the backend echoes back on
  // oracle_token / result / error events). null = no in-flight request.
  currentId: null,
  // Streaming DOM node for the assistant reply currently being written.
  activeAssistantEl: null,
  activeAssistantTextEl: null,
  streamingText: '',
  history: [],           // [{role:"user"|"assistant", content}]
  sessionKey: '',        // fingerprint of (transcript + summary) for this chronicle
  transcript: '',
  summary: '',
};

function oracleSplitMarkdown(md) {
  // The markdown produced by backend/formatter.py has this shape:
  //   # Title ... --- \n ## Summary \n {summary} \n --- \n ## Full Transcript \n [timestamps] ...
  // We recover summary + transcript so we can hand them to the Oracle separately.
  const text = md || '';
  let summary = '';
  let transcript = '';

  const summaryMatch = text.match(/^##\s+(?:Summary|Chronicle|Сводка|Ход допроса|Итог)\s*$/im);
  const transcriptMatch = text.match(/^##\s+(?:Full\s+Transcript|Transcript|Стенограмма|Полная\s+стенограмма)\s*$/im);

  if (summaryMatch && transcriptMatch && summaryMatch.index < transcriptMatch.index) {
    const summaryStart = summaryMatch.index + summaryMatch[0].length;
    summary = text.slice(summaryStart, transcriptMatch.index).trim();
    summary = summary.replace(/^-{3,}\s*$/gm, '').replace(/\n{3,}/g, '\n\n').trim();
    const transcriptStart = transcriptMatch.index + transcriptMatch[0].length;
    transcript = text.slice(transcriptStart).trim();
  } else if (summaryMatch) {
    const summaryStart = summaryMatch.index + summaryMatch[0].length;
    summary = text.slice(summaryStart).trim();
  } else {
    // Fallback: whole document as "transcript".
    transcript = text.trim();
  }

  return { summary, transcript };
}

function oracleHashKey(str) {
  // Cheap non-cryptographic fingerprint — the backend has its own hash for
  // session identity; this one only needs to distinguish "same chronicle"
  // from "new chronicle" on the client.
  let h = 2166136261 >>> 0;
  const s = str || '';
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h.toString(16);
}

function oracleShowCta() {
  if (!oracleCta) return;
  oracleCta.classList.remove('hidden');
}
function oracleHideCta() {
  if (!oracleCta) return;
  oracleCta.classList.add('hidden');
}

function oracleEnsureSessionFromMarkdown(md) {
  const { summary, transcript } = oracleSplitMarkdown(md);
  const newKey = oracleHashKey(transcript + '\n---\n' + summary);
  if (newKey !== oracleState.sessionKey) {
    // New chronicle — drop previous chat context.
    oracleState.sessionKey = newKey;
    oracleState.history = [];
    oracleState.transcript = transcript;
    oracleState.summary = summary;
    oracleRenderMessages();
    // Also ask the backend to release any prior model state.
    try { window.api.oracleClose(); } catch (_) {}
  }
}

function oracleRenderMessages() {
  if (!oracleMessages) return;
  oracleMessages.innerHTML = '';
  if (oracleState.history.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'oracle-empty';
    empty.textContent = 'The Oracle knows only this video. Ask about what was said, who said it, when, or what it meant.';
    oracleMessages.appendChild(empty);
    return;
  }
  for (const turn of oracleState.history) {
    oracleMessages.appendChild(oracleRenderMessage(turn.role, turn.content));
  }
  oracleMessages.scrollTop = oracleMessages.scrollHeight;
}

function oracleRenderMessage(role, text, { streaming = false, error = false } = {}) {
  const wrap = document.createElement('div');
  wrap.className = `oracle-msg oracle-msg-${role}` + (error ? ' oracle-msg-error' : '');
  const label = document.createElement('div');
  label.className = 'oracle-msg-role';
  label.textContent = role === 'user' ? 'You' : 'Oracle';
  const body = document.createElement('div');
  body.className = 'oracle-msg-body' + (streaming ? ' oracle-msg-streaming' : '');
  body.textContent = text;
  wrap.appendChild(label);
  wrap.appendChild(body);
  return wrap;
}

function oracleOpenModal() {
  if (!oracleModal) return;
  // Make sure we have a current session before opening.
  if (rawMarkdown) {
    oracleEnsureSessionFromMarkdown(rawMarkdown);
  }
  oracleModal.classList.remove('hidden');
  oracleRenderMessages();
  requestAnimationFrame(() => { oracleInput?.focus(); });
  document.addEventListener('keydown', oracleOnKeyDown);
}

function oracleCloseModal() {
  if (!oracleModal) return;
  oracleModal.classList.add('hidden');
  oracleSetSpeaking(false);
  document.removeEventListener('keydown', oracleOnKeyDown);
  // If a stream is in-flight, ask the backend to stop.
  if (oracleState.currentId !== null) {
    try { window.api.oracleCancel(); } catch (_) {}
  }
}

function oracleOnKeyDown(ev) {
  if (ev.key === 'Escape') {
    oracleCloseModal();
  }
}

function oracleSetSpeaking(on) {
  if (!oracleModal) return;
  oracleModal.classList.toggle('oracle-speaking', !!on);
  if (oracleSendBtn) oracleSendBtn.disabled = !!on;
  if (oracleStopBtn) oracleStopBtn.classList.toggle('hidden', !on);
}

function oracleBeginAssistantStream() {
  // Drop the "empty" placeholder if present.
  const empty = oracleMessages.querySelector('.oracle-empty');
  if (empty) empty.remove();

  const wrap = document.createElement('div');
  wrap.className = 'oracle-msg oracle-msg-assistant';
  const label = document.createElement('div');
  label.className = 'oracle-msg-role';
  label.textContent = 'Oracle';
  const body = document.createElement('div');
  body.className = 'oracle-msg-body oracle-msg-streaming';
  body.textContent = '';
  wrap.appendChild(label);
  wrap.appendChild(body);
  oracleMessages.appendChild(wrap);
  oracleMessages.scrollTop = oracleMessages.scrollHeight;

  oracleState.activeAssistantEl = wrap;
  oracleState.activeAssistantTextEl = body;
  oracleState.streamingText = '';
}

function oracleAppendDelta(delta) {
  if (!oracleState.activeAssistantTextEl) return;
  oracleState.streamingText += delta;
  oracleState.activeAssistantTextEl.textContent = oracleState.streamingText;
  // Auto-scroll if the user is already near the bottom.
  const nearBottom =
    oracleMessages.scrollHeight - oracleMessages.scrollTop - oracleMessages.clientHeight < 120;
  if (nearBottom) oracleMessages.scrollTop = oracleMessages.scrollHeight;
}

function oracleFinishAssistantStream(finalText, { cancelled = false, error = null } = {}) {
  if (oracleState.activeAssistantTextEl) {
    oracleState.activeAssistantTextEl.classList.remove('oracle-msg-streaming');
    const text = (finalText != null && finalText.length > 0)
      ? finalText
      : oracleState.streamingText;
    oracleState.activeAssistantTextEl.textContent = text + (cancelled ? ' …(stopped)' : '');
    if (error) {
      oracleState.activeAssistantEl?.classList.add('oracle-msg-error');
      oracleState.activeAssistantTextEl.textContent = `(oracle error) ${error}`;
    } else if (text) {
      oracleState.history.push({ role: 'assistant', content: text });
    }
  }
  oracleState.activeAssistantEl = null;
  oracleState.activeAssistantTextEl = null;
  oracleState.streamingText = '';
  oracleState.currentId = null;
  oracleSetSpeaking(false);
}

function oracleBuildConfig() {
  // Mirror the settings used for the pipeline so the Oracle uses the same
  // model and context the user actually picked.
  return {
    llm_model_preset: llmModelSelect ? llmModelSelect.value : null,
    llm_context_length: contextWindowSelect ? (parseInt(contextWindowSelect.value, 10) || 0) : 0,
    llm_gpu_layers: readLlmGpuLayers(),
    disable_thinking: disableThinkingToggle ? disableThinkingToggle.checked : true,
  };
}

function oracleSend() {
  if (!oracleInput) return;
  const text = oracleInput.value.trim();
  if (!text) return;
  if (oracleState.currentId !== null) return;  // already streaming
  if (!rawMarkdown) {
    showError('No chronicle to discuss yet. Finish a transcription first.');
    return;
  }
  oracleEnsureSessionFromMarkdown(rawMarkdown);

  // Add user turn.
  oracleState.history.push({ role: 'user', content: text });
  // Remove any "empty" placeholder once we have a real conversation.
  const empty = oracleMessages.querySelector('.oracle-empty');
  if (empty) empty.remove();
  oracleMessages.appendChild(oracleRenderMessage('user', text));
  oracleMessages.scrollTop = oracleMessages.scrollHeight;

  oracleInput.value = '';
  oracleInput.style.height = '';

  const id = Date.now() * 10 + Math.floor(Math.random() * 10);
  oracleState.currentId = id;
  oracleBeginAssistantStream();
  oracleSetSpeaking(true);

  window.api.oracleChat({
    id,
    question: text,
    // Send the history EXCLUDING the current question — Python re-appends it.
    history: oracleState.history.slice(0, -1),
    transcript: oracleState.transcript,
    summary: oracleState.summary,
    config: oracleBuildConfig(),
  });
}

// --- Event wiring --------------------------------------------------------

if (oracleCta) {
  oracleCta.addEventListener('click', oracleOpenModal);
  oracleCta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      oracleOpenModal();
    }
  });
}

if (oracleModal) {
  oracleModal.addEventListener('click', (ev) => {
    const t = ev.target;
    if (t && t instanceof HTMLElement && t.dataset.oracleClose === '1') {
      oracleCloseModal();
    }
  });
}

if (oracleCloseBtn) {
  oracleCloseBtn.addEventListener('click', oracleCloseModal);
}

if (oracleForm) {
  oracleForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    oracleSend();
  });
}

if (oracleInput) {
  oracleInput.addEventListener('keydown', (ev) => {
    // Enter = send, Shift+Enter = newline.
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      oracleSend();
    }
  });
  // Basic auto-grow
  oracleInput.addEventListener('input', () => {
    oracleInput.style.height = 'auto';
    oracleInput.style.height = Math.min(200, oracleInput.scrollHeight) + 'px';
  });
}

if (oracleStopBtn) {
  oracleStopBtn.addEventListener('click', () => {
    if (oracleState.currentId !== null) {
      try { window.api.oracleCancel(); } catch (_) {}
    }
  });
}

// --- Backend event listener for Oracle -----------------------------------
// We attach a second onMessage listener — the preload exposes the raw
// 'python-message' channel, so registering twice is safe: both handlers fire.
window.api.onMessage((msg) => {
  if (!msg) return;
  // Streaming token
  if (msg.type === 'oracle_token' && typeof msg.delta === 'string') {
    if (oracleState.currentId !== null && (msg.id == null || msg.id === oracleState.currentId)) {
      oracleAppendDelta(msg.delta);
    }
    return;
  }
  // Completion
  if (msg.type === 'result' && msg.oracle === true) {
    if (oracleState.currentId !== null && (msg.id == null || msg.id === oracleState.currentId)) {
      oracleFinishAssistantStream(msg.text || '', { cancelled: !!msg.cancelled });
    }
    return;
  }
  // Error (only handle oracle-scoped errors here; the main pipeline listener
  // handles everything else already).
  if (msg.type === 'error' && msg.oracle === true) {
    if (oracleState.currentId !== null && (msg.id == null || msg.id === oracleState.currentId)) {
      oracleFinishAssistantStream('', { error: msg.message || 'Oracle failed' });
    }
    return;
  }
});

// --- Surface the CTA when a chronicle appears, hide while processing -----
// We patch into the existing pipeline lifecycle by observing the result
// DOM rather than forking the main onMessage callback (which already owns
// the happy path). A MutationObserver keeps this local to the Oracle feature.
(function oracleWatchChronicleAppearance() {
  if (!resultContent) return;
  const observer = new MutationObserver(() => {
    const visible = !resultContent.classList.contains('hidden');
    const hasMarkdown = typeof rawMarkdown === 'string' && rawMarkdown.length > 0;
    // Only show the CTA when we actually have a summary (no_summary users
    // still get a usable transcript, but the Oracle is far less useful
    // without a chronicle — still allow it in case they want pure Q&A).
    if (visible && hasMarkdown) {
      oracleEnsureSessionFromMarkdown(rawMarkdown);
      oracleShowCta();
    } else {
      oracleHideCta();
    }
  });
  observer.observe(resultContent, { attributes: true, attributeFilter: ['class'] });
  // Also watch the placeholder to hide the CTA when a new run starts.
  if (resultPlaceholder) {
    const ph = new MutationObserver(() => {
      if (!resultPlaceholder.classList.contains('hidden')) oracleHideCta();
    });
    ph.observe(resultPlaceholder, { attributes: true, attributeFilter: ['class'] });
  }
})();
