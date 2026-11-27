package com.lab.air.reservation;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

/** Reservation API veri tipleri. */
public final class Model {

    private Model() {
    }

    public record Flight(long flightId, String flightNo, String origin, String destination,
                         OffsetDateTime depTime, OffsetDateTime arrTime, String status,
                         int capacity, int sold, boolean hot) {
        public int available() {
            return capacity - sold;
        }
    }

    public record Booking(long bookingId, String pnr, long flightId, String flightNo, String origin,
                          String destination, OffsetDateTime depTime, String status, String fareClass,
                          BigDecimal price, String firstName, String lastName, String email,
                          OffsetDateTime createdAt, OffsetDateTime cancelledAt, boolean checkedIn) {
    }

    public record CreateBooking(@NotNull Long flightId,
                                @NotBlank @Size(max = 64) String firstName,
                                @NotBlank @Size(max = 64) String lastName,
                                @NotBlank @Email @Size(max = 128) String email,
                                @Pattern(regexp = "[YBJ]") String fareClass) {
        public String fare() {
            return fareClass == null ? "Y" : fareClass;
        }
    }

    /** replayed=true: aynı Idempotency-Key ile tekrar gönderim; veri değişmedi. */
    public record BookingResult(Booking booking, boolean replayed) {
    }

    public record RouteDemand(String origin, String destination, long bookings, long cancelled,
                              BigDecimal avgPrice, BigDecimal revenue) {
    }

    public record LabHoldRequest(Long flightId, @NotNull Long holdMs) {
    }
}
