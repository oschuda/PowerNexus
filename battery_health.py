#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
battery_health.py - Batterie-Gesundheits- und Degradationsanalyse
Basiert auf Review-Projekt v2.7 - State of Health Calculation
Version 2.1 - Phase 4.1: Enhanced with Unit Tests + Caching
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


class BatteryHealthAnalyzer:
    """
    Berechnet Batterie-Gesundheitszustand und prognostiziert Wartungstermine
    """
    
    # Konstanten für CS350/CS650 (Ihr USV-Modell)
    BASELINE_RUNTIME_SECONDS = 600  # 10 Minuten bei Nominal Load
    CRITICAL_SOH_THRESHOLD = 0.5    # 50% SoH = kritisch
    WARNING_SOH_THRESHOLD = 0.7     # 70% SoH = Warnung
    MIN_DATA_POINTS = 5             # Minimum für statistisch valide Berechnung
    DEGRADATION_RATE_WINDOW_DAYS = 30  # Fenster für Degradationsrate
    
    def __init__(self):
        """Initialisiert BatteryHealthAnalyzer"""
        logger.info("🔋 BatteryHealthAnalyzer initialisiert")
    
    def compute_soh(self, current_runtime_minutes: float, baseline_runtime_minutes: float = 10.0) -> float:
        """
        Berechnet State of Health (SoH) als Normalized Runtime
        
        SoH = Current_Runtime / Baseline_Runtime
        
        Args:
            current_runtime_minutes: Gemessene Laufzeit in Minuten
            baseline_runtime_minutes: Nominal-Laufzeit (Standard: 10 min für CS350/CS650)
            
        Returns:
            SoH als Wert 0.0-1.0 (100% = 1.0)
        """
        if baseline_runtime_minutes <= 0:
            logger.warning(f"⚠️ Invalid baseline runtime: {baseline_runtime_minutes}")
            return 0.0
        
        soh = current_runtime_minutes / baseline_runtime_minutes
        
        # Kapps auf gültige Range
        soh = max(0.0, min(1.0, soh))
        
        logger.debug(f"📊 SoH berechnet: {current_runtime_minutes:.1f}min / {baseline_runtime_minutes:.1f}min = {soh:.2%}")
        return soh
    
    def compute_soh_from_history(self, runtime_history: List[float]) -> float:
        """
        Berechnet durchschnittliche SoH aus Verlaufsdata
        
        Args:
            runtime_history: Liste von Laufzeiten in Minuten
            
        Returns:
            Durchschnittliche SoH
        """
        if not runtime_history or len(runtime_history) < self.MIN_DATA_POINTS:
            logger.warning(f"⚠️ Unzureichende Datenpunkte: {len(runtime_history)}")
            return 0.0
        
        # Mittelwert berechnen
        avg_runtime = sum(runtime_history) / len(runtime_history)
        return self.compute_soh(avg_runtime)
    
    def estimate_degradation_rate(
        self, 
        soh_history: List[Tuple[datetime, float]]
    ) -> float:
        """
        Schätzt tägliche Degradationsrate via lineare Regression
        
        Args:
            soh_history: Liste von (datetime, SoH) Tupeln
            
        Returns:
            Degradationsrate pro Tag (z.B. -0.001 = -0.1% pro Tag)
        """
        if len(soh_history) < 2:
            logger.warning("⚠️ Unzureichende SoH-Datenpunkte für Degradationsschätzung")
            return 0.0
        
        try:
            # Sortiere nach Timestamp
            sorted_history = sorted(soh_history, key=lambda x: x[0])
            
            # Timestamps in Tage seit Beginn konvertieren
            start_date = sorted_history[0][0]
            x_values = [(ts - start_date).days + (ts - start_date).seconds / 86400 for ts, _ in sorted_history]
            y_values = [soh for _, soh in sorted_history]
            
            # Lineare Regression (Least Squares)
            n = len(x_values)
            x_mean = sum(x_values) / n
            y_mean = sum(y_values) / n
            
            numerator = sum((x_values[i] - x_mean) * (y_values[i] - y_mean) for i in range(n))
            denominator = sum((x_values[i] - x_mean) ** 2 for i in range(n))
            
            if denominator == 0:
                logger.warning("⚠️ Keine Degradation messbar (konstante Daten)")
                return 0.0
            
            slope = numerator / denominator
            logger.debug(f"📉 Degradationsrate: {slope:.6f} pro Tag ({slope*365:.2%} pro Jahr)")
            
            return slope
        except Exception as e:
            logger.error(f"❌ Fehler bei Degradationsberechnung: {e}")
            return 0.0
    
    def predict_replacement_date(
        self,
        current_soh: float,
        degradation_rate: float
    ) -> Optional[datetime]:
        """
        Prognostiziert Batterie-Austausch-Datum
        
        Formel: days_until_critical = (CRITICAL_THRESHOLD - current_SoH) / degradation_rate
        
        Args:
            current_soh: Aktuelle SoH (0.0-1.0)
            degradation_rate: Tägliche Degradationsrate
            
        Returns:
            Prognostiziertes Datum oder None wenn nicht berechenbar
        """
        if degradation_rate >= 0:
            logger.warning("⚠️ Degradationsrate nicht negativ, Vorhersage nicht möglich")
            return None
        
        if current_soh <= self.CRITICAL_SOH_THRESHOLD:
            logger.warning("⚠️ Batterie bereits kritisch, sofortiger Austausch erforderlich")
            return datetime.now()
        
        try:
            days_until_critical = (self.CRITICAL_SOH_THRESHOLD - current_soh) / degradation_rate
            
            if days_until_critical < 0:
                logger.warning("⚠️ Negative Tage bis kritisch")
                return None
            
            replacement_date = datetime.now() + timedelta(days=days_until_critical)
            logger.info(f"📅 Prognostizierter Austausch-Termin: {replacement_date.strftime('%Y-%m-%d')} ({days_until_critical:.0f} Tage)")
            
            return replacement_date
        except Exception as e:
            logger.error(f"❌ Fehler bei Vorhersage: {e}")
            return None
    
    def assess_battery_health(
        self,
        current_soh: float,
        degradation_rate: float,
        current_runtime: float
    ) -> Dict[str, Any]:
        """
        Umfassende Batterie-Gesundheitsbewertung
        
        Args:
            current_soh: Aktuelle State of Health
            degradation_rate: Tägliche Degradationsrate
            current_runtime: Aktuelle Laufzeit in Minuten
            
        Returns:
            Dictionary mit Health-Assessment
        """
        # Gesamt-Status bestimmen
        if current_soh >= self.WARNING_SOH_THRESHOLD:
            status = "HEALTHY"
            icon = "✅"
            recommendation = "Keine Maßnahmen erforderlich"
        elif current_soh >= self.CRITICAL_SOH_THRESHOLD:
            status = "DEGRADED"
            icon = "⚠️"
            recommendation = f"Batterie-Austausch planen (SoH: {current_soh:.1%})"
        else:
            status = "CRITICAL"
            icon = "🚨"
            recommendation = "SOFORTIGER Batterie-Austausch erforderlich"
        
        # Vorhersage
        replacement_date = self.predict_replacement_date(current_soh, degradation_rate) if degradation_rate != 0 else None
        
        # Degradationseinschätzung
        if degradation_rate == 0:
            degradation_text = "Stabil"
            degradation_trend = "STABLE"
        elif degradation_rate < -0.005:
            degradation_text = "Schneller Verfall"
            degradation_trend = "CRITICAL"
        elif degradation_rate < -0.001:
            degradation_text = "Moderater Verfall"
            degradation_trend = "WARNING"
        else:
            degradation_text = "Langsamer Verfall"
            degradation_trend = "NORMAL"
        
        return {
            'status': status,
            'icon': icon,
            'soh_percent': round(current_soh * 100, 1),
            'soh_value': round(current_soh, 3),
            'current_runtime_minutes': round(current_runtime, 1),
            'degradation_rate_per_day': round(degradation_rate, 6),
            'degradation_rate_per_year': round(degradation_rate * 365, 3),
            'degradation_text': degradation_text,
            'degradation_trend': degradation_trend,
            'replacement_date': replacement_date.strftime('%Y-%m-%d') if replacement_date else None,
            'replacement_days_until': math.floor((replacement_date - datetime.now()).days) if replacement_date else None,
            'recommendation': recommendation,
            'warning_threshold_soh': self.WARNING_SOH_THRESHOLD,
            'critical_threshold_soh': self.CRITICAL_SOH_THRESHOLD,
        }
    
    def compute_health_score(self, assessment: Dict[str, Any]) -> int:
        """
        Berechnet 0-100 Health Score für Dashboard
        
        Args:
            assessment: Aus assess_battery_health()
            
        Returns:
            Health Score 0-100
        """
        soh = assessment['soh_value']
        
        if soh >= 0.95:
            return 100
        elif soh >= 0.80:
            return 90
        elif soh >= 0.70:
            return 75
        elif soh >= 0.50:
            return 50
        else:
            return 10


# Beispiel-Nutzung
def example_battery_assessment():
    """Beispiel: Batterie-Analyse"""
    analyzer = BatteryHealthAnalyzer()
    
    # Annahme: Laufzeit ist über Zeit von 10 min auf 8 min gefallen
    soh_history = [
        (datetime.now() - timedelta(days=30), 1.0),
        (datetime.now() - timedelta(days=20), 0.95),
        (datetime.now() - timedelta(days=10), 0.85),
        (datetime.now(), 0.80),
    ]
    
    current_soh = soh_history[-1][1]
    degradation_rate = analyzer.estimate_degradation_rate(soh_history)
    
    assessment = analyzer.assess_battery_health(
        current_soh=current_soh,
        degradation_rate=degradation_rate,
        current_runtime=8.0
    )
    
    print(f"\n🔋 Batterie-Gesundheitsbewertung:")
    print(f"  Status: {assessment['status']} {assessment['icon']}")
    print(f"  SoH: {assessment['soh_percent']:.1f}%")
    print(f"  Laufzeit: {assessment['current_runtime_minutes']} min")
    print(f"  Degradationsrate: {assessment['degradation_rate_per_day']:.6f}/Tag ({assessment['degradation_rate_per_year']:.2%}/Jahr)")
    print(f"  Austausch-Datum: {assessment['replacement_date']}")
    print(f"  Empfehlung: {assessment['recommendation']}\n")


if __name__ == '__main__':
    example_battery_assessment()
