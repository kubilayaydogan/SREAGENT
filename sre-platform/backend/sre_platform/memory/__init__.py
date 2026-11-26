"""Agent Memory Management: Memory Manager abstraction, event publisher, policy, observed adapters.

Bkz. docs/memory/MEMORY_ARCHITECTURE.md. Bu paket mevcut memory mekanizmalarını (agent/memory.py MemoryService,
agent/working_memory.py, repositories/episodes.py, LangGraph checkpointer/Store) DEĞİŞTİRMEZ; onları gözlemler
(observer / adapter) ve yeni long-term kayıtları (sre.memory_records) yönetir.
"""
