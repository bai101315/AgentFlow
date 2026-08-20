# 延迟工具加载是否有用？

全量：6242 tokens
初始：469 tokens
当前：42个MCP Tool，平均单工具 Schema 约 148.6 tokens；不同Tool Schema对应的token数是不同的

# 压力测试：每次promote最大的
初始：469 tokens，相比全量 6242，节省 92.49%
promote 3 个后：1786 tokens，节省 71.39%
promote 6 个后：2518 tokens，节省 59.66%
promote 9 个后：3040 tokens，节省 51.30%
promote 12 个后：3499 tokens，节省 43.94%


# 混合场景结果：
初始未 promote：469 / 6242 tokens，节省 92.49%
promote 3 个平均大小工具后：899 tokens，节省 85.60%
promote 6 个后：1311 tokens，节省 79.00%
promote 9 个后：1756 tokens，节省 71.87%
promote 12 个后：2112 tokens，节省 66.16%


