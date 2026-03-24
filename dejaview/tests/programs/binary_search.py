def binary_search(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo

arr = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
i = binary_search(arr, 23)
print(i, arr[i])
assert arr[i] == 23
