package com.lab.air.chaos;

import org.springframework.stereotype.Component;

import java.util.function.Supplier;

/**
 * Klasik engine ile chaos v2 arasında ortak kilit: aynı anda yalnız bir deney yük üretebilir.
 * v2 Orchestrator kendini {@link #registerV2} ile kaydeder; klasik engine başlamadan önce {@link #v2Busy()} sorar.
 */
@Component
public class RunGate {

    private volatile Supplier<String> v2 = () -> null;

    public void registerV2(Supplier<String> busy) {
        this.v2 = busy;
    }

    /** v2'de aktif/kuyrukta deney varsa açıklaması, yoksa null. */
    public String v2Busy() {
        return v2.get();
    }
}
