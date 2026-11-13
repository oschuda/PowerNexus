/* Wartende Regeln: Hinweis mit Countdown und Abbrechen auf jeder Seite. */
(function () {
    'use strict';
    var A = window.Auto;
    var offset = 0;              // Serverzeit − Browserzeit
    var waiting = [];

    function remaining(executeAt) {
        var seconds = Math.max(0, Math.round(executeAt - (Date.now() / 1000 + offset)));
        var minutes = Math.floor(seconds / 60);
        return minutes + ':' + String(seconds % 60).padStart(2, '0');
    }

    function render() {
        var box = document.getElementById('autoCountdown');
        if (!box) { return; }
        box.style.display = waiting.length ? '' : 'none';
        box.innerHTML = waiting.map(function (run) {
            return '<div class="auto-countdown-row">⏳ ' + (run.dry_run ? '<b>[TEST]</b> ' : '') +
                A.esc(A.t('runs.countdown', { policy: run.policy, ups: run.ups_id })) +
                ' <b data-countdown="' + run.execute_at + '">' + remaining(run.execute_at) + '</b>' +
                (run.resumed ? ' <span class="auto-badge warn">' + A.esc(A.t('runs.resumed')) + '</span>' : '') +
                ' <button class="chart-btn auto-danger" data-run="' + A.esc(run.id) + '">' + A.esc(A.t('runs.cancel')) + '</button></div>';
        }).join('');
    }

    function tick() {
        document.querySelectorAll('[data-countdown]').forEach(function (el) {
            el.textContent = remaining(Number(el.getAttribute('data-countdown')));
        });
    }

    async function poll() {
        try {
            var data = await A.request('GET', '/api/policies/runs');
            offset = (data.server_time || Date.now() / 1000) - Date.now() / 1000;
            waiting = (data.runs || []).filter(function (run) { return run.status === 'waiting'; });
            render();
        } catch (e) { /* ohne Anmeldung oder kurz nicht erreichbar */ }
    }

    document.addEventListener('click', async function (ev) {
        var button = ev.target.closest('#autoCountdown [data-run]');
        if (!button) { return; }
        try { await A.request('POST', '/api/policies/runs/' + button.getAttribute('data-run') + '/cancel'); }
        catch (e) { A.message(e.message, 'error'); }
        poll();
    });

    poll();
    setInterval(poll, 5000);
    setInterval(tick, 1000);
    window.AutoCountdown = { remaining: remaining };
})();
