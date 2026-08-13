/**
 * AI ContentStudio — shared frontend runtime.
 *
 * Every page loads this file. It provides the authenticated API client, toasts,
 * formatting helpers, status badges and the Supabase Realtime subscription used
 * for live progress.
 *
 * Design rules enforced here (proposal §11, §36, §52, §56):
 *  - The browser never calls Google APIs and never holds an API key.
 *  - No simulated progress: every number rendered comes from a backend response.
 *  - Every request is bounded by a timeout and surfaces a real error message.
 */

// ============================================================
// API client
// ============================================================
const API = {
    // Generous enough to survive a cold start on a sleeping free-tier API host,
    // which can take ~50s to wake.
    DEFAULT_TIMEOUT: 60000,
    UPLOAD_TIMEOUT: 180000,

    async getToken() {
        const client = window.supabaseClient || (window.api && window.api.supabase);
        if (!client) return null;
        const { data } = await client.auth.getSession();
        return data?.session?.access_token || null;
    },

    async fetch(endpoint, options = {}) {
        const token = await this.getToken();
        if (!token) {
            window.location.href = '/login.html';
            throw new Error('Not signed in');
        }

        const isForm = options.body instanceof FormData;
        const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) };
        if (options.body && !isForm) headers['Content-Type'] = 'application/json';

        const controller = new AbortController();
        const timeout = options.timeout || (isForm ? this.UPLOAD_TIMEOUT : this.DEFAULT_TIMEOUT);
        const timer = setTimeout(() => controller.abort(), timeout);

        let response;
        try {
            response = await fetch(`${window.API_BASE_URL || ''}${endpoint}`, {
                ...options,
                headers,
                signal: controller.signal,
            });
        } catch (err) {
            clearTimeout(timer);
            const host = window.API_BASE_URL || 'this server';

            // Frontend published without a backend: say so plainly instead of
            // reporting it as a network failure.
            if (!window.API_BASE_URL && !/^(localhost|127\.)/.test(location.hostname)) {
                throw new Error(
                    'Backend API is not configured for this deployment. Set API_BASE_URL to your API host and redeploy.'
                );
            }
            if (err.name === 'AbortError') {
                throw new Error(
                    `Request timed out after ${Math.round(timeout / 1000)}s. ` +
                    `If the API host sleeps when idle it may still be waking — retry in a moment.`
                );
            }
            throw new Error(`Cannot reach the API at ${host}. Check that it is running and that CORS allows this origin.`);
        }
        clearTimeout(timer);

        if (response.status === 401) {
            window.location.href = '/login.html';
            throw new Error('Session expired. Please sign in again.');
        }

        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            let message = payload.detail || `Request failed (${response.status})`;
            if (Array.isArray(payload.detail)) {
                message = payload.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
            }
            if (response.status === 429) message = 'API usage limit reached. Generation is paused.';
            throw new Error(message);
        }

        if (response.status === 204) return null;
        return await response.json();
    },

    get: (endpoint, options) => API.fetch(endpoint, options),
    post: (endpoint, body, options = {}) =>
        API.fetch(endpoint, {
            ...options,
            method: 'POST',
            body: body instanceof FormData ? body : JSON.stringify(body || {}),
        }),
    put: (endpoint, body) => API.fetch(endpoint, { method: 'PUT', body: JSON.stringify(body || {}) }),
    patch: (endpoint, body) => API.fetch(endpoint, { method: 'PATCH', body: JSON.stringify(body || {}) }),
    delete: (endpoint) => API.fetch(endpoint, { method: 'DELETE' }),

    /** Build a query string, skipping empty values. */
    qs(params) {
        const search = new URLSearchParams();
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') search.append(key, value);
        });
        const text = search.toString();
        return text ? `?${text}` : '';
    },
};

// ============================================================
// Toasts — the container is created on demand so every page has them
// ============================================================
const Toast = {
    get container() {
        let element = document.getElementById('toast-container');
        if (!element) {
            element = document.createElement('div');
            element.id = 'toast-container';
            element.className = 'toast-container';
            document.body.appendChild(element);
        }
        return element;
    },

    show(message, type = 'info', duration = 4500) {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        const text = document.createElement('span');
        text.className = 'toast-message';
        text.textContent = message;
        const close = document.createElement('button');
        close.className = 'toast-close';
        close.textContent = '✕';
        close.onclick = () => toast.remove();
        toast.append(text, close);
        this.container.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
        return toast;
    },

    success: (msg) => Toast.show(msg, 'success'),
    error: (msg) => Toast.show(msg, 'error', 8000),
    warning: (msg) => Toast.show(msg, 'warning', 6000),
    info: (msg) => Toast.show(msg, 'info'),
};

// ============================================================
// Formatting + rendering helpers
// ============================================================
const Fmt = {
    esc(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    truncate(value, length = 80) {
        const text = String(value || '');
        return text.length > length ? `${text.slice(0, length)}…` : text;
    },

    bytes(value) {
        const size = Number(value || 0);
        if (!size) return '—';
        const units = ['B', 'KB', 'MB', 'GB'];
        let index = 0;
        let result = size;
        while (result >= 1024 && index < units.length - 1) {
            result /= 1024;
            index += 1;
        }
        return `${result.toFixed(result < 10 && index > 0 ? 1 : 0)} ${units[index]}`;
    },

    date(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString();
    },

    time(value) {
        if (!value) return '';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleTimeString();
    },

    percent(done, total) {
        return total > 0 ? Math.round((done / total) * 100) : 0;
    },

    /**
     * Absolute URL for anything served by the API host: generated media under
     * /output and download endpoints under /api. In a split deployment the
     * pages come from Vercel while these live on the API host, so a bare
     * relative path would resolve to the wrong origin.
     */
    media(path) {
        if (!path) return '';
        if (/^(https?:|data:|blob:)/i.test(path)) return path;
        const base = window.API_BASE_URL || '';
        return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
    },
};

const Status = {
    CLASSES: {
        COMPLETED: 'badge-success',
        VISUAL_COMPLETED: 'badge-success',
        VOICE_COMPLETED: 'badge-success',
        PROCESSING: 'badge-warning',
        RETRYING: 'badge-warning',
        VISUAL_GENERATING: 'badge-warning',
        VOICE_GENERATING: 'badge-warning',
        MERGING: 'badge-warning',
        QUEUED: 'badge-secondary',
        PENDING: 'badge-secondary',
        FAILED: 'badge-danger',
        CANCELLED: 'badge-secondary',
        SKIPPED: 'badge-secondary',
        UNSUPPORTED: 'badge-secondary',
        PAUSED: 'badge-warning',
        IDLE: 'badge-secondary',
    },

    className(status) {
        return this.CLASSES[String(status || 'PENDING').toUpperCase()] || 'badge-secondary';
    },

    /** A blue check is only rendered for a genuinely completed asset (§34). */
    badge(status) {
        const value = String(status || 'PENDING').toUpperCase();
        const mark = value.endsWith('COMPLETED') ? '✓ ' : '';
        return `<span class="badge ${this.className(value)}">${mark}${Fmt.esc(value)}</span>`;
    },
};

// ============================================================
// Page shell — auth guard, user email, logout
// ============================================================
const Shell = {
    async init() {
        const user = await Auth.requireAuth();
        if (!user) return null;

        document.querySelectorAll('#userEmail').forEach(el => {
            el.textContent = user.email;
        });

        const logout = document.getElementById('logoutBtn');
        if (logout) {
            logout.addEventListener('click', async (event) => {
                event.preventDefault();
                await Auth.signOut();
                window.location.href = '/login.html';
            });
        }
        return user;
    },
};

// ============================================================
// Live updates — Supabase Realtime with a polling fallback (§37, §38)
// ============================================================
const Live = {
    channels: [],
    timers: [],

    /**
     * Subscribe to database changes for a project and call `onChange`.
     * Polling continues at a slower cadence as a safety net so the UI stays
     * correct even if the realtime socket cannot connect.
     */
    subscribe(projectId, onChange, { pollMs = 4000, tables = ['scenes', 'generation_tasks', 'generation_jobs'] } = {}) {
        const client = window.supabaseClient;
        if (client && projectId) {
            const channel = client.channel(`project-${projectId}-${Date.now()}`);
            tables.forEach(table => {
                channel.on(
                    'postgres_changes',
                    { event: '*', schema: 'public', table, filter: `project_id=eq.${projectId}` },
                    () => onChange('realtime')
                );
            });
            channel.subscribe();
            this.channels.push(channel);
        }

        const timer = setInterval(() => {
            if (document.visibilityState === 'visible') onChange('poll');
        }, pollMs);
        this.timers.push(timer);

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') onChange('visible');
        });

        return () => this.stop();
    },

    stop() {
        this.timers.forEach(clearInterval);
        this.timers = [];
        const client = window.supabaseClient;
        this.channels.forEach(channel => {
            try { client?.removeChannel(channel); } catch { /* already closed */ }
        });
        this.channels = [];
    },
};

window.addEventListener('beforeunload', () => Live.stop());

// Expose for inline page scripts.
window.API = API;
window.Toast = Toast;
window.Fmt = Fmt;
window.Status = Status;
window.Shell = Shell;
window.Live = Live;
