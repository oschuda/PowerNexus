/* Regeln: Übersicht, Aktivieren/Deaktivieren, Löschen, letzte Ausführungen. */
(function () {
    'use strict';
    var A = window.Auto;
    var cache = [];

    function eventList(events) {
        return (events || []).map(function (e) { return A.t('events.' + e); }).join(', ');
    }

    function scopeText(scope) {
        if (!scope || scope.type === 'all') { return A.t('scope.all'); }
        return A.t('scope.' + scope.type) + ': ' + scope.id;
    }

    function card(policy) {
        var steps = policy.steps.map(function (step, i) {
            return '<li>' + A.esc((i + 1) + '. ' + A.t('actions.' + step.action) + ' → ' + step.targets.join(', ')) + '</li>';
        }).join('');
        var last = policy.last_run
            ? '<div class="auto-sub">' + A.esc(A.t('policies.last_run')) + ': ' +
                A.esc(A.t('runs.status.' + policy.last_run.status)) + ' (' +
                A.esc(new Date(policy.last_run.started_at * 1000).toLocaleString(A.lang())) + ')</div>'
            : '';
        return '<div class="card auto-card' + (policy.enabled ? '' : ' auto-off') + '">' +
            '<h3>' + A.esc(policy.name) + '</h3>' +
            '<div class="auto-badges">' +
                '<span class="auto-badge ' + (policy.enabled ? 'ok' : 'warn') + '">' +
                    A.esc(policy.enabled ? A.t('common.enabled') : A.t('common.disabled')) + '</span>' +
                (policy.dry_run ? ' <span class="auto-badge warn">' + A.esc(A.t('policies.field.dry_run')) + '</span>' : '') +
            '</div>' +
            '<div class="auto-sub"><b>' + A.esc(A.t('policies.field.events')) + ':</b> ' + A.esc(eventList(policy.trigger.events)) + '</div>' +
            '<div class="auto-sub"><b>' + A.esc(A.t('policies.field.scope')) + ':</b> ' + A.esc(scopeText(policy.trigger.scope)) + '</div>' +
            '<div class="auto-sub"><b>' + A.esc(A.t('policies.field.wait')) + ':</b> ' + A.esc(policy.wait_seconds) + ' s · ' +
                '<b>' + A.esc(A.t('policies.field.cancel_on')) + ':</b> ' + A.esc(eventList(policy.cancel_on) || '–') + '</div>' +
            '<ul class="auto-steps">' + steps + '</ul>' +
            (policy.notify && policy.notify.length ? '<div class="auto-sub"><b>' + A.esc(A.t('policies.section.notify')) + ':</b> ' +
                A.esc(policy.notify.join(', ')) + '</div>' : '') + last +
            '<div class="auto-actions">' +
                '<button class="chart-btn" data-pol="toggle" data-name="' + A.esc(policy.name) + '">' +
                    A.esc(policy.enabled ? A.t('policies.disable') : A.t('policies.enable')) + '</button>' +
                '<button class="chart-btn" data-pol="edit" data-name="' + A.esc(policy.name) + '">' + A.esc(A.t('common.edit')) + '</button>' +
                '<button class="chart-btn auto-danger" data-pol="delete" data-name="' + A.esc(policy.name) + '">' + A.esc(A.t('common.delete')) + '</button>' +
            '</div></div>';
    }

    function runRow(run) {
        var active = run.status === 'waiting' || run.status === 'running';
        var when = run.status === 'waiting'
            ? A.t('runs.executes_at', { time: new Date(run.execute_at * 1000).toLocaleTimeString(A.lang()) }) +
                ' (' + window.AutoCountdown.remaining(run.execute_at) + ')'
            : new Date(run.started_at * 1000).toLocaleString(A.lang());
        return '<tr><td>' + A.esc(run.policy) + (run.dry_run ? ' <span class="auto-badge warn">TEST</span>' : '') +
            (run.resumed ? ' <span class="auto-badge warn">' + A.esc(A.t('runs.resumed')) + '</span>' : '') + '</td>' +
            '<td>' + A.esc(run.ups_id) + '</td><td>' + A.esc(A.t('events.' + run.event)) + '</td>' +
            '<td>' + A.esc(A.t('runs.status.' + run.status)) + '</td><td>' + A.esc(when) + '</td>' +
            '<td>' + (active ? '<button class="chart-btn auto-danger" data-run="' + A.esc(run.id) + '">' +
                A.esc(A.t('runs.cancel')) + '</button>' : '') + '</td></tr>';
    }

    async function loadRuns() {
        var box = document.getElementById('policyRuns');
        if (!box) { return; }
        try {
            var runs = (await A.request('GET', '/api/policies/runs')).runs || [];
            box.innerHTML = runs.length
                ? '<table class="auto-table"><thead><tr><th>' + [A.t('runs.col.policy'), A.t('runs.col.ups'), A.t('runs.col.event'),
                    A.t('runs.col.status'), A.t('runs.col.time'), ''].map(A.esc).join('</th><th>') + '</th></tr></thead><tbody>' +
                    runs.slice(0, 15).map(runRow).join('') + '</tbody></table>'
                : '<div class="auto-sub">' + A.esc(A.t('runs.empty')) + '</div>';
        } catch (e) { box.textContent = e.message; }
    }

    async function load() {
        var grid = document.getElementById('policiesGrid');
        try {
            cache = (await A.request('GET', '/api/policies')).policies || [];
            grid.innerHTML = cache.length ? cache.map(card).join('')
                : '<div class="card auto-empty">' + A.esc(A.t('policies.empty')) + '</div>';
        } catch (e) {
            grid.innerHTML = '<div class="card auto-empty">' + A.esc(e.message) + '</div>';
        }
        loadRuns();
    }

    async function act(kind, name) {
        try {
            if (kind === 'toggle') { await A.request('POST', '/api/policies/' + encodeURIComponent(name) + '/toggle'); }
            if (kind === 'delete') {
                if (!window.confirm(A.t('policies.confirm_delete', { name: name }))) { return; }
                await A.request('DELETE', '/api/policies/' + encodeURIComponent(name));
            }
            load();
        } catch (e) { A.message(e.message, 'error'); }
    }

    document.addEventListener('click', async function (ev) {
        var button = ev.target.closest('#policiesGrid [data-pol]');
        if (button) {
            var kind = button.getAttribute('data-pol');
            var name = button.getAttribute('data-name');
            if (kind === 'edit') { window.AutoPolicyForm.open(cache.find(function (p) { return p.name === name; })); }
            else { act(kind, name); }
            return;
        }
        var cancel = ev.target.closest('#policyRuns [data-run]');
        if (cancel) {
            try { await A.request('POST', '/api/policies/runs/' + cancel.getAttribute('data-run') + '/cancel'); }
            catch (e) { A.message(e.message, 'error'); }
            loadRuns();
        }
    });

    setInterval(function () {
        var tab = document.getElementById('tab-policies');
        if (tab && tab.style.display !== 'none') { loadRuns(); }
    }, 5000);

    window.AutoPolicies = { load: load };
})();
