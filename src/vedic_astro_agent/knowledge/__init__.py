"""Interpretation knowledge base.

The interpreter combines *computed facts* (from the evidence ledger) with the fixed
traditional meanings stored here. Nothing in this package can introduce chart-specific
claims on its own — it only supplies the conventional reading vocabulary for facts that
PyJHora already computed.
"""

from vedic_astro_agent.knowledge import houses, planets, signs  # noqa: F401
