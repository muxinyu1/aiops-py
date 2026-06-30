package io.niceseason.gulimall.member.tracing;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class TraceStore {

    private static final long TTL_MS = 5 * 60_000L;

    private static class Entry {
        final long createdAt;
        final List<SpanRecord> spans;
        Entry(long createdAt, List<SpanRecord> spans) {
            this.createdAt = createdAt; this.spans = spans;
        }
        long createdAt() { return createdAt; }
        List<SpanRecord> spans() { return spans; }
    }

    private final ConcurrentHashMap<String, Entry> store = new ConcurrentHashMap<>();

    public void add(String traceId, SpanRecord record) {
        store.computeIfAbsent(traceId, k ->
            new Entry(System.currentTimeMillis(),
                      Collections.synchronizedList(new ArrayList<>()))
        ).spans().add(record);
    }

    public List<SpanRecord> getAndRemove(String traceId) {
        Entry entry = store.remove(traceId);
        return entry == null ? Collections.emptyList() : new ArrayList<>(entry.spans());
    }

    @Scheduled(fixedDelay = 60_000)
    public void cleanup() {
        long cutoff = System.currentTimeMillis() - TTL_MS;
        store.entrySet().removeIf(e -> e.getValue().createdAt() < cutoff);
    }
}
