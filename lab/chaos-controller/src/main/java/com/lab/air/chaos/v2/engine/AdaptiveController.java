package com.lab.air.chaos.v2.engine;

/**
 * Oransal (P) controller + deadband + adım sınırı. Çıkış level ∈ [0,1]; kontrollü actuator'lar worker sayısını
 * bundan türetir. Ölçüm gecikmeli olduğundan (2 s örnek, 10 s pencere) kazanç düşük, adım sınırlıdır.
 *
 * <p>Ramp: effectiveTarget = start + (target - start) × progress. Level 1'de olup hâlâ hedefin altındaysa
 * {@link #saturated()} true olur: hedef, güvenlik limitleri içinde ulaşılamaz (raporda dürüstçe belirtilir).
 */
public final class AdaptiveController {

    private final double target;
    private final double tolerance;
    private final double kp;
    private final double maxStep;
    private double level;
    private int saturatedTicks;
    private int ticks;
    private Double lastValue;
    private double lastTarget;

    public AdaptiveController(double target, double tolerancePct, double initialLevel) {
        this(target, tolerancePct, initialLevel, 0.35, 0.15);
    }

    public AdaptiveController(double target, double tolerancePct, double initialLevel, double kp, double maxStep) {
        this.target = target;
        this.tolerance = Math.max(0.01, tolerancePct / 100.0);
        this.level = clamp(initialLevel);
        this.kp = kp;
        this.maxStep = maxStep;
    }

    /** Bir kontrol adımı; value null ise (ölçüm yok) level korunur. */
    public double update(Double value, double progress, double startValue) {
        ticks++;
        double eff = effectiveTarget(progress, startValue);
        lastTarget = eff;
        lastValue = value;
        if (value == null || Double.isNaN(value)) {
            return level;
        }
        double denom = Math.max(Math.abs(eff), 1e-6);
        double err = (eff - value) / denom;
        if (Math.abs(err) > tolerance) {
            double step = Math.max(-maxStep, Math.min(maxStep, kp * err));
            level = clamp(level + step);
        }
        if (level >= 0.999 && value < eff * (1 - tolerance)) {
            saturatedTicks++;
        } else {
            saturatedTicks = 0;
        }
        return level;
    }

    public double effectiveTarget(double progress, double startValue) {
        double p = Math.max(0, Math.min(1, progress));
        double s = Math.min(startValue, target);
        return s + (target - s) * p;
    }

    public boolean saturated() {
        return saturatedTicks >= 5;
    }

    public double level() {
        return level;
    }

    public double target() {
        return target;
    }

    public Double lastValue() {
        return lastValue;
    }

    public double lastTarget() {
        return lastTarget;
    }

    public int ticks() {
        return ticks;
    }

    private static double clamp(double v) {
        return Math.max(0, Math.min(1, v));
    }
}
