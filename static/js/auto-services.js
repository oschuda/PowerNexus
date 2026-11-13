/* Dienste: NUT-Server und Selbst-Shutdown (beide optional, Standard aus). */
(function () {
    'use strict';
    var A = window.Auto;
    var state = null;

    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function statusHtml(s) {
        if (s.error) { return '<span class="auto-badge warn">' + A.esc(A.t('services.status.error', { error: s.error })) + '</span>'; }
        if (!s.running) { return '<span class="auto-badge warn">' + A.esc(A.t('services.status.off')) + '</span>'; }
        var logins = Object.keys(s.logins).reduce(function (sum, name) { return sum + s.logins[name]; }, 0);
        return '<span class="auto-badge ok">' + A.esc(A.t('services.status.on', { clients: s.clients, logins: logins })) + '</span>' +
            (s.fsd.length ? ' <span class="auto-badge warn">' + A.esc(A.t('services.status.fsd', { ups: s.fsd.join(', ') })) + '</span> ' +
                '<button class="chart-btn" type="button" id="svClearFsd">' + A.esc(A.t('services.clear_fsd')) + '</button>' : '');
    }

    function render() {
        var nut = state.settings.nut;
        var boxes = state.profiles.map(function (p) {
            return '<label class="auto-check"><input type="checkbox" class="svUps" value="' + A.esc(p.id) + '"' +
                (nut.ups.indexOf(p.id) >= 0 ? ' checked' : '') + '> ' + A.esc(p.name) + ' <small>(' + A.esc(p.nut_name) + ')</small></label>';
        }).join('');
        document.getElementById('servicesForm').innerHTML =
            '<h4>' + A.esc(A.t('services.nut.title')) + '</h4>' +
            '<div class="auto-sub">' + A.esc(A.t('services.nut.hint')) + '</div>' +
            '<label class="auto-check"><input type="checkbox" id="svNut"' + (nut.enabled ? ' checked' : '') + '> ' + A.esc(A.t('services.nut.enable')) + '</label> ' + statusHtml(state.status) +
            '<div class="auto-sub">' + A.esc(A.t('services.nut.ups')) + '</div><div class="auto-checks">' + boxes + '</div>' +
            field(A.t('services.nut.networks'), '<textarea id="svNetworks" rows="2" placeholder="172.16.11.0/24">' + A.esc(nut.networks.join('\n')) + '</textarea>') +
            field(A.t('services.nut.port'), '<input id="svPort" type="number" min="1024" max="65535" value="' + A.esc(nut.port) + '">') +
            field(A.t('services.nut.username'), '<input id="svUser" autocomplete="off" value="' + A.esc(nut.username) + '">') +
            field(A.t('services.nut.password'), '<input id="svPassword" type="password" autocomplete="new-password" placeholder="' +
                A.esc(nut.has_password ? A.t('targets.field.password_keep') : '') + '">') +
            '<p><a href="#" data-help="nut">' + A.esc(A.t('help.page.nut')) + '</a></p>' +
            '<h4>' + A.esc(A.t('services.self.title')) + '</h4>' +
            '<div class="auto-sub">' + A.esc(A.t('services.self.hint')) + '</div>' +
            '<label class="auto-check"><input type="checkbox" id="svSelf"' + (state.settings.self_shutdown ? ' checked' : '') + '> ' + A.esc(A.t('services.self.enable')) + '</label>' +
            '<p><a href="#" data-help="self-shutdown">' + A.esc(A.t('help.page.self-shutdown')) + '</a></p>' +
            '<div class="auto-actions"><button class="chart-btn auto-primary" type="button" id="svSave">' + A.esc(A.t('common.save')) + '</button></div>' +
            '<div id="svResult" class="auto-result"></div>';
        document.getElementById('svSave').addEventListener('click', save);
        var clear = document.getElementById('svClearFsd');
        if (clear) { clear.addEventListener('click', clearFsd); }
    }

    function formData() {
        var data = { self_shutdown: document.getElementById('svSelf').checked,
                     nut: { enabled: document.getElementById('svNut').checked, port: Number(document.getElementById('svPort').value),
                            networks: document.getElementById('svNetworks').value, username: document.getElementById('svUser').value.trim(),
                            ups: Array.prototype.map.call(document.querySelectorAll('.svUps:checked'), function (el) { return el.value; }) } };
        var password = document.getElementById('svPassword').value;
        if (password) { data.nut.password = password; }
        return data;
    }

    async function load() {
        try { state = await A.request('GET', '/api/services'); render(); }
        catch (e) { document.getElementById('servicesForm').innerHTML = '<div class="card auto-empty">' + A.esc(e.message) + '</div>'; }
    }

    async function save() {
        try {
            state = await A.request('PUT', '/api/services', formData());
            render();
            A.message(A.t('common.saved'), 'success');
        } catch (e) { document.getElementById('svResult').textContent = e.message; }
    }

    async function clearFsd() {
        try { state = await A.request('POST', '/api/services/clear-fsd'); render(); }
        catch (e) { A.message(e.message, 'error'); }
    }

    window.AutoServices = { load: load };
})();
