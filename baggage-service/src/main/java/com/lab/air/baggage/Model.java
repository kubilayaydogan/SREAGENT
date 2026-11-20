package com.lab.air.baggage;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;

public final class Model {

    private Model() {
    }

    public record Bag(long bagId, String tag, String pnr, long bookingId, long flightId, BigDecimal weightKg,
                      String status, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    public record BagEvent(long eventId, String eventType, String location, OffsetDateTime createdAt) {
    }

    public record BagDetail(Bag bag, List<BagEvent> events) {
    }

    public record RegisterBag(@NotBlank @Pattern(regexp = "[A-Za-z0-9]{6,8}") String pnr,
                              @NotNull @DecimalMin("0.5") @DecimalMax("50.0") BigDecimal weightKg) {
    }

    public record AddEvent(@NotBlank @Pattern(regexp = "LOADED|IN_TRANSIT|ARRIVED|DELIVERED|MISHANDLED") String eventType,
                           @NotBlank @Pattern(regexp = "[A-Z0-9-]{2,16}") String location) {
    }

    public record Result(BagDetail detail, boolean replayed) {
    }

    public record HistoryRow(String eventType, String location, long events, OffsetDateTime firstAt,
                             OffsetDateTime lastAt, BigDecimal avgNoteBytes) {
    }
}
