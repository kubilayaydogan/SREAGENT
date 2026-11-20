package com.lab.air.checkin;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.time.OffsetDateTime;

public final class Model {

    private Model() {
    }

    public record CheckinView(String pnr, long bookingId, long flightId, String flightNo, String origin,
                              String destination, OffsetDateTime depTime, String flightStatus, String bookingStatus,
                              String firstName, String lastName, boolean checkedIn, OffsetDateTime checkedInAt,
                              String channel, String seatNo) {
    }

    public record CheckinRequest(@NotBlank @Pattern(regexp = "[A-Za-z0-9]{6,8}") String pnr,
                                 @Pattern(regexp = "WEB|KIOSK|COUNTER|MOBILE|API") String channel) {
        public String ch() {
            return channel == null ? "API" : channel;
        }
    }

    public record SeatRequest(@NotBlank @Pattern(regexp = "[1-9][0-9]?[A-F]") String seatNo) {
    }

    /** replayed=true: işlem daha önce yapılmıştı; veri değişmedi. */
    public record Result(CheckinView checkin, boolean replayed) {
    }

    public record Candidate(String pnr, long flightId) {
    }

    public record Seat(String seatNo, String pnr) {
    }

    public record LabHoldRequest(String pnr, @NotNull Long holdMs) {
    }
}
