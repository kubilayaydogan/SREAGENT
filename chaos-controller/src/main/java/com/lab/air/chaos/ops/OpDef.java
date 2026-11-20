package com.lab.air.chaos.ops;

/**
 * Bir uygulamanın tek bir iş operasyonu.
 *
 * @param kind     READ / WRITE / LAB (lab: X-Lab-Token gerektirir, yalnız lab profili)
 * @param builder  istek üretici; havuzda veri yoksa null döner ("no_data" sayılır)
 * @param onResult başarılı yanıttan havuzlara geri besleme (ör. yeni PNR)
 */
public record OpDef(String app, String name, Kind kind, String description, Builder builder, ResultHook onResult) {

    public enum Kind { READ, WRITE, LAB }

    @FunctionalInterface
    public interface Builder {
        HttpCall build(OpContext ctx);
    }

    @FunctionalInterface
    public interface ResultHook {
        void accept(OpContext ctx, HttpCall call, int status, String body);
    }

    /** Hedef başına operasyon parametreleri. */
    public record Params(int dataSize, boolean hotRow, long holdMs) {
    }

    public record OpContext(Params params, DataPools pools, tools.jackson.databind.json.JsonMapper json) {
    }
}
