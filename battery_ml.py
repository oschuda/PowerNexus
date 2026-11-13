#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Battery Life Predictor - ML-basierte Restlebensdauer-Vorhersage für USV-Batterien.

Features:
  - Random Forest Regressor (scikit-learn)
  - 10 Features aus SQLite-Historie extrahiert
  - Heuristik-Fallback wenn <30 Tage Daten vorhanden
  - Prediction-Cache (TTL 6 Stunden)
  - Modell-Persistenz via joblib

Benötigt: scikit-learn, numpy, joblib
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Konfiguration
MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "battery_rf_model.joblib"
SCALER_PATH = MODEL_DIR / "battery_scaler.joblib"
CACHE_TTL_SECONDS = 6 * 3600  # 6 Stunden
MIN_HISTORY_DAYS = 30          # Mindest-Tage für ML-Vorhersage
BATTERY_MAX_LIFE_DAYS = 1825   # 5 Jahre = typische max. Batterielebensdauer

# Risk-Level-Schwellenwerte (in Tagen Restlebensdauer)
RISK_GOOD = 180
RISK_WARNING = 90
RISK_CRITICAL = 30


# ---------------------------------------------------------------------------
# Verfügbarkeitsprüfung scikit-learn
# ---------------------------------------------------------------------------
_sklearn_available = False
try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import cross_val_score, train_test_split
    from sklearn.preprocessing import StandardScaler
    import joblib
    _sklearn_available = True
    logger.info("✅ scikit-learn verfügbar — ML-Vorhersage aktiv")
except ImportError:
    logger.warning("⚠️  scikit-learn nicht installiert — nur Heuristik verfügbar")


class BatteryLifePredictor:
    """ML-gestützte Restlebensdauer-Vorhersage für USV-Batterien.

    Nutzt einen Random Forest Regressor, der aus historischen Messwerten
    der SQLite-Datenbank trainiert wird. Fällt auf regelbasierte Heuristik
    zurück, wenn nicht genügend Daten vorhanden sind oder scikit-learn fehlt.
    """

    def __init__(self) -> None:
        self._model: Optional[Any] = None
        self._scaler: Optional[Any] = None
        self._feature_importances: Dict[str, float] = {}
        self._cache: Dict[str, Dict] = {}  # {profile: {result, expires_at}}
        self._cv_scores: List[float] = []

        MODEL_DIR.mkdir(exist_ok=True)
        self._load_model()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_features(self, history: Dict[str, List]) -> Optional[np.ndarray]:
        """Extrahiert 10 Features aus einer Geschichte-Dict (get_history()-Format).

        Args:
            history: Dict mit Listen 'battery_charge', 'battery_runtime',
                     'battery_temperature', 'ups_load', 'timestamp', …

        Returns:
            numpy-Array der Form (1, 10) oder None wenn zu wenig Daten.
        """
        charges = [v for v in history.get("battery_charge", []) if v and v > 0]
        runtimes = [v for v in history.get("battery_runtime", []) if v and v > 0]
        temps = [v for v in history.get("battery_temperature", []) if v and v > 0]
        loads = [v for v in history.get("ups_load", []) if v is not None]
        timestamps = history.get("timestamp", [])

        if len(charges) < 5:
            return None

        # 1. Kapazitäts-Trend (Degradationsrate pro Tag)
        capacity_trend = self._linear_trend(charges)

        # 2. Durchschnittstemperatur
        avg_temp = float(np.mean(temps)) if temps else 25.0

        # 3. Temperatur-Trend
        temp_trend = self._linear_trend(temps) if len(temps) >= 5 else 0.0

        # 4. Geschätzte Ladezyklen (Wechsel von Entlade- auf Ladebetrieb)
        charge_cycles = self._count_charge_cycles(charges)

        # 5. Batteriealter in Tagen (abgeleitet aus Timestamp-Spanne)
        age_days = 0.0
        if len(timestamps) >= 2:
            try:
                t_start = float(timestamps[0])
                t_end = float(timestamps[-1])
                age_days = (t_end - t_start) / 86400.0
            except (TypeError, ValueError):
                age_days = 0.0

        # 6. Durchschnittliche Last
        avg_load = float(np.mean(loads)) if loads else 50.0

        # 7. Last-Varianz (Stabilitätsindikator)
        load_variance = float(np.var(loads)) if len(loads) >= 2 else 0.0

        # 8. Laufzeit-Trend (Abnahme der Pufferzeit)
        runtime_trend = self._linear_trend(runtimes) if len(runtimes) >= 5 else 0.0

        # 9. Spannungs-Trend (aus battery_charge als Proxy, wenn kein direktes Feld)
        voltage_series = [v for v in history.get("battery_voltage", []) if v and v > 0]
        voltage_trend = self._linear_trend(voltage_series) if len(voltage_series) >= 5 else 0.0

        # 10. Kritische Ereignisse (Ladung < 20%)
        critical_events = sum(1 for c in charges if c < 20)

        features = np.array([[
            capacity_trend,
            avg_temp,
            temp_trend,
            float(charge_cycles),
            age_days,
            avg_load,
            load_variance,
            runtime_trend,
            voltage_trend,
            float(critical_events),
        ]])

        logger.debug(
            f"Features extrahiert: trend={capacity_trend:.4f}, temp={avg_temp:.1f}°C, "
            f"cycles={charge_cycles}, age={age_days:.0f}d, load={avg_load:.1f}%"
        )
        return features

    def train(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """Trainiert den Random Forest Regressor.

        Args:
            X: Feature-Matrix (n_samples, 10)
            y: Zielvektor — verbleibende Tage bis Batterieaustausch (n_samples,)

        Returns:
            Dict mit train_score, cv_mean, cv_std, feature_importances.
        """
        if not _sklearn_available:
            return {"error": "scikit-learn nicht verfügbar"}

        if len(X) < 10:
            return {"error": "Zu wenig Trainingsdaten (min. 10 Samples)"}

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        self._scaler = StandardScaler()
        X_train_scaled = self._scaler.fit_transform(X_train)
        X_test_scaled = self._scaler.transform(X_test)

        self._model = RandomForestRegressor(
            n_estimators=100,
            max_depth=10,
            min_samples_split=3,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X_train_scaled, y_train)

        # Cross-Validation
        cv_scores = cross_val_score(
            self._model, X_train_scaled, y_train, cv=5, scoring="r2"
        )
        self._cv_scores = cv_scores.tolist()

        # Feature Importances
        feature_names = [
            "capacity_trend", "avg_temp", "temp_trend", "charge_cycles",
            "age_days", "avg_load", "load_variance", "runtime_trend",
            "voltage_trend", "critical_events",
        ]
        self._feature_importances = {
            name: round(float(imp), 4)
            for name, imp in zip(feature_names, self._model.feature_importances_)
        }

        train_score = float(self._model.score(X_test_scaled, y_test))

        self._save_model()

        result = {
            "train_score_r2": round(train_score, 4),
            "cv_mean_r2": round(float(np.mean(cv_scores)), 4),
            "cv_std": round(float(np.std(cv_scores)), 4),
            "n_samples": len(X),
            "feature_importances": self._feature_importances,
        }
        logger.info(f"✅ ML-Modell trainiert: R²={train_score:.3f}, CV={np.mean(cv_scores):.3f}±{np.std(cv_scores):.3f}")
        return result

    def predict(self, history: Dict[str, List], profile: str = "unknown") -> Dict[str, Any]:
        """Sagt die Restlebensdauer der Batterie voraus.

        Args:
            history: Dict aus database.get_history() (180-Tage-Daten empfohlen)
            profile: Profilname für Cache-Key und Logging

        Returns:
            Dict mit days_remaining, confidence_percent, risk_level,
            recommendation, degradation_rate, feature_importances, method.
        """
        # Cache-Check
        cached = self._cache.get(profile)
        if cached and cached["expires_at"] > time.time():
            logger.debug(f"Cache-Hit für Profil '{profile}'")
            return cached["result"]

        timestamps = history.get("timestamp", [])
        available_days = 0
        if len(timestamps) >= 2:
            try:
                available_days = (float(timestamps[-1]) - float(timestamps[0])) / 86400.0
            except (TypeError, ValueError):
                pass

        # ML-Vorhersage wenn genug Daten und Modell vorhanden
        if _sklearn_available and self._model and self._scaler and available_days >= MIN_HISTORY_DAYS:
            result = self._ml_predict(history, profile)
        else:
            result = self._heuristic_predict(history, profile)
            if available_days < MIN_HISTORY_DAYS and available_days > 0:
                result["note"] = (
                    f"Nur {available_days:.0f} Tage Daten verfügbar "
                    f"(min. {MIN_HISTORY_DAYS} für ML). Heuristik genutzt."
                )

        # Feature Importances anhängen
        result["feature_importances"] = self._feature_importances or {}

        # In Cache speichern
        self._cache[profile] = {
            "result": result,
            "expires_at": time.time() + CACHE_TTL_SECONDS,
        }
        return result

    def can_predict(self) -> bool:
        """Nur mit trainiertem Modell lohnt es sich, die Messhistorie zu laden"""
        return bool(_sklearn_available and self._model and self._scaler)

    def invalidate_cache(self, profile: Optional[str] = None) -> None:
        """Cache invalidieren — für alle oder ein Profil."""
        if profile:
            self._cache.pop(profile, None)
        else:
            self._cache.clear()

    # ------------------------------------------------------------------
    # Private: ML-Vorhersage
    # ------------------------------------------------------------------

    def _ml_predict(self, history: Dict[str, List], profile: str) -> Dict[str, Any]:
        """Vorhersage mit trainiertem RF-Modell."""
        features = self.extract_features(history)
        if features is None:
            return self._heuristic_predict(history, profile)

        try:
            X_scaled = self._scaler.transform(features)
            # Einzelvorhersagen aller Bäume → Konfidenz aus Streuung ableiten
            tree_predictions = np.array([
                tree.predict(X_scaled)[0]
                for tree in self._model.estimators_
            ])
            days_remaining = float(np.clip(np.mean(tree_predictions), 0, BATTERY_MAX_LIFE_DAYS))
            std_dev = float(np.std(tree_predictions))

            # Konfidenz: 100% wenn Streuung ~0, sinkt bei hoher Streuung
            max_std = BATTERY_MAX_LIFE_DAYS / 4
            confidence = max(0.0, min(100.0, 100.0 * (1.0 - std_dev / max_std)))

            # Degradationsrate aus Feature extrahieren
            degradation_rate = float(features[0][0])

            result = self._build_result(
                days_remaining=round(days_remaining),
                confidence=round(confidence, 1),
                degradation_rate=degradation_rate,
                method="ml",
                profile=profile,
            )
            logger.info(
                f"🤖 ML-Vorhersage '{profile}': {days_remaining:.0f}d, "
                f"Konfidenz={confidence:.0f}%, Risiko={result['risk_level']}"
            )
            return result

        except Exception as e:
            logger.error(f"❌ ML-Vorhersage fehlgeschlagen für '{profile}': {e}")
            return self._heuristic_predict(history, profile)

    # ------------------------------------------------------------------
    # Private: Heuristik-Fallback
    # ------------------------------------------------------------------

    def _heuristic_predict(self, history: Dict[str, List], profile: str) -> Dict[str, Any]:
        """Regelbasierte Heuristik wenn zu wenig Daten oder kein ML-Modell."""
        charges = [v for v in history.get("battery_charge", []) if v and v > 0]
        temps = [v for v in history.get("battery_temperature", []) if v and v > 0]
        runtimes = [v for v in history.get("battery_runtime", []) if v and v > 0]

        # Baseline: 365 Tage
        days = 365.0

        if charges:
            # Kapazitäts-Trend (negativ = Degradation)
            trend = self._linear_trend(charges)
            # Konservative Schätzung: Trend * 100 Tage Skalierung
            trend_adjustment = trend * 200
            days += trend_adjustment

        if temps:
            avg_temp = np.mean(temps)
            # Arrhenius-Regel: +10°C halbiert Batterielebensdauer
            if avg_temp > 45:
                days *= 0.4
            elif avg_temp > 35:
                days *= 0.7
            elif avg_temp < 15:
                days *= 0.85  # Kälte ist auch ungünstig

        if runtimes and len(runtimes) >= 5:
            runtime_trend = self._linear_trend(runtimes)
            # Negativer Trend = schlechter werdende Pufferzeit
            days += runtime_trend * 100

        days = max(0.0, min(days, BATTERY_MAX_LIFE_DAYS))
        degradation_rate = self._linear_trend(charges) if charges else 0.0

        result = self._build_result(
            days_remaining=round(days),
            confidence=45.0,  # Heuristik hat niedrigere Konfidenz
            degradation_rate=degradation_rate,
            method="heuristic",
            profile=profile,
        )
        logger.info(
            f"📐 Heuristik '{profile}': {days:.0f}d, Risiko={result['risk_level']}"
        )
        return result

    # ------------------------------------------------------------------
    # Private: Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _linear_trend(series: List[float]) -> float:
        """Berechnet den linearen Trend einer Zeitreihe (Slope per Index)."""
        if len(series) < 2:
            return 0.0
        x = np.arange(len(series), dtype=float)
        try:
            slope = float(np.polyfit(x, series, 1)[0])
        except (np.linalg.LinAlgError, ValueError):
            slope = 0.0
        return slope

    @staticmethod
    def _count_charge_cycles(charges: List[float]) -> int:
        """Zählt Lade-/Entladezyklen (Übergang von fallend → steigend)."""
        if len(charges) < 3:
            return 0
        cycles = 0
        was_falling = False
        for i in range(1, len(charges)):
            delta = charges[i] - charges[i - 1]
            if delta < -2:
                was_falling = True
            elif delta > 2 and was_falling:
                cycles += 1
                was_falling = False
        return cycles

    @staticmethod
    def _build_result(
        days_remaining: int,
        confidence: float,
        degradation_rate: float,
        method: str,
        profile: str,
    ) -> Dict[str, Any]:
        """Baut das standardisierte Ergebnis-Dict auf."""
        if days_remaining >= RISK_GOOD:
            risk_level = "GOOD"
            recommendation = "✅ Batterie in gutem Zustand. Nächste Überprüfung in 6 Monaten."
        elif days_remaining >= RISK_WARNING:
            risk_level = "WARNING"
            recommendation = "⚠️ Batterie zeigt Alterungserscheinungen. Ersatz in ~3 Monaten planen."
        elif days_remaining >= RISK_CRITICAL:
            risk_level = "CRITICAL"
            recommendation = "🔶 Batterietausch bald erforderlich. Ersatzteile bestellen."
        else:
            risk_level = "IMMINENT"
            recommendation = "🔴 Sofortiger Batterietausch notwendig! Ausfallgefahr hoch."

        return {
            "profile": profile,
            "days_remaining": max(0, days_remaining),
            "confidence_percent": round(confidence, 1),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "degradation_rate": round(degradation_rate, 6),
            "method": method,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _save_model(self) -> None:
        """Persistiert Modell und Scaler auf Disk."""
        if not _sklearn_available:
            return
        try:
            joblib.dump(self._model, MODEL_PATH)
            joblib.dump(self._scaler, SCALER_PATH)
            logger.info(f"💾 Modell gespeichert: {MODEL_PATH}")
        except Exception as e:
            logger.error(f"❌ Modell konnte nicht gespeichert werden: {e}")

    def _load_model(self) -> None:
        """Lädt gespeichertes Modell von Disk (falls vorhanden)."""
        if not _sklearn_available:
            return
        try:
            if MODEL_PATH.exists() and SCALER_PATH.exists():
                self._model = joblib.load(MODEL_PATH)
                self._scaler = joblib.load(SCALER_PATH)
                logger.info(f"✅ Gespeichertes ML-Modell geladen: {MODEL_PATH}")
        except Exception as e:
            logger.warning(f"⚠️ Modell konnte nicht geladen werden (wird neu trainiert): {e}")
            self._model = None
            self._scaler = None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_predictor: Optional[BatteryLifePredictor] = None


def get_battery_predictor() -> BatteryLifePredictor:
    """Gibt BatteryLifePredictor-Singleton zurück."""
    global _predictor
    if _predictor is None:
        _predictor = BatteryLifePredictor()
    return _predictor
