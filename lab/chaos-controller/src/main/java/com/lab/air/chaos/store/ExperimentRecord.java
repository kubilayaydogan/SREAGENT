package com.lab.air.chaos.store;

import com.lab.air.chaos.engine.ExperimentSpec;
import com.lab.air.chaos.engine.Stats;

import java.util.List;
import java.util.Map;

/**
 * Kalıcı deney kaydı. Tüm zamanlar ISO-8601 UTC metin olarak saklanır.
 *
 * @param phases   faz → [başlangıç, bitiş] (UTC); Dynatrace sorgu pencereleri bu değerlerden kurulur
 * @param results  faz × kaynak × uygulama × operasyon satırları
 * @param producer yük üreticisinin (chaos-controller JVM) kaynak tüketimi
 * @param cleanup  durdurma sonrası uygulama /lab/cleanup yanıtları
 * @param events   olay günlüğü (faz geçişleri, yük başlat/durdur, hata özetleri, cleanup); eski kayıtlarda null
 * @param timeline 5 sn'lik örnekler: kaynak/uygulama → rps, errors, dropped, inFlight; eski kayıtlarda null
 */
public record ExperimentRecord(String experimentId, String clientRequestId, String specHash, String state,
                               String createdAt, String startedAt, String endedAt, String currentPhase,
                               String stopReason, ExperimentSpec spec, Map<String, List<String>> phases,
                               List<Stats.Row> results, Map<String, Object> producer, Map<String, String> cleanup,
                               Map<String, String> notes, List<Map<String, Object>> events,
                               List<Map<String, Object>> timeline) {
}
