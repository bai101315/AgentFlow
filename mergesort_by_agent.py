"""
归并排序（Merge Sort）实现

思路：分治
1. 把数组从中间分成两半
2. 递归对左右两半分别排序
3. 把排好序的两半合并（merge）成一个有序数组
"""

from typing import List


def mergesort(arr: List[int]) -> List[int]:
    """
    归并排序（返回新列表，不修改原数组）
    时间复杂度：O(n log n)
    空间复杂度：O(n)（生成了新列表）
    """
    if len(arr) <= 1:
        return arr

    mid = len(arr) // 2
    left = mergesort(arr[:mid])
    right = mergesort(arr[mid:])

    return _merge(left, right)


def mergesort_inplace(arr: List[int], low: int = 0, high: int = None) -> List[int]:
    """
    原地归并排序（直接在原数组上修改，但合并时使用临时数组）
    时间复杂度：O(n log n)
    空间复杂度：O(n)（合并时的临时数组）

    直接用 arr 排序更加直观，但合并仍需额外空间。
    """
    if high is None:
        high = len(arr) - 1

    if low < high:
        mid = (low + high) // 2
        mergesort_inplace(arr, low, mid)
        mergesort_inplace(arr, mid + 1, high)
        _merge_inplace(arr, low, mid, high)

    return arr


def _merge(left: List[int], right: List[int]) -> List[int]:
    """
    合并两个有序数组，返回一个新的有序数组。
    使用双指针法，依次比较并放入结果中。
    """
    result = []
    i = j = 0

    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1

    # 把剩余的元素追加到末尾
    result.extend(left[i:])
    result.extend(right[j:])

    return result


def _merge_inplace(arr: List[int], low: int, mid: int, high: int) -> None:
    """
    原地合并：将 arr[low..mid] 和 arr[mid+1..high] 两个有序段合并。
    先把左右两边分别复制到临时数组，再双指针合并回原数组。
    """
    left = arr[low:mid + 1]
    right = arr[mid + 1:high + 1]

    i = j = 0
    k = low

    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            arr[k] = left[i]
            i += 1
        else:
            arr[k] = right[j]
            j += 1
        k += 1

    # 把剩余的元素写回原数组
    while i < len(left):
        arr[k] = left[i]
        i += 1
        k += 1

    while j < len(right):
        arr[k] = right[j]
        j += 1
        k += 1


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
        sorted_copy = mergesort(case)
        # 原地版本
        case_copy = case[:]
        mergesort_inplace(case_copy)
        assert sorted_copy == sorted(case), f"Failed on {case}"
        print(f"原始: {case} → 排序后: {sorted_copy}")
