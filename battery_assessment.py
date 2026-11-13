#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batteriebewertung in drei getrennten Dimensionen

A. Technischer Zustand (condition)
   Was die USV selbst über die Batterie meldet: Lebensdauer-Status, Austausch-
   Indikator, Zustands-Flags, Batteriestatus, Selbsttest. "Unauffällig" nur mit
   positivem, aktuellem Nachweis (Selbsttest ≤ 1 Jahr bestanden oder Lebensdauer-
   Status "okay"); ohne Nachweis "Unbekannt".

B. Restlebensdauer (lifetime)
   Aus dem empfohlenen Austauschdatum der USV (APC upsAdvBatteryRecommendedReplaceDate)
   oder aus Einbaudatum + typischer Lebensdauer (VRLA 5 Jahre). Ohne beides "Unbekannt".

C. Datenbasis (data_quality)
   Welche Angaben vorliegen bzw. fehlen: Einbau-/Austauschdatum, aktueller Selbsttest,
   Messhistorie, Laufzeitkalibrierung.

Gesamtbewertung = schlechterer Wert aus A und B. Sind A und B unbekannt, ist die
Gesamtbewertung UNKNOWN. Die statistische Heuristik (battery_ml.py) fließt nicht in
die Bewertung ein; ihr Trend ist erst ab MIN_TREND_DAYS Messhistorie aussagekräftig.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from i18n import current, t

LIFETIME_YEARS = 5.0
WARN_AGE_YEARS = 3.5
WARN_REPLACE_DAYS = 180
SELFTEST_MAX_AGE_DAYS = 365
MIN_TREND_DAYS = 30

# Rangfolge für die Gesamtbewertung; UNKNOWN ist kein Risiko, sondern fehlendes Wissen
SEVERITY = {'GOOD': 0, 'WARNING': 1, 'CRITICAL': 2}


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _flags(value) -> str:
    text = str(value or '').strip().strip('"')
    return text if text and set(text) <= {'0', '1'} else ''


def apc_state_flag(flags, number: int) -> bool:
    """Flag n (1-basiert) aus upsBasicStateOutputState, z. B. "0000100000…" → Flag 5 gesetzt."""
    text = _flags(flags)
    return len(text) >= number and text[number - 1] == '1'


def apc_health_bit(health, bit: int) -> bool:
    """Bit n (0-basiert) aus upsHighPrecBatteryHealth, z. B. "0010000000000000" → Bit 2 gesetzt."""
    text = _flags(health)
    return len(text) > bit and text[bit] == '1'


def parse_date(value) -> Optional[date]:
    """Akzeptiert 'YYYY-MM-DD' (config.json), 'YYYY/MM/DD' (NUT) und 'mm/dd/yyyy' bzw. 'mm/dd/yy' (APC)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().strip('"')
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fmt(d: date) -> str:
    return d.strftime('%d.%m.%Y') if current() == 'de' else d.isoformat()


def _worst(*levels: str) -> str:
    known = [lvl for lvl in levels if lvl in SEVERITY]
    return max(known, key=SEVERITY.get) if known else 'UNKNOWN'


def assess_condition(data: Dict[str, Any], today: date) -> Dict[str, Any]:
    """A. Technischer Zustand aus den Meldungen der USV."""
    status, findings, evidence = None, [], []

    def flag(level: str, text: str) -> None:
        nonlocal status
        status = _worst(status or 'GOOD', level)
        findings.append(text)

    sku = str(data.get('battery_sku_apc') or '').strip().strip('"')
    cartridge = f' ({sku})' if sku and sku != 'N/A' else ''
    health = data.get('battery_health_apc')
    if apc_health_bit(health, 2):
        flag('CRITICAL', t('assess.life_exceeded', cartridge=cartridge))
    elif apc_health_bit(health, 1) or apc_health_bit(health, 3):
        flag('WARNING', t('assess.life_soon', cartridge=cartridge))
    elif apc_health_bit(health, 0):
        evidence.append(t('assess.life_ok'))

    if _int(data.get('battery_replace_indicator')) == 2 or apc_state_flag(data.get('state_flags_apc'), 5):
        flag('CRITICAL', t('assess.replace'))
    battery_status = _int(data.get('battery_status_mib'))
    if battery_status == 3:
        flag('WARNING', t('assess.low'))
    elif battery_status == 4:
        flag('CRITICAL', t('assess.depleted'))

    selftest_date = parse_date(data.get('selftest_date_apc'))
    selftest_recent = bool(selftest_date) and (today - selftest_date).days <= SELFTEST_MAX_AGE_DAYS
    apc_test, mib_test = _int(data.get('selftest_result_apc')), _int(data.get('selftest_result_mib'))
    if apc_test == 2 or mib_test == 3:
        flag('CRITICAL', t('assess.selftest_failed'))
    elif mib_test == 2:
        flag('WARNING', t('assess.selftest_warning'))
    elif (apc_test == 1 or mib_test == 1) and selftest_recent:
        evidence.append(t('assess.selftest_passed', date=_fmt(selftest_date)))
    elif mib_test == 1 and not selftest_date and data.get('connection_type') == 'usb':
        # NUT nennt kein Datum, kennt das Ergebnis aber nur seit dem letzten Start des Treibers
        evidence.append(t('assess.selftest_passed_undated'))

    if status is None:
        status = 'GOOD' if evidence else 'UNKNOWN'
    if status == 'UNKNOWN':
        findings.append(t('assess.no_diagnosis'))
    return {'status': status, 'findings': findings, 'evidence': evidence,
            'last_selftest': selftest_date.isoformat() if selftest_date else None}


def assess_lifetime(data: Dict[str, Any], profile_cfg: Dict[str, Any], today: date) -> Dict[str, Any]:
    """B. Restlebensdauer aus Austauschdatum der USV oder Einbaudatum + 5 Jahre."""
    configured = parse_date(profile_cfg.get('battery_installed'))
    installed = configured or parse_date(data.get('battery_replace_date'))
    recommended = parse_date(data.get('battery_replace_recommended_apc'))
    # Empfehlung älter als der eingetragene Einbau: Sie stammt von der alten Batterie (USV-Datum nicht zurückgesetzt)
    if recommended and configured and recommended <= configured:
        recommended = None
    age_years = round((today - installed).days / 365.25, 1) if installed else None

    if recommended:
        days_left = (recommended - today).days
        basis = t('assess.basis_ups', date=_fmt(recommended))
    elif installed:
        days_left = round((LIFETIME_YEARS - age_years) * 365.25)
        basis = t('assess.basis_installed', date=_fmt(installed), years=f'{LIFETIME_YEARS:.0f}')
    else:
        return {'status': 'UNKNOWN', 'months': None, 'basis': None, 'findings': [],
                'installed': None, 'installed_configured': None, 'age_years': None, 'replace_date': None}

    months = max(0, round(days_left / 30.44))
    findings = []
    if days_left < 0:
        status = 'CRITICAL'
        findings.append(t('assess.overdue', date=_fmt(recommended)) if recommended
                        else t('assess.too_old', years=f'{age_years:.1f}'))
    elif days_left <= WARN_REPLACE_DAYS or (not recommended and age_years >= WARN_AGE_YEARS):
        status = 'WARNING'
        findings.append(t('assess.plan', months=months))
    else:
        status = 'GOOD'
    return {'status': status, 'months': months, 'basis': basis, 'findings': findings,
            'installed': installed.isoformat() if installed else None,
            'installed_configured': configured.isoformat() if configured else None, 'age_years': age_years,
            'replace_date': recommended.isoformat() if recommended else None}


def assess_data_quality(data: Dict[str, Any], condition: Dict[str, Any], lifetime: Dict[str, Any],
                        history_days: float) -> Dict[str, Any]:
    """C. Datenbasis: welche Angaben für eine belastbare Aussage vorliegen bzw. fehlen."""
    available, missing = [], []
    if lifetime['replace_date'] or lifetime['installed']:
        available.append(t('assess.have_date'))
    else:
        missing.append(t('assess.need_date'))
    if condition['evidence'] or condition['status'] in ('WARNING', 'CRITICAL'):
        available.append(t('assess.have_diagnosis'))
    else:
        missing.append(t('assess.need_selftest'))
    if history_days >= MIN_TREND_DAYS:
        available.append(t('assess.have_history', days=f'{history_days:.0f}'))
    else:
        missing.append(t('assess.need_history', days=f'{history_days:.0f}', min=MIN_TREND_DAYS))
    if _int(data.get('calibration_result_apc')) == 2:
        missing.append(t('assess.need_calibration'))

    level = 'GOOD' if len(missing) == 0 else 'LIMITED' if available else 'INSUFFICIENT'
    return {'level': level, 'available': available, 'missing': missing}


def assess(data: Dict[str, Any], profile_cfg: Dict[str, Any], history_days: float = 0.0,
           today: Optional[date] = None) -> Dict[str, Any]:
    """Bewertet die Batterie in drei Dimensionen und fasst sie zusammen."""
    today = today or date.today()
    condition = assess_condition(data, today)
    lifetime = assess_lifetime(data, profile_cfg, today)
    quality = assess_data_quality(data, condition, lifetime, history_days)

    overall = _worst(condition['status'], lifetime['status'])
    if overall == 'GOOD' and 'UNKNOWN' in (condition['status'], lifetime['status']):
        overall_text = t('assess.overall_partial')
    else:
        overall_text = t(f'assess.overall.{overall}')

    hints: List[str] = []
    if apc_state_flag(data.get('state_flags_apc'), 1):
        hints.append(t('assess.abnormal'))
    selftest = parse_date(data.get('selftest_date_apc'))
    if selftest and (today - selftest).days > SELFTEST_MAX_AGE_DAYS:
        hints.append(t('assess.selftest_old', date=_fmt(selftest)))
    if _int(data.get('selftest_result_mib')) == 6:
        hints.append(t('assess.selftest_never'))
    if data.get('battery_date_unconfirmed') and not profile_cfg.get('battery_installed'):
        hints.append(t('assess.date_unconfirmed'))
    if _int(data.get('calibration_result_apc')) == 2:
        calibrated = parse_date(data.get('calibration_date_apc'))
        since = t('assess.calibration_since', date=_fmt(calibrated)) if calibrated else ''
        effect = t('assess.no_runtime') if data.get('runtime_available') is False \
            else t('assess.runtime_inaccurate')
        hints.append(t('assess.calibration_invalid', since=since, effect=effect))

    return {
        'risk_level': overall,
        'overall_text': overall_text,
        'condition': condition,
        'lifetime': lifetime,
        'data_quality': quality,
        # Kurzfassungen (Kompatibilität)
        'reasons': [f for f in condition['findings'] if condition['status'] in ('WARNING', 'CRITICAL')]
                   + lifetime['findings'],
        'hints': hints,
        'battery_installed': lifetime['installed'],
        'battery_age_years': lifetime['age_years'],
        'last_selftest': condition['last_selftest'],
        'lifespan_remaining_months': lifetime['months'],
    }
