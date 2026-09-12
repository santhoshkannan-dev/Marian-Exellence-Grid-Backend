"""
users/evaluation_engine.py
Re-exports authoritative evaluation_engine symbols.
"""
from evaluation_engine import (
    calculate_pass_bonus,
    calculate_academics_marks,
    resolve_evaluator_marks,
)

__all__ = [
    'calculate_pass_bonus',
    'calculate_academics_marks',
    'resolve_evaluator_marks',
]
