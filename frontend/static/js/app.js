/**
 * AI Batch Studio — Main Application Controller
 *
 * Single-page application managing the complete production workflow:
 *   Upload → Review → Settings → Generate → Dashboard
 */

// ============================================================
// API Client
// ============================================================
const API = {
    async request(url, options = {}) {
        const defaults = {
            headers: { 'Content-Type': 'application/json' },
        };
        if (options.body && !(options.body instanceof FormData)) {
            options.body = JSON.stringify(options.body);
        } else if (options.body instanceof FormData) {
            delete defaults.headers['Content-Type'];
        }
        const res = await fetch(url, { ...defaults, ...options });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || err.detail || `HTTP ${res.status}`);
        }
        return res.json();
    },
    get: (url) => API.request(url),
    post: (url, body) => API.request(url, { method: 'POST', body }),
    put: (url, body) => API.request(url, { method: 'PUT', body }),
    del: (url) => API.request(url, { method: 'DELETE' }),
    upload: (url, formData) => API.request(url, { method: 'POST', body: formData }),
};

// ============================================================
// Toast Notifications
// ============================================================
const Toast = {
    container: null,
    init() {
        this.container = document.getElementById('toast-container');
    },
    show(message, type = 'info', duration = 4000) {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <span class="toast-message">${message}</span>
            <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
        `;
        this.container.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
    },
    success: (msg) => Toast.show(msg, 'success'),
    error: (msg) => Toast.show(msg, 'error', 6000),
    warning: (msg) => Toast.show(msg, 'warning', 5000),
    info: (msg) => Toast.show(msg, 'info'),
};

// ============================================================
// Router
// ============================================================
const Router = {
    currentView: 'upload',
    views: ['upload', 'review', 'settings', 'generate', 'dashboard', 'api-settings'],

    init() {
        document.querySelectorAll('.nav-item[data-view]').forEach(item => {
            item.addEventListener('click', () => {
                this.navigate(item.dataset.view);
            });
        });
        // Initial route
        const hash = location.hash.slice(1) || 'upload';
        this.navigate(hash);
    },

    navigate(view) {
        if (!this.views.includes(view)) view = 'upload';
        this.currentView = view;
        location.hash = view;

        // Update nav
        document.querySelectorAll('.nav-item[data-view]').forEach(item => {
            item.classList.toggle('active', item.dataset.view === view);
        });

        // Update content
        document.querySelectorAll('.view-panel').forEach(panel => {
            panel.classList.toggle('hidden', panel.id !== `view-${view}`);
        });

        // Update header
        const titles = {
            'upload': 'Upload CSV',
            'review': 'Scene Review',
            'settings': 'Generation Settings',
            'generate': 'Batch Generation',
            'dashboard': 'Production Dashboard',
            'api-settings': 'API Configuration',
        };
        document.getElementById('content-title').textContent = titles[view] || '';

        // View-specific init
        if (view === 'dashboard') Dashboard.refresh();
        if (view === 'generate') Generation.refresh();
        if (view === 'api-settings') ApiSettings.refresh();
        if (view === 'settings') Settings.refresh();
    },
};

// ============================================================
// CSV Upload
// ============================================================
const Upload = {
    csvData: null,

    init() {
        const zone = document.getElementById('upload-zone');
        const input = document.getElementById('csv-file-input');

        zone.addEventListener('click', () => input.click());
        zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            if (e.dataTransfer.files.length) this.handleFile(e.dataTransfer.files[0]);
        });

        input.addEventListener('change', () => {
            if (input.files.length) this.handleFile(input.files[0]);
        });

        document.getElementById('btn-load-sample').addEventListener('click', () => this.loadSample());
    },

    async handleFile(file) {
        if (!file.name.toLowerCase().endsWith('.csv')) {
            Toast.error('Please upload a CSV file');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        try {
            document.getElementById('upload-status').classList.remove('hidden');
            document.getElementById('upload-status').innerHTML = '<p class="text-muted">Validating CSV...</p>';

            const result = await API.upload('/api/upload/csv', formData);
            this.csvData = result;
            this.showValidation(result);

            if (result.rows.length > 0) {
                App.csvRows = result.rows;
                Toast.success(`CSV loaded: ${result.total_rows} scenes`);
            }
        } catch (err) {
            Toast.error(`Upload failed: ${err.message}`);
            document.getElementById('upload-status').innerHTML = `<p class="text-danger">Error: ${err.message}</p>`;
        }
    },

    async loadSample() {
        try {
            const res = await fetch('/static/sample.csv');
            if (!res.ok) {
                // Try to load from sample.csv relative path
                Toast.info('Loading sample data...');
            }
            const blob = await res.blob();
            const file = new File([blob], 'sample.csv', { type: 'text/csv' });
            this.handleFile(file);
        } catch {
            Toast.warning('Sample file not found. Upload a CSV manually.');
        }
    },

    showValidation(result) {
        const statusEl = document.getElementById('upload-status');
        statusEl.classList.remove('hidden');

        const errorsHtml = result.errors.length > 0
            ? `<div class="mt-md">${result.errors.slice(0, 20).map(e =>
                `<div class="log-entry error"><span class="log-message">Row ${e.row}: [${e.column}] ${e.message}</span></div>`
              ).join('')}</div>`
            : '';

        statusEl.innerHTML = `
            <div class="stats-grid mb-lg">
                <div class="stat-card blue">
                    <div class="stat-label">Total Scenes</div>
                    <div class="stat-value blue">${result.total_rows}</div>
                </div>
                <div class="stat-card green">
                    <div class="stat-label">Valid Scenes</div>
                    <div class="stat-value green">${result.valid_rows}</div>
                </div>
                <div class="stat-card red">
                    <div class="stat-label">Invalid Scenes</div>
                    <div class="stat-value red">${result.invalid_rows}</div>
                </div>
                <div class="stat-card purple">
                    <div class="stat-label">Columns Found</div>
                    <div class="stat-value purple">${result.detected_columns.length}</div>
                </div>
            </div>
            <div class="flex gap-sm">
                <span class="text-xs text-muted">Columns: ${result.detected_columns.join(', ')}</span>
            </div>
            ${errorsHtml}
            ${result.rows.length > 0 ? `
                <div class="mt-lg flex gap-sm">
                    <button class="btn btn-primary" onclick="Router.navigate('review')">
                        📋 Review Scenes
                    </button>
                    <button class="btn btn-secondary" onclick="Router.navigate('settings')">
                        ⚙️ Configure Settings
                    </button>
                </div>
            ` : ''}
        `;
    },
};

// ============================================================
// Scene Review Table
// ============================================================
const Review = {
    selectedScenes: new Set(),
    editingCell: null,

    init() {
        document.getElementById('btn-select-all')?.addEventListener('click', () => this.selectAll());
        document.getElementById('btn-deselect-all')?.addEventListener('click', () => this.deselectAll());
        document.getElementById('btn-delete-selected')?.addEventListener('click', () => this.deleteSelected());
    },

    render() {
        const container = document.getElementById('scene-table-body');
        if (!container || !App.csvRows) return;

        container.innerHTML = App.csvRows.map((row, idx) => `
            <tr class="${this.selectedScenes.has(idx) ? 'selected' : ''}" data-idx="${idx}">
                <td>
                    <input type="checkbox" ${this.selectedScenes.has(idx) ? 'checked' : ''}
                           onchange="Review.toggleSelect(${idx})">
                </td>
                <td class="font-mono">${row.id || idx + 1}</td>
                <td class="editable-cell truncate" title="${this.esc(row.visual_prompt)}"
                    ondblclick="Review.editCell(this, ${idx}, 'visual_prompt')">
                    ${this.esc(row.visual_prompt?.substring(0, 80))}${(row.visual_prompt?.length || 0) > 80 ? '...' : ''}
                </td>
                <td class="editable-cell truncate" title="${this.esc(row.voiceover_script)}"
                    ondblclick="Review.editCell(this, ${idx}, 'voiceover_script')">
                    ${this.esc(row.voiceover_script?.substring(0, 80))}${(row.voiceover_script?.length || 0) > 80 ? '...' : ''}
                </td>
                <td>${row.aspect_ratio || '16:9'}</td>
                <td class="font-mono">${row.filename || ''}</td>
                <td>
                    <div class="flex gap-sm">
                        <button class="btn btn-ghost btn-sm" onclick="Review.duplicateRow(${idx})" title="Duplicate">📋</button>
                        <button class="btn btn-ghost btn-sm" onclick="Review.deleteRow(${idx})" title="Delete">🗑️</button>
                    </div>
                </td>
            </tr>
        `).join('');

        document.getElementById('scene-count').textContent = `${App.csvRows.length} scenes`;
    },

    editCell(td, idx, field) {
        const currentVal = App.csvRows[idx][field] || '';
        td.innerHTML = `<textarea class="form-textarea" style="min-height:60px"
            onblur="Review.saveCell(this, ${idx}, '${field}')"
            onkeydown="if(event.key==='Escape')Review.render()">${this.esc(currentVal)}</textarea>`;
        td.querySelector('textarea').focus();
    },

    saveCell(textarea, idx, field) {
        App.csvRows[idx][field] = textarea.value;
        this.render();
        Toast.info('Cell updated');
    },

    toggleSelect(idx) {
        if (this.selectedScenes.has(idx)) this.selectedScenes.delete(idx);
        else this.selectedScenes.add(idx);
        this.render();
    },

    selectAll() {
        App.csvRows.forEach((_, idx) => this.selectedScenes.add(idx));
        this.render();
    },

    deselectAll() {
        this.selectedScenes.clear();
        this.render();
    },

    duplicateRow(idx) {
        const row = { ...App.csvRows[idx] };
        row.id = `${row.id}_copy`;
        row.filename = `${row.filename || 'scene'}_copy`;
        App.csvRows.splice(idx + 1, 0, row);
        Toast.info('Scene duplicated');
        this.render();
    },

    deleteRow(idx) {
        App.csvRows.splice(idx, 1);
        this.selectedScenes.delete(idx);
        Toast.info('Scene deleted');
        this.render();
    },

    deleteSelected() {
        if (this.selectedScenes.size === 0) return;
        const indices = [...this.selectedScenes].sort((a, b) => b - a);
        indices.forEach(i => App.csvRows.splice(i, 1));
        this.selectedScenes.clear();
        Toast.info(`${indices.length} scenes deleted`);
        this.render();
    },

    esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};

// ============================================================
// Settings Panel
// ============================================================
const Settings = {
    voices: [],

    init() {
        document.getElementById('generation-mode')?.addEventListener('change', (e) => {
            this.updateModeUI(e.target.value);
        });
        document.getElementById('voice-language')?.addEventListener('change', (e) => {
            this.loadVoices(e.target.value);
        });
    },

    async refresh() {
        try {
            const models = await API.get('/api/settings/models');
            if (document.getElementById('image-model')) {
                document.getElementById('image-model').value = models.image_model;
            }
            if (document.getElementById('video-model')) {
                document.getElementById('video-model').value = models.video_model;
            }
            // Set defaults
            const d = models.defaults;
            this.setVal('aspect-ratio', d.aspect_ratio);
            this.setVal('concurrency', d.concurrency);
            this.setVal('retry-count', d.retry_count);
            this.setVal('voice-language', d.voice_language);
            this.setVal('speech-speed', d.speech_speed);
            this.setVal('speech-pitch', d.speech_pitch);
            this.setVal('audio-format', d.audio_format);

            this.updateSpeedLabel();
            this.loadVoices(d.voice_language);

            // Check FFmpeg
            const ffmpeg = await API.get('/api/settings/ffmpeg');
            const mergeSection = document.getElementById('merge-section');
            if (mergeSection) {
                if (!ffmpeg.available) {
                    mergeSection.innerHTML += '<p class="text-xs text-warning mt-md">⚠ FFmpeg not found. Merge features disabled.</p>';
                }
            }
        } catch (err) {
            console.warn('Settings load failed:', err);
        }
    },

    async loadVoices(language) {
        try {
            const data = await API.get(`/api/settings/voices?language=${language || ''}`);
            this.voices = data.voices;
            const select = document.getElementById('voice-name');
            if (select) {
                select.innerHTML = '<option value="">Auto (default)</option>';
                data.voices.forEach(v => {
                    select.innerHTML += `<option value="${v.name}">${v.name} (${v.gender})</option>`;
                });
            }
        } catch (err) {
            console.warn('Voice loading failed:', err);
        }
    },

    updateModeUI(mode) {
        const videoFields = document.getElementById('video-settings');
        const voiceFields = document.getElementById('voice-settings');
        if (videoFields) videoFields.classList.toggle('hidden', mode !== 'VIDEO_VOICE');
        if (voiceFields) voiceFields.classList.toggle('hidden', mode === 'IMAGE_ONLY');
    },

    updateSpeedLabel() {
        const slider = document.getElementById('speech-speed');
        const label = document.getElementById('speed-label');
        if (slider && label) label.textContent = `${slider.value}x`;
    },

    setVal(id, val) {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
    },

    getSettings() {
        return {
            mode: this.getVal('generation-mode', 'IMAGE_VOICE'),
            image_model: this.getVal('image-model', ''),
            video_model: this.getVal('video-model', ''),
            aspect_ratio: this.getVal('aspect-ratio', '16:9'),
            concurrency: parseInt(this.getVal('concurrency', '1')),
            retry_count: parseInt(this.getVal('retry-count', '3')),
            voice_name: this.getVal('voice-name', ''),
            language: this.getVal('voice-language', 'en-US'),
            speech_speed: parseFloat(this.getVal('speech-speed', '1.0')),
            speech_pitch: parseFloat(this.getVal('speech-pitch', '0')),
            audio_format: this.getVal('audio-format', 'WAV'),
            sample_rate: 24000,
            enhance_prompts: document.getElementById('enhance-prompts')?.checked || false,
            enhance_scripts: document.getElementById('enhance-scripts')?.checked || false,
            merge_enabled: document.getElementById('merge-enabled')?.checked || false,
            voiceover_timing: this.getVal('voiceover-timing', 'none'),
            profile_id: parseInt(this.getVal('profile-select', '1')),
        };
    },

    getVal(id, def) {
        const el = document.getElementById(id);
        return el ? el.value : def;
    },
};

// ============================================================
// Generation Control
// ============================================================
const Generation = {
    eventSource: null,
    isRunning: false,

    init() {
        document.getElementById('btn-start-batch')?.addEventListener('click', () => this.start());
        document.getElementById('btn-pause')?.addEventListener('click', () => this.pause());
        document.getElementById('btn-resume')?.addEventListener('click', () => this.resume());
        document.getElementById('btn-cancel')?.addEventListener('click', () => this.cancel());
        document.getElementById('btn-retry-failed')?.addEventListener('click', () => this.retryFailed());
    },

    async refresh() {
        // Check for resumable job
        try {
            const data = await API.get('/api/generation/check-resumable');
            if (data.resumable) {
                this.showResumeBanner(data.resumable);
            }
        } catch (err) {
            console.warn('Resume check failed:', err);
        }
    },

    showResumeBanner(info) {
        const banner = document.getElementById('resume-banner');
        if (!banner) return;
        banner.classList.remove('hidden');
        banner.innerHTML = `
            <div class="resume-info">
                <h3>📂 Previous Job Found</h3>
                <div class="resume-stats">
                    <span>✅ Completed: <strong>${info.completed}</strong></span>
                    <span>❌ Failed: <strong>${info.failed}</strong></span>
                    <span>⏳ Pending: <strong>${info.pending}</strong></span>
                </div>
            </div>
            <button class="btn btn-primary" onclick="Generation.resume()">▶ Resume Job</button>
        `;
    },

    async start() {
        if (!App.csvRows || App.csvRows.length === 0) {
            Toast.error('No scenes loaded. Upload a CSV first.');
            return;
        }

        const settings = Settings.getSettings();

        try {
            const result = await API.post('/api/generation/start', {
                job_name: `Batch ${new Date().toLocaleString()}`,
                mode: settings.mode,
                rows: App.csvRows,
                settings: settings,
            });

            App.currentJobId = result.job_id;
            this.isRunning = true;
            Toast.success(`Job started: ${result.total_scenes} scenes`);
            this.updateControls();
            this.connectSSE();
            Router.navigate('dashboard');
        } catch (err) {
            Toast.error(`Failed to start: ${err.message}`);
        }
    },

    async pause() {
        try {
            await API.post('/api/generation/pause', {});
            Toast.info('Job paused');
            this.updateControls();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async resume() {
        try {
            const result = await API.post('/api/generation/resume', {});
            App.currentJobId = result.job_id || App.currentJobId;
            this.isRunning = true;
            Toast.success('Job resumed');
            this.updateControls();
            this.connectSSE();
            Router.navigate('dashboard');
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async cancel() {
        if (!confirm('Cancel the current job? Completed work will be preserved.')) return;
        try {
            await API.post('/api/generation/cancel', {});
            this.isRunning = false;
            Toast.warning('Job cancelled');
            this.disconnectSSE();
            this.updateControls();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async retryFailed() {
        if (!App.currentJobId) {
            Toast.error('No active job');
            return;
        }
        try {
            const result = await API.post('/api/generation/retry-failed', {
                job_id: App.currentJobId,
            });
            this.isRunning = true;
            Toast.success(`Retrying ${result.scenes_to_retry} failed scenes`);
            this.connectSSE();
            this.updateControls();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    connectSSE() {
        this.disconnectSSE();
        this.eventSource = new EventSource('/api/generation/events');

        this.eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleEvent(data);
            } catch (err) {
                console.warn('SSE parse error:', err);
            }
        };

        this.eventSource.onerror = () => {
            console.warn('SSE connection error');
        };
    },

    disconnectSSE() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    },

    handleEvent(event) {
        switch (event.type) {
            case 'progress_update':
                Dashboard.updateProgress(event.data);
                break;
            case 'scene_started':
            case 'visual_started':
            case 'visual_completed':
            case 'voice_started':
            case 'voice_completed':
            case 'scene_completed':
            case 'scene_failed':
                Dashboard.addLogEntry(event);
                Dashboard.refreshSceneTable(event.data);
                break;
            case 'job_finished':
                this.isRunning = false;
                this.disconnectSSE();
                this.updateControls();
                Dashboard.updateProgress(event.data);
                if (event.data.status === 'COMPLETED') {
                    Toast.success('🎉 All scenes completed successfully!');
                } else {
                    Toast.warning(`Job finished with status: ${event.data.status}`);
                }
                break;
            case 'job_error':
                this.isRunning = false;
                this.disconnectSSE();
                Toast.error(`Job error: ${event.data.error}`);
                break;
            case 'quota_error':
                Toast.error('⚠️ Quota/billing limit reached. Job paused.');
                Dashboard.showQuotaBanner(event.data.message);
                break;
        }
    },

    updateControls() {
        const startBtn = document.getElementById('btn-start-batch');
        const pauseBtn = document.getElementById('btn-pause');
        const resumeBtn = document.getElementById('btn-resume');
        const cancelBtn = document.getElementById('btn-cancel');

        if (startBtn) startBtn.disabled = this.isRunning;
        if (pauseBtn) pauseBtn.classList.toggle('hidden', !this.isRunning);
        if (cancelBtn) cancelBtn.classList.toggle('hidden', !this.isRunning);
    },
};

// ============================================================
// Dashboard
// ============================================================
const Dashboard = {
    async refresh() {
        if (!App.currentJobId) return;
        try {
            const job = await API.get(`/api/jobs/${App.currentJobId}`);
            this.updateProgress(job.progress || job);

            const logs = await API.get(`/api/jobs/${App.currentJobId}/logs?limit=100`);
            this.renderLogs(logs.logs);

            const scenes = await API.get(`/api/jobs/${App.currentJobId}/scenes?limit=50`);
            this.renderSceneStatus(scenes.scenes);
        } catch (err) {
            console.warn('Dashboard refresh failed:', err);
        }
    },

    updateProgress(data) {
        if (!data) return;

        this.setTextContent('dash-total', data.total || 0);
        this.setTextContent('dash-completed', data.completed || 0);
        this.setTextContent('dash-failed', data.failed || 0);
        this.setTextContent('dash-processing', data.processing || 0);
        this.setTextContent('dash-pending', data.pending || 0);

        const progress = data.progress || 0;
        this.setTextContent('dash-progress', `${progress}%`);

        // Update progress ring
        const ring = document.getElementById('progress-ring-fill');
        if (ring) {
            const circumference = 2 * Math.PI * 54;
            const offset = circumference - (progress / 100) * circumference;
            ring.style.strokeDashoffset = offset;
        }

        // Progress bars
        this.updateBar('bar-visuals', data.visuals_completed, data.total);
        this.updateBar('bar-voices', data.voices_completed, data.total);
        this.updateBar('bar-overall', data.completed, data.total);

        // Counts labels
        this.setTextContent('visuals-count', `${data.visuals_completed || 0} / ${data.total || 0}`);
        this.setTextContent('voices-count', `${data.voices_completed || 0} / ${data.total || 0}`);
        this.setTextContent('overall-count', `${data.completed || 0} / ${data.total || 0}`);

        // Failed section
        const failedSection = document.getElementById('failed-section');
        if (failedSection) {
            failedSection.classList.toggle('hidden', (data.failed || 0) === 0);
            this.setTextContent('failed-count', data.failed || 0);
        }
    },

    updateBar(id, value, total) {
        const bar = document.getElementById(id);
        if (bar && total > 0) {
            bar.style.width = `${(value / total) * 100}%`;
        }
    },

    addLogEntry(event) {
        const log = document.getElementById('activity-log');
        if (!log) return;

        const time = new Date(event.timestamp || Date.now()).toLocaleTimeString();
        const sceneNum = event.data.scene_number || '';
        let levelClass = '';
        let message = '';

        switch (event.type) {
            case 'scene_started':
                message = `Scene ${sceneNum} → Processing started`;
                break;
            case 'visual_started':
                message = `Scene ${sceneNum} → Visual generation started`;
                break;
            case 'visual_completed':
                message = `Scene ${sceneNum} → Visual completed`;
                levelClass = 'success';
                break;
            case 'voice_started':
                message = `Scene ${sceneNum} → Voice generation started`;
                break;
            case 'voice_completed':
                message = `Scene ${sceneNum} → Voice completed`;
                levelClass = 'success';
                break;
            case 'scene_completed':
                message = `Scene ${sceneNum} → ✅ COMPLETED`;
                levelClass = 'success';
                break;
            case 'scene_failed':
                message = `Scene ${sceneNum} → ❌ FAILED${event.data.error ? ': ' + event.data.error : ''}`;
                levelClass = 'error';
                break;
            default:
                message = event.type;
        }

        const entry = document.createElement('div');
        entry.className = `log-entry ${levelClass}`;
        entry.innerHTML = `<span class="log-time">${time}</span><span class="log-message">${message}</span>`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    },

    renderLogs(logs) {
        const container = document.getElementById('activity-log');
        if (!container) return;
        container.innerHTML = logs.map(l => `
            <div class="log-entry ${l.level === 'SUCCESS' ? 'success' : l.level === 'ERROR' ? 'error' : l.level === 'WARNING' ? 'warning' : ''}">
                <span class="log-time">${new Date(l.timestamp).toLocaleTimeString()}</span>
                <span class="log-message">${l.message}</span>
            </div>
        `).join('');
        container.scrollTop = container.scrollHeight;
    },

    renderSceneStatus(scenes) {
        const container = document.getElementById('scene-status-body');
        if (!container) return;

        container.innerHTML = scenes.map(s => `
            <tr>
                <td class="font-mono">${s.scene_number}</td>
                <td class="truncate" title="${Review.esc(s.visual_prompt)}">${Review.esc(s.visual_prompt?.substring(0, 50))}</td>
                <td>${this.statusBadge(s.visual_status)}</td>
                <td>${this.statusBadge(s.voice_status)}</td>
                <td>${this.statusBadge(s.overall_status)}</td>
                <td class="truncate text-xs text-danger">${s.error_message || ''}</td>
                <td>
                    ${s.overall_status === 'FAILED' ? `<button class="btn btn-ghost btn-sm" onclick="Dashboard.retryScene(${s.id})">🔄</button>` : ''}
                    ${s.overall_status === 'PENDING' ? `<button class="btn btn-ghost btn-sm" onclick="Dashboard.skipScene(${s.id})">⏭</button>` : ''}
                </td>
            </tr>
        `).join('');
    },

    refreshSceneTable(data) {
        // Light refresh when we get an SSE update
        if (App.currentJobId) {
            this.refresh();
        }
    },

    statusBadge(status) {
        if (!status) return '';
        const s = status.toLowerCase();
        const labels = {
            'pending': '⏳ Pending',
            'processing': '🔄 Processing',
            'visual_generating': '🎨 Generating',
            'visual_completed': '✅ Visual Done',
            'voice_generating': '🎤 Generating',
            'voice_completed': '✅ Voice Done',
            'merging': '🔀 Merging',
            'completed': '✅ Completed',
            'failed': '❌ Failed',
            'skipped': '⏭ Skipped',
        };
        const cls = s.includes('completed') || s.includes('voice_completed') || s.includes('visual_completed') ? 'completed'
            : s.includes('fail') ? 'failed'
            : s.includes('process') || s.includes('generating') || s.includes('merging') ? 'processing'
            : s === 'skipped' ? 'skipped'
            : 'pending';
        return `<span class="status-badge ${cls}"><span class="status-dot"></span>${labels[s] || status}</span>`;
    },

    async retryScene(sceneId) {
        try {
            await API.post(`/api/jobs/${App.currentJobId}/scenes/${sceneId}/retry`, {});
            Toast.info('Scene reset for retry');
            this.refresh();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async skipScene(sceneId) {
        try {
            await API.post(`/api/jobs/${App.currentJobId}/scenes/${sceneId}/skip`, {});
            Toast.info('Scene skipped');
            this.refresh();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    showQuotaBanner(message) {
        const el = document.getElementById('quota-banner');
        if (el) {
            el.classList.remove('hidden');
            el.querySelector('.banner-message').textContent = message || 'API quota or billing limit reached.';
        }
    },

    setTextContent(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    },
};

// ============================================================
// API Settings
// ============================================================
const ApiSettings = {
    async refresh() {
        try {
            const data = await API.get('/api/settings/profiles');
            this.renderProfiles(data);
        } catch (err) {
            console.warn('Profile load failed:', err);
        }
    },

    renderProfiles(data) {
        const container = document.getElementById('profiles-list');
        if (!container) return;

        const allProfiles = [
            ...(data.env_profiles || []).map(p => ({ ...p, source: 'env' })),
            ...(data.profiles || []).map(p => ({ ...p, source: 'db' })),
        ];

        if (allProfiles.length === 0) {
            container.innerHTML = '<p class="text-muted text-center">No profiles configured. Add API keys to .env file.</p>';
            return;
        }

        container.innerHTML = allProfiles.map(p => `
            <div class="card mb-md">
                <div class="flex items-center justify-between">
                    <div>
                        <strong>${p.name}</strong>
                        <span class="text-xs text-muted ml-sm">(${p.source === 'env' ? '.env file' : 'database'})</span>
                    </div>
                    <div class="flex gap-sm items-center">
                        <span class="profile-dot ${p.has_api_key ? '' : 'disconnected'}"></span>
                        <span class="text-xs ${p.has_api_key ? 'text-success' : 'text-danger'}">
                            ${p.has_api_key ? 'Configured' : 'No API Key'}
                        </span>
                    </div>
                </div>
                <div class="mt-md">
                    <button class="btn btn-secondary btn-sm" onclick="ApiSettings.testConnection(${p.id})">
                        🔌 Test Connection
                    </button>
                </div>
                <div id="test-result-${p.id}" class="mt-md hidden"></div>
            </div>
        `).join('');
    },

    async testConnection(profileId) {
        const resultEl = document.getElementById(`test-result-${profileId}`);
        if (!resultEl) return;

        resultEl.classList.remove('hidden');
        resultEl.innerHTML = '<div class="test-item loading"><span class="test-icon">⏳</span> Testing...</div>';

        try {
            const result = await API.post(`/api/settings/profiles/${profileId}/test`, {});

            resultEl.innerHTML = `
                <div class="connection-test">
                    <div class="test-item ${result.auth_ok ? 'pass' : 'fail'}">
                        <span class="test-icon">${result.auth_ok ? '✅' : '❌'}</span>
                        Authentication ${result.details?.auth || ''}
                    </div>
                    <div class="test-item ${result.image_ok ? 'pass' : 'fail'}">
                        <span class="test-icon">${result.image_ok ? '✅' : '❌'}</span>
                        Image Generation
                    </div>
                    <div class="test-item ${result.video_ok ? 'pass' : 'fail'}">
                        <span class="test-icon">${result.video_ok ? '✅' : '❌'}</span>
                        Video Generation
                    </div>
                    <div class="test-item ${result.tts_ok ? 'pass' : 'fail'}">
                        <span class="test-icon">${result.tts_ok ? '✅' : '❌'}</span>
                        Text-to-Speech
                    </div>
                </div>
                ${result.errors?.length ? `<p class="text-xs text-warning mt-md">${result.errors.join('; ')}</p>` : ''}
            `;

            if (result.success) Toast.success('Connection test passed!');
            else Toast.warning('Some services unavailable');
        } catch (err) {
            resultEl.innerHTML = `<div class="test-item fail"><span class="test-icon">❌</span> ${err.message}</div>`;
            Toast.error('Connection test failed');
        }
    },
};

// ============================================================
// App Bootstrap
// ============================================================
const App = {
    csvRows: null,
    currentJobId: null,

    init() {
        Toast.init();
        Router.init();
        Upload.init();
        Review.init();
        Settings.init();
        Generation.init();

        // Watch for view changes to render review table
        const observer = new MutationObserver(() => {
            if (Router.currentView === 'review' && App.csvRows) {
                Review.render();
            }
        });
        const reviewPanel = document.getElementById('view-review');
        if (reviewPanel) observer.observe(reviewPanel, { attributes: true });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === '1') Router.navigate('upload');
            if (e.ctrlKey && e.key === '2') Router.navigate('review');
            if (e.ctrlKey && e.key === '3') Router.navigate('settings');
            if (e.ctrlKey && e.key === '4') Router.navigate('generate');
            if (e.ctrlKey && e.key === '5') Router.navigate('dashboard');
        });
    },
};

// Start
document.addEventListener('DOMContentLoaded', () => App.init());
