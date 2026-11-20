package com.lab.air.flightops;

import com.lab.air.common.BusinessException;
import com.lab.air.common.DbErrors;
import com.lab.air.common.Op;
import com.lab.air.flightops.Model.StatusUpdate;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

import java.util.List;

@Controller
public class FlightOpsUiController {

    private final FlightOpsRepository repo;
    private final FlightOpsService service;

    public FlightOpsUiController(FlightOpsRepository repo, FlightOpsService service) {
        this.repo = repo;
        this.service = service;
    }

    @Op("ui_list_flights")
    @GetMapping("/")
    public String index(@RequestParam(required = false) String status, @RequestParam(defaultValue = "48") int hoursAhead,
                        Model m) {
        m.addAttribute("status", status);
        m.addAttribute("hoursAhead", hoursAhead);
        try {
            String st = status == null || status.isBlank() ? null : status;
            m.addAttribute("flights", repo.list(st, FlightOpsApiController.clamp(hoursAhead, 1, 1440), 100));
            m.addAttribute("hot", repo.hot());
        } catch (DataAccessException e) {
            m.addAttribute("flights", List.of());
            m.addAttribute("hot", List.of());
            m.addAttribute("error", describe(e));
        }
        return "index";
    }

    @Op("ui_get_flight")
    @GetMapping("/ui/flights/{id}")
    public String flight(@PathVariable long id, Model m) {
        try {
            m.addAttribute("f", repo.get(id).orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok")));
            m.addAttribute("events", repo.events(id, 30));
        } catch (BusinessException | DataAccessException e) {
            m.addAttribute("error", describe(e));
        }
        return "flight";
    }

    @Op("ui_update_status")
    @PostMapping("/ui/flights/{id}/status")
    public String status(@PathVariable long id, @RequestParam String status, @RequestParam(required = false) String gate,
                         @RequestParam(required = false) Integer delayMin, RedirectAttributes ra) {
        try {
            String g = gate == null || gate.isBlank() ? null : gate.trim().toUpperCase();
            if (g != null && !g.matches("[A-Z][0-9]{1,3}")) {
                throw BusinessException.badRequest("GATE_FORMAT", "kapı biçimi ör. B12");
            }
            var r = service.updateStatus(id, new StatusUpdate(status, g, delayMin));
            ra.addFlashAttribute("msg", r.replayed() ? "Değişiklik yok" : "Durum güncellendi: " + r.flight().status());
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/flights/" + id;
    }

    @Op("ui_update_capacity")
    @PostMapping("/ui/flights/{id}/capacity")
    public String capacity(@PathVariable long id, @RequestParam int capacity, RedirectAttributes ra) {
        try {
            if (capacity < 1 || capacity > 1_000_000) {
                throw BusinessException.badRequest("CAPACITY_RANGE", "kapasite 1..1000000");
            }
            service.updateCapacity(id, capacity, 0);
            ra.addFlashAttribute("msg", "Kapasite güncellendi: " + capacity);
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/flights/" + id;
    }

    static String describe(Exception e) {
        if (e instanceof BusinessException b) {
            return b.code() + ": " + b.getMessage();
        }
        return DbErrors.classify(e) + ": " + DbErrors.rootMessage(e);
    }
}
