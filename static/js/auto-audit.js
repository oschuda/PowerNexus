/* Verwaltung: Sicherheitsprotokoll anzeigen (nur Admin). */
(function () {
    'use strict';
    var A = window.Auto;

    async function loadStatus() {
        var data = await A.request('GET', '/api/audit/overview');
        document.getElementById('securityStatus').innerHTML = data.findings.length
            ? '<ul class="auto-steps">' + data.findings.map(function (text) { return '<li>⚠️ ' + A.esc(text) + '</li>'; }).join('') + '</ul>'
            : '<div class="auto-sub">✅ ' + A.esc(data.ok_text) + '</div>';
    }

    async function load() {
        var card = document.getElementById('auditCard');
        var box = document.getElementById('auditList');
        if (!card) { return; }
        try {
            var entries = (await A.request('GET', '/api/audit?hours=168&limit=200')).entries || [];
            card.style.display = '';
            await loadStatus();
            box.innerHTML = entries.length
                ? '<table class="auto-table"><thead><tr><th>' + [A.t('audit.time'), A.t('audit.user'), A.t('audit.address'), A.t('audit.action'),
                    A.t('audit.status')].map(A.esc).join('</th><th>') + '</th></tr></thead><tbody>' + entries.map(function (e) {
                    return '<tr><td>' + A.esc(new Date(e.timestamp * 1000).toLocaleString(A.lang())) + '</td><td>' + A.esc(e.user_id) +
                        '</td><td>' + A.esc(e.client_ip || '') + '</td><td>' + A.esc(e.action) + (e.target ? ' <small>(' + A.esc(e.target) + ')</small>' : '') +
                        '</td><td><span class="auto-badge ' + (e.status === 'OK' ? 'ok' : 'warn') + '">' + A.esc(e.status) + '</span></td></tr>';
                }).join('') + '</tbody></table>'
                : '<div class="auto-sub">' + A.esc(A.t('audit.empty')) + '</div>';
        } catch (e) {
            card.style.display = 'none';        // nur für Admins
        }
    }

    window.AutoAudit = { load: load };
})();
