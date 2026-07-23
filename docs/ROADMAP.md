# Roadmap (post-MVP, ordered)

From `docs/SCOPE.md`:

1. SNMPv3 support (requires credentials, hence configuration — deliberately post-MVP).
2. Export to ticketing systems.
3. Automatic MIB enrichment (readable names without user obligation).
4. Precursor/gauge layer (performance-monitoring signals ahead of alarms).
5. PostgreSQL / NATS if scale demands it (queue and storage already sit behind
   interfaces).

Ideas noted during the build (one line each, per the anti-overengineering rules):

- Unlearning/expiry for learned raise/clear pairs (currently permanent once promoted).
- Second read-only SQLite connection for the API if UI latency under storms matters.
- WebSocket push for the UI instead of 2.5 s polling.
- Replay tool: read pcap captures of real trap traffic, not only JSON fixtures.
- Root-cause hint confidence score surfaced in the UI (precedence margin).
- Situation timeline view (alarms on a time axis) in the UI.
