# Architecture

see README.md «Архитектура» + contracts/meeting_contracts.schema.json

Runtime shape: one container = polling bot + pipeline (single trigger; state in PostgreSQL -> restart-safe, resume-capable). meeting-db stays on the internal Docker network.

Broker gateway transports:
- magos_local: direct Access & Integration Broker Python API (capability checks, scoped handles, one-shot workers);
- container_env: canonical scoped env delivery at deploy (same variables the Broker injects into one-shot workers locally); capability map mirrors the Broker registry.
