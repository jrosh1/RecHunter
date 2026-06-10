/**
 * RecHunter UI Components
 * Pure rendering functions that return HTML strings.
 */
const Components = {

    // ── Helpers ──────────────────────────────────────────────

    /**
     * Format a timestamp into a relative human string.
     * @param {string|number|Date} ts
     * @returns {string}
     */
    relativeTime(ts) {
        if (!ts) return '—';
        const now = Date.now();
        const then = new Date(ts).getTime();
        const diff = Math.max(0, Math.floor((now - then) / 1000));

        if (diff < 10) return 'just now';
        if (diff < 60) return `${diff}s ago`;
        const mins = Math.floor(diff / 60);
        if (mins < 60) return `${mins} min ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        return `${days}d ago`;
    },

    /**
     * Format seconds into a countdown string mm:ss or hh:mm:ss.
     * @param {number} seconds
     * @returns {string}
     */
    formatCountdown(seconds) {
        if (!seconds || seconds <= 0) return '—';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        const mm = String(m).padStart(2, '0');
        const ss = String(s).padStart(2, '0');
        if (h > 0) return `${h}:${mm}:${ss}`;
        return `${mm}:${ss}`;
    },

    /**
     * Escape HTML special characters.
     */
    esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    },

    // ── Status Colours ───────────────────────────────────────

    statusMeta: {
        active:    { color: 'var(--color-success)', label: 'Active',    dot: 'dot-active' },
        paused:    { color: 'var(--color-warning)', label: 'Paused',    dot: 'dot-paused' },
        triggered: { color: 'var(--color-success)', label: 'Triggered', dot: 'dot-triggered' },
        error:     { color: 'var(--color-error)',   label: 'Error',     dot: 'dot-error' },
        completed: { color: 'var(--color-muted)',   label: 'Completed', dot: 'dot-completed' },
    },

    modeLabels: {
        drop_time:    '⏱️ Drop Time',
        cancellation: '🔄 Cancellation',
        one_shot:     '🌲 One Shot',
    },

    typeLabels: {
        campground:   '🏕️ Campground',
        permit:       '🏔️ Permit',
        timed_entry:  '🎫 Timed Entry',
    },

    eventMeta: {
        availability_found: { color: 'var(--color-success)', icon: '✅', label: 'Availability' },
        check_complete:     { color: 'var(--color-primary)', icon: '🔍', label: 'Check' },
        sms_sent:           { color: '#a78bfa',              icon: '📤', label: 'SMS' },
        error:              { color: 'var(--color-error)',    icon: '❌', label: 'Error' },
        watch_status_change:{ color: 'var(--color-warning)',  icon: '🔄', label: 'Status' },
    },

    // ── Renderers ────────────────────────────────────────────

    /**
     * Render a coloured status badge pill.
     */
    renderStatusBadge(status) {
        const meta = this.statusMeta[status] || { color: 'var(--color-muted)', label: status, dot: '' };
        return `<span class="status-badge" style="--badge-color: ${meta.color}">
            <span class="status-badge-dot ${meta.dot}"></span>
            ${this.esc(meta.label)}
        </span>`;
    },

    /**
     * Render a single watch card.
     */
    renderWatchCard(watch) {
        const status = watch.status || 'active';
        const isPaused = status === 'paused';

        const dateRange = watch.date_end
            ? `${watch.date_start} → ${watch.date_end}`
            : watch.date_start;

        const typeClass = `type-${watch.reservation_type}`;

        return `
        <div class="watch-card status-${status}" data-watch-id="${this.esc(watch.id)}">
            <div class="watch-card-header">
                <span class="watch-card-title">${this.esc(watch.name)}</span>
                <span class="watch-card-status ${status}">${this.esc(status)}</span>
            </div>
            <div class="watch-card-meta">
                <span class="meta-badge ${typeClass}">${this.typeLabels[watch.reservation_type] || watch.reservation_type}</span>
                <span class="meta-badge">#${this.esc(watch.facility_id)}</span>
                <span class="meta-badge">${this.modeLabels[watch.mode] || watch.mode}</span>
            </div>
            <div class="watch-card-dates">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                ${this.esc(dateRange)}
                ${watch.mode === 'drop_time' && watch.drop_time ? ` · ⏰ ${this.esc(watch.drop_time)}` : ''}
            </div>
            ${(function() {
                const siteNames = watch.filters && watch.filters.site_names || [];
                const siteIds = watch.filters && (watch.filters.site_ids || (watch.filters.site_id ? [watch.filters.site_id] : [])) || [];
                const availableIds = watch.available_site_ids || [];
                
                const items = [];
                if (siteNames.length > 0) {
                    for (let i = 0; i < siteNames.length; i++) {
                        const name = siteNames[i];
                        const id = siteIds[i];
                        const normId = id ? String(id).replace(/\D/g, '') : '';
                        const isAvailable = availableIds.some(aid => String(aid).replace(/\D/g, '') === normId);
                        items.push({ name, id, isAvailable });
                    }
                } else if (siteIds.length > 0) {
                    for (let i = 0; i < siteIds.length; i++) {
                        const id = siteIds[i];
                        const normId = String(id).replace(/\D/g, '');
                        const isAvailable = availableIds.some(aid => String(aid).replace(/\D/g, '') === normId);
                        items.push({ name: `ID: ${id}`, id, isAvailable });
                    }
                }
                
                if (items.length > 0) {
                    return `<div class="watch-card-sites">
                        <span class="sites-label">Monitoring (${items.length}):</span>
                        <div class="sites-list">
                            ${items.map(item => {
                                const klass = item.isAvailable ? 'site-tag available' : 'site-tag unavailable';
                                return `<span class="${klass}" title="${Components.esc(item.name)}">${Components.esc(item.name)}</span>`;
                            }).join('')}
                        </div>
                    </div>`;
                }
                return '';
            })()}
            <div class="watch-card-timing">
                <div class="timing-item">
                    <span class="timing-label">Last Check</span>
                    <span class="timing-value last-checked" data-last="${watch.last_checked || ''}">${this.relativeTime(watch.last_checked)}</span>
                </div>
                <div class="timing-item">
                    <span class="timing-label">Interval</span>
                    <span class="timing-value">${watch.poll_interval_minutes || '—'} min</span>
                </div>
            </div>
            <div class="watch-card-actions">
                <button class="btn-icon check-btn" data-action="check" data-id="${this.esc(watch.id)}" title="Check Now">▶</button>
                <button class="btn-icon pause-btn" data-action="toggle" data-id="${this.esc(watch.id)}" title="${isPaused ? 'Resume' : 'Pause'}">${isPaused ? '▶' : '⏸'}</button>
                <button class="btn-icon delete-btn" data-action="delete" data-id="${this.esc(watch.id)}" title="Delete">🗑</button>
            </div>
        </div>`;
    },

    /**
     * Render an activity feed item.
     */
    renderActivityItem(event) {
        const hasDetails = event.details && Object.keys(event.details).length > 0;
        const detailId = `detail-${event.id || Date.now() + Math.random()}`;
        const eventType = event.event_type || 'check_complete';

        const time = event.timestamp ? new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'America/Los_Angeles' }) + ' PT' : '';

        return `
        <div class="activity-item" data-event-id="${this.esc(event.id)}">
            <span class="activity-timestamp">${time}</span>
            <span class="activity-badge ${eventType}">${this.esc(eventType.replace(/_/g, ' '))}</span>
            <div class="activity-content">
                ${event.watch_name ? `<div class="activity-watch-name">${this.esc(event.watch_name)}</div>` : ''}
                <div class="activity-message">${this.esc(event.message)}</div>
            </div>
        </div>`;
    },

    /**
     * Render a search result row for the facility search dropdown.
     */
    renderSearchResult(facility) {
        // RIDB returns PascalCase keys like FacilityID, FacilityName
        const fid = facility.FacilityID || facility.facility_id || '';
        const name = facility.FacilityName || facility.name || 'Unknown';
        const parent = facility.ParentOrgName || facility.parent_name || '';
        const fType = (facility.FacilityTypeDescription || facility.type || '').toLowerCase();
        
        const typeIcon = fType.includes('campground') ? '🏕️'
                       : fType.includes('permit') ? '🏔️'
                       : fType.includes('timed') ? '🎫' : '📍';
        return `
        <div class="search-result-item" 
             data-facility-id="${this.esc(fid)}"
             data-facility-name="${this.esc(name)}"
             data-facility-parent="${this.esc(parent)}"
             data-facility-type="${this.esc(fType)}">
            <div class="search-result-info">
                <span class="search-result-name">${typeIcon} ${this.esc(name)}</span>
                <span class="search-result-meta">${this.esc(parent)} · #${this.esc(fid)}</span>
            </div>
            <span class="search-result-add">Select →</span>
        </div>`;
    },

    /**
     * Render the stats bar content.
     */
    renderStatsBar(status) {
        // This updates values in-place via data attributes
        return {
            totalWatches: status.total_watches ?? 0,
            activeWatches: status.active_watches ?? 0,
            totalChecks: status.total_checks ?? 0,
        };
    },

    /**
     * Show a toast notification. Appends to #toast-container and auto-removes.
     * @param {string} message
     * @param {'success'|'error'|'info'|'warning'} type
     * @param {number} duration  ms before auto-dismiss (default 3000)
     */
    renderToast(message, type = 'info', duration = 3000) {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <span class="toast-msg">${this.esc(message)}</span>
            <button class="toast-close" aria-label="Dismiss">&times;</button>
        `;

        // Dismiss on click
        toast.querySelector('.toast-close').addEventListener('click', () => {
            toast.classList.add('toast-exit');
            setTimeout(() => toast.remove(), 300);
        });

        container.appendChild(toast);

        // Auto-dismiss
        setTimeout(() => {
            if (toast.parentNode) {
                toast.classList.add('toast-exit');
                setTimeout(() => toast.remove(), 300);
            }
        }, duration);
    },
};
