package com.lab.air.chaos.ops;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.concurrent.ThreadLocalRandom;

/** Sınırlı boyutlu, thread-safe veri havuzu (PNR, uçuş, etiket ...). Dolunca yeni eleman atılır. */
public final class BoundedPool<T> {

    private final int capacity;
    private final List<T> items = new ArrayList<>();

    public BoundedPool(int capacity) {
        this.capacity = capacity;
    }

    public synchronized void offer(T t) {
        if (t != null && items.size() < capacity) {
            items.add(t);
        }
    }

    public synchronized void replaceAll(Collection<T> ts) {
        items.clear();
        for (T t : ts) {
            if (items.size() >= capacity) {
                break;
            }
            items.add(t);
        }
    }

    public synchronized void addAll(Collection<T> ts) {
        ts.forEach(this::offer);
    }

    /** Rastgele elemanı çıkararak döndürür (tüketen operasyonlar: iptal, check-in). */
    public synchronized T take() {
        if (items.isEmpty()) {
            return null;
        }
        int i = ThreadLocalRandom.current().nextInt(items.size());
        T last = items.removeLast();
        if (i < items.size()) {
            T t = items.get(i);
            items.set(i, last);
            return t;
        }
        return last;
    }

    /** Rastgele elemanı çıkarmadan döndürür (okuma operasyonları). */
    public synchronized T peek() {
        if (items.isEmpty()) {
            return null;
        }
        return items.get(ThreadLocalRandom.current().nextInt(items.size()));
    }

    public synchronized int size() {
        return items.size();
    }
}
