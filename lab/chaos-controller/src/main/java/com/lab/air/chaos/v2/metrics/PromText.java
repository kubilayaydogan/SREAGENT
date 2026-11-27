package com.lab.air.chaos.v2.metrics;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Predicate;

/** Prometheus text exposition format için küçük, bağımlılıksız parser + histogram quantile hesabı. */
public final class PromText {

    public record Line(String name, Map<String, String> labels, double value) {
        public String label(String k) {
            return labels.getOrDefault(k, "");
        }
    }

    private final List<Line> lines;

    private PromText(List<Line> lines) {
        this.lines = lines;
    }

    public static PromText parse(String text) {
        List<Line> out = new ArrayList<>();
        for (String raw : text.split("\n")) {
            String l = raw.strip();
            if (l.isEmpty() || l.startsWith("#")) {
                continue;
            }
            try {
                int brace = l.indexOf('{');
                int sp;
                String name;
                Map<String, String> labels = new HashMap<>();
                if (brace >= 0 && brace < l.indexOf(' ')) {
                    name = l.substring(0, brace);
                    int end = l.indexOf('}', brace);
                    parseLabels(l.substring(brace + 1, end), labels);
                    sp = end + 1;
                } else {
                    sp = l.indexOf(' ');
                    name = l.substring(0, sp);
                }
                String rest = l.substring(sp).strip();
                int s2 = rest.indexOf(' ');
                String v = s2 > 0 ? rest.substring(0, s2) : rest;
                out.add(new Line(name, labels, parseValue(v)));
            } catch (RuntimeException ignored) {
                // tanınmayan satır atlanır
            }
        }
        return new PromText(out);
    }

    private static double parseValue(String v) {
        return switch (v) {
            case "+Inf" -> Double.POSITIVE_INFINITY;
            case "-Inf" -> Double.NEGATIVE_INFINITY;
            case "NaN" -> Double.NaN;
            default -> Double.parseDouble(v);
        };
    }

    static void parseLabels(String s, Map<String, String> into) {
        int i = 0;
        while (i < s.length()) {
            int eq = s.indexOf('=', i);
            if (eq < 0) {
                break;
            }
            String k = s.substring(i, eq).strip();
            if (k.startsWith(",")) {
                k = k.substring(1).strip();
            }
            int q = s.indexOf('"', eq);
            StringBuilder v = new StringBuilder();
            int j = q + 1;
            while (j < s.length() && s.charAt(j) != '"') {
                if (s.charAt(j) == '\\' && j + 1 < s.length()) {
                    j++;
                }
                v.append(s.charAt(j));
                j++;
            }
            into.put(k, v.toString());
            i = j + 1;
        }
    }

    public List<Line> lines() {
        return lines;
    }

    /** Eşleşen satırların toplamı; hiç satır yoksa null. */
    public Double sum(String name, Predicate<Line> filter) {
        double s = 0;
        boolean any = false;
        for (Line l : lines) {
            if (l.name.equals(name) && filter.test(l) && !Double.isNaN(l.value)) {
                s += l.value;
                any = true;
            }
        }
        return any ? s : null;
    }

    public Double first(String name, Predicate<Line> filter) {
        for (Line l : lines) {
            if (l.name.equals(name) && filter.test(l)) {
                return l.value;
            }
        }
        return null;
    }

    /** Histogram kovaları (le → kümülatif sayı), filtreye uyan seriler toplanarak. */
    public TreeMap<Double, Double> buckets(String name, Predicate<Line> filter) {
        TreeMap<Double, Double> m = new TreeMap<>();
        for (Line l : lines) {
            if (l.name.equals(name) && filter.test(l)) {
                String le = l.label("le");
                if (!le.isEmpty()) {
                    m.merge(parseValue(le), l.value, Double::sum);
                }
            }
        }
        return m;
    }

    /** İki kümülatif kova seti arasındaki fark (pencere içindeki istekler). */
    public static TreeMap<Double, Double> delta(TreeMap<Double, Double> now, TreeMap<Double, Double> before) {
        TreeMap<Double, Double> d = new TreeMap<>();
        now.forEach((le, v) -> d.put(le, Math.max(0, v - (before == null ? 0 : before.getOrDefault(le, 0.0)))));
        return d;
    }

    /**
     * Prometheus histogram_quantile ile aynı yöntem: kümülatif kovalarda rank'ın düştüğü kova içinde doğrusal
     * interpolasyon. Gözlem yoksa null. Sonuç kova birimindedir (saniye).
     */
    public static Double quantile(double q, TreeMap<Double, Double> cumulative) {
        if (cumulative.isEmpty()) {
            return null;
        }
        double total = cumulative.lastEntry().getValue();
        if (total <= 0) {
            return null;
        }
        double rank = q * total;
        double prevLe = 0;
        double prevCount = 0;
        double lastFinite = 0;
        for (var e : cumulative.entrySet()) {
            double le = e.getKey();
            double c = e.getValue();
            if (c >= rank) {
                if (Double.isInfinite(le)) {
                    return lastFinite;
                }
                double inBucket = c - prevCount;
                return inBucket <= 0 ? le : prevLe + (le - prevLe) * (rank - prevCount) / inBucket;
            }
            prevLe = le;
            prevCount = c;
            if (!Double.isInfinite(le)) {
                lastFinite = le;
            }
        }
        return lastFinite;
    }
}
