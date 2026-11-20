package com.lab.air.chaos.web;

import com.lab.air.chaos.engine.ExperimentService;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@Controller
public class ChaosUiController {

    private final ExperimentService service;

    public ChaosUiController(ExperimentService service) {
        this.service = service;
    }

    @GetMapping("/")
    public String panel() {
        return "panel";
    }

    @GetMapping("/experiments")
    public String list(Model m) {
        m.addAttribute("experiments", service.list());
        return "experiments";
    }

    @GetMapping("/experiments/{id}")
    public String detail(@PathVariable String id, Model m) {
        service.get(id).ifPresent(r -> m.addAttribute("r", r));
        return "experiment";
    }
}
