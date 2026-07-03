"""
快速排序（Quick Sort）实现

思路：分治
1. 选一个基准（pivot）
2. 把比 pivot 小的放左边，大的放右边
3. 递归对左右两边做同样的事
"""

from typing import List


def quicksort(arr: List[int]) -> List[int]:
    """
    快速排序（返回新列表，不修改原数组）
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(n)（生成了新列表）
    """
    if len(arr) <= 1:
        return arr

    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]

    return quicksort(left) + middle + quicksort(right)


def quicksort_inplace(arr: List[int], low: int = 0, high: int = None) -> List[int]:
    """
    原地快速排序（Lomuto 分区方案）
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(log n)（递归栈）

    直接用 arr 排序更加快速，无需复制。
    """
    if high is None:
        high = len(arr) - 1

    if low < high:
        pi = _partition(arr, low, high)
        quicksort_inplace(arr, low, pi - 1)
        quicksort_inplace(arr, pi + 1, high)

    return arr


def _partition(arr: List[int], low: int, high: int) -> int:
    """
    Lomuto 分区：选最后一个元素为 pivot，
    把小的放左边，大的放右边，返回 pivot 最终位置。
    """
    pivot = arr[high]
    i = low - 1  # i 指向"小于 pivot 的区域"的末尾

    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]

    # 把 pivot 放到正确的位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


if __name__ == "__main__":
    # 测试
    test_cases = [
        [3, 6, 8, 10, 1, 2, 1],
        [5],
        [],
        [1, 2, 3, 4, 5],
        [5, 4, 3, 2, 1],
        [42, 42, 42, 42],
    ]

    for case in test_cases:
        # 非原地版本
        sorted_copy = quicksort(case)
        # 原地版本
        case_copy = case[:]
        quicksort_inplace(case_copy)
        assert sorted_copy == sorted(case), f"Failed on {case}"
        print(f"原始: {case} → 排序后: {sorted_copy}")
