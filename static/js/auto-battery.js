/* Reiter Batterie: Einbaudatum anzeigen und ändern. */
(function () {
    'use strict';
    var A = window.Auto;

    function today() {
        var now = new Date();
        var month = String(now.getMonth() + 1).padStart(2, '0');
        var day = String(now.getDate()).padStart(2, '0');
        return now.getFullYear() + '-' + month + '-' + day;
    }

    function show(data) {
        var input = document.getElementById('batInstalled');
        if (!input) { return; }
        var lifetime = data.lifetime || {};
        input.max = today();
        input.value = lifetime.installed_configured || '';
        var hint = document.getElementById('batInstalledHint');
        hint.textContent = !lifetime.installed_configured && lifetime.installed
            ? A.t('battery.installed.from_ups', { date: new Date(lifetime.installed).toLocaleDateString(A.lang()) }) : '';
    }

    async function save(date) {
        var box = document.getElementById('batInstalledResult');
        var select = document.getElementById('batteryProfileSelect');
        try {
            await A.request('POST', '/api/battery/installed', { ups_id: select ? select.value : '', date: date });
            box.textContent = A.t('common.saved');
            await window.loadBatteryStatus();
        } catch (e) { box.textContent = e.message; }
    }

    document.addEventListener('click', function (ev) {
        if (ev.target.id === 'batInstalledSave') { save(document.getElementById('batInstalled').value); }
        if (ev.target.id === 'batInstalledToday') { save(today()); }
    });

    window.AutoBattery = { show: show };
})();
