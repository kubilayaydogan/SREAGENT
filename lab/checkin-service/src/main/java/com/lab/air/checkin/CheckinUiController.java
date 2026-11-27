package com.lab.air.checkin;

import com.lab.air.common.BusinessException;
import com.lab.air.common.DbErrors;
import com.lab.air.common.Op;
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
public class CheckinUiController {

    private final CheckinService service;
    private final CheckinRepository repo;

    public CheckinUiController(CheckinService service, CheckinRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("ui_index")
    @GetMapping("/")
    public String index(Model m) {
        try {
            m.addAttribute("candidates", repo.candidates(null, false, 15));
        } catch (DataAccessException e) {
            m.addAttribute("candidates", List.of());
            m.addAttribute("error", describe(e));
        }
        return "index";
    }

    @GetMapping("/ui/lookup")
    public String lookup(@RequestParam String pnr) {
        return "redirect:/ui/checkin/" + pnr.trim().toUpperCase();
    }

    @Op("ui_lookup_booking")
    @GetMapping("/ui/checkin/{pnr}")
    public String view(@PathVariable String pnr, Model m) {
        try {
            var v = service.get(pnr);
            m.addAttribute("c", v);
            m.addAttribute("seats", repo.seatMap(v.flightId()));
        } catch (BusinessException | DataAccessException e) {
            m.addAttribute("error", describe(e));
        }
        return "checkin";
    }

    @Op("ui_checkin")
    @PostMapping("/ui/checkin/{pnr}")
    public String checkIn(@PathVariable String pnr, @RequestParam(defaultValue = "WEB") String channel,
                          RedirectAttributes ra) {
        try {
            var r = service.checkIn(pnr, channel, 0);
            ra.addFlashAttribute("msg", r.replayed() ? "Check-in zaten yapılmış" : "Check-in tamamlandı");
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/checkin/" + pnr;
    }

    @Op("ui_assign_seat")
    @PostMapping("/ui/checkin/{pnr}/seat")
    public String seat(@PathVariable String pnr, @RequestParam String seatNo, RedirectAttributes ra) {
        try {
            String s = seatNo.trim().toUpperCase();
            if (!s.matches("[1-9][0-9]?[A-F]")) {
                throw BusinessException.badRequest("SEAT_FORMAT", "koltuk biçimi ör. 12C");
            }
            var r = service.assignSeat(pnr, s);
            ra.addFlashAttribute("msg", r.replayed() ? "Koltuk zaten atanmış" : "Koltuk atandı: " + r.checkin().seatNo());
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/checkin/" + pnr;
    }

    static String describe(Exception e) {
        if (e instanceof BusinessException b) {
            return b.code() + ": " + b.getMessage();
        }
        return DbErrors.classify(e) + ": " + DbErrors.rootMessage(e);
    }
}
