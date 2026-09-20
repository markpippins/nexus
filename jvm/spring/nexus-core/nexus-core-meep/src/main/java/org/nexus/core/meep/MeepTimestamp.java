package org.nexus.core.meep;

import java.time.Clock;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;

/**
 * Shared timestamp format for parity with the Python reference:
 * {@code datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} —
 * second precision (sub-second truncated), literal Z suffix. Used by the
 * lowering stamp and every CER event timestamp.
 */
public final class MeepTimestamp {
    private static final DateTimeFormatter FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC);

    private MeepTimestamp() {}

    public static String now(Clock clock) {
        return FORMAT.format(clock.instant().truncatedTo(ChronoUnit.SECONDS));
    }
}
