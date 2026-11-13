/* Hilfeseiten (help/<sprache>/<seite>.md) in einem eigenen Fenster anzeigen. */
(function () {
    'use strict';
    var A = window.Auto;
    var PAGE = /^[a-z0-9-]{1,40}$/;

    async function show(page) {
        if (!PAGE.test(page)) { return; }
        var overlay = document.getElementById('autoHelp');
        var body = document.getElementById('autoHelpBody');
        body.textContent = A.t('common.loading');
        overlay.style.display = 'flex';
        try {
            var response = await fetch('/help/' + A.lang() + '/' + page);
            if (!response.ok) { throw new Error('HTTP ' + response.status); }
            body.innerHTML = await response.text();    // eigene, im Repository gepflegte Inhalte
        } catch (e) {
            body.textContent = e.message;
        }
    }

    function close() { document.getElementById('autoHelp').style.display = 'none'; }

    document.addEventListener('click', function (ev) {
        var link = ev.target.closest('#autoHelpBody a');
        if (link) {
            var href = link.getAttribute('href') || '';
            if (PAGE.test(href)) { ev.preventDefault(); show(href); }
            return;
        }
        var opener = ev.target.closest('[data-help]');
        if (opener) { ev.preventDefault(); show(opener.getAttribute('data-help')); }
    });

    window.AutoHelp = { show: show, close: close };
})();
