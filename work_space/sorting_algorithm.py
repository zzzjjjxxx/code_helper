def quicksort(arr):
    """
    Sorts an array using the quicksort algorithm.
    Time complexity: O(n log n) average, O(n^2) worst-case.
    Space complexity: O(n) due to creating new lists (not in-place).
    An in-place version would have O(log n) space for recursion stack.
    """
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)

if __name__ == "__main__":
    test_arr = [3, 6, 8, 10, 1, 2, 1]
    print("Original:", test_arr)
    sorted_arr = quicksort(test_arr)
    print("Sorted:", sorted_arr)
    # Complexity explanation:
    # Time:
    # - Average case: O(n log n) – partition splits evenly
    # - Worst case: O(n^2) – already sorted or many duplicates, partitions are unbalanced
    # Space:
    # - This implementation uses extra lists for left, middle, right → O(n)
    # - Recursion depth is O(log n) on average, O(n) worst.
