package org.dromara.auth.tracesmoke;

import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

@Profile("trace-smoke")
@RestController
public class TraceSmokeController {

    @GetMapping({"/__trace_smoke", "/api/trace-smoke"})
    public Map<String, Object> smoke() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        body.put("component", "trace-smoke");
        body.put("message", "execution trace smoke endpoint");
        return body;
    }
}
