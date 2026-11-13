/* Regel-Dialog: Auslöser, Geltungsbereich, Bedingungen, Wartezeit, Abbruch, Schritte, Regel testen. */
(function () {
    'use strict';
    var A = window.Auto;
    var meta = null;
    var targets = [];
    var original = null;

    function checkboxes(prefix, values, selected, labelPrefix) {
        return values.map(function (value) {
            return '<label class="auto-check"><input type="checkbox" class="' + prefix + '" value="' + A.esc(value) + '"' +
                (selected.indexOf(value) >= 0 ? ' checked' : '') + '> ' + A.esc(A.t(labelPrefix + value)) + '</label>';
        }).join('');
    }

    function field(label, inner) {
        return '<label class="auto-field"><span>' + A.esc(label) + '</span>' + inner + '</label>';
    }

    function scopeOptions(type, selectedId) {
        var list = type === 'ups' ? meta.profiles : type === 'group' ? meta.groups : [];
        return list.map(function (item) {
            return '<option value="' + A.esc(item.id) + '"' + (String(item.id) === String(selectedId) ? ' selected' : '') + '>' +
                A.esc(item.name) + '</option>';
        }).join('');
    }

    function stepHtml(step) {
        var actions = {};
        targets.forEach(function (t) { t.actions.forEach(function (a) { actions[a] = true; }); });
        var actionOptions = Object.keys(actions).map(function (a) {
            return '<option value="' + a + '"' + (step.action === a ? ' selected' : '') + '>' + A.esc(A.t('actions.' + a)) + '</option>';
        }).join('');
        var targetBoxes = targets.map(function (t) {
            return '<label class="auto-check"><input type="checkbox" class="stTarget" value="' + A.esc(t.name) + '"' +
                (step.targets.indexOf(t.name) >= 0 ? ' checked' : '') + '> ' + A.esc(t.name) +
                ' <small>(' + A.esc(A.t('types.' + t.type)) + ')</small></label>';
        }).join('') || '<div class="auto-sub">' + A.esc(A.t('policies.no_targets')) + '</div>';
        return '<fieldset class="auto-step"><legend>' + A.esc(A.t('policies.step')) + '</legend>' +
            '<div class="auto-step-targets">' + targetBoxes + '</div>' +
            field(A.t('policies.field.action'), '<select class="stAction">' + actionOptions + '</select>') +
            field(A.t('policies.field.delay'), '<input class="stDelay" type="number" min="0" max="3600" value="' + A.esc(step.delay_seconds || 0) + '">') +
            field(A.t('policies.field.timeout'), '<input class="stTimeout" type="number" min="10" max="3600" value="' + A.esc(step.timeout_seconds || 180) + '">') +
            '<label class="auto-check"><input type="checkbox" class="stContinue"' + (step.continue_on_error === false ? '' : ' checked') + '> ' +
                A.esc(A.t('policies.field.continue_on_error')) + '</label>' +
            '<button class="chart-btn auto-danger stRemove" type="button">' + A.esc(A.t('policies.remove_step')) + '</button></fieldset>';
    }

    function notifyHtml(selected) {
        if (!meta.mail_lists.length) { return '<div class="auto-sub">' + A.esc(A.t('policies.no_mail_lists')) + '</div>'; }
        return '<div class="auto-checks">' + meta.mail_lists.map(function (name) {
            return '<label class="auto-check"><input type="checkbox" class="pcNotify" value="' + A.esc(name) + '"' +
                (selected.indexOf(name) >= 0 ? ' checked' : '') + '> ' + A.esc(name) + '</label>';
        }).join('') + '</div>';
    }

    async function open(policy) {
        try {
            meta = await A.request('GET', '/api/policies/meta');     // Empfängerlisten können sich ändern
            targets = (await A.request('GET', '/api/hosts')).hosts || [];
        } catch (e) { A.message(e.message, 'error'); return; }
        original = policy || null;
        var p = policy || { name: '', enabled: true, dry_run: true, wait_seconds: 120, conditions: {},
                            trigger: { events: ['POWER_FAILURE'], scope: { type: 'all' } },
                            cancel_on: ['POWER_RESTORED'], repeat_guard_minutes: 30, notify: [],
                            steps: [{ targets: [], action: 'shutdown', delay_seconds: 0, timeout_seconds: 180 }] };
        var scope = p.trigger.scope || { type: 'all' };
        var scopeTypes = meta.scope_types.map(function (s) {
            return '<option value="' + s + '"' + (scope.type === s ? ' selected' : '') + '>' + A.esc(A.t('scope.' + s)) + '</option>';
        }).join('');
        var conditions = Object.keys(meta.conditions).map(function (key) {
            var range = meta.conditions[key];
            var value = p.conditions[key] === undefined ? '' : p.conditions[key];
            return field(A.t('conditions.' + key), '<input class="pcCond" data-key="' + key + '" type="number" min="' + range.min +
                '" max="' + range.max + '" value="' + A.esc(value) + '">');
        }).join('');

        var body =
            field(A.t('policies.field.name'), '<input id="pcName" value="' + A.esc(p.name) + '">') +
            '<label class="auto-check"><input type="checkbox" id="pcEnabled"' + (p.enabled ? ' checked' : '') + '> ' + A.esc(A.t('common.enabled')) + '</label>' +
            '<label class="auto-check"><input type="checkbox" id="pcDryRun"' + (p.dry_run ? ' checked' : '') + '> ' + A.esc(A.t('policies.field.dry_run')) + '</label>' +
            '<h4>' + A.esc(A.t('policies.section.trigger')) + '</h4>' +
            '<div class="auto-checks">' + checkboxes('pcEvent', meta.trigger_events, p.trigger.events, 'events.') + '</div>' +
            field(A.t('policies.field.scope'), '<select id="pcScopeType">' + scopeTypes + '</select>') +
            '<div id="pcScopeIdBox"' + (scope.type === 'all' ? ' style="display:none"' : '') + '>' +
                field(A.t('policies.field.scope_id'), '<select id="pcScopeId">' + scopeOptions(scope.type, scope.id) + '</select>') + '</div>' +
            '<h4>' + A.esc(A.t('policies.section.conditions')) + '</h4>' + conditions +
            '<h4>' + A.esc(A.t('policies.section.timing')) + '</h4>' +
            field(A.t('policies.field.wait'), '<input id="pcWait" type="number" min="0" max="3600" value="' + A.esc(p.wait_seconds) + '">') +
            '<div class="auto-checks">' + checkboxes('pcCancel', meta.cancel_events, p.cancel_on, 'events.') + '</div>' +
            field(A.t('policies.field.repeat_guard'), '<input id="pcGuard" type="number" min="0" max="1440" value="' + A.esc(p.repeat_guard_minutes) + '">') +
            '<h4>' + A.esc(A.t('policies.section.steps')) + '</h4>' +
            '<div id="pcSteps">' + p.steps.map(stepHtml).join('') + '</div>' +
            '<button class="chart-btn" type="button" id="pcAddStep">' + A.esc(A.t('policies.add_step')) + '</button>' +
            '<h4>' + A.esc(A.t('policies.section.notify')) + '</h4>' + notifyHtml(p.notify || []) +
            '<p><a href="#" data-help="automation">' + A.esc(A.t('policies.help')) + '</a></p>' +
            '<div id="pcResult" class="auto-result"></div>';
        var footer =
            '<button class="chart-btn" id="pcTest">' + A.esc(A.t('policies.test')) + '</button>' +
            '<button class="chart-btn" id="pcCancelBtn">' + A.esc(A.t('common.cancel')) + '</button>' +
            '<button class="chart-btn auto-primary" id="pcSave">' + A.esc(A.t('common.save')) + '</button>';
        A.openDialog(original ? A.t('policies.dialog_edit') : A.t('policies.dialog_new'), body, footer);

        document.getElementById('pcScopeType').addEventListener('change', function (ev) {
            document.getElementById('pcScopeIdBox').style.display = ev.target.value === 'all' ? 'none' : '';
            document.getElementById('pcScopeId').innerHTML = scopeOptions(ev.target.value, null);
        });
        document.getElementById('pcAddStep').addEventListener('click', function () {
            document.getElementById('pcSteps').insertAdjacentHTML('beforeend',
                stepHtml({ targets: [], action: 'shutdown', delay_seconds: 0, timeout_seconds: 180 }));
        });
        document.getElementById('pcSteps').addEventListener('click', function (ev) {
            if (ev.target.classList.contains('stRemove')) { ev.target.closest('.auto-step').remove(); }
        });
        document.getElementById('pcTest').addEventListener('click', test);
        document.getElementById('pcCancelBtn').addEventListener('click', A.closeDialog);
        document.getElementById('pcSave').addEventListener('click', save);
    }

    function values(selector) {
        return Array.prototype.map.call(document.querySelectorAll(selector), function (el) { return el.value; });
    }

    function formData() {
        var scopeType = document.getElementById('pcScopeType').value;
        var conditions = {};
        document.querySelectorAll('.pcCond').forEach(function (input) {
            if (input.value !== '') { conditions[input.getAttribute('data-key')] = Number(input.value); }
        });
        return {
            name: document.getElementById('pcName').value.trim(),
            enabled: document.getElementById('pcEnabled').checked,
            dry_run: document.getElementById('pcDryRun').checked,
            trigger: { events: values('.pcEvent:checked'),
                       scope: scopeType === 'all' ? { type: 'all' } : { type: scopeType, id: document.getElementById('pcScopeId').value } },
            conditions: conditions,
            wait_seconds: Number(document.getElementById('pcWait').value || 0),
            cancel_on: values('.pcCancel:checked'),
            repeat_guard_minutes: Number(document.getElementById('pcGuard').value || 0),
            notify: values('.pcNotify:checked'),
            steps: Array.prototype.map.call(document.querySelectorAll('#pcSteps .auto-step'), function (box) {
                return {
                    targets: Array.prototype.map.call(box.querySelectorAll('.stTarget:checked'), function (el) { return el.value; }),
                    action: box.querySelector('.stAction').value,
                    delay_seconds: Number(box.querySelector('.stDelay').value || 0),
                    timeout_seconds: Number(box.querySelector('.stTimeout').value || 180),
                    continue_on_error: box.querySelector('.stContinue').checked,
                };
            }),
        };
    }

    function reportHtml(report) {
        var html = '<div class="auto-badge ' + (report.ready ? 'ok' : 'warn') + '">' +
            A.esc(report.ready ? A.t('policies.test_ready') : A.t('policies.test_not_ready')) + '</div>';
        if (report.notify && report.notify.length) {
            html += '<div class="auto-sub">' + A.esc(A.t('policies.test_notify', { lists: report.notify.join(', ') })) + '</div>';
        }
        if (report.applies === false) { html += '<div class="auto-sub">' + A.esc(A.t('policies.test_not_applies')) + '</div>'; }
        report.steps.forEach(function (step) {
            html += '<div class="auto-sub"><b>' + A.esc(A.t('policies.step')) + ' ' + step.step + '</b></div><ul class="auto-steps">';
            step.targets.forEach(function (check) {
                html += '<li>' + (check.success ? '✅ ' : '❌ ') + A.esc(check.target) + ': ' + A.esc(check.message) +
                    (check.command ? '<br><code>' + A.esc(check.command) + '</code>' : '') + '</li>';
            });
            html += '</ul>';
        });
        return html;
    }

    async function test() {
        var box = document.getElementById('pcResult');
        box.textContent = A.t('common.testing');
        try { box.innerHTML = reportHtml(await A.request('POST', '/api/policies/simulate', formData())); }
        catch (e) { box.textContent = e.message; }
    }

    async function save() {
        try {
            if (original) { await A.request('PUT', '/api/policies/' + encodeURIComponent(original.name), formData()); }
            else { await A.request('POST', '/api/policies', formData()); }
            A.closeDialog();
            A.message(A.t('common.saved'), 'success');
            window.AutoPolicies.load();
        } catch (e) {
            document.getElementById('pcResult').textContent = e.message;
        }
    }

    window.AutoPolicyForm = { open: open };
})();
