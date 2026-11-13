/* Gemeinsame Hilfen für Automatisierung (Regeln, Zielrechner, Testmodus, Hilfe). */
window.Auto = (function () {
    'use strict';

    function t(key, params) {
        var text = (window.I18N || {})[key] || key;
        Object.keys(params || {}).forEach(function (name) {
            text = text.split('{' + name + '}').join(params[name]);
        });
        return text;
    }

    function esc(value) {
        var div = document.createElement('div');
        div.textContent = value === undefined || value === null ? '' : String(value);
        return div.innerHTML.replace(/"/g, '&quot;');
    }

    async function request(method, url, body) {
        var options = { method: method, headers: {} };
        if (body !== undefined) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
        var response = await fetch(url, options);
        var data = {};
        try { data = await response.json(); } catch (e) { data = {}; }
        if (!response.ok) {
            var error = new Error(data.error || ('HTTP ' + response.status));
            error.data = data;
            throw error;
        }
        return data;
    }

    function openDialog(title, body, footer) {
        document.getElementById('autoDialogTitle').textContent = title;
        document.getElementById('autoDialogBody').innerHTML = body;
        document.getElementById('autoDialogFooter').innerHTML = footer;
        document.getElementById('autoDialog').style.display = 'flex';
    }

    function closeDialog() {
        document.getElementById('autoDialog').style.display = 'none';
    }

    function message(text, kind) {
        if (typeof window.showAlert === 'function') { window.showAlert(text, kind || 'info'); }
        else { window.alert(text); }
    }

    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape') { return; }
        var help = document.getElementById('autoHelp');
        if (help && help.style.display === 'flex') { help.style.display = 'none'; return; }
        closeDialog();
    });

    return { t: t, esc: esc, request: request, openDialog: openDialog, closeDialog: closeDialog,
             message: message, lang: function () { return window.LANG || 'de'; } };
})();
