"""brokers/ -- broker execution abstraction (Tradier auto-execution).

Parked-then-building per docs/TRADIER_MIGRATION.md. order_mapper is pure and
risk-free; the live client is sandbox-gated behind TRADIER_LIVE. STRICTLY
defined-risk: the mapper/whitelist reject naked structures.
"""
