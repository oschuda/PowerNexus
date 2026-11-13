/* PV-Wechselrichter: Live-Werte, Ertrag, Verlauf, Vergleich mit der USV, MQTT-Anbindung (Admin). */
(function () {
    'use strict';
    var A = window.Auto;
    var days = 1;
    var settingsState = null;
    var FIELD_UNITS = { ac_power_w: 'W', dc_power_w: 'W', grid_feed_in_w: 'W', grid_consumption_w: 'W', pv_yield_day_wh: 'Wh',
                        pv_yield_total_wh: 'Wh', inverter_temperature: '°C', string_voltage_v: 'V', string_current_a: 'A',
                        inverter_status: '' };

    function num(value, digits) {
        if (value === null || value === undefined || value === '') { return '--'; }
        return Number(value).toLocaleString(A.lang(), { maximumFractionDigits: digits === undefined ? 0 : digits });
    }

    function energy(wh) {
        if (wh === null || wh === undefined) { return '--'; }
        return Math.abs(wh) >= 1000 ? num(wh / 1000, 2) + ' <small>kWh</small>' : num(wh) + ' <small>Wh</small>';
    }

    function when(ts) {
        return ts ? new Date(ts * 1000).toLocaleString(A.lang()) : '--';
    }

    function row(label, value) {
        return '<div class="data-row"><span class="data-label">' + A.esc(label) + '</span><span>' + value + '</span></div>';
    }

    function card(title, inner) {
        return '<div class="card"><h3>' + A.esc(title) + '</h3>' + inner + '</div>';
    }

    async function get(url) {
        return A.request('GET', url);
    }

    // ------------------------------------------------------------
    // Werte
    // ------------------------------------------------------------

    function statusBadge(status) {
        var s = status || {};
        if (!s.enabled) { return '<span class="auto-badge warn">' + A.esc(A.t('solar.status.off')) + '</span>'; }
        if (!s.connected) { return '<span class="auto-badge warn">' + A.esc(A.t('solar.status.disconnected')) + '</span>'; }
        return '<span class="auto-badge ok">' + A.esc(A.t('solar.status.connected')) + '</span>' +
            (s.available === false ? ' <span class="auto-badge warn">' + A.esc(A.t('solar.status.unavailable')) + '</span>' : '');
    }

    function renderCards(live, today, month) {
        var d = live.data || {};
        var direction = { EXPORT: A.t('solar.grid.export'), IMPORT: A.t('solar.grid.import'), BALANCED: A.t('solar.grid.balanced') };
        var source = live.live ? A.t('solar.source.live') : (d.timestamp ? A.t('solar.source.stored', { time: when(d.timestamp) }) : A.t('solar.no_data'));
        document.getElementById('solarCards').innerHTML =
            card('☀️ ' + A.t('solar.card.power'),
                 '<div class="value">' + num(d.ac_power_w) + ' <small>W</small></div>' +
                 row(A.t('solar.field.dc_power_w'), num(d.dc_power_w) + ' W') +
                 row(A.t('solar.field.inverter_status'), A.esc(d.inverter_status || '--')) +
                 '<div class="auto-sub">' + A.esc(source) + '</div>') +
            card('🔌 ' + A.t('solar.card.grid'),
                 '<div class="value">' + A.esc(direction[d.grid_direction] || '--') + '</div>' +
                 row(A.t('solar.field.grid_feed_in_w'), num(d.grid_feed_in_w) + ' W') +
                 row(A.t('solar.field.grid_consumption_w'), num(d.grid_consumption_w) + ' W')) +
            card('📅 ' + A.t('solar.card.today'),
                 '<div class="value">' + energy(today.yield_wh) + '</div>' +
                 row(A.t('solar.peak'), num(today.peak_ac_power_w) + ' W') +
                 row(A.t('solar.active_ratio'), num((today.pv_active_ratio || 0) * 100) + ' %') +
                 row(A.t('solar.card.month', { month: month.month || '' }), energy(month.yield_wh))) +
            card('🌡️ ' + A.t('solar.card.inverter'),
                 row(A.t('solar.field.inverter_temperature'), num(d.inverter_temperature, 1) + ' °C') +
                 row(A.t('solar.field.string_voltage_v'), num(d.string_voltage_v, 1) + ' V') +
                 row(A.t('solar.field.string_current_a'), num(d.string_current_a, 2) + ' A') +
                 row(A.t('solar.field.pv_yield_total_wh'), energy(d.pv_yield_total_wh)) +
                 row(A.t('solar.inverter'), A.esc(d.inverter_name || '--')));
        document.getElementById('solarStatus').innerHTML = statusBadge(live.status);
    }

    var LAYOUT = {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#94a3b8', size: 11 },
        height: 300, margin: { l: 55, r: 60, t: 10, b: 40 }, hovermode: 'x unified', legend: { orientation: 'h', y: -0.22 },
        xaxis: { type: 'date', gridcolor: 'rgba(255,255,255,0.05)' },
        yaxis: { title: { text: 'W' }, gridcolor: 'rgba(255,255,255,0.05)', zeroline: false }
    };

    function line(x, y, name, color, extra) {
        return Object.assign({ x: x, y: y, name: name, type: 'scatter', mode: 'lines', connectgaps: false,
                               line: { color: color, width: 1.6 } }, extra || {});
    }

    async function loadSeries() {
        if (!window.Plotly) { _plotMessage('solarChart', 300, A.t('chart.lib_failed')); return; }
        var d;
        try { d = await get('/api/solar/history?days=' + days); }
        catch (e) { _plotMessage('solarChart', 300, e.message); return; }
        if (!d.count) { _plotMessage('solarChart', 300, A.t('solar.no_data')); return; }
        var x = d.timestamp.map(function (ts) { return new Date(ts * 1000); });
        var traces = [
            line(x, d.ac_power_w, A.t('solar.field.ac_power_w'), '#f59e0b', { fill: 'tozeroy', fillcolor: 'rgba(245,158,11,0.12)' }),
            line(x, d.grid_feed_in_w, A.t('solar.field.grid_feed_in_w'), '#22c55e'),
            line(x, d.grid_consumption_w, A.t('solar.field.grid_consumption_w'), '#ef4444')
        ].filter(function (t) { return t.y.some(function (v) { return v !== null; }); });
        var temps = d.inverter_temperature.some(function (v) { return v !== null; });
        if (temps) { traces.push(line(x, d.inverter_temperature, A.t('solar.field.inverter_temperature'), '#a855f7', { yaxis: 'y2', line: { color: '#a855f7', width: 1, dash: 'dot' } })); }
        var layout = Object.assign({}, LAYOUT);
        if (temps) { layout.yaxis2 = { title: { text: '°C' }, overlaying: 'y', side: 'right', showgrid: false, zeroline: false }; }
        _plotDraw('solarChart', 300, traces, layout);
        document.getElementById('solarChartInfo').textContent = A.t('solar.points', { count: d.count }) +
            (d.bucket_seconds ? ' · ' + A.t('solar.averaged', { minutes: d.bucket_seconds / 60 }) : '');
    }

    async function loadCorrelation() {
        if (!window.Plotly) { return; }
        var select = document.getElementById('solarUpsSelect');
        var d;
        try { d = await get('/api/solar/correlation?days=' + Math.max(days, 2) + (select && select.value ? '&profile=' + encodeURIComponent(select.value) : '')); }
        catch (e) { _plotMessage('solarCorrChart', 300, e.message); return; }
        var info = document.getElementById('solarCorrInfo');
        if (!d.timestamp.length) { _plotMessage('solarCorrChart', 300, A.t('solar.no_data')); info.textContent = ''; return; }
        var x = d.timestamp.map(function (ts) { return new Date(ts * 1000); });
        var battery = d.on_battery.map(function (b, i) { return b ? (d.ac_power_w[i] || 0) : null; });
        var traces = [
            line(x, d.ac_power_w, A.t('solar.field.ac_power_w'), '#f59e0b'),
            line(x, d.input_voltage, A.t('dash.voltage'), '#3b82f6', { yaxis: 'y2' }),
            line(x, d.ups_load, A.t('solar.ups_load'), '#14b8a6', { yaxis: 'y3', line: { color: '#14b8a6', width: 1, dash: 'dot' } })
        ];
        if (battery.some(function (v) { return v !== null; })) {
            traces.push({ x: x, y: battery, name: A.t('solar.on_battery'), type: 'scatter', mode: 'markers', marker: { color: '#ef4444', size: 7 } });
        }
        var layout = Object.assign({}, LAYOUT, {
            yaxis2: { title: { text: 'V' }, overlaying: 'y', side: 'right', showgrid: false, zeroline: false },
            yaxis3: { overlaying: 'y', side: 'right', showgrid: false, zeroline: false, visible: false, range: [0, 100] }
        });
        _plotDraw('solarCorrChart', 300, traces, layout);
        var s = d.summary;
        info.textContent = A.t('solar.corr.summary', {
            ups: d.profile || '--', pairs: s.pairs,
            r: s.voltage_pv_correlation === null ? '--' : num(s.voltage_pv_correlation, 2),
            peak: num(s.feed_in_peak_w), battery: s.on_battery_with_pv
        });
    }

    async function loadEvents() {
        var list = document.getElementById('solarEvents');
        try {
            var d = await get('/api/solar/events?days=30');
            if (!d.events.length) { list.innerHTML = '<div class="auto-sub">' + A.esc(A.t('solar.events.none')) + '</div>'; return; }
            list.innerHTML = d.events.map(function (e) {
                return row(when(e.timestamp), (e.severity === 'warning' ? '⚠️ ' : 'ℹ️ ') + A.esc(A.t('solar.event.' + e.type, e.params)));
            }).join('');
        } catch (e) { list.textContent = e.message; }
    }

    async function loadValues() {
        try {
            var results = await Promise.all([get('/api/solar/live'), get('/api/solar/day'), get('/api/solar/month')]);
            renderCards(results[0], results[1], results[2]);
        } catch (e) {
            document.getElementById('solarCards').innerHTML = '<div class="card auto-empty">' + A.esc(e.message) + '</div>';
        }
    }

    // ------------------------------------------------------------
    // Einstellungen (nur Admin)
    // ------------------------------------------------------------

    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function fieldOptions(selected) {
        return '<option value="">' + A.esc(A.t('solar.mapping.auto')) + '</option>' + settingsState.fields.map(function (f) {
            return '<option value="' + A.esc(f) + '"' + (f === selected ? ' selected' : '') + '>' + A.esc(A.t('solar.field.' + f)) + '</option>';
        }).join('');
    }

    function sensorsHtml() {
        var sensors = settingsState.sensors;
        if (!sensors.length) { return '<div class="auto-sub">' + A.esc(A.t('solar.sensors.none')) + '</div>'; }
        return '<table class="data-table"><thead><tr><th>' + A.esc(A.t('solar.sensors.name')) + '</th><th>' + A.esc(A.t('solar.sensors.topic')) +
            '</th><th>' + A.esc(A.t('solar.sensors.value')) + '</th><th>' + A.esc(A.t('solar.sensors.field')) + '</th></tr></thead><tbody>' +
            sensors.map(function (s) {
                return '<tr><td>' + A.esc(s.name) + (s.device_name ? '<br><small>' + A.esc(s.device_name) + '</small>' : '') + '</td>' +
                    '<td><code>' + A.esc(s.reference) + '</code>' + (s.reference !== s.state_topic ? '<br><small>' + A.esc(s.state_topic) + '</small>' : '') +
                    '</td><td>' + A.esc(s.value === null ? '--' : s.value + ' ' + s.unit) + (s.available ? '' : ' ⏸️') + '</td>' +
                    '<td>' + A.esc(s.field ? A.t('solar.field.' + s.field) : '–') + '</td></tr>';
            }).join('') + '</tbody></table>';
    }

    function mappingHtml() {
        var mapping = settingsState.settings.mapping || {};
        var topics = settingsState.sensors.map(function (s) { return s.reference; });
        return '<datalist id="solarTopics">' + topics.map(function (t) { return '<option value="' + A.esc(t) + '">'; }).join('') + '</datalist>' +
            settingsState.fields.map(function (f) {
                return field(A.t('solar.field.' + f) + (FIELD_UNITS[f] ? ' (' + FIELD_UNITS[f] + ')' : ''),
                             '<input class="solarMap" data-field="' + A.esc(f) + '" list="solarTopics" autocomplete="off" placeholder="' +
                             A.esc(A.t('solar.mapping.auto')) + '" value="' + A.esc(mapping[f] || '') + '">');
            }).join('');
    }

    function renderSettings() {
        var s = settingsState.settings, st = settingsState.status;
        var profiles = '<option value="">' + A.esc(A.t('pred.active_profile')) + '</option>' + settingsState.profiles.map(function (p) {
            return '<option value="' + A.esc(p.id) + '"' + (p.id === s.ups_profile ? ' selected' : '') + '>' + A.esc(p.name) + '</option>';
        }).join('');
        document.getElementById('solarForm').innerHTML =
            '<div class="auto-sub">' + A.esc(A.t('solar.settings.hint')) + '</div>' +
            '<label class="auto-check"><input type="checkbox" id="slEnabled"' + (s.enabled ? ' checked' : '') + '> ' + A.esc(A.t('solar.settings.enable')) + '</label> ' +
            statusBadge(st) + (st.error ? ' <span class="auto-badge warn">' + A.esc(st.error) + '</span>' : '') +
            (st.connected ? ' <span class="auto-sub">' + A.esc(A.t('solar.status.details', { sensors: st.sensors, fields: st.fields.length })) + '</span>' : '') +
            field(A.t('solar.settings.host'), '<input id="slHost" autocomplete="off" placeholder="mqtt.example.lan" value="' + A.esc(s.host) + '">') +
            field(A.t('solar.settings.port'), '<input id="slPort" type="number" min="1" max="65535" value="' + A.esc(s.port) + '">') +
            '<label class="auto-check"><input type="checkbox" id="slTls"' + (s.tls ? ' checked' : '') + '> ' + A.esc(A.t('solar.settings.tls')) + '</label> ' +
            '<label class="auto-check"><input type="checkbox" id="slVerify"' + (s.tls_verify ? ' checked' : '') + '> ' + A.esc(A.t('solar.settings.tls_verify')) + '</label>' +
            field(A.t('solar.settings.username'), '<input id="slUser" autocomplete="off" value="' + A.esc(s.username) + '">') +
            field(A.t('solar.settings.password'), '<input id="slPassword" type="password" autocomplete="new-password" placeholder="' +
                  A.esc(s.has_password ? A.t('targets.field.password_keep') : '') + '">') +
            field(A.t('solar.settings.prefix'), '<input id="slPrefix" autocomplete="off" value="' + A.esc(s.discovery_prefix) + '">') +
            field(A.t('solar.settings.device'), '<input id="slDevice" autocomplete="off" value="' + A.esc(s.device) + '">') +
            field(A.t('solar.settings.sample'), '<input id="slSample" type="number" min="10" max="3600" value="' + A.esc(s.sample_seconds) + '">') +
            field(A.t('solar.settings.ups'), '<select id="slUps">' + profiles + '</select>') +
            '<details><summary>' + A.esc(A.t('solar.mapping.title')) + '</summary><div class="auto-sub">' + A.esc(A.t('solar.mapping.hint')) + '</div>' +
            mappingHtml() + '</details>' +
            '<p><a href="#" data-help="solar">' + A.esc(A.t('help.page.solar')) + '</a></p>' +
            '<div class="auto-actions"><button class="chart-btn" type="button" id="slTest">' + A.esc(A.t('solar.settings.test')) + '</button> ' +
            '<button class="chart-btn auto-primary" type="button" id="slSave">' + A.esc(A.t('common.save')) + '</button></div>' +
            '<div id="slResult" class="auto-result"></div>' +
            '<h4>' + A.esc(A.t('solar.sensors.title')) + '</h4>' + sensorsHtml();
        document.getElementById('slSave').addEventListener('click', save);
        document.getElementById('slTest').addEventListener('click', test);
    }

    function formData() {
        var mapping = {};
        Array.prototype.forEach.call(document.querySelectorAll('.solarMap'), function (el) {
            if (el.value.trim()) { mapping[el.dataset.field] = el.value.trim(); }
        });
        var data = {
            enabled: document.getElementById('slEnabled').checked, host: document.getElementById('slHost').value.trim(),
            port: Number(document.getElementById('slPort').value), tls: document.getElementById('slTls').checked,
            tls_verify: document.getElementById('slVerify').checked, username: document.getElementById('slUser').value.trim(),
            discovery_prefix: document.getElementById('slPrefix').value.trim(), device: document.getElementById('slDevice').value.trim(),
            sample_seconds: Number(document.getElementById('slSample').value), ups_profile: document.getElementById('slUps').value,
            mapping: mapping
        };
        var password = document.getElementById('slPassword').value;
        if (password) { data.password = password; }
        return data;
    }

    async function loadSettings() {
        // Nur für Admins; der Server entscheidet, ohne Berechtigung bleibt der Bereich verborgen
        var box = document.getElementById('solarSettingsBox');
        var response = await fetch('/api/solar/settings');
        if (response.status === 401 || response.status === 403) { box.style.display = 'none'; return; }
        box.style.display = '';
        var data = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            document.getElementById('solarForm').innerHTML = '<div class="card auto-empty">' + A.esc(data.error || ('HTTP ' + response.status)) + '</div>';
            return;
        }
        settingsState = data;
        renderSettings();
    }

    async function save() {
        try {
            settingsState = await A.request('PUT', '/api/solar/settings', formData());
            renderSettings();
            A.message(A.t('common.saved'), 'success');
            // Die ersten Sensoren kommen nach dem Verbinden; dann die Liste noch einmal holen
            setTimeout(loadSettings, 4000);
        } catch (e) { document.getElementById('slResult').textContent = e.message; }
    }

    async function test() {
        var result = document.getElementById('slResult');
        result.textContent = A.t('solar.settings.testing');
        try { result.textContent = (await A.request('POST', '/api/solar/test', formData())).message; }
        catch (e) { result.textContent = e.message; }
    }

    // ------------------------------------------------------------

    function populateUps() {
        var select = document.getElementById('solarUpsSelect');
        if (select.options.length > 1) { return Promise.resolve(); }
        return get('/api/profiles').then(function (profiles) {
            profiles.filter(function (p) { return p.enabled; }).forEach(function (p) {
                var option = document.createElement('option');
                option.value = p.name;
                option.textContent = p.display_name || p.name;
                select.appendChild(option);
            });
        }).catch(function () {});
    }

    function setDays(value, button) {
        days = value;
        document.querySelectorAll('#solarDays .chart-btn').forEach(function (b) { b.classList.remove('active'); });
        button.classList.add('active');
        loadSeries();
        loadCorrelation();
    }

    function init() {
        document.querySelectorAll('#solarDays .chart-btn').forEach(function (button) {
            button.addEventListener('click', function () { setDays(Number(button.dataset.days), button); });
        });
        document.getElementById('solarRefresh').addEventListener('click', load);
        document.getElementById('solarUpsSelect').addEventListener('change', loadCorrelation);
    }

    function load() {
        loadValues();
        loadSeries();
        populateUps().then(loadCorrelation);
        loadEvents();
        loadSettings().catch(function () {});
    }

    document.addEventListener('DOMContentLoaded', init);
    window.AutoSolar = { load: load };
})();
