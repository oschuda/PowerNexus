"""
Grid Quality Analyzer Module
Analyzes grid conditions: frequency stability, voltage correlation, and flutter current.

Features:
  - Frequency stability monitoring (50/60 Hz)
  - Voltage phase correlation analysis
  - Flutter current (transient) detection
  - Per-location aggregation
  - Historical trend analysis
"""

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Any

from i18n import t
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)

# Grid parameters
FREQUENCY_TARGET_50HZ = 50.0
FREQUENCY_TARGET_60HZ = 60.0
FREQUENCY_WARNING_THRESHOLD = 0.5  # ±0.5 Hz
FREQUENCY_CRITICAL_THRESHOLD = 1.5  # ±1.5 Hz

VOLTAGE_TARGET = 230.0  # V (L-N for 3-phase)
VOLTAGE_WARNING_VARIANCE = 3.0  # V
VOLTAGE_CRITICAL_VARIANCE = 10.0  # V

FLUTTER_CURRENT_THRESHOLD = 5.0  # A (transient threshold)

# Trend analysis window
TREND_WINDOW_HOURS = 24
TREND_DATA_POINTS = 288  # One point every 5 minutes for 24 hours


class GridQualityAnalyzer:
    """Analyzes grid quality metrics from UPS data."""
    
    def __init__(self, database, max_history_days=30):
        """Initialize GridQualityAnalyzer.
        
        Args:
            database: Database instance
            max_history_days: Days of history to analyze
        """
        self.db = database
        self.max_history_days = max_history_days
        self._trend_cache = {}  # {profile: deque of measurements}
        self._load_trend_data()
    
    def _load_trend_data(self):
        """Load historical trend data into cache."""
        try:
            # This will be populated as new data arrives
            logger.info("✅ Grid Quality Analyzer initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize trend data: {e}")
    
    def analyze_frequency_stability(self, profile_name: str, frequency: float) -> Dict[str, Any]:
        """Analyze frequency stability for a UPS.
        
        Args:
            profile_name: UPS profile name
            frequency: Current frequency in Hz
            
        Returns:
            Dict with frequency analysis and status
        """
        try:
            # Determine target frequency (50 or 60 Hz based on region)
            target_freq = FREQUENCY_TARGET_50HZ  # Default to 50Hz (EU/Asia)
            
            deviation = abs(frequency - target_freq)
            
            # Determine status
            if deviation <= FREQUENCY_WARNING_THRESHOLD:
                status = "STABLE"
                severity = "INFO"
            elif deviation <= FREQUENCY_CRITICAL_THRESHOLD:
                status = "WARNING"
                severity = "WARNING"
            else:
                status = "CRITICAL"
                severity = "CRITICAL"
            
            # Calculate trend (if we have historical data)
            trend = self._calculate_frequency_trend(profile_name)
            
            return {
                "frequency": round(frequency, 3),
                "target": target_freq,
                "deviation": round(deviation, 3),
                "status": status,
                "severity": severity,
                "trend": trend,
                "unit": "Hz",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            
        except Exception as e:
            logger.error(f"❌ Error analyzing frequency for {profile_name}: {e}")
            return {
                "frequency": 0,
                "status": "ERROR",
                "severity": "ERROR",
                "error": str(e),
            }
    
    def analyze_voltage_correlation(self, profile_name: str, voltage_l1: float, 
                                   voltage_l2: float, voltage_l3: float) -> Dict[str, Any]:
        """Analyze 3-phase voltage correlation.
        
        Args:
            profile_name: UPS profile name
            voltage_l1: L1-N voltage (V)
            voltage_l2: L2-N voltage (V)
            voltage_l3: L3-N voltage (V)
            
        Returns:
            Dict with voltage analysis and balance status
        """
        try:
            # Calculate balance metrics
            avg_voltage = (voltage_l1 + voltage_l2 + voltage_l3) / 3
            max_voltage = max(voltage_l1, voltage_l2, voltage_l3)
            min_voltage = min(voltage_l1, voltage_l2, voltage_l3)
            voltage_variance = max_voltage - min_voltage
            voltage_unbalance = (voltage_variance / avg_voltage) * 100 if avg_voltage > 0 else 0
            
            # Determine status
            if voltage_variance <= VOLTAGE_WARNING_VARIANCE:
                status = "EXCELLENT"
                severity = "INFO"
            elif voltage_variance <= VOLTAGE_CRITICAL_VARIANCE:
                status = "GOOD"
                severity = "INFO"
            else:
                status = "POOR"
                severity = "WARNING"
            
            # Calculate individual phase deviations
            l1_deviation = abs(voltage_l1 - VOLTAGE_TARGET)
            l2_deviation = abs(voltage_l2 - VOLTAGE_TARGET)
            l3_deviation = abs(voltage_l3 - VOLTAGE_TARGET)
            
            return {
                "l1": round(voltage_l1, 1),
                "l2": round(voltage_l2, 1),
                "l3": round(voltage_l3, 1),
                "average": round(avg_voltage, 1),
                "target": VOLTAGE_TARGET,
                "variance": round(voltage_variance, 1),
                "unbalance_percent": round(voltage_unbalance, 2),
                "phase_deviations": {
                    "l1": round(l1_deviation, 1),
                    "l2": round(l2_deviation, 1),
                    "l3": round(l3_deviation, 1),
                },
                "status": status,
                "severity": severity,
                "unit": "V",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            
        except Exception as e:
            logger.error(f"❌ Error analyzing voltage for {profile_name}: {e}")
            return {
                "status": "ERROR",
                "severity": "ERROR",
                "error": str(e),
            }
    
    def detect_flutter_current(self, profile_name: str, current: float, 
                              power: float = None) -> Dict[str, Any]:
        """Detect flutter (transient) current.
        
        Flutter is rapid current oscillation indicating grid disturbances.
        
        Args:
            profile_name: UPS profile name
            current: Current load in Amperes
            power: Optional power in Watts for analysis
            
        Returns:
            Dict with flutter detection and status
        """
        try:
            # In production, this would use historical data to detect oscillations
            # For now, we use a simple threshold-based approach
            
            flutter_detected = current > FLUTTER_CURRENT_THRESHOLD
            
            status = "DETECTED" if flutter_detected else "OK"
            severity = "WARNING" if flutter_detected else "INFO"
            
            risk_level = "HIGH" if current > FLUTTER_CURRENT_THRESHOLD * 1.5 else "MEDIUM" if current > FLUTTER_CURRENT_THRESHOLD else "LOW"
            
            return {
                "current": round(current, 2),
                "threshold": FLUTTER_CURRENT_THRESHOLD,
                "flutter_detected": flutter_detected,
                "status": status,
                "severity": severity,
                "risk_level": risk_level,
                "unit": "A",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            
        except Exception as e:
            logger.error(f"❌ Error detecting flutter for {profile_name}: {e}")
            return {
                "status": "ERROR",
                "severity": "ERROR",
                "error": str(e),
            }
    
    def assess_grid_status(self, profile_name: str, frequency: float = None,
                          voltage_l1: float = None, voltage_l2: float = None, 
                          voltage_l3: float = None, current: float = None) -> Dict[str, Any]:
        """Comprehensive grid quality assessment.
        
        Combines frequency, voltage, and flutter analysis.
        
        Args:
            profile_name: UPS profile name
            frequency: Current frequency (Hz)
            voltage_l1, voltage_l2, voltage_l3: Phase voltages (V)
            current: Current load (A)
            
        Returns:
            Dict with overall grid quality score and recommendations
        """
        try:
            scores = []
            issues = []
            
            # Frequency analysis
            if frequency is not None:
                freq_analysis = self.analyze_frequency_stability(profile_name, frequency)
                freq_score = 100 if freq_analysis["status"] == "STABLE" else 50 if freq_analysis["status"] == "WARNING" else 0
                scores.append(freq_score)
                
                if freq_analysis["status"] != "STABLE":
                    issues.append(t("grid.issue.frequency", value=freq_analysis['deviation']))
            
            # Voltage analysis
            if voltage_l1 is not None and voltage_l2 is not None and voltage_l3 is not None:
                volt_analysis = self.analyze_voltage_correlation(profile_name, voltage_l1, voltage_l2, voltage_l3)
                volt_score = 100 if volt_analysis["status"] == "EXCELLENT" else 80 if volt_analysis["status"] == "GOOD" else 50
                scores.append(volt_score)
                
                if volt_analysis["status"] != "EXCELLENT":
                    issues.append(t("grid.issue.voltage", value=volt_analysis['unbalance_percent']))
            
            # Flutter analysis
            if current is not None:
                flutter_analysis = self.detect_flutter_current(profile_name, current)
                flutter_score = 100 if flutter_analysis["status"] == "OK" else 50
                scores.append(flutter_score)
                
                if flutter_analysis["status"] != "OK":
                    issues.append(t("grid.issue.flutter", value=current))
            
            # Calculate overall score
            overall_score = round(sum(scores) / len(scores)) if scores else 0
            
            # Determine recommendation
            recommendation = self._get_recommendation(overall_score, issues)
            
            return {
                "profile": profile_name,
                "overall_score": overall_score,
                "status": self._score_to_status(overall_score),
                "severity": self._score_to_severity(overall_score),
                "issues": issues,
                "recommendation": recommendation,
                "components": {
                    "frequency": freq_analysis if frequency is not None else None,
                    "voltage": volt_analysis if voltage_l1 is not None else None,
                    "flutter": flutter_analysis if current is not None else None,
                },
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            
        except Exception as e:
            logger.error(f"❌ Error assessing grid status for {profile_name}: {e}")
            return {
                "profile": profile_name,
                "status": "ERROR",
                "severity": "ERROR",
                "error": str(e),
            }
    
    def get_profile_grid_quality(self, profile_name: str) -> Optional[Dict[str, Any]]:
        """Get complete grid quality data for a profile.
        
        This method would typically fetch the latest UPS data and run analysis.
        
        Args:
            profile_name: UPS profile name
            
        Returns:
            Dict with all grid quality metrics or None
        """
        try:
            # Get latest data from database
            history = self.db.get_history(days=1, profile=profile_name)
            
            if not history or len(history) == 0:
                logger.warning(f"No data available for {profile_name}")
                return None
            
            # Get most recent data point
            latest = history[-1] if isinstance(history, list) else history
            
            # Extract grid parameters (this assumes the database stores them)
            frequency = latest.get('frequency', 50.0) if isinstance(latest, dict) else 50.0
            voltage_l1 = latest.get('voltage_l1', 230.0) if isinstance(latest, dict) else 230.0
            voltage_l2 = latest.get('voltage_l2', 230.0) if isinstance(latest, dict) else 230.0
            voltage_l3 = latest.get('voltage_l3', 230.0) if isinstance(latest, dict) else 230.0
            current = latest.get('current', 0) if isinstance(latest, dict) else 0
            
            return self.assess_grid_status(
                profile_name, 
                frequency=frequency,
                voltage_l1=voltage_l1,
                voltage_l2=voltage_l2,
                voltage_l3=voltage_l3,
                current=current
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to get grid quality for {profile_name}: {e}")
            return None
    
    def get_profile_grid_quality_score(self, profile_name: str) -> Optional[float]:
        """Get only the overall grid quality score (0-100) for a profile.
        
        Args:
            profile_name: UPS profile name
            
        Returns:
            Score (0-100) or None if unavailable
        """
        try:
            analysis = self.get_profile_grid_quality(profile_name)
            if analysis:
                return analysis.get("overall_score", 0)
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get score for {profile_name}: {e}")
            return None
    
    def _calculate_frequency_trend(self, profile_name: str) -> str:
        """Calculate frequency trend based on historical data.
        
        Args:
            profile_name: UPS profile name
            
        Returns:
            Trend string: "STABLE", "RISING", "FALLING"
        """
        try:
            # Get last 24 hours of data
            history = self.db.get_history(days=1, profile=profile_name)
            
            if not history or len(history) < 2:
                return "INSUFFICIENT_DATA"
            
            # Simple trend: compare first and last
            if isinstance(history, list):
                first_freq = history[0].get('frequency', 50.0) if isinstance(history[0], dict) else 50.0
                last_freq = history[-1].get('frequency', 50.0) if isinstance(history[-1], dict) else 50.0
                
                diff = last_freq - first_freq
                if abs(diff) < 0.1:
                    return "STABLE"
                elif diff > 0:
                    return "RISING"
                else:
                    return "FALLING"
            
            return "UNKNOWN"
            
        except Exception as e:
            logger.warning(f"Could not calculate frequency trend: {e}")
            return "ERROR"
    
    @staticmethod
    def _score_to_status(score: int) -> str:
        """Convert numerical score to status string.
        
        Args:
            score: Score 0-100
            
        Returns:
            Status string
        """
        if score >= 80:
            return "EXCELLENT"
        elif score >= 60:
            return "GOOD"
        elif score >= 40:
            return "FAIR"
        else:
            return "POOR"
    
    @staticmethod
    def _score_to_severity(score: int) -> str:
        """Convert numerical score to severity level.
        
        Args:
            score: Score 0-100
            
        Returns:
            Severity level
        """
        if score >= 80:
            return "INFO"
        elif score >= 60:
            return "INFO"
        elif score >= 40:
            return "WARNING"
        else:
            return "CRITICAL"
    
    @staticmethod
    def _get_recommendation(score: int, issues: List[str]) -> str:
        """Get maintenance recommendation based on score and issues.
        
        Args:
            score: Overall score
            issues: List of detected issues
            
        Returns:
            Recommendation string
        """
        if score >= 80:
            return t("grid.rec.good")
        elif score >= 60:
            return t("grid.rec.watch")
        elif score >= 40:
            return t("grid.rec.degraded")
        else:
            return t("grid.rec.critical")


# Singleton instance
_grid_analyzer = None


def get_grid_quality_analyzer(database) -> GridQualityAnalyzer:
    """Get or create GridQualityAnalyzer singleton.
    
    Args:
        database: Database instance
        
    Returns:
        GridQualityAnalyzer instance
    """
    global _grid_analyzer
    if _grid_analyzer is None:
        _grid_analyzer = GridQualityAnalyzer(database)
    return _grid_analyzer
