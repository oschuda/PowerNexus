/* E-Mail: Versandweg, Microsoft 365 / SMTP, Empfängerlisten, Test-E-Mail. */
(function () {
    'use strict';
    var A = window.Auto;
    var meta = null;
    var settings = null;
    var pfxBase64 = null;        // neu gewählte PFX-Datei, noch nicht gespeichert

    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function input(id, value, extra) {
        return '<input id="' + id + '" value="' + A.esc(value === undefined || value === null ? '' : value) + '"' + (extra || '') + '>';
    }

    function select(id, values, selected, prefix) {
        return '<select id="' + id + '">' + values.map(function (v) {
            return '<option value="' + A.esc(v) + '"' + (v === selected ? ' selected' : '') + '>' + A.esc(A.t(prefix + v)) + '</option>';
        }).join('') + '</select>';
    }

    function badge(text, ok) {
        return '<span class="auto-badge ' + (ok ? 'ok' : 'warn') + '">' + A.esc(text) + '</span>';
    }

    function secretField(id, label, stored, clearId, clearLabel) {
        return field(label, '<input id="' + id + '" type="password" autocomplete="new-password" placeholder="' +
                A.esc(stored ? A.t('targets.field.password_keep') : '') + '">') +
            (stored ? '<label class="auto-check"><input type="checkbox" id="' + clearId + '"> ' + A.esc(clearLabel) + '</label>' : '');
    }

    function m365Html(m) {
        return field(A.t('mail.field.tenant_id'), input('mlTenant', m.tenant_id)) +
            field(A.t('mail.field.client_id'), input('mlClient', m.client_id)) +
            field(A.t('mail.field.m365_auth'), select('mlAuth', meta.m365_auth, m.auth, 'mail.m365_auth.')) +
            '<div id="mlSecretBox">' +
                secretField('mlSecret', A.t('mail.field.secret'), m.has_secret, 'mlClearSecret', A.t('mail.field.clear_secret')) +
                field(A.t('mail.field.secret_expires'), input('mlSecretExpires', m.secret_expires, ' type="date"')) +
            '</div>' +
            '<div id="mlCertBox">' +
                (m.has_certificate ? '<div class="auto-sub">' + A.esc(A.t('mail.certificate_info', { thumbprint: m.certificate_thumbprint,
                    date: m.certificate_expires })) + '</div><label class="auto-check"><input type="checkbox" id="mlClearCert"> ' +
                    A.esc(A.t('mail.field.clear_certificate')) + '</label>' : '') +
                field(A.t('mail.field.certificate'), '<input id="mlPfx" type="file" accept=".pfx,.p12">') +
                field(A.t('mail.field.certificate_password'), '<input id="mlPfxPassword" type="password" autocomplete="new-password">') +
            '</div>';
    }

    function smtpHtml(s) {
        return field(A.t('mail.field.host'), input('mlHost', s.host)) +
            field(A.t('mail.field.port'), input('mlPort', s.port, ' type="number" min="1" max="65535"')) +
            field(A.t('mail.field.security'), select('mlSecurity', meta.security, s.security, 'mail.security.')) +
            field(A.t('mail.field.username'), input('mlUser', s.username)) +
            secretField('mlPassword', A.t('mail.field.password'), s.has_password, 'mlClearPassword', A.t('targets.field.clear_password'));
    }

    function listHtml(item) {
        var language = meta.languages.map(function (lang) {
            return '<option value="' + lang + '"' + (item.language === lang ? ' selected' : '') + '>' + lang.toUpperCase() + '</option>';
        }).join('');
        var events = meta.events.map(function (e) {
            return '<label class="auto-check"><input type="checkbox" class="mlEvent" value="' + A.esc(e) + '"' +
                (item.events.indexOf(e) >= 0 ? ' checked' : '') + '> ' + A.esc(A.t('events.' + e)) + '</label>';
        }).join('');
        return '<fieldset class="auto-step mlList"><legend>' + A.esc(A.t('mail.list')) + '</legend>' +
            field(A.t('mail.field.list_name'), '<input class="mlListName" value="' + A.esc(item.name) + '">') +
            field(A.t('mail.field.recipients'), '<textarea class="mlRecipients" rows="2">' + A.esc(item.recipients.join('\n')) + '</textarea>') +
            field(A.t('mail.field.language'), '<select class="mlLanguage">' + language + '</select>') +
            '<div class="auto-sub">' + A.esc(A.t('mail.field.list_events')) + '</div><div class="auto-checks">' + events + '</div>' +
            '<button class="chart-btn auto-danger mlRemove" type="button">' + A.esc(A.t('mail.remove_list')) + '</button></fieldset>';
    }

    function refreshVisibility() {
        var transport = document.getElementById('mlTransport').value;
        document.getElementById('mlM365').style.display = transport === 'smtp' ? 'none' : '';
        document.getElementById('mlSmtp').style.display = transport === 'smtp' ? '' : 'none';
        var auth = document.getElementById('mlAuth').value;
        document.getElementById('mlSecretBox').style.display = auth === 'secret' ? '' : 'none';
        document.getElementById('mlCertBox').style.display = auth === 'certificate' ? '' : 'none';
    }

    function render() {
        var s = settings;
        document.getElementById('mailForm').innerHTML =
            '<label class="auto-check"><input type="checkbox" id="mlEnabled"' + (s.enabled ? ' checked' : '') + '> ' + A.esc(A.t('mail.field.enabled')) + '</label> ' +
            badge(s.enabled ? A.t('common.enabled') : A.t('common.disabled'), s.enabled) +
            field(A.t('mail.field.transport'), select('mlTransport', meta.transports, s.transport, 'mail.transport.')) +
            field(A.t('mail.field.sender'), input('mlSender', s.sender)) +
            field(A.t('mail.field.dashboard_url'), input('mlUrl', s.dashboard_url)) +
            '<div id="mlM365"><h4>' + A.esc(A.t('mail.section.m365')) + '</h4>' + m365Html(s.m365) + '</div>' +
            '<div id="mlSmtp"><h4>' + A.esc(A.t('mail.section.smtp')) + '</h4>' + smtpHtml(s.smtp) + '</div>' +
            '<h4>' + A.esc(A.t('mail.section.lists')) + '</h4>' +
            '<div id="mlLists">' + s.lists.map(listHtml).join('') + '</div>' +
            '<button class="chart-btn" type="button" id="mlAddList">' + A.esc(A.t('mail.add_list')) + '</button>' +
            '<h4>' + A.esc(A.t('mail.section.test')) + '</h4>' +
            field(A.t('mail.field.test_recipient'), input('mlTestRecipient', '')) +
            '<div class="auto-actions">' +
                '<button class="chart-btn" type="button" id="mlTest">' + A.esc(A.t('mail.send_test')) + '</button>' +
                '<button class="chart-btn auto-primary" type="button" id="mlSave">' + A.esc(A.t('common.save')) + '</button>' +
            '</div>' +
            '<p><a href="#" data-help="email">' + A.esc(A.t('mail.help')) + '</a></p>' +
            '<div id="mlResult" class="auto-result"></div>';
        pfxBase64 = null;
        refreshVisibility();
        document.getElementById('mlTransport').addEventListener('change', refreshVisibility);
        document.getElementById('mlAuth').addEventListener('change', refreshVisibility);
        document.getElementById('mlPfx').addEventListener('change', readPfx);
        document.getElementById('mlAddList').addEventListener('click', function () {
            document.getElementById('mlLists').insertAdjacentHTML('beforeend',
                listHtml({ name: '', recipients: [], language: A.lang(), events: [] }));
        });
        document.getElementById('mlLists').addEventListener('click', function (ev) {
            if (ev.target.classList.contains('mlRemove')) { ev.target.closest('.mlList').remove(); }
        });
        document.getElementById('mlTest').addEventListener('click', test);
        document.getElementById('mlSave').addEventListener('click', save);
    }

    function readPfx(ev) {
        var file = ev.target.files[0];
        pfxBase64 = null;
        if (!file) { return; }
        var reader = new FileReader();
        reader.onload = function () { pfxBase64 = String(reader.result).split(',')[1]; };
        reader.readAsDataURL(file);
    }

    function value(id) {
        var el = document.getElementById(id);
        return el ? el.value.trim() : '';
    }

    function checked(id) {
        var el = document.getElementById(id);
        return !!(el && el.checked);
    }

    function formData() {
        var m365 = { tenant_id: value('mlTenant'), client_id: value('mlClient'), auth: value('mlAuth'),
                     secret_expires: value('mlSecretExpires') };
        if (value('mlSecret')) { m365.secret = value('mlSecret'); }
        if (checked('mlClearSecret')) { m365.clear_secret = true; }
        if (checked('mlClearCert')) { m365.clear_certificate = true; }
        if (pfxBase64) { m365.certificate = pfxBase64; m365.certificate_password = document.getElementById('mlPfxPassword').value; }
        var smtp = { host: value('mlHost'), port: Number(value('mlPort')), security: value('mlSecurity'), username: value('mlUser') };
        if (document.getElementById('mlPassword').value) { smtp.password = document.getElementById('mlPassword').value; }
        if (checked('mlClearPassword')) { smtp.clear_password = true; }
        return {
            enabled: checked('mlEnabled'), transport: value('mlTransport'), sender: value('mlSender'),
            dashboard_url: value('mlUrl'), m365: m365, smtp: smtp,
            lists: Array.prototype.map.call(document.querySelectorAll('#mlLists .mlList'), function (box) {
                return {
                    name: box.querySelector('.mlListName').value.trim(),
                    recipients: box.querySelector('.mlRecipients').value,
                    language: box.querySelector('.mlLanguage').value,
                    events: Array.prototype.map.call(box.querySelectorAll('.mlEvent:checked'), function (el) { return el.value; }),
                };
            }),
        };
    }

    async function load() {
        var box = document.getElementById('mailForm');
        try {
            var data = await A.request('GET', '/api/notifications');
            meta = data.meta;
            settings = data.settings;
            render();
        } catch (e) {
            box.innerHTML = '<div class="card auto-empty">' + A.esc(e.message) + '</div>';
        }
    }

    async function save() {
        try {
            settings = (await A.request('PUT', '/api/notifications', formData())).settings;
            render();
            A.message(A.t('common.saved'), 'success');
        } catch (e) {
            document.getElementById('mlResult').textContent = e.message;
        }
    }

    async function test() {
        var box = document.getElementById('mlResult');
        box.textContent = A.t('common.testing');
        var data = formData();
        data.recipient = value('mlTestRecipient');
        try {
            var result = await A.request('POST', '/api/notifications/test', data);
            box.innerHTML = result.steps.map(function (step) {
                return '<div>' + (step.success ? '✅ ' : '❌ ') + A.esc(step.message) + '</div>';
            }).join('');
        } catch (e) { box.textContent = e.message; }
    }

    window.AutoMail = { load: load };
})();
