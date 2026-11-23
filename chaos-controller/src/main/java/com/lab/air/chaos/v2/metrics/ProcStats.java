package com.lab.air.chaos.v2.metrics;

/** /proc/stat ve /proc/meminfo içeriğinden CPU ve bellek kullanımı (iki okuma arasındaki farkla). */
public final class ProcStats {

    public record Cpu(long total, long idle, int cpus) {
    }

    private Cpu prev;

    public static Cpu parseCpu(String stat) {
        long total = 0;
        long idle = 0;
        int cpus = 0;
        for (String line : stat.split("\n")) {
            if (line.startsWith("cpu ")) {
                String[] f = line.trim().split("\\s+");
                for (int i = 1; i < f.length && i <= 8; i++) {      // user..steal (guest zaten user içinde)
                    total += Long.parseLong(f[i]);
                }
                idle = Long.parseLong(f[4]) + (f.length > 5 ? Long.parseLong(f[5]) : 0);  // idle + iowait
            } else if (line.matches("^cpu\\d+ .*")) {
                cpus++;
            }
        }
        return new Cpu(total, idle, cpus);
    }

    /** Önceki okumaya göre CPU %; ilk çağrıda null. */
    public synchronized Double cpuPct(String stat) {
        Cpu now = parseCpu(stat);
        Cpu p = prev;
        prev = now;
        if (p == null || now.total <= p.total) {
            return null;
        }
        double dt = now.total - p.total;
        return Math.max(0, Math.min(100, 100.0 * (1 - (now.idle - p.idle) / dt)));
    }

    public synchronized Integer cpus() {
        return prev == null ? null : prev.cpus;
    }

    /** (MemTotal - MemAvailable) / MemTotal %. */
    public static Double memUsedPct(String meminfo) {
        long total = -1;
        long avail = -1;
        for (String line : meminfo.split("\n")) {
            if (line.startsWith("MemTotal:")) {
                total = Long.parseLong(line.replaceAll("\\D+", ""));
            } else if (line.startsWith("MemAvailable:")) {
                avail = Long.parseLong(line.replaceAll("\\D+", ""));
            }
        }
        return total <= 0 || avail < 0 ? null : 100.0 * (total - avail) / total;
    }

    public static Double load1(String loadavg) {
        try {
            return Double.parseDouble(loadavg.trim().split("\\s+")[0]);
        } catch (RuntimeException e) {
            return null;
        }
    }
}
