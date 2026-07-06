"""
冒泡排序（Bubble Sort）实现

思路：反复遍历数组，比较相邻元素
1. 每一轮将当前未排序部分的最大值"冒泡"到末尾
2. 经过 n-1 轮后，整个数组有序

特点：
- 稳定排序：相等元素不会交换相对顺序
- 最好情况 O(n)（已排序 + 提前终止），最坏 O(n²)
- 空间复杂度 O(1)（原地排序，无需额外空间）
"""

from typing import List


def bubblesort(arr: List[int]) -> List[int]:
    """
    冒泡排序（返回新列表，不修改原数组）
    时间复杂度：O(n²)
    空间复杂度：O(n)（生成了新列表）
    """
    result = arr[:]
    n = len(result)

    for i in range(n - 1):
        # 每轮把当前未排序部分的最大值放到末尾
        for j in range(n - 1 - i):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]

    return result


def bubblesort_inplace(arr: List[int]) -> List[int]:
    """
    原地冒泡排序
    时间复杂度：O(n²)
    空间复杂度：O(1)

    直接在 arr 上排序，无需复制。
    """
    n = len(arr)

    for i in range(n - 1):
        for j in range(n - 1 - i):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]

    return arr


def bubblesort_optimized(arr: List[int]) -> List[int]:
    """
    优化冒泡排序（提前终止 + 记录最后交换位置）
    时间复杂度：最好 O(n)，最坏 O(n²)
    空间复杂度：O(1)

    两个优化：
    1. 如果某一轮没有发生任何交换，说明已经有序，提前结束
    2. 记录每轮最后一次交换的位置，下一轮只需扫描到这里
    """
    n = len(arr)
    if n <= 1:
        return arr

    # 初始扫描边界为数组末尾
    end = n - 1

    while end > 0:
        last_swap = 0  # 记录本轮最后一次交换的位置

        for j in range(end):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                last_swap = j  # 更新最后交换位置

        # 下一轮只需扫描到 last_swap，
        # 因为 last_swap 之后已经有序
        end = last_swap

    return arr


if __name__ == "__main__":
    test_cases = [
        [3, 6, 8, 10, 1, 2, 1],
        [5],
        [],
        [1, 2, 3, 4, 5],
        [5, 4, 3, 2, 1],
        [42, 42, 42, 42],
    ]

    print("=" * 50)
    print("非原地冒泡排序")
    print("=" * 50)
    for case in test_cases:
        result = bubblesort(case)
        assert result == sorted(case), f"Failed on {case}"
        print(f"原始: {case} → 排序后: {result}")

    print()
    print("=" * 50)
    print("原地冒泡排序")
    print("=" * 50)
    for case in test_cases:
        case_copy = case[:]
        bubblesort_inplace(case_copy)
        assert case_copy == sorted(case), f"Failed on {case}"
        print(f"原始: {case} → 排序后: {case_copy}")

    print()
    print("=" * 50)
    print("优化冒泡排序（提前终止）")
    print("=" * 50)
    for case in test_cases:
        case_copy = case[:]
        bubblesort_optimized(case_copy)
        assert case_copy == sorted(case), f"Failed on {case}"
        print(f"原始: {case} → 排序后: {case_copy}")

    print()
    print("全部测试通过 ✅")
