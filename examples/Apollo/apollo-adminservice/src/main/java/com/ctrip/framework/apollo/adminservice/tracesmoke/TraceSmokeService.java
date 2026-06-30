package com.ctrip.framework.apollo.adminservice.tracesmoke;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

@Profile("trace-smoke")
@Service
public class TraceSmokeService {

    public Map<String, Object> smoke() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        body.put("component", "trace-smoke");
        body.put("message", "execution trace smoke endpoint");
        return body;
    }
}
