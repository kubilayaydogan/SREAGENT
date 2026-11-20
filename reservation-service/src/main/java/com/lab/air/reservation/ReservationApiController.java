package com.lab.air.reservation;

import com.lab.air.common.BusinessException;
import com.lab.air.common.Op;
import com.lab.air.reservation.Model.Booking;
import com.lab.air.reservation.Model.BookingResult;
import com.lab.air.reservation.Model.CreateBooking;
import com.lab.air.reservation.Model.Flight;
import com.lab.air.reservation.Model.RouteDemand;
import jakarta.validation.Valid;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;
import java.util.regex.Pattern;

@RestController
@RequestMapping("/api")
public class ReservationApiController {

    static final Pattern IDEMPOTENCY_KEY = Pattern.compile("[A-Za-z0-9._:-]{8,64}");

    private final ReservationRepository repo;
    private final BookingService bookings;

    public ReservationApiController(ReservationRepository repo, BookingService bookings) {
        this.repo = repo;
        this.bookings = bookings;
    }

    @Op("search_flights")
    @GetMapping("/flights")
    public List<Flight> search(@RequestParam(required = false) String origin,
                               @RequestParam(required = false) String destination,
                               @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date,
                               @RequestParam(defaultValue = "1") int flexDays,
                               @RequestParam(defaultValue = "20") int limit) {
        return repo.search(origin, destination, date, clamp(flexDays, 0, 30), clamp(limit, 1, 200));
    }

    @Op("get_flight")
    @GetMapping("/flights/{id}")
    public Flight flight(@PathVariable long id) {
        return repo.flight(id).orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok"));
    }

    @Op("hot_flights")
    @GetMapping("/flights/hot")
    public List<Flight> hot() {
        return repo.hotFlights();
    }

    @Op("create_booking")
    @PostMapping("/bookings")
    public ResponseEntity<BookingResult> create(@Valid @RequestBody CreateBooking req,
                                                @RequestHeader(value = "Idempotency-Key", required = false) String key) {
        BookingResult r = bookings.create(req, validKey(key), 0);
        return ResponseEntity.status(r.replayed() ? HttpStatus.OK : HttpStatus.CREATED).body(r);
    }

    @Op("get_booking")
    @GetMapping("/bookings/{pnr}")
    public Booking get(@PathVariable String pnr) {
        return bookings.get(pnr);
    }

    @Op("cancel_booking")
    @PostMapping("/bookings/{pnr}/cancel")
    public BookingResult cancel(@PathVariable String pnr) {
        return bookings.cancel(pnr, 0);
    }

    @Op("sample_bookings")
    @GetMapping("/bookings/sample")
    public List<String> sample(@RequestParam(defaultValue = "50") int limit) {
        return repo.samplePnrs(clamp(limit, 1, 500));
    }

    @Op("route_demand_report")
    @GetMapping("/reports/route-demand")
    public List<RouteDemand> routeDemand(@RequestParam(defaultValue = "30") int days,
                                         @RequestParam(defaultValue = "20") int limit) {
        return repo.routeDemand(clamp(days, 1, 365), clamp(limit, 1, 100));
    }

    static String validKey(String key) {
        if (key == null || key.isBlank()) {
            return null;
        }
        if (!IDEMPOTENCY_KEY.matcher(key).matches()) {
            throw new IllegalArgumentException("Idempotency-Key 8-64 karakter [A-Za-z0-9._:-] olmalı");
        }
        return key;
    }

    static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }
}
