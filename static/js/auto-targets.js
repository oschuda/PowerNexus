/* Zielrechner: Übersicht, Dialog (Felder je Schnittstelle), Verbindung testen. */
(function () {
    'use strict';
    var A = window.Auto;
    var current = null;           // gerade bearbeiteter Zielrechner (null = neu)
    var DEFAULT_PORTS = { ssh: 22, winrm: 5986, ipmi: 623, redfish: 443, http: 443, opcua: 4840 };
    var TYPES = ['ssh', 'winrm', 'redfish', 'ipmi', 'opcua', 'http', 'nut_fsd', 'local'];
    var VIRTUAL = ['nut_fsd', 'local'];   // ohne Adresse und Anmeldung
    var DETAIL_KEYS = ['redfish_version', 'system_id', 'model', 'power_state', 'reset_types', 'unsupported', 'status',
                       'security_policy', 'current_value', 'actual_type'];

    function badge(text, ok) {
        return '<span class="auto-badge ' + (ok ? 'ok' : 'warn') + '">' + A.esc(text) + '</span>';
    }

    function card(target) {
        var anonymous = target.type === 'opcua' && target.options.auth === 'anonymous';
        if (VIRTUAL.indexOf(target.type) >= 0) {
            return card2(target, [], A.esc(target.options.ups || (target.type === 'nut_fsd' ? A.t('scope.all') : '')));
        }
        var badges = [anonymous ? badge(A.t('opcua.auth.anonymous'), true)
                                : badge(target.has_password ? A.t('targets.badge.password') : A.t('targets.badge.no_password'),
                                        target.has_password)];
        if (target.type === 'ssh') {
            badges.push(badge(target.options.host_key ? A.t('targets.badge.fingerprint_ok')
                                                      : A.t('targets.badge.fingerprint_missing'),
                              !!target.options.host_key));
        }
        if (target.type === 'redfish' || target.type === 'http') {
            if (target.type === 'http' && target.options.scheme === 'http') {
                badges.push(badge(A.t('targets.badge.unencrypted'), false));
            } else {
                badges.push(badge(target.options.cert_fingerprint ? A.t('targets.badge.cert_pinned')
                                                                  : A.t('targets.badge.cert_ca'), true));
            }
        }
        if (target.type === 'opcua') {
            badges.push(badge(A.t('opcua.security.' + target.options.security), target.options.security !== 'none'));
        }
        var where = A.esc(target.address) + ':' + A.esc(target.port);
        if (target.type === 'http') {
            where = A.esc(target.options.method) + ' ' + A.esc(target.options.scheme) + '://' + where +
                A.esc(target.options.path);
        }
        if (target.type === 'opcua') {
            where += ' · ' + A.esc(target.options.node_id) + ' = ' + A.esc(target.options.value);
        }
        return card2(target, badges, where);
    }

    function card2(target, badges, where) {
        if (!target.enabled) { badges = badges.concat(badge(A.t('common.disabled'), false)); }
        return '<div class="card auto-card">' +
            '<h3>' + A.esc(target.name) + '</h3>' +
            '<div class="auto-sub">' + A.esc(A.t('types.' + target.type)) + ' · ' + where + '</div>' +
            (target.description ? '<div class="auto-sub">' + A.esc(target.description) + '</div>' : '') +
            '<div class="auto-badges">' + badges.join(' ') + '</div>' +
            '<div class="auto-actions">' +
                '<button class="chart-btn" data-act="test" data-name="' + A.esc(target.name) + '">' +
                    A.esc(A.t('targets.test')) + '</button>' +
                '<button class="chart-btn" data-act="edit" data-name="' + A.esc(target.name) + '">' +
                    A.esc(A.t('common.edit')) + '</button>' +
                '<button class="chart-btn auto-danger" data-act="delete" data-name="' + A.esc(target.name) + '">' +
                    A.esc(A.t('common.delete')) + '</button>' +
            '</div><div class="auto-result" id="targetResult-' + A.esc(target.name) + '"></div></div>';
    }

    var cache = [];

    async function load() {
        var grid = document.getElementById('hostsGrid');
        try {
            cache = (await A.request('GET', '/api/hosts')).hosts || [];
            grid.innerHTML = cache.length ? cache.map(card).join('')
                : '<div class="card auto-empty">' + A.esc(A.t('targets.empty')) + '</div>';
        } catch (e) {
            grid.innerHTML = '<div class="card auto-empty">' + A.esc(e.message) + '</div>';
        }
    }

    function resultHtml(result, type) {
        var details = result.details || {};
        var html = '<div class="auto-badge ' + (result.success ? 'ok' : 'warn') + '">' + A.esc(result.message) + '</div>';
        var rows = DETAIL_KEYS.filter(function (key) { return details[key] !== undefined; }).map(function (key) {
            return '<div class="auto-sub">' + A.esc(A.t('details.' + key)) + ': ' + A.esc(details[key]) + '</div>';
        });
        html += rows.join('');
        if (details.fingerprint) {
            var label = type === 'ssh' ? 'targets.field.host_key' : 'targets.field.cert_fingerprint';
            html += '<div class="auto-sub">' + A.esc(A.t(label)) + ': <code>' + A.esc(details.fingerprint) + '</code></div>';
        }
        return html;
    }

    async function testSaved(name) {
        var box = document.getElementById('targetResult-' + name);
        box.textContent = A.t('common.testing');
        var target = cache.find(function (t) { return t.name === name; }) || {};
        try {
            var result = await A.request('POST', '/api/hosts/' + encodeURIComponent(name) + '/test');
            box.innerHTML = resultHtml(result, target.type);
            window.AutoOpcua.afterResult(box, result, target.address, target.port);
        }
        catch (e) { box.textContent = e.message; }
    }

    async function remove(name) {
        if (!window.confirm(A.t('targets.confirm_delete', { name: name }))) { return; }
        try {
            await A.request('DELETE', '/api/hosts/' + encodeURIComponent(name));
            A.message(A.t('common.deleted'), 'success');
            load();
        } catch (e) { A.message(e.message, 'error'); }
    }

    // ------------------------------------------------------------ Dialog
    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function typeFields(type, options) {
        options = options || {};
        if (type === 'ssh') {
            return field(A.t('targets.field.key_path'), '<input id="tgKeyPath" value="' + A.esc(options.key_path || '') + '">') +
                '<label class="auto-check"><input type="checkbox" id="tgSudo"' + (options.sudo === false ? '' : ' checked') + '> ' +
                    A.esc(A.t('targets.field.sudo')) + '</label>' +
                field(A.t('targets.field.host_key'), '<input id="tgHostKey" value="' + A.esc(options.host_key || '') + '">');
        }
        if (type === 'winrm') {
            var transport = options.transport || 'ntlm';
            return field(A.t('targets.field.transport'),
                '<select id="tgTransport"><option value="ntlm"' + (transport === 'ntlm' ? ' selected' : '') + '>NTLM</option>' +
                '<option value="kerberos"' + (transport === 'kerberos' ? ' selected' : '') + '>Kerberos</option></select>');
        }
        if (type === 'opcua') { return window.AutoOpcua.fields(options); }
        if (type === 'local') { return '<div class="auto-sub">' + A.esc(A.t('services.self.target_hint')) + '</div>'; }
        if (type === 'nut_fsd') {
            return '<div class="auto-sub">' + A.esc(A.t('services.nut.target_hint')) + '</div>' +
                field(A.t('services.nut.target_ups'), '<select id="tgNutUps" data-value="' + A.esc(options.ups || '') + '"></select>');
        }
        var certField = field(A.t('targets.field.cert_fingerprint'), '<input id="tgCertFp" placeholder="' +
            A.esc(A.t('targets.field.cert_fingerprint_hint')) + '" value="' + A.esc(options.cert_fingerprint || '') + '">');
        if (type === 'redfish') {
            return field(A.t('targets.field.system_id'), '<input id="tgSystemId" placeholder="' +
                    A.esc(A.t('targets.field.system_id_auto')) + '" value="' + A.esc(options.system_id || '') + '">') +
                certField;
        }
        if (type === 'http') {
            return field(A.t('targets.field.scheme'), select('tgScheme', ['https', 'http'], options.scheme || 'https')) +
                field(A.t('targets.field.method'), select('tgMethod', ['POST', 'GET', 'PUT', 'PATCH', 'DELETE'],
                                                          options.method || 'POST')) +
                field(A.t('targets.field.path'), '<input id="tgPath" value="' + A.esc(options.path || '/') + '">') +
                field(A.t('targets.field.body'), '<textarea id="tgBody" rows="3">' + A.esc(options.body || '') + '</textarea>') +
                field(A.t('targets.field.content_type'), '<input id="tgContentType" value="' +
                    A.esc(options.content_type || 'application/json') + '">') +
                field(A.t('targets.field.headers'), '<textarea id="tgHeaders" rows="2">' +
                    A.esc((options.headers || []).map(function (h) { return h[0] + ': ' + h[1]; }).join('\n')) + '</textarea>') +
                field(A.t('targets.field.auth'), select('tgAuth', ['none', 'basic', 'bearer', 'header'], options.auth || 'none',
                                                        function (v) { return A.t('auth.' + v); })) +
                field(A.t('targets.field.auth_header'), '<input id="tgAuthHeader" value="' +
                    A.esc(options.auth_header || 'Authorization') + '">') +
                field(A.t('targets.field.expected_status'), '<input id="tgExpected" value="' +
                    A.esc(options.expected_status || '200-299') + '">') +
                field(A.t('targets.field.test_path'), '<input id="tgTestPath" value="' + A.esc(options.test_path || '') + '">') +
                '<div id="tgCertBox"' + (options.scheme === 'http' ? ' style="display:none"' : '') + '>' + certField + '</div>';
        }
        return '';
    }

    function select(id, values, selected, label) {
        return '<select id="' + id + '">' + values.map(function (v) {
            return '<option value="' + A.esc(v) + '"' + (v === selected ? ' selected' : '') + '>' +
                A.esc(label ? label(v) : v) + '</option>';
        }).join('') + '</select>';
    }

    async function fillNutUps() {
        var box = document.getElementById('tgNutUps');
        if (!box) { return; }
        var state = await A.request('GET', '/api/services');
        var selected = box.getAttribute('data-value');
        box.innerHTML = '<option value="">' + A.esc(A.t('scope.all')) + '</option>' + state.settings.nut.ups.map(function (key) {
            return '<option value="' + A.esc(key) + '"' + (key === selected ? ' selected' : '') + '>' + A.esc(key) + '</option>';
        }).join('');
    }

    function showConnection(type) {
        document.getElementById('tgConnection').style.display = VIRTUAL.indexOf(type) >= 0 ? 'none' : '';
    }

    function bindTypeFields() {
        window.AutoOpcua.bind();
        fillNutUps().catch(function (e) { A.message(e.message, 'error'); });
        showConnection(document.getElementById('tgType').value);
        var scheme = document.getElementById('tgScheme');
        if (!scheme) { return; }
        scheme.addEventListener('change', function () {
            var port = document.getElementById('tgPort');
            if (port.value === '443' || port.value === '80') { port.value = scheme.value === 'http' ? 80 : 443; }
            document.getElementById('tgCertBox').style.display = scheme.value === 'http' ? 'none' : '';
        });
    }

    function open(name) {
        current = name ? cache.find(function (t) { return t.name === name; }) : null;
        var c = current || { type: 'ssh', port: 22, enabled: true, timeout_seconds: 30, options: {} };
        var types = TYPES.map(function (type) {
            return '<option value="' + type + '"' + (c.type === type ? ' selected' : '') + '>' + A.esc(A.t('types.' + type)) + '</option>';
        }).join('');
        var body =
            field(A.t('targets.field.name'), '<input id="tgName" value="' + A.esc(c.name || '') + '">') +
            field(A.t('targets.field.type'), '<select id="tgType">' + types + '</select>') +
            '<div id="tgConnection">' +
            field(A.t('targets.field.address'), '<input id="tgAddress" value="' + A.esc(c.address || '') + '">') +
            field(A.t('targets.field.port'), '<input id="tgPort" type="number" min="1" max="65535" value="' + A.esc(c.port) + '">') +
            field(A.t('targets.field.username'), '<input id="tgUser" value="' + A.esc(c.username || '') + '">') +
            field(A.t('targets.field.password'), '<input id="tgPassword" type="password" autocomplete="new-password" placeholder="' +
                A.esc(c.has_password ? A.t('targets.field.password_keep') : '') + '">') +
            (c.has_password ? '<label class="auto-check"><input type="checkbox" id="tgClearPassword"> ' +
                A.esc(A.t('targets.field.clear_password')) + '</label>' : '') +
            '</div>' +
            '<div id="tgTypeFields">' + typeFields(c.type, c.options) + '</div>' +
            field(A.t('targets.field.timeout'), '<input id="tgTimeout" type="number" min="5" max="600" value="' + A.esc(c.timeout_seconds) + '">') +
            field(A.t('targets.field.description'), '<input id="tgDescription" value="' + A.esc(c.description || '') + '">') +
            '<label class="auto-check"><input type="checkbox" id="tgEnabled"' + (c.enabled ? ' checked' : '') + '> ' +
                A.esc(A.t('common.enabled')) + '</label>' +
            '<p><a href="#" id="tgHelp">' + A.esc(A.t('targets.field.help')) + '</a></p>' +
            '<div id="tgResult" class="auto-result"></div>';
        var footer =
            '<button class="chart-btn" id="tgTest">' + A.esc(A.t('targets.test')) + '</button>' +
            '<button class="chart-btn" id="tgCancel">' + A.esc(A.t('common.cancel')) + '</button>' +
            '<button class="chart-btn auto-primary" id="tgSave">' + A.esc(A.t('common.save')) + '</button>';
        A.openDialog(current ? A.t('targets.dialog_edit') : A.t('targets.dialog_new'), body, footer);

        document.getElementById('tgType').addEventListener('change', function (ev) {
            document.getElementById('tgTypeFields').innerHTML = typeFields(ev.target.value, {});
            document.getElementById('tgPort').value = DEFAULT_PORTS[ev.target.value];
            bindTypeFields();
        });
        bindTypeFields();
        document.getElementById('tgHelp').addEventListener('click', function (ev) {
            ev.preventDefault();
            window.AutoHelp.show(document.getElementById('tgType').value);
        });
        document.getElementById('tgTest').addEventListener('click', testForm);
        document.getElementById('tgCancel').addEventListener('click', A.closeDialog);
        document.getElementById('tgSave').addEventListener('click', save);
    }

    function formData() {
        var type = document.getElementById('tgType').value;
        var data = {
            name: document.getElementById('tgName').value.trim(),
            type: type,
            address: document.getElementById('tgAddress').value.trim(),
            port: parseInt(document.getElementById('tgPort').value, 10),
            username: document.getElementById('tgUser').value.trim(),
            timeout_seconds: parseInt(document.getElementById('tgTimeout').value, 10),
            description: document.getElementById('tgDescription').value.trim(),
            enabled: document.getElementById('tgEnabled').checked,
            options: {},
        };
        var password = document.getElementById('tgPassword').value;
        if (password) { data.password = password; }
        var clear = document.getElementById('tgClearPassword');
        if (clear && clear.checked) { data.clear_password = true; }
        if (type === 'ssh') {
            data.options = { key_path: document.getElementById('tgKeyPath').value.trim(),
                             sudo: document.getElementById('tgSudo').checked,
                             host_key: document.getElementById('tgHostKey').value.trim() };
        } else if (type === 'winrm') {
            data.options = { transport: document.getElementById('tgTransport').value };
        } else if (type === 'opcua') {
            data.options = window.AutoOpcua.options();
        } else if (type === 'nut_fsd') {
            data.options = { ups: document.getElementById('tgNutUps').value };
        } else if (type === 'redfish') {
            data.options = { system_id: document.getElementById('tgSystemId').value.trim(),
                             cert_fingerprint: document.getElementById('tgCertFp').value.trim() };
        } else if (type === 'http') {
            data.options = { scheme: document.getElementById('tgScheme').value,
                             method: document.getElementById('tgMethod').value,
                             path: document.getElementById('tgPath').value.trim(),
                             body: document.getElementById('tgBody').value,
                             content_type: document.getElementById('tgContentType').value.trim(),
                             headers: document.getElementById('tgHeaders').value,
                             auth: document.getElementById('tgAuth').value,
                             auth_header: document.getElementById('tgAuthHeader').value.trim(),
                             expected_status: document.getElementById('tgExpected').value.trim(),
                             test_path: document.getElementById('tgTestPath').value.trim(),
                             cert_fingerprint: document.getElementById('tgCertFp').value.trim() };
        }
        return data;
    }

    async function testForm() {
        var box = document.getElementById('tgResult');
        box.textContent = A.t('common.testing');
        var data = formData();
        if (current) { data.original_name = current.name; }
        try {
            var result = await A.request('POST', '/api/hosts/test', data);
            box.innerHTML = resultHtml(result, data.type);
            window.AutoOpcua.afterResult(box, result, data.address, data.port);
            var fp = result.details && result.details.fingerprint;
            var fpInput = document.getElementById('tgHostKey') || document.getElementById('tgCertFp');
            if (fp && fpInput && !result.details.fingerprint_trusted) {
                var accept = document.createElement('button');
                accept.className = 'chart-btn';
                accept.textContent = A.t('targets.field.accept_fingerprint');
                accept.addEventListener('click', function () {
                    fpInput.value = fp;
                    accept.disabled = true;
                });
                box.appendChild(accept);
            }
        } catch (e) { box.textContent = e.message; }
    }

    async function save() {
        try {
            if (current) { await A.request('PUT', '/api/hosts/' + encodeURIComponent(current.name), formData()); }
            else { await A.request('POST', '/api/hosts', formData()); }
            A.closeDialog();
            A.message(A.t('common.saved'), 'success');
            load();
        } catch (e) {
            document.getElementById('tgResult').textContent = e.message;
        }
    }

    document.addEventListener('click', function (ev) {
        var button = ev.target.closest('#hostsGrid [data-act]');
        if (!button) { return; }
        var name = button.getAttribute('data-name');
        if (button.getAttribute('data-act') === 'test') { testSaved(name); }
        if (button.getAttribute('data-act') === 'edit') { open(name); }
        if (button.getAttribute('data-act') === 'delete') { remove(name); }
    });

    window.AutoTargets = { load: load, open: open };
})();
