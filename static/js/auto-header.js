/* Kopfzeile: Testmodus (Schalter + Banner) und Sprachauswahl. */
(function () {
    'use strict';
    var A = window.Auto;

    function showDryRun(state) {
        var banner = document.getElementById('dryRunBanner');
        var button = document.getElementById('dryRunToggle');
        if (banner) { banner.style.display = state.dry_run ? '' : 'none'; }
        if (button) {
            button.textContent = state.dry_run ? A.t('dryrun.button_on') : A.t('dryrun.button_off');
            button.classList.toggle('dry-run-on', !!state.dry_run);
            button.disabled = !!state.forced;
            button.title = state.forced ? A.t('dryrun.error.forced') : '';
        }
    }

    async function showNoProfileHint() {
        try {
            var profiles = await A.request('GET', '/api/profiles');
            document.getElementById('noProfileHint').style.display = profiles.length ? 'none' : '';
        } catch (e) { /* ohne Anmeldung */ }
    }

    async function loadDryRun() {
        try { showDryRun(await A.request('GET', '/api/config/dry-run')); } catch (e) { /* ohne Anmeldung */ }
    }

    async function toggleDryRun() {
        var button = document.getElementById('dryRunToggle');
        var enable = !button.classList.contains('dry-run-on');
        try {
            showDryRun(await A.request('POST', '/api/config/dry-run', { enabled: enable }));
        } catch (e) {
            A.message(e.message, 'error');
        }
    }

    async function setLanguage(lang) {
        try {
            await A.request('POST', '/api/session/lang', { lang: lang });
            window.location.reload();
        } catch (e) {
            A.message(e.message, 'error');
        }
    }

    window.toggleDryRun = toggleDryRun;
    window.setLanguage = setLanguage;
    document.addEventListener('DOMContentLoaded', function () {
        var select = document.getElementById('languageSelect');
        if (select) { select.value = A.lang(); }
        loadDryRun();
        showNoProfileHint();
        setInterval(loadDryRun, 30000);
    });
})();
