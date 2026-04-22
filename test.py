import sys
from math import inf
import math
from collections import deque, Counter, defaultdict
import heapq
import bisect
from functools import cache

def solve():
    input = sys.stdin.readline
    n, x = list(map(int, input().split()))
    nums = list(map(int, input().split()))

    sm = [0] * (n + 1)
    resl, resr = 0, n-1

    sm = 0
    i = j = 0
    for j, num in enumerate(nums):
        sm += num
        while sm >= x:
            if resr - resl >= j - i:
                resl = i
                resr = j
            sm -= nums[i]
            i += 1
    
    print(resl, resr)



solve()
