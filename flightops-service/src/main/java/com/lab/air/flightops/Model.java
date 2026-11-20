package com.lab.air.flightops;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

public final class Model {

    private Model() {
    }

    public record Flight(long flightId, String flightNo, String origin, String destination, OffsetDateTime depTime,
                         OffsetDateTime arrTime, String aircraft, String status, String gate, int delayMin,
                         int capacity, int sold, int checkedIn, boolean hot, long version, OffsetDateTime updatedAt) {
    }

    public record OpsEvent(long eventId, String eventType, String detail, OffsetDateTime createdAt) {
    }

    public record FlightDetail(Flight flight, java.util.List<OpsEvent> events) {
    }

    public record StatusUpdate(@NotNull @Pattern(regexp = "SCHEDULED|DELAYED|BOARDING|DEPARTED|ARRIVED|CANCELLED") String status,
                               @Pattern(regexp = "[A-Z][0-9]{1,3}") String gate,
                               @Min(0) @Max(1440) Integer delayMin) {
    }

    public record CapacityUpdate(@NotNull @Min(1) @Max(1_000_000) Integer capacity) {
    }

    public record Result(Flight flight, boolean replayed) {
    }

    public record LoadFactor(long flightId, String flightNo, String origin, String destination, OffsetDateTime depTime,
                             int capacity, int sold, long confirmedBookings, long cancelledBookings,
                             BigDecimal loadFactorPct, boolean inventoryMismatch) {
    }

    public record LabHoldRequest(Long flightId, @NotNull Long holdMs) {
    }
}
