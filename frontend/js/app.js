/**
 * RecHunter — Main Application Controller
 */
(function () {
    'use strict';

    // ── State ────────────────────────────────────────────────
    let watches = [];
    let eventSource = null;
    let statusInterval = null;
    let countdownInterval = null;
    let searchTimeout = null;
    const MAX_ACTIVITY_ITEMS = 100;

    // ── DOM Cache ────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ── Initialisation ───────────────────────────────────────
    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        bindNavigation();
        bindModal();
        bindSettings();
        bindWatchActions();

        // Initial data load
        await Promise.all([
            loadWatches(),
            loadLogs(),
            loadStatus(),
        ]);

        // Real-time connections
        connectSSE();
        startStatusPolling();
        startCountdownTimers();
    }

    // ── Navigation ───────────────────────────────────────────
    function bindNavigation() {
        $$('.nav-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                $$('.nav-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                const target = tab.dataset.tab;
                $$('.view').forEach(v => v.classList.remove('active'));
                $(`#view-${target}`).classList.add('active');
            });
        });
    }

    // ── Watch Loading ────────────────────────────────────────
    async function loadWatches() {
        try {
            watches = await API.getWatches();
            renderWatchGrid();
        } catch (err) {
            Components.renderToast(`Failed to load watches: ${err.message}`, 'error');
        }
    }

    function renderWatchGrid() {
        const grid = $('#watch-grid');
        const empty = $('#empty-state');

        if (!watches || watches.length === 0) {
            grid.innerHTML = '';
            if (empty) {
                grid.appendChild(empty);
                empty.style.display = '';
            }
            return;
        }

        // Remove empty state
        if (empty) empty.style.display = 'none';

        grid.innerHTML = watches.map(w => Components.renderWatchCard(w)).join('');
    }

    function updateWatchCard(watchId, updatedWatch) {
        const idx = watches.findIndex(w => w.id === watchId);
        if (idx >= 0) {
            watches[idx] = { ...watches[idx], ...updatedWatch };
        }
        const card = $(`.watch-card[data-watch-id="${watchId}"]`);
        if (card) {
            const tmp = document.createElement('div');
            tmp.innerHTML = Components.renderWatchCard(watches[idx] || updatedWatch);
            card.replaceWith(tmp.firstElementChild);
        }
    }

    // ── Watch Actions (delegated) ────────────────────────────
    function bindWatchActions() {
        document.addEventListener('click', async (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;

            const action = btn.dataset.action;
            const id = btn.dataset.id;

            if (action === 'check') {
                btn.classList.add('loading');
                try {
                    await API.triggerCheck(id);
                    Components.renderToast('Check triggered', 'success');
                } catch (err) {
                    Components.renderToast(`Check failed: ${err.message}`, 'error');
                }
                btn.classList.remove('loading');
            }

            if (action === 'toggle') {
                const watch = watches.find(w => w.id === id);
                if (!watch) return;
                const newStatus = watch.status === 'paused' ? 'active' : 'paused';
                try {
                    const updated = await API.updateWatch(id, { status: newStatus });
                    updateWatchCard(id, updated);
                    Components.renderToast(`Watch ${newStatus === 'paused' ? 'paused' : 'resumed'}`, 'info');
                } catch (err) {
                    Components.renderToast(`Failed: ${err.message}`, 'error');
                }
            }

            if (action === 'delete') {
                if (!confirm('Delete this watch?')) return;
                try {
                    await API.deleteWatch(id);
                    watches = watches.filter(w => w.id !== id);
                    renderWatchGrid();
                    Components.renderToast('Watch deleted', 'info');
                } catch (err) {
                    Components.renderToast(`Delete failed: ${err.message}`, 'error');
                }
            }
        });

        // Empty state add button
        document.addEventListener('click', (e) => {
            if (e.target.closest('#btn-add-watch-empty')) {
                openModal();
            }
        });
    }

    // ── Activity Feed ────────────────────────────────────────
    async function loadLogs() {
        try {
            const logs = await API.getLogs(50);
            const feed = $('#activity-feed');
            const emptyMsg = $('#activity-empty');
            if (logs && logs.length > 0) {
                if (emptyMsg) emptyMsg.style.display = 'none';
                feed.innerHTML = logs.map(l => Components.renderActivityItem(l)).join('');
                bindActivityExpanders();
            }
        } catch (err) {
            // Silent — feed is supplementary
        }
    }

    function addActivityItem(event) {
        const feed = $('#activity-feed');
        const emptyMsg = $('#activity-empty');
        if (emptyMsg) emptyMsg.style.display = 'none';

        const tmp = document.createElement('div');
        tmp.innerHTML = Components.renderActivityItem(event);
        const item = tmp.firstElementChild;

        feed.prepend(item);

        // Limit items
        while (feed.children.length > MAX_ACTIVITY_ITEMS) {
            feed.lastElementChild.remove();
        }

        bindActivityExpanders();
    }

    function bindActivityExpanders() {
        $$('.activity-item-main.has-details').forEach(el => {
            // Avoid double-binding
            if (el.dataset.bound) return;
            el.dataset.bound = '1';
            el.style.cursor = 'pointer';
            el.addEventListener('click', () => {
                const detailId = el.dataset.detailId;
                const detail = document.getElementById(detailId);
                if (!detail) return;
                const isOpen = detail.style.display !== 'none';
                detail.style.display = isOpen ? 'none' : 'block';
                const arrow = el.querySelector('.activity-expand');
                if (arrow) arrow.textContent = isOpen ? '▸' : '▾';
            });
        });
    }

    // ── Modal ────────────────────────────────────────────────
    function bindModal() {
        $('#btn-add-watch').addEventListener('click', openModal);
        $('#modal-close').addEventListener('click', closeModal);
        $('#btn-cancel-watch').addEventListener('click', closeModal);
        $('#modal-backdrop').addEventListener('click', (e) => {
            if (e.target === $('#modal-backdrop')) closeModal();
        });

        // ESC to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && $('#modal-backdrop').classList.contains('open')) {
                closeModal();
            }
        });

        // Mode selector → toggle drop time group
        $$('input[name="watch-mode"]').forEach(radio => {
            radio.addEventListener('change', () => {
                const dropGroup = $('#drop-time-group');
                const pollGroup = $('#poll-interval-group');
                if (radio.value === 'drop_time') {
                    dropGroup.style.display = '';
                    pollGroup.style.display = '';
                } else if (radio.value === 'one_shot') {
                    dropGroup.style.display = 'none';
                    pollGroup.style.display = 'none';
                } else {
                    dropGroup.style.display = 'none';
                    pollGroup.style.display = '';
                }
            });
        });

        // Poll interval slider label
        $('#watch-poll-interval').addEventListener('input', (e) => {
            $('#poll-interval-value').textContent = e.target.value;
        });

        // Facility search
        $('#watch-facility-search').addEventListener('input', (e) => {
            const q = e.target.value.trim();
            clearTimeout(searchTimeout);
            if (q.length < 2) {
                $('#search-results').style.display = 'none';
                $('#search-spinner').style.display = 'none';
                return;
            }
            $('#search-spinner').style.display = '';
            searchTimeout = setTimeout(() => searchFacilities(q), 300);
        });

        // Clear facility
        $('#clear-facility').addEventListener('click', () => {
            $('#watch-facility-id').value = '';
            $('#selected-facility').style.display = 'none';
            $('#watch-facility-search').value = '';
            $('#watch-facility-search').disabled = false;
            $('#watch-facility-search').focus();
            fetchAndPopulateSubEntities('', '');
        });

        // Watch type change
        $('#watch-type').addEventListener('change', () => {
            const fid = $('#watch-facility-id').value;
            const type = $('#watch-type').value;
            fetchAndPopulateSubEntities(fid, type);
        });

        // Create watch
        $('#btn-create-watch').addEventListener('click', handleCreateWatch);
    }

    function openModal() {
        $('#modal-backdrop').classList.add('open');
        document.body.style.overflow = 'hidden';
        // Reset form
        $('#add-watch-form').reset();
        $('#watch-facility-id').value = '';
        $('#selected-facility').style.display = 'none';
        $('#search-results').style.display = 'none';
        $('#watch-facility-search').disabled = false;
        $('#drop-time-group').style.display = '';
        $('#poll-interval-group').style.display = '';
        $('#poll-interval-value').textContent = '5';
        fetchAndPopulateSubEntities('', '');
    }

    function closeModal() {
        $('#modal-backdrop').classList.remove('open');
        document.body.style.overflow = '';
    }

    async function searchFacilities(query) {
        try {
            const results = await API.searchFacilities(query);
            const container = $('#search-results');
            $('#search-spinner').style.display = 'none';

            let customResultsHTML = '';
            if (/^\d+$/.test(query)) {
                customResultsHTML = `
                    <div class="search-result-item" data-facility-id="${query}" data-facility-name="Facility #${query}" data-facility-parent="Direct ID Entry">
                        <div class="search-result-name">Direct ID Entry: #${query}</div>
                        <div class="search-result-type">Use this exact ID if you know it is correct</div>
                    </div>
                `;
            }

            if (results.length === 0 && !customResultsHTML) {
                container.innerHTML = '<div class="search-no-results">No facilities found</div>';
            } else {
                container.innerHTML = customResultsHTML + results.map(r => Components.renderSearchResult(r)).join('');
            }
            container.style.display = '';

            // Bind clicks
            container.querySelectorAll('.search-result-item').forEach(item => {
                item.addEventListener('click', () => {
                    const fid = item.dataset.facilityId;
                    const fname = item.dataset.facilityName;
                    const fparent = item.dataset.facilityParent;
                    const ftype = item.dataset.facilityType || '';

                    $('#watch-facility-id').value = fid;
                    $('#selected-facility-name').textContent = fname;
                    $('#selected-facility-meta').textContent = fparent ? `${fparent} · #${fid}` : `#${fid}`;
                    $('#selected-facility').style.display = 'flex';
                    $('#watch-facility-search').disabled = true;
                    container.style.display = 'none';

                    // Auto-fill watch name if empty or generic
                    if (!$('#watch-name').value || $('#watch-name').value.startsWith('Facility #')) {
                        $('#watch-name').value = fname;
                    }

                    // Auto-select type
                    let mappedType = '';
                    if (ftype.includes('campground') || fname.toLowerCase().includes('campground')) mappedType = 'campground';
                    else if (ftype.includes('permit') || fname.toLowerCase().includes('permit') || fname.toLowerCase().includes('wilderness')) mappedType = 'permit';
                    else if (ftype.includes('timed') || ftype.includes('ticket') || ftype.includes('tour') || fname.toLowerCase().includes('timed entry') || fname.toLowerCase().includes('tour')) mappedType = 'timed_entry';

                    if (mappedType) {
                        $('#watch-type').value = mappedType;
                    }

                    // Fetch sub-entities
                    fetchAndPopulateSubEntities(fid, mappedType || $('#watch-type').value);
                });
            });
        } catch (err) {
            $('#search-spinner').style.display = 'none';
            Components.renderToast(`Search failed: ${err.message}`, 'error');
        }
    }

    async function fetchAndPopulateSubEntities(facilityId, type) {
        const group = $('#watch-sub-entity-group');
        const listContainer = $('#watch-sub-entity-list');

        if (!facilityId || (type !== 'permit' && type !== 'timed_entry')) {
            group.style.display = 'none';
            if (listContainer) listContainer.innerHTML = '';
            return;
        }

        try {
            if (listContainer) listContainer.innerHTML = '<div style="padding: 10px; color: var(--text-secondary);">Loading entrances/tours…</div>';
            group.style.display = '';

            const subEntities = await API.getSubEntities(facilityId, type);

            if (!subEntities || subEntities.length === 0) {
                group.style.display = 'none';
                if (listContainer) listContainer.innerHTML = '';
                return;
            }

            if (listContainer) {
                listContainer.innerHTML = subEntities.map(se => `
                    <label class="sub-entity-checkbox-item">
                        <input type="checkbox" name="watch-sub-entity-val" value="${Components.esc(se.id)}">
                        <span>${Components.esc(se.name)}</span>
                    </label>
                `).join('');
            }
            group.style.display = '';
        } catch (err) {
            console.error('Failed to load sub-entities:', err);
            group.style.display = 'none';
            if (listContainer) listContainer.innerHTML = '';
        }
    }

    async function handleCreateWatch() {
        const facilityId = $('#watch-facility-id').value;
        const name = $('#watch-name').value.trim();
        const type = $('#watch-type').value;
        const dateStart = $('#watch-date-start').value;
        const dateEnd = $('#watch-date-end').value || undefined;
        const mode = $('input[name="watch-mode"]:checked').value;
        const pollInterval = parseInt($('#watch-poll-interval').value, 10);
        const dropTime = mode === 'drop_time' ? $('#watch-drop-time').value : undefined;

        const minNights = parseInt($('#watch-min-nights').value, 10) || 1;
        const equipment = $('#watch-equipment').value.trim() || undefined;

        // Validate
        if (!facilityId) {
            Components.renderToast('Please search and select a facility', 'warning');
            return;
        }
        if (!name) {
            Components.renderToast('Please enter a watch name', 'warning');
            return;
        }
        if (!type) {
            Components.renderToast('Please select a reservation type', 'warning');
            return;
        }
        if (!dateStart) {
            Components.renderToast('Please select a start date', 'warning');
            return;
        }

        const selectedBoxes = Array.from($$('input[name="watch-sub-entity-val"]:checked'));
        const selectedSubEntities = selectedBoxes.map(el => el.value);
        const selectedSubEntityNames = selectedBoxes.map(el => el.nextElementSibling.textContent.trim());

        const filters = {};
        if (minNights > 1) {
            filters.min_consecutive_nights = minNights;
        }
        if (equipment) {
            filters.equipment = equipment;
        }
        if (selectedSubEntities.length === 1) {
            filters.site_id = selectedSubEntities[0];
            filters.site_names = [selectedSubEntityNames[0]];
        } else if (selectedSubEntities.length > 1) {
            filters.site_ids = selectedSubEntities;
            filters.site_names = selectedSubEntityNames;
        }

        const data = {
            name,
            facility_id: facilityId,
            reservation_type: type,
            date_start: dateStart,
            date_end: dateEnd,
            mode,
            poll_interval_minutes: pollInterval,
            drop_time: dropTime,
            filters: Object.keys(filters).length > 0 ? filters : undefined,
        };

        const btn = $('#btn-create-watch');
        btn.disabled = true;
        btn.textContent = 'Creating…';

        try {
            const created = await API.createWatch(data);
            watches.push(created);
            renderWatchGrid();
            closeModal();
            Components.renderToast(`Watch "${name}" created!`, 'success');
        } catch (err) {
            Components.renderToast(`Failed: ${err.message}`, 'error');
        }

        btn.disabled = false;
        btn.textContent = '🌲 Create Watch';
    }

    // ── SSE ──────────────────────────────────────────────────
    function connectSSE() {
        if (eventSource) {
            eventSource.close();
        }
        eventSource = API.connectSSE(handleSSEEvent);
    }

    function handleSSEEvent(event) {
        const { type, data } = event;

        if (type === 'sse_error') {
            // Connection lost — will auto-reconnect via EventSource
            return;
        }

        // Add to activity feed
        if (data && data.event_type) {
            addActivityItem({
                id: Date.now().toString(),
                timestamp: data.timestamp || new Date().toISOString(),
                event_type: data.event_type,
                watch_id: data.watch_id,
                watch_name: data.watch_name,
                message: data.message || '',
                details: data.details || {},
            });
        }

        // Type-specific handling based on data.event_type (SSE sends all as 'message')
        const eventType = data && data.event_type;
        switch (eventType) {
            case 'availability_found':
                Components.renderToast(`🎉 Availability found: ${data.watch_name || 'Unknown'}`, 'success', 5000);
                if (data.watch_id) {
                    loadWatches(); // Refresh to show triggered state
                }
                break;

            case 'check_complete':
            case 'watch_completed':
                if (data.watch_id) {
                    const w = watches.find(w => w.id === data.watch_id);
                    if (w) {
                        updateWatchCard(data.watch_id, {
                            last_checked: new Date().toISOString(),
                        });
                    }
                }
                break;

            case 'sms_sent':
            case 'test_sms_sent':
                Components.renderToast(`📤 SMS sent: ${data.message || ''}`, 'info');
                break;

            case 'error':
                Components.renderToast(`Error: ${data.message || 'Unknown error'}`, 'error');
                break;

            case 'watch_created':
            case 'watch_updated':
            case 'watch_deleted':
                loadWatches(); // Full refresh for watch changes
                break;
        }
    }

    // ── Status Polling ───────────────────────────────────────
    async function loadStatus() {
        try {
            const status = await API.getStatus();
            updateEngineStatus(status);
            updateStatsBar(status);
        } catch {
            // offline state
            updateEngineStatus({ running: false });
        }
    }

    function updateEngineStatus(status) {
        const dot = $('#status-dot');
        const label = $('#status-label');
        const uptimeVal = $('#uptime-value');

        if (status.running) {
            dot.className = 'status-dot running';
            label.textContent = 'Engine Running';
            if (status.uptime_seconds != null) {
                uptimeVal.textContent = formatUptime(status.uptime_seconds);
            }
        } else {
            dot.className = 'status-dot stopped';
            label.textContent = 'Engine Stopped';
            uptimeVal.textContent = '--:--:--';
        }
    }

    function updateStatsBar(status) {
        const stats = Components.renderStatsBar(status);
        const totalEl = $('[data-stat="totalWatches"]');
        const activeEl = $('[data-stat="activeWatches"]');
        const checksEl = $('[data-stat="totalChecks"]');

        if (totalEl) animateNumber(totalEl, stats.totalWatches);
        if (activeEl) animateNumber(activeEl, stats.activeWatches);
        if (checksEl) animateNumber(checksEl, stats.totalChecks);
    }

    function animateNumber(el, target) {
        const current = parseInt(el.textContent, 10) || 0;
        if (current === target) return;

        const steps = 20;
        const increment = (target - current) / steps;
        let step = 0;

        const interval = setInterval(() => {
            step++;
            if (step >= steps) {
                el.textContent = target;
                clearInterval(interval);
            } else {
                el.textContent = Math.round(current + increment * step);
            }
        }, 30);
    }

    function formatUptime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function startStatusPolling() {
        statusInterval = setInterval(loadStatus, 10000);
    }

    // ── Countdown Timers ─────────────────────────────────────
    function startCountdownTimers() {
        countdownInterval = setInterval(() => {
            $$('.next-check').forEach(el => {
                const next = el.dataset.next;
                if (!next) return;
                const remaining = Math.max(0, Math.floor((new Date(next).getTime() - Date.now()) / 1000));
                el.textContent = Components.formatCountdown(remaining);
            });

            // Also update relative times
            $$('.last-checked').forEach(el => {
                const last = el.dataset.last;
                if (last) el.textContent = Components.relativeTime(last);
            });
        }, 1000);
    }

    // ── Settings ─────────────────────────────────────────────
    function bindSettings() {
        // Carrier custom toggle
        $('#setting-carrier').addEventListener('change', (e) => {
            const customGroup = $('#custom-gateway-group');
            const whatsappTip = $('#whatsapp-info-tip');
            const appPassHint = $('label[for="setting-app-password"]').parentElement.querySelector('.form-hint');
            const gmailHint = $('label[for="setting-gmail"]').parentElement.querySelector('.form-hint');

            customGroup.style.display = e.target.value === '' ? '' : 'none';

            if (e.target.value === 'whatsapp') {
                if (whatsappTip) {
                    whatsappTip.style.display = '';
                    whatsappTip.innerHTML = `💡 <strong>WhatsApp Setup:</strong> Add CallMeBot's number on WhatsApp and send <code>I allow callmebot to send me messages</code> to get your free API key. Enter that key in the <strong>App Password</strong> field above.`;
                }
                $('label[for="setting-gmail"]').textContent = 'Gmail Address (Optional)';
                $('label[for="setting-app-password"]').textContent = 'WhatsApp API Key (CallMeBot)';
                $('#setting-gmail').placeholder = 'your.email@gmail.com (optional)';
                $('#setting-app-password').placeholder = '6-digit key (e.g. 123456)';
                if (appPassHint) appPassHint.textContent = 'Enter the CallMeBot API key received on WhatsApp';
                if (gmailHint) gmailHint.textContent = 'Only needed if sending email notifications';
            } else if (e.target.value === 'telegram') {
                if (whatsappTip) {
                    whatsappTip.style.display = '';
                    whatsappTip.innerHTML = `💡 <strong>Telegram Setup:</strong> Message <code>@CallMeBot_txtbot</code> on Telegram and send <code>/start</code> to get your API Key. Enter your Telegram username (e.g. <code>@myusername</code>) in the <strong>Phone Number</strong> field, and the Telegram API key in the <strong>App Password</strong> field above.`;
                }
                $('label[for="setting-gmail"]').textContent = 'Gmail Address (Optional)';
                $('label[for="setting-app-password"]').textContent = 'Telegram API Key (CallMeBot)';
                $('#setting-gmail').placeholder = 'your.email@gmail.com (optional)';
                $('#setting-app-password').placeholder = 'CallMeBot API key';
                if (appPassHint) appPassHint.textContent = 'Enter the CallMeBot API key received on Telegram';
                if (gmailHint) gmailHint.textContent = 'Only needed if sending email notifications';
            } else {
                if (whatsappTip) whatsappTip.style.display = 'none';
                $('label[for="setting-gmail"]').textContent = 'Gmail Address';
                $('label[for="setting-app-password"]').textContent = 'App Password';
                $('#setting-gmail').placeholder = 'your.email@gmail.com';
                $('#setting-app-password').placeholder = '••••••••••••••••';
                if (appPassHint) appPassHint.textContent = 'Generate at myaccount.google.com → Security → App passwords';
                if (gmailHint) gmailHint.textContent = 'Used as the SMTP sender for SMS gateway';
            }
        });

        // Load settings when switching to tab
        $('#tab-settings').addEventListener('click', loadSettings);

        // Save settings
        $('#settings-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = gatherSettings();
            const btn = $('#btn-save-settings');
            btn.disabled = true;
            btn.textContent = 'Saving…';

            try {
                await API.updateSettings(data);
                Components.renderToast('Settings saved!', 'success');
            } catch (err) {
                Components.renderToast(`Save failed: ${err.message}`, 'error');
            }

            btn.disabled = false;
            btn.textContent = 'Save Settings';
        });

        // Test SMS
        $('#btn-test-sms').addEventListener('click', async () => {
            const btn = $('#btn-test-sms');
            btn.disabled = true;
            btn.textContent = '📤 Sending…';

            try {
                await API.testNotification();
                Components.renderToast('Test SMS sent!', 'success');
            } catch (err) {
                Components.renderToast(`Test failed: ${err.message}`, 'error');
            }

            btn.disabled = false;
            btn.textContent = '📤 Send Test SMS';
        });
    }

    async function loadSettings() {
        try {
            const s = await API.getSettings();
            if (s.gmail_address) $('#setting-gmail').value = s.gmail_address;
            // Don't prefill app password (it's masked server-side)
            if (s.phone_number) $('#setting-phone').value = s.phone_number;
            if (s.carrier_gateway) {
                const sel = $('#setting-carrier');
                const match = [...sel.options].find(o => o.value === s.carrier_gateway);
                if (match) {
                    sel.value = s.carrier_gateway;
                } else {
                    sel.value = '';
                    $('#custom-gateway-group').style.display = '';
                    $('#setting-custom-gateway').value = s.carrier_gateway;
                }
                // Trigger change event to update helper labels
                sel.dispatchEvent(new Event('change'));
            }
        } catch {
            // settings not yet configured — that's fine
        }
    }

    function gatherSettings() {
        const carrier = $('#setting-carrier').value || $('#setting-custom-gateway').value;
        const data = {
            gmail_address: $('#setting-gmail').value.trim(),
            phone_number: $('#setting-phone').value.trim(),
            carrier_gateway: carrier,
        };

        const appPass = $('#setting-app-password').value;
        if (appPass) {
            data.gmail_app_password = appPass;
        }
        return data;
    }

})();
