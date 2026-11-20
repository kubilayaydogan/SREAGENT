package com.lab.air.reservation;

import com.lab.air.common.BusinessException;
import com.lab.air.common.Op;
import com.lab.air.reservation.Model.BookingResult;
import com.lab.air.reservation.Model.CreateBooking;
import com.lab.air.reservation.Model.LabHoldRequest;
import jakarta.validation.Valid;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.concurrent.ThreadLocalRandom;

/**
 * Lab senaryosu: GERÇEK rezervasyon iş yolunu çalıştırır, envanter satırı kilitliyken
 * sınırlı süre (≤ lab.max-hold-ms) bekleyip commit eder. Check-in'in aynı uçuştaki
 * check-in işlemi (checked_in+1) bu sürede bekler → gerçek blocking zinciri.
 */
@RestController
@RequestMapping("/lab")
@ConditionalOnProperty(prefix = "lab", name = "enabled", havingValue = "true")
public class ReservationLabController {

    private final BookingService bookings;
    private final ReservationRepository repo;

    public ReservationLabController(BookingService bookings, ReservationRepository repo) {
        this.bookings = bookings;
        this.repo = repo;
    }

    @Op("lab_hold_booking")
    @PostMapping("/bookings/hold")
    public BookingResult holdBooking(@Valid @RequestBody LabHoldRequest req) {
        long flightId = req.flightId() != null ? req.flightId() : randomHotFlight();
        int n = ThreadLocalRandom.current().nextInt(1_000_000);
        return bookings.create(new CreateBooking(flightId, "Lab", "Hold" + n, "lab" + n + "@example.test", "Y"),
                null, req.holdMs());
    }

    private long randomHotFlight() {
        var hot = repo.hotFlights();
        if (hot.isEmpty()) {
            throw BusinessException.conflict("NO_HOT_FLIGHT", "seed'de sıcak uçuş yok");
        }
        return hot.get(ThreadLocalRandom.current().nextInt(hot.size())).flightId();
    }
}
