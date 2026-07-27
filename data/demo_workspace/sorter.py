from __future__ import annotations

"""Quick sort helper generated from a chat request."""

def quicksort(values: list[int]) -> list[int]:
    """Return a sorted copy of ``values`` using quicksort."""
    if len(values) <= 1:
        return values[:]
    pivot = values[len(values) // 2]
    left = [value for value in values if value < pivot]
    middle = [value for value in values if value == pivot]
    right = [value for value in values if value > pivot]
    return quicksort(left) + middle + quicksort(right)

def sort_values(values: list[int]) -> list[int]:
    """Convenience wrapper for quicksort."""
    return quicksort(list(values))

# Time complexity: average O(n log n), worst O(n^2).
# Space complexity: O(n) for recursive partitions.
