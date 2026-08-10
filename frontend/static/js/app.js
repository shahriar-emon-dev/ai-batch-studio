/**
 * AI Batch Studio — Main Application Controller (Serverless Edition)
 *
 * All data operations go directly to Supabase Postgres.
 * CSV parsing, generation, and media processing happen client-side.
 *
 * Workflow: Upload → Review → Settings → Generate → Dashboard
 */

// ============================================================
// Supabase Data Layer (replaces old backend API calls)
// ============================================================
const DB = {
    get supabase() {
        return window.api?.supabase;
    },

    async getUserId() {
        const { data } = await this.supabase.auth.getSession();
        return data?.session?.user?.id;
    },

    // --- Projects ---
    async getProjects() {
        const { data, error } = await this.supabase
            .from('projects')
            .select('*')
            .order('created_at', { ascending: false });
        if (error) throw error;
        return data || [];
    },

    async getProject(id) {
        const { data, error } = await this.supabase
            .from('projects')
            .select('*')
            .eq('id', id)
            .single();
        if (error) throw error;
        return data;
    },

    async createProject(project) {
        const userId = await this.getUserId();
        const { data, error } = await this.supabase
            .from('projects')
            .insert({ ...project, user_id: userId })
            .select()
            .single();
        if (error) throw error;
        return data;
    },

    async updateProject(id, updates) {
        const { data, error } = await this.supabase
            .from('projects')
            .update(updates)
            .eq('id', id)
            .select()
            .single();
        if (error) throw error;
        return data;
    },

    async deleteProject(id) {
        const { error } = await this.supabase
            .from('projects')
            .delete()
            .eq('id', id);
        if (error) throw error;
    },

    // --- Scenes ---
    async getScenes(projectId) {
        const { data, error } = await this.supabase
            .from('scenes')
            .select('*')
            .eq('project_id', projectId)
            .order('scene_number', { ascending: true });
        if (error) throw error;
        return data || [];
    },

    async createScenes(scenes) {
        const userId = await this.getUserId();
        const rows = scenes.map(s => ({ ...s, user_id: userId }));
        const { data, error } = await this.supabase
            .from('scenes')
            .insert(rows)
            .select();
        if (error) throw error;
        return data;
    },

    async updateScene(id, updates) {
        const { data, error } = await this.supabase
            .from('scenes')
            .update(updates)
            .eq('id', id)
            .select()
            .single();
        if (error) throw error;
        return data;
    },

    // --- User Settings ---
    async getSettings() {
        const userId = await this.getUserId();
        const { data, error } = await this.supabase
            .from('user_settings')
            .select('*')
            .eq('user_id', userId)
            .maybeSingle();
        if (error) throw error;
        return data;
    },

    async upsertSettings(settings) {
        const userId = await this.getUserId();
        const { data, error } = await this.supabase
            .from('user_settings')
            .upsert({ ...settings, user_id: userId }, { onConflict: 'user_id' })
            .select()
            .single();
        if (error) throw error;
        return data;
    },
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
// Client-Side CSV Parser (replaces backend /api/upload/csv)
// ============================================================
const CSVParser = {
    parse(text) {
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        if (lines.length < 2) {
            return { total_rows: 0, valid_rows: 0, invalid_rows: 0, errors: [{ row: 0, column: 'file', message: 'CSV must have a header row and at least one data row.' }], rows: [], detected_columns: [] };
        }

        const headers = this.parseLine(lines[0]).map(h => h.trim().toLowerCase());
        const detected_columns = [...headers];

        // Map common header variations
        const colMap = {};
        headers.forEach((h, i) => {
            if (['scene', 'scene_number', 'scene_id', 'id', 'no', 'number', '#'].includes(h)) colMap.scene_number = i;
            if (['visual_prompt', 'visual', 'image_prompt', 'prompt', 'image'].includes(h)) colMap.visual_prompt = i;
            if (['voiceover_script', 'voiceover', 'script', 'narration', 'voice', 'text', 'tts'].includes(h)) colMap.voiceover_script = i;
            if (['aspect_ratio', 'ratio', 'size'].includes(h)) colMap.aspect_ratio = i;
            if (['filename', 'file', 'name', 'output'].includes(h)) colMap.filename = i;
            if (['voice_name', 'voice_id'].includes(h)) colMap.voice = i;
        });

        const rows = [];
        const errors = [];

        for (let i = 1; i < lines.length; i++) {
            const fields = this.parseLine(lines[i]);
            if (fields.length === 0 || (fields.length === 1 && fields[0].trim() === '')) continue;

            const row = {
                id: colMap.scene_number !== undefined ? fields[colMap.scene_number]?.trim() : String(i),
                visual_prompt: colMap.visual_prompt !== undefined ? fields[colMap.visual_prompt]?.trim() : '',
                voiceover_script: colMap.voiceover_script !== undefined ? fields[colMap.voiceover_script]?.trim() : '',
                aspect_ratio: colMap.aspect_ratio !== undefined ? fields[colMap.aspect_ratio]?.trim() : '16:9',
                filename: colMap.filename !== undefined ? fields[colMap.filename]?.trim() : `scene_${i}`,
                voice: colMap.voice !== undefined ? fields[colMap.voice]?.trim() : '',
            };

            if (!row.visual_prompt) {
                errors.push({ row: i + 1, column: 'visual_prompt', message: 'Visual prompt is required' });
            } else {
                rows.push(row);
            }
        }

        return {
            total_rows: lines.length - 1,
            valid_rows: rows.length,
            invalid_rows: errors.length,
            errors,
            rows,
            detected_columns,
        };
    },

    parseLine(line) {
        const result = [];
        let current = '';
        let inQuotes = false;
        for (let i = 0; i < line.length; i++) {
            const c = line[i];
            if (inQuotes) {
                if (c === '"' && line[i + 1] === '"') {
                    current += '"'; i++;
                } else if (c === '"') {
                    inQuotes = false;
                } else {
                    current += c;
                }
            } else {
                if (c === '"') {
                    inQuotes = true;
                } else if (c === ',') {
                    result.push(current);
                    current = '';
                } else {
                    current += c;
                }
            }
        }
        result.push(current);
        return result;
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

        try {
            document.getElementById('upload-status').classList.remove('hidden');
            document.getElementById('upload-status').innerHTML = '<p class="text-muted">Validating CSV...</p>';

            const text = await file.text();
            const result = CSVParser.parse(text);
            this.csvData = result;
            this.showValidation(result);

            if (result.rows.length > 0) {
                App.csvRows = result.rows;
                Toast.success(`CSV loaded: ${result.valid_rows} scenes`);
            }
        } catch (err) {
            Toast.error(`Upload failed: ${err.message}`);
            document.getElementById('upload-status').innerHTML = `<p class="text-danger">Error: ${err.message}</p>`;
        }
    },

    async loadSample() {
        try {
            Toast.info('Loading sample data...');
            const res = await fetch('static/sample.csv');
            if (!res.ok) throw new Error('Sample not found');
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
// Settings Panel (client-side, no backend calls)
// ============================================================
const Settings = {
    voices: [],

    init() {
        document.getElementById('generation-mode')?.addEventListener('change', (e) => {
            this.updateModeUI(e.target.value);
        });
    },

    async refresh() {
        try {
            const userSettings = await DB.getSettings();
            if (userSettings) {
                this.setVal('aspect-ratio', userSettings.default_aspect_ratio);
                this.setVal('generation-mode', userSettings.default_generation_mode);
                if (userSettings.default_voice) this.setVal('voice-name', userSettings.default_voice);
            }
        } catch (err) {
            console.warn('Settings load failed:', err);
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
        };
    },

    getVal(id, def) {
        const el = document.getElementById(id);
        return el ? el.value : def;
    },
};

// ============================================================
// Client-Side Generation Engine
// ============================================================
const Generation = {
    isRunning: false,
    isPaused: false,
    currentProject: null,
    queue: [],
    concurrency: 1,
    activeWorkers: 0,

    init() {
        document.getElementById('btn-start-batch')?.addEventListener('click', () => this.start());
        document.getElementById('btn-pause')?.addEventListener('click', () => this.pause());
        document.getElementById('btn-resume')?.addEventListener('click', () => this.resume());
        document.getElementById('btn-cancel')?.addEventListener('click', () => this.cancel());
        document.getElementById('btn-retry-failed')?.addEventListener('click', () => this.retryFailed());
    },

    async refresh() {
        // Nothing to check from backend anymore
    },

    async start() {
        if (!App.csvRows || App.csvRows.length === 0) {
            Toast.error('No scenes loaded. Upload a CSV first.');
            return;
        }

        const settings = Settings.getSettings();
        const userSettings = await DB.getSettings();
        const apiKey = userSettings?.google_api_key;

        if (!apiKey) {
            Toast.error('Google API Key not configured! Go to API Configuration first.');
            Router.navigate('api-settings');
            return;
        }

        try {
            // 1. Create project in Supabase
            const project = await DB.createProject({
                name: `Batch ${new Date().toLocaleString()}`,
                mode: settings.mode,
                status: 'PROCESSING',
                total_scenes: App.csvRows.length,
            });

            this.currentProject = project;
            App.currentJobId = project.id;

            // 2. Create scenes in Supabase
            const scenesToInsert = App.csvRows.map((row, idx) => ({
                project_id: project.id,
                scene_number: row.id || String(idx + 1),
                visual_prompt: row.visual_prompt,
                voiceover_script: row.voiceover_script || '',
                aspect_ratio: row.aspect_ratio || settings.aspect_ratio,
                voice: row.voice || settings.voice_name,
                overall_status: 'PENDING',
            }));
            const createdScenes = await DB.createScenes(scenesToInsert);

            // 3. Start client-side processing
            this.isRunning = true;
            this.isPaused = false;
            this.queue = [...createdScenes];
            this.concurrency = settings.concurrency || 1;

            Toast.success(`Job started: ${createdScenes.length} scenes`);
            this.updateControls();
            Router.navigate('dashboard');

            // 4. Process the queue
            this.processQueue(apiKey, settings);
        } catch (err) {
            Toast.error(`Failed to start: ${err.message}`);
        }
    },

    async processQueue(apiKey, settings) {
        const processScene = async (scene) => {
            if (!this.isRunning) return;
            while (this.isPaused) {
                await new Promise(r => setTimeout(r, 500));
                if (!this.isRunning) return;
            }

            try {
                // Update status to PROCESSING
                await DB.updateScene(scene.id, { overall_status: 'PROCESSING' });
                Dashboard.addLogEntry({ type: 'scene_started', data: { scene_number: scene.scene_number } });

                // --- VISUAL GENERATION ---
                Dashboard.addLogEntry({ type: 'visual_started', data: { scene_number: scene.scene_number } });

                const imageUrl = await this.generateImage(apiKey, scene.visual_prompt, scene.aspect_ratio);
                await DB.updateScene(scene.id, { visual_url: imageUrl });
                Dashboard.addLogEntry({ type: 'visual_completed', data: { scene_number: scene.scene_number } });

                // --- VOICE GENERATION (if applicable) ---
                if (settings.mode !== 'IMAGE_ONLY' && scene.voiceover_script) {
                    Dashboard.addLogEntry({ type: 'voice_started', data: { scene_number: scene.scene_number } });

                    const audioUrl = await this.generateVoice(apiKey, scene.voiceover_script, settings);
                    await DB.updateScene(scene.id, { audio_url: audioUrl });
                    Dashboard.addLogEntry({ type: 'voice_completed', data: { scene_number: scene.scene_number } });
                }

                // Mark scene as completed
                await DB.updateScene(scene.id, { overall_status: 'COMPLETED' });
                Dashboard.addLogEntry({ type: 'scene_completed', data: { scene_number: scene.scene_number } });

                // Update project counters
                App.completedScenes = (App.completedScenes || 0) + 1;
                this.updateDashboardProgress();
            } catch (err) {
                await DB.updateScene(scene.id, {
                    overall_status: 'FAILED',
                    error_message: err.message,
                });
                Dashboard.addLogEntry({
                    type: 'scene_failed',
                    data: { scene_number: scene.scene_number, error: err.message },
                });
                App.failedScenes = (App.failedScenes || 0) + 1;
                this.updateDashboardProgress();
            }
        };

        // Run with concurrency
        App.completedScenes = 0;
        App.failedScenes = 0;

        const chunks = [];
        for (let i = 0; i < this.queue.length; i += this.concurrency) {
            chunks.push(this.queue.slice(i, i + this.concurrency));
        }

        for (const chunk of chunks) {
            if (!this.isRunning) break;
            await Promise.all(chunk.map(scene => processScene(scene)));
        }

        // Job finished
        this.isRunning = false;
        const finalStatus = App.failedScenes > 0 ? 'COMPLETED' : 'COMPLETED';
        await DB.updateProject(App.currentJobId, {
            status: finalStatus,
            completed_scenes: App.completedScenes,
            failed_scenes: App.failedScenes,
        });
        Toast.success('🎉 Batch generation finished!');
        this.updateControls();
    },

    async generateImage(apiKey, prompt, aspectRatio) {
        // Call Google Gemini / Imagen API directly from the browser
        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key=${apiKey}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    instances: [{ prompt }],
                    parameters: {
                        sampleCount: 1,
                        aspectRatio: aspectRatio || '16:9',
                    },
                }),
            }
        );

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error?.message || `Image API error: ${response.status}`);
        }

        const result = await response.json();
        if (result.predictions && result.predictions[0]?.bytesBase64Encoded) {
            // Return as data URL for now (can upload to Supabase Storage later)
            return `data:image/png;base64,${result.predictions[0].bytesBase64Encoded}`;
        }
        throw new Error('No image generated');
    },

    async generateVoice(apiKey, text, settings) {
        // Call Google Cloud TTS API directly from the browser
        const response = await fetch(
            `https://texttospeech.googleapis.com/v1/text:synthesize?key=${apiKey}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    input: { text },
                    voice: {
                        languageCode: settings.language || 'en-US',
                        name: settings.voice_name || '',
                    },
                    audioConfig: {
                        audioEncoding: 'MP3',
                        speakingRate: settings.speech_speed || 1.0,
                        pitch: settings.speech_pitch || 0,
                    },
                }),
            }
        );

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error?.message || `TTS API error: ${response.status}`);
        }

        const result = await response.json();
        if (result.audioContent) {
            return `data:audio/mp3;base64,${result.audioContent}`;
        }
        throw new Error('No audio generated');
    },

    updateDashboardProgress() {
        const total = this.queue.length;
        const completed = App.completedScenes || 0;
        const failed = App.failedScenes || 0;
        const processing = this.isRunning ? Math.min(this.concurrency, total - completed - failed) : 0;
        const pending = total - completed - failed - processing;
        const progress = total > 0 ? Math.round((completed / total) * 100) : 0;

        Dashboard.updateProgress({
            total,
            completed,
            failed,
            processing,
            pending,
            progress,
            visuals_completed: completed,
            voices_completed: completed,
        });
    },

    pause() {
        this.isPaused = true;
        Toast.info('Job paused');
        this.updateControls();
    },

    resume() {
        this.isPaused = false;
        Toast.success('Job resumed');
        this.updateControls();
    },

    cancel() {
        if (!confirm('Cancel the current job? Completed work will be preserved.')) return;
        this.isRunning = false;
        this.isPaused = false;
        Toast.warning('Job cancelled');
        this.updateControls();
    },

    async retryFailed() {
        if (!App.currentJobId) {
            Toast.error('No active job');
            return;
        }
        try {
            const scenes = await DB.getScenes(App.currentJobId);
            const failedScenes = scenes.filter(s => s.overall_status === 'FAILED');
            if (failedScenes.length === 0) {
                Toast.info('No failed scenes to retry.');
                return;
            }

            // Reset failed scenes
            for (const s of failedScenes) {
                await DB.updateScene(s.id, { overall_status: 'PENDING', error_message: null });
            }

            const userSettings = await DB.getSettings();
            const apiKey = userSettings?.google_api_key;
            if (!apiKey) {
                Toast.error('Google API Key not configured!');
                return;
            }

            this.isRunning = true;
            this.isPaused = false;
            this.queue = failedScenes;
            App.completedScenes = 0;
            App.failedScenes = 0;

            Toast.success(`Retrying ${failedScenes.length} failed scenes`);
            this.updateControls();
            this.processQueue(apiKey, Settings.getSettings());
        } catch (err) {
            Toast.error(err.message);
        }
    },

    updateControls() {
        const startBtn = document.getElementById('btn-start-batch');
        const pauseBtn = document.getElementById('btn-pause');
        const resumeBtn = document.getElementById('btn-resume');
        const cancelBtn = document.getElementById('btn-cancel');

        if (startBtn) startBtn.disabled = this.isRunning;
        if (pauseBtn) pauseBtn.classList.toggle('hidden', !this.isRunning || this.isPaused);
        if (resumeBtn) resumeBtn.classList.toggle('hidden', !this.isPaused);
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
            const scenes = await DB.getScenes(App.currentJobId);
            const completed = scenes.filter(s => s.overall_status === 'COMPLETED').length;
            const failed = scenes.filter(s => s.overall_status === 'FAILED').length;
            const processing = scenes.filter(s => s.overall_status === 'PROCESSING').length;
            const pending = scenes.filter(s => s.overall_status === 'PENDING').length;
            const total = scenes.length;
            const progress = total > 0 ? Math.round((completed / total) * 100) : 0;

            this.updateProgress({ total, completed, failed, processing, pending, progress, visuals_completed: completed, voices_completed: completed });
            this.renderSceneStatus(scenes);
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

        const time = new Date().toLocaleTimeString();
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

    renderSceneStatus(scenes) {
        const container = document.getElementById('scene-status-body');
        if (!container) return;

        container.innerHTML = scenes.map(s => `
            <tr>
                <td class="font-mono">${s.scene_number}</td>
                <td class="truncate" title="${Review.esc(s.visual_prompt)}">${Review.esc(s.visual_prompt?.substring(0, 50))}</td>
                <td>${this.statusBadge(s.overall_status)}</td>
                <td class="truncate text-xs text-danger">${s.error_message || ''}</td>
                <td>
                    ${s.overall_status === 'FAILED' ? `<button class="btn btn-ghost btn-sm" onclick="Dashboard.retryScene('${s.id}')">🔄</button>` : ''}
                    ${s.overall_status === 'PENDING' ? `<button class="btn btn-ghost btn-sm" onclick="Dashboard.skipScene('${s.id}')">⏭</button>` : ''}
                </td>
            </tr>
        `).join('');
    },

    statusBadge(status) {
        if (!status) return '';
        const s = status.toLowerCase();
        const labels = {
            'pending': '⏳ Pending',
            'processing': '🔄 Processing',
            'completed': '✅ Completed',
            'failed': '❌ Failed',
            'skipped': '⏭ Skipped',
        };
        const cls = s.includes('completed') ? 'completed'
            : s.includes('fail') ? 'failed'
            : s.includes('process') ? 'processing'
            : s === 'skipped' ? 'skipped'
            : 'pending';
        return `<span class="status-badge ${cls}"><span class="status-dot"></span>${labels[s] || status}</span>`;
    },

    async retryScene(sceneId) {
        try {
            await DB.updateScene(sceneId, { overall_status: 'PENDING', error_message: null });
            Toast.info('Scene reset for retry');
            this.refresh();
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async skipScene(sceneId) {
        try {
            await DB.updateScene(sceneId, { overall_status: 'SKIPPED' });
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
// API Settings (stores Google API key in Supabase user_settings)
// ============================================================
const ApiSettings = {
    async refresh() {
        try {
            const settings = await DB.getSettings();
            this.renderSettings(settings);
        } catch (err) {
            console.warn('Settings load failed:', err);
        }
    },

    renderSettings(settings) {
        const container = document.getElementById('profiles-list');
        if (!container) return;

        const hasKey = settings?.google_api_key ? true : false;

        container.innerHTML = `
            <div class="card mb-md">
                <div class="flex items-center justify-between mb-md">
                    <div>
                        <strong>Google AI API Key</strong>
                        <span class="text-xs text-muted ml-sm">(stored in Supabase)</span>
                    </div>
                    <div class="flex gap-sm items-center">
                        <span class="profile-dot ${hasKey ? '' : 'disconnected'}"></span>
                        <span class="text-xs ${hasKey ? 'text-success' : 'text-danger'}">
                            ${hasKey ? 'Configured' : 'Not Set'}
                        </span>
                    </div>
                </div>
                <div class="form-group">
                    <input type="password" id="google-api-key-input" class="form-input" 
                           placeholder="Enter your Google AI API key..."
                           value="${settings?.google_api_key || ''}">
                </div>
                <div class="flex gap-sm mt-md">
                    <button class="btn btn-primary btn-sm" onclick="ApiSettings.saveKey()">
                        💾 Save API Key
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="ApiSettings.testKey()">
                        🔌 Test Connection
                    </button>
                </div>
                <div id="api-test-result" class="mt-md hidden"></div>
            </div>
        `;
    },

    async saveKey() {
        const input = document.getElementById('google-api-key-input');
        const key = input?.value?.trim();
        if (!key) {
            Toast.error('Please enter an API key');
            return;
        }

        try {
            await DB.upsertSettings({ google_api_key: key });
            Toast.success('API key saved!');
            this.refresh();
        } catch (err) {
            Toast.error(`Save failed: ${err.message}`);
        }
    },

    async testKey() {
        const input = document.getElementById('google-api-key-input');
        const key = input?.value?.trim();
        if (!key) {
            Toast.error('Please enter an API key first');
            return;
        }

        const resultEl = document.getElementById('api-test-result');
        if (!resultEl) return;
        resultEl.classList.remove('hidden');
        resultEl.innerHTML = '<div class="test-item loading"><span class="test-icon">⏳</span> Testing connection...</div>';

        try {
            // Test with a simple models list request
            const response = await fetch(
                `https://generativelanguage.googleapis.com/v1beta/models?key=${key}`
            );

            if (response.ok) {
                resultEl.innerHTML = '<div class="test-item pass"><span class="test-icon">✅</span> API Key is valid! Connection successful.</div>';
                Toast.success('Connection test passed!');
            } else {
                const err = await response.json().catch(() => ({}));
                resultEl.innerHTML = `<div class="test-item fail"><span class="test-icon">❌</span> ${err.error?.message || 'Invalid API key'}</div>`;
                Toast.error('Connection test failed');
            }
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
    completedScenes: 0,
    failedScenes: 0,

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
