# 📅 2026年3月23日 LeetCode 刷题总结

## 📊 概述
今天是2026年3月23日，作为一个勤奋的程序员，我在LeetCode上继续我的算法学习之旅。今天总共通过了 **9道** 题目，涵盖了动态规划、数组操作、矩阵处理、数学推理和博弈论等多个领域。以下是对每道题目的详细总结，包括应该学习的知识点、难点分析和解题代码。

## 📈 当天统计
| 难度等级 | 数量 | 占比 |
| -------- | ---- | ---- |
| 🟢 简单   | 3    | 33%  |
| 🟡 中等   | 6    | 67%  |
| 🔴 困难   | 0    | 0%   |

**主要知识点分布**: 动态规划 (5题) · 数组操作 (6题) · 矩阵处理 (4题) · 数学推理 (4题) · 博弈论 (1题)

---

## 🧩 题目总结

### 1. 🟡 爬楼梯 II (Climbing Stairs II)
**难度**: 中等 | **知识点**: 动态规划, 状态转移方程设计, 记忆化搜索优化

**🔍 难点分析**:
- 需要理解多步跳跃的代价计算 (1, 4, 9步对应+1, +4, +9代价)
- 状态转移方程的正确设计：`dp[i] = min(dp[i-1] + cost[i-1] + 1, dp[i-2] + cost[i-2] + 4, dp[i-3] + cost[i-3] + 9)`
- 处理边界条件和初始化

**💻 解题代码**:
```python
class Solution:
    def climbStairs(self, n: int, costs: List[int]) -> int:
        # 记忆化搜索
        @cache
        def dfs(i):
            if i <= 0:
                return 0
            return min(dfs(i-1)+1, dfs(i-2)+4, dfs(i-3)+9) + costs[i-1]
        return dfs(n)
```

---

### 2. 🟢 使用最小花费爬楼梯 (Min Cost Climbing Stairs)
**难度**: 简单 | **知识点**: 数组, 动态规划, 路径优化问题

**🔍 难点分析**:
- 理解从第0或第1阶开始的两种情况
- 状态转移：`dp[i] = min(dp[i-1] + cost[i-1], dp[i-2] + cost[i-2])`
- 避免重复计算和空间优化

**💻 解题代码**:
```python
class Solution:
    def minCostClimbingStairs(self, cost: List[int]) -> int:
        n = len(cost)
        f = [0] * (n+1)
        f[0] = 0
        f[1] = 0
        for i in range(2, n+1):
            f[i] = min(f[i-1] + cost[i-1], f[i-2] + cost[i-2])
        return f[n]
```

---

### 3. 🟢 爬楼梯 (Climbing Stairs)
**难度**: 简单 | **知识点**: 记忆化, 数学, 动态规划, 斐波那契数列

**🔍 难点分析**:
- 递归解法的时间复杂度优化（记忆化）
- 理解斐波那契数列在路径计数中的应用
- 空间复杂度从O(n)优化到O(1)

**💻 解题代码**:
```python
class Solution:
    def climbStairs(self, n: int) -> int:
        # 递推
        f = [0] * (n + 1)
        f[0] = 1
        f[1] = 1
        for i in range(2, n+1):
            f[i] = f[i-1] + f[i-2]
        return f[n]
```

---

### 4. 🟡 旋转图像 (Rotate Image)
**难度**: 中等 | **知识点**: 数组, 数学, 矩阵, 原地操作

**🔍 难点分析**:
- 理解矩阵旋转的数学原理（转置+翻转）
- 原地修改的要求，避免额外空间
- 处理不同维度的矩阵边界

**💻 解题代码**:
```python
class Solution:
    def rotate(self, matrix: List[List[int]]) -> None:
        matrix[:] = [col[::-1] for col in zip(*matrix)]
```

---

### 5. 🟢 判断矩阵经轮转后是否一致 (Determine Whether Matrix Can Be Obtained By Rotation)
**难度**: 简单 | **知识点**: 数组, 矩阵, 旋转对称性

**🔍 难点分析**:
- 理解90度旋转的4种可能情况
- 矩阵比较的效率
- 处理不同大小的矩阵

**💻 解题代码**:
```python
class Solution:
    def findRotation(self, mat: List[List[int]], target: List[List[int]]) -> bool:
        cur = mat
        for _ in range(4):
            if cur == target:
                return True
            cur = [list(col[::-1]) for col in zip(*cur)]
        return False
```

---

### 6. 🟡 构造奇偶一致的数组 II (Construct Uniform Parity Array II)
**难度**: 中等 | **知识点**: 数组, 数学, 奇偶性判断

**🔍 难点分析**:
- 理解通过差值改变奇偶性的机制
- 选择最小元素进行差值计算的策略
- 处理所有元素同为奇数或偶数的情况

**💻 解题代码**:
```python
class Solution:
    def uniformArray(self, nums1: list[int]) -> bool:
        odd = []
        even = []
        n = len(nums1)
        for x in nums1:
            if x % 2:
                odd.append(x)
            else: 
                even.append(x)
        if len(odd) == n or len(even) == n:
            return True
        return min(even) > min(odd)
```

---

### 7. 🟡 石子游戏 (Stone Game)
**难度**: 中等 | **知识点**: 数组, 数学, 动态规划, 博弈论

**🔍 难点分析**:
- 理解先手优势在偶数堆石头中的必然性
- 动态规划状态设计：dp[i][j]表示从i到j的最优得分
- 博弈论中的最优策略分析

**💻 解题代码**:
```python
class Solution:
    def stoneGame(self, piles: List[int]) -> bool:
        return True
```

---

### 8. 🟡 严格回文的数字 (Strictly Palindromic Number)
**难度**: 中等 | **知识点**: 脑筋急转弯, 数学, 双指针, 进制转换

**🔍 难点分析**:
- 理解在不同进制下回文的要求
- 发现n在base (n-2)下总是"12"，不是回文
- 数学推理而非暴力枚举

**💻 解题代码**:
```python
class Solution:
    def isStrictlyPalindromic(self, n: int) -> bool:
        return False
```

---

### 9. 🟡 矩阵的最大非负积 (Maximum Non Negative Product in a Matrix)
**难度**: 中等 | **知识点**: 数组, 动态规划, 矩阵, 路径最大化

**🔍 难点分析**:
- 同时跟踪最大值和最小值（处理负数）
- 状态设计：每个位置保存最大和最小乘积
- 处理0和负数的边界情况

**💻 解题代码**:
```python
class Solution:
    def maxProductPath(self, grid: List[List[int]]) -> int:
        n, m = len(grid), len(grid[0])
        mod = 10 ** 9 + 7
        @cache
        def dfs(i, j):
            val = grid[i][j]
            if i == n - 1 and j == m - 1:
                return val, val
            mx, mn = -inf, inf
            for x, y in [(i+1, j), (i, j+1)]:
                if 0 <= x < n and 0 <= y < m:
                    mx_t, mn_t = dfs(x, y)
                    mx = max(mx, mx_t * val, mn_t * val)
                    mn = min(mn, mx_t * val, mn_t * val)
            return mx, mn
        mx, mn = dfs(0, 0)
        return mx % mod if mx >= 0 else -1
```

---

## 🎯 总体总结

### 📈 当天重点
- **🔄 动态规划**: 5道题目涉及DP，包括路径优化、博弈和矩阵路径
- **📊 数组与矩阵操作**: 6道题目涉及数组处理和矩阵变换
- **🧮 数学推理**: 涉及奇偶性、进制转换和博弈论

### 🌟 学习收获
1. ✅ 动态规划在路径和博弈问题中的应用更加熟练
2. ✅ 矩阵操作技巧得到提升，特别是原地旋转和路径搜索
3. ✅ 数学思维在算法题中的重要性得到强化

### 🚀 改进方向
- 🔄 需要加强博弈论的理解
- 📊 矩阵DP的状态设计还需要更多练习
- 🧮 数学推理题的解题速度有待提升

### 📅 明日计划
- 🔄 继续练习动态规划题目
- 🎲 深入学习博弈论相关算法
- 📊 复习矩阵操作的各种技巧

---
*✨ 总结日期: 2026年3月23日*  
*🎯 总通过题目: 9道*  
*🏷️ 主要知识点: 动态规划、数组、矩阵、数学*  
*💪 坚持就是胜利！*