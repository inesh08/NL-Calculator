"""Shared LLM call instrumentation used by both application entry points."""

from prompt import Operation, get_operation
from storage import record_llm_activity


def get_tracked_operation(question: str) -> Operation:
    try:
        operation = get_operation(question)
    except Exception:
        record_llm_activity("failure")
        raise
    record_llm_activity("success")
    return operation
