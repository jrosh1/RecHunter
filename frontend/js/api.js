/**
 * RecHunter API Client
 * Communicates with the FastAPI backend at the same origin.
 */
const API = {

    /** Base URL — same origin, so empty string works. */
    _base: '',

    /**
     * Internal fetch wrapper with error handling.
     * @param {string} path
     * @param {RequestInit} options
     * @returns {Promise<any>}
     */
    async _request(path, options = {}) {
        const url = `${this._base}${path}`;
        const headers = { 'Content-Type': 'application/json', ...options.headers };
        try {
            const res = await fetch(url, { ...options, headers });
            if (res.status === 401) {
                if (window.location.pathname !== '/login' && window.location.pathname !== '/login.html') {
                    window.location.href = '/login';
                }
                throw new Error(`API 401: Not authenticated`);
            }
            if (!res.ok) {
                let detail = res.statusText;
                try {
                    const body = await res.json();
                    detail = body.detail || body.message || JSON.stringify(body);
                } catch { /* ignore parse errors */ }
                throw new Error(`API ${res.status}: ${detail}`);
            }
            // 204 No Content
            if (res.status === 204) return null;
            return await res.json();
        } catch (err) {
            if (err.message.startsWith('API ')) throw err;
            throw new Error(`Network error: ${err.message}`);
        }
    },

    // ── Watches ──────────────────────────────────────────────

    /** List all watches with current status. */
    async getWatches() {
        return this._request('/api/watches');
    },

    /** Create a new watch. */
    async createWatch(data) {
        return this._request('/api/watches', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    /** Update an existing watch by ID. */
    async updateWatch(id, data) {
        return this._request(`/api/watches/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    },

    /** Delete a watch by ID. */
    async deleteWatch(id) {
        return this._request(`/api/watches/${id}`, {
            method: 'DELETE',
        });
    },

    /** Trigger an immediate one-shot check for a watch. */
    async triggerCheck(id) {
        return this._request(`/api/watches/${id}/check`, {
            method: 'POST',
        });
    },

    // ── Search ───────────────────────────────────────────────

    /** Search RIDB for facilities by name. */
    async searchFacilities(query) {
        const q = encodeURIComponent(query);
        return this._request(`/api/search?q=${q}`);
    },

    /** Fetch sub-entities (entrances or tours) for a facility. */
    async getSubEntities(facilityId, type) {
        const fid = encodeURIComponent(facilityId);
        const t = encodeURIComponent(type);
        return this._request(`/api/facilities/${fid}/sub-entities?type=${t}`);
    },

    // ── Logs ─────────────────────────────────────────────────

    /** Retrieve recent event log entries. */
    async getLogs(limit = 50) {
        return this._request(`/api/logs?limit=${limit}`);
    },

    // ── Settings ─────────────────────────────────────────────

    /** Get notification settings. */
    async getSettings() {
        return this.getMe();
    },

    /** Update notification settings. */
    async updateSettings(data) {
        return this.updateUserSettings(data);
    },

    /** Send a test SMS notification. */
    async testNotification() {
        return this._request('/api/notifications/test', {
            method: 'POST',
        });
    },

    // ── Auth ─────────────────────────────────────────────────
    async register(data) {
        return this._request('/api/auth/register', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    async requestOTP(username) {
        return this._request('/api/auth/request-otp', {
            method: 'POST',
            body: JSON.stringify({ username }),
        });
    },

    async verifyOTP(username, code) {
        return this._request('/api/auth/verify-otp', {
            method: 'POST',
            body: JSON.stringify({ username, code }),
        });
    },

    async logout() {
        return this._request('/api/auth/logout', {
            method: 'POST',
        });
    },

    async getMe() {
        return this._request('/api/auth/me');
    },

    async updateUserSettings(data) {
        return this._request('/api/auth/settings', {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    },

    // ── Status ───────────────────────────────────────────────

    /** Get engine status. */
    async getStatus() {
        return this._request('/api/status');
    },

    // ── SSE ──────────────────────────────────────────────────

    /**
     * Connect to the Server-Sent Events stream.
     * @param {function} onEvent  Callback receiving { type, data } for each event.
     * @returns {EventSource} The live EventSource instance (caller can close it).
     */
    connectSSE(onEvent) {
        const url = `${this._base}/api/events`;
        const source = new EventSource(url);

        const eventTypes = [
            'availability_found',
            'check_complete',
            'sms_sent',
            'error',
            'watch_status_change',
        ];

        // Listen to each named event type
        eventTypes.forEach(type => {
            source.addEventListener(type, (e) => {
                let data;
                try {
                    data = JSON.parse(e.data);
                } catch {
                    data = e.data;
                }
                onEvent({ type, data });
            });
        });

        // Also handle unnamed "message" events as generic
        source.onmessage = (e) => {
            let data;
            try {
                data = JSON.parse(e.data);
            } catch {
                data = e.data;
            }
            onEvent({ type: 'message', data });
        };

        source.onerror = () => {
            onEvent({ type: 'sse_error', data: { message: 'SSE connection lost. Reconnecting…' } });
        };

        return source;
    },
};
