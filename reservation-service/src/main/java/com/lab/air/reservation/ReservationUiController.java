package com.lab.air.reservation;

import com.lab.air.common.BusinessException;
import com.lab.air.common.DbErrors;
import com.lab.air.common.Op;
import com.lab.air.reservation.Model.CreateBooking;
import org.springframework.dao.DataAccessException;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/** Sade Thymeleaf arayüzü. Her işlem REST ile aynı servis/transaction yolunu kullanır. */
@Controller
public class ReservationUiController {

    private final ReservationRepository repo;
    private final BookingService bookings;

    public ReservationUiController(ReservationRepository repo, BookingService bookings) {
        this.repo = repo;
        this.bookings = bookings;
    }

    @Op("ui_search")
    @GetMapping("/")
    public String index(@RequestParam(required = false) String origin,
                        @RequestParam(required = false) String destination,
                        @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date,
                        @RequestParam(defaultValue = "3") int flexDays,
                        Model m) {
        m.addAttribute("origin", origin);
        m.addAttribute("destination", destination);
        m.addAttribute("date", date == null ? LocalDate.now() : date);
        m.addAttribute("flexDays", flexDays);
        try {
            m.addAttribute("flights", repo.search(origin, destination, date,
                    ReservationApiController.clamp(flexDays, 0, 30), 50));
            m.addAttribute("hot", repo.hotFlights());
        } catch (DataAccessException e) {
            m.addAttribute("flights", List.of());
            m.addAttribute("hot", List.of());
            m.addAttribute("error", DbErrors.classify(e) + ": " + DbErrors.rootMessage(e));
        }
        m.addAttribute("idemKey", UUID.randomUUID().toString());
        return "index";
    }

    @Op("ui_create_booking")
    @PostMapping("/ui/bookings")
    public String create(@RequestParam long flightId, @RequestParam String firstName, @RequestParam String lastName,
                         @RequestParam String email, @RequestParam(defaultValue = "Y") String fareClass,
                         @RequestParam String idemKey, RedirectAttributes ra) {
        try {
            var r = bookings.create(new CreateBooking(flightId, firstName, lastName, email, fareClass),
                    ReservationApiController.validKey(idemKey), 0);
            ra.addFlashAttribute("msg", r.replayed() ? "Tekrar gönderim: mevcut rezervasyon gösteriliyor"
                    : "Rezervasyon oluşturuldu");
            return "redirect:/ui/bookings/" + r.booking().pnr();
        } catch (BusinessException | DataAccessException | IllegalArgumentException e) {
            ra.addFlashAttribute("error", describe(e));
            return "redirect:/";
        }
    }

    @Op("ui_get_booking")
    @GetMapping("/ui/bookings/{pnr}")
    public String booking(@PathVariable String pnr, Model m) {
        try {
            m.addAttribute("b", bookings.get(pnr));
        } catch (BusinessException e) {
            m.addAttribute("error", describe(e));
        }
        return "booking";
    }

    @GetMapping("/ui/lookup")
    public String lookup(@RequestParam String pnr) {
        return "redirect:/ui/bookings/" + pnr.trim().toUpperCase();
    }

    @Op("ui_cancel_booking")
    @PostMapping("/ui/bookings/{pnr}/cancel")
    public String cancel(@PathVariable String pnr, RedirectAttributes ra) {
        try {
            var r = bookings.cancel(pnr, 0);
            ra.addFlashAttribute("msg", r.replayed() ? "Rezervasyon zaten iptal" : "Rezervasyon iptal edildi");
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/bookings/" + pnr;
    }

    static String describe(Exception e) {
        if (e instanceof BusinessException b) {
            return b.code() + ": " + b.getMessage();
        }
        if (e instanceof DataAccessException d) {
            return DbErrors.classify(d) + ": " + DbErrors.rootMessage(d);
        }
        return e.getMessage();
    }
}
