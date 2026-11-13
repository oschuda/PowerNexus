/* OPC-UA im Zielrechner-Dialog: Felder, Client-Zertifikat, Server-Zertifikat vertrauen. */
(function () {
    'use strict';
    var A = window.Auto;
    var TYPES = ['Boolean', 'Int16', 'Int32', 'UInt16', 'UInt32', 'Float', 'String'];

    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function select(id, values, selected, label) {
        return '<select id="' + id + '">' + values.map(function (v) {
            return '<option value="' + A.esc(v) + '"' + (v === selected ? ' selected' : '') + '>' +
                A.esc(label ? label(v) : v) + '</option>';
        }).join('') + '</select>';
    }

    function fields(o) {
        return field(A.t('opcua.field.security'), select('opSecurity', ['none', 'sign_and_encrypt'], o.security || 'sign_and_encrypt',
                                                         function (v) { return A.t('opcua.security.' + v); })) +
            field(A.t('opcua.field.auth'), select('opAuth', ['anonymous', 'user'], o.auth || 'anonymous',
                                                  function (v) { return A.t('opcua.auth.' + v); })) +
            field(A.t('opcua.field.node_id'), '<input id="opNode" placeholder="ns=2;s=Power.ShutdownRequest" value="' + A.esc(o.node_id || '') + '">') +
            field(A.t('opcua.field.data_type'), select('opType', TYPES, o.data_type || 'Boolean')) +
            field(A.t('opcua.field.value'), '<input id="opValue" value="' + A.esc(o.value === undefined ? 'true' : o.value) + '">') +
            '<label class="auto-check"><input type="checkbox" id="opVerify"' + (o.verify_write === false ? '' : ' checked') + '> ' +
                A.esc(A.t('opcua.field.verify_write')) + '</label>' +
            '<div id="opPanel"></div>';
    }

    function options() {
        return { security: document.getElementById('opSecurity').value, auth: document.getElementById('opAuth').value,
                 node_id: document.getElementById('opNode').value.trim(), data_type: document.getElementById('opType').value,
                 value: document.getElementById('opValue').value.trim(), verify_write: document.getElementById('opVerify').checked };
    }

    function certLines(c) {
        return '<div class="auto-sub">' + A.esc(c.subject) + '</div>' +
            '<div class="auto-sub">' + A.esc(A.t('opcua.sha1')) + ': <code>' + A.esc(c.sha1) + '</code></div>' +
            '<div class="auto-sub">' + A.esc(A.t('opcua.cert_summary', { from: c.not_before, to: c.not_after,
                signature: c.signature, bits: c.key_bits })) + '</div>' +
            (c.weak ? '<div class="auto-badge warn">' + A.esc(A.t('opcua.cert_weak')) + '</div>' : '');
    }

    async function renderPanel() {
        var panel = document.getElementById('opPanel');
        if (!panel) { return; }
        if (document.getElementById('opSecurity').value !== 'sign_and_encrypt') { panel.innerHTML = ''; return; }
        panel.textContent = A.t('common.loading');
        try {
            var own = await A.request('GET', '/api/opcua/client-certificate');
            var trusted = (await A.request('GET', '/api/opcua/trusted')).trusted;
            panel.innerHTML = '<h4>' + A.esc(A.t('opcua.client_title')) + '</h4>' +
                '<div class="auto-sub">' + A.esc(A.t('opcua.client_hint')) + '</div>' + certLines(own) +
                '<div class="auto-sub">ApplicationUri: <code>' + A.esc(own.application_uri) + '</code></div>' +
                (own.replaced_reason ? '<div class="auto-badge warn">' + A.esc(A.t('opcua.client_replaced', { sha1: own.replaced_sha1 })) + '</div>' : '') +
                '<p><a href="/api/opcua/client-certificate/download">' + A.esc(A.t('opcua.client_download')) + '</a></p>' +
                '<h4>' + A.esc(A.t('opcua.trusted_title')) + '</h4>' +
                (trusted.length ? trusted.map(function (c) {
                    return '<div class="auto-step">' + certLines(c) + '<button class="chart-btn auto-danger opRemove" type="button" data-sha1="' +
                        A.esc(c.sha1) + '">' + A.esc(A.t('opcua.trusted_remove')) + '</button></div>';
                }).join('') : '<div class="auto-sub">' + A.esc(A.t('opcua.trusted_empty')) + '</div>');
            panel.querySelectorAll('.opRemove').forEach(function (button) {
                button.addEventListener('click', async function () {
                    try {
                        await A.request('DELETE', '/api/opcua/trusted/' + button.getAttribute('data-sha1'));
                        renderPanel();
                    } catch (e) { A.message(e.message, 'error'); }
                });
            });
        } catch (e) { panel.textContent = e.message; }
    }

    function bind() {
        var security = document.getElementById('opSecurity');
        if (!security) { return; }
        security.addEventListener('change', renderPanel);
        renderPanel();
    }

    /* Ergebnis von „Verbindung testen“: Server-Zertifikat zeigen und auf Wunsch vertrauen. */
    function afterResult(box, result, address, port) {
        var details = result.details || {};
        if (!details.server_certificate) { return; }
        var holder = document.createElement('div');
        holder.innerHTML = '<div class="auto-sub"><b>' + A.esc(A.t('opcua.server_title')) + '</b></div>' + certLines(details.server_certificate);
        box.appendChild(holder);
        if (details.server_trusted) { return; }
        var button = document.createElement('button');
        button.className = 'chart-btn';
        button.textContent = A.t('opcua.trust');
        button.addEventListener('click', async function () {
            try {
                await A.request('POST', '/api/opcua/trust', { address: address, port: port, sha1: details.sha1 });
                button.disabled = true;
                button.textContent = A.t('opcua.trusted_done');
                renderPanel();
            } catch (e) { A.message(e.message, 'error'); }
        });
        box.appendChild(button);
    }

    window.AutoOpcua = { fields: fields, options: options, bind: bind, afterResult: afterResult };
})();
